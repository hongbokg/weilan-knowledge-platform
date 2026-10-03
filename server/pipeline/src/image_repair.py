"""Checkpointed local image-text repair. Originals and good pages are immutable.

Only image_text_requires_ocr is automatically repaired. Tables, credential flags,
unknown coordinates, absent OCR text and other quality failures stay quarantined.
No cloud model calls. Publication remains in the existing authenticated API worker.
"""
import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import threading
import time

from image_ingest import visible_text
from sync import Sync, Halt, atomic, sensitive, quality

POLICY = 'image-text-cpu-v2-orientation'
FLAG = 'image_text_requires_ocr'
PLACEHOLDER = '[图片保留于原文件]'
OFFICE_PLACEHOLDER = '[图片保留于原文件，图片内容尚未单独识别]'
MODEL_ROOT = Path('/opt/paddleocr-local/models/official_models')


class RepairInfrastructureError(RuntimeError):
    """Do not turn a broken runtime into hundreds of document quality failures."""


def flagged(md, q):
    return FLAG in q.get('errors', []) or (
        (PLACEHOLDER in md or OFFICE_PLACEHOLDER in md) and
        len(visible_text(md.replace(OFFICE_PLACEHOLDER, '').replace(PLACEHOLDER, ''))) < 100)


def checked_box(box):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError('image_coordinates_missing')
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in box):
        raise ValueError('image_coordinates_invalid')
    x0, y0, x1, y1 = box
    if not 0 <= x0 < x1 <= 1 or not 0 <= y0 < y1 <= 1:
        raise ValueError('image_coordinates_not_normalized')
    return list(box)


def qualify(lines):
    # Detect secrets in ALL recognized lines, even those below confidence cutoff.
    if sensitive('\n'.join(x['text'] for x in lines)):
        return 'sensitive_content_admin_review'
    useful = [x for x in lines if visible_text(x['text'])]
    if not useful or len(visible_text('\n'.join(x['text'] for x in useful))) < 10:
        return 'image_ocr_no_text_needs_visual_review'
    if any(not isinstance(x['score'], (int, float)) or not math.isfinite(x['score']) or not .75 <= x['score'] <= 1 for x in useful):
        return 'image_ocr_low_confidence_review'
    return None


def looks_like_grid(image):
    """Conservative routing guard: text-only fallback must not flatten tables."""
    import cv2
    import numpy as np
    gray = np.array(image.convert('L'))
    scale = min(1, 1600/max(gray.shape))
    if scale < 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale)
    _, ink = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)
    h, w = ink.shape
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, max(30, w//4)), np.uint8))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((max(30, h//4), 1), np.uint8))
    def groups(values):
        active = values > 0
        return int(np.count_nonzero(active & ~np.r_[False, active[:-1]]))
    return groups(horizontal.sum(axis=1)) >= 3 and groups(vertical.sum(axis=0)) >= 3


def map_polygon(poly, crop, width, height):
    if not isinstance(poly, list) or len(poly) < 4:
        raise ValueError('ocr_coordinates_missing')
    for pair in poly:
        if len(pair) != 2 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in pair):
            raise ValueError('ocr_coordinates_invalid')
        if not 0 <= pair[0] <= width or not 0 <= pair[1] <= height:
            raise ValueError('ocr_coordinates_outside_image')
    x0, y0, x1, y1 = checked_box(crop)
    return [[x0 + p[0]/width*(x1-x0), y0 + p[1]/height*(y1-y0)] for p in poly]


def unrotate_polygon(poly, width, height, angle):
    """Inverse of PaddleX rotate_image's affine transform; no invented boxes."""
    import cv2
    import numpy as np
    if angle not in (0, 90, 180, 270):
        raise ValueError('unsupported_ocr_rotation')
    mat = cv2.getRotationMatrix2D((width/2, height/2), angle, 1.0)
    cosine, sine = abs(mat[0, 0]), abs(mat[0, 1])
    new_width = int(height*sine+width*cosine)
    new_height = int(height*cosine+width*sine)
    mat[0, 2] += (new_width-width)/2
    mat[1, 2] += (new_height-height)/2
    inverse = cv2.invertAffineTransform(mat)
    points = np.asarray(poly, dtype=float)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError('ocr_coordinates_invalid')
    raw = np.c_[points, np.ones(4)] @ inverse.T
    if (raw < -2).any() or (raw[:, 0] > width+2).any() or (raw[:, 1] > height+2).any():
        raise ValueError('ocr_coordinates_outside_image')
    raw[:, 0] = np.clip(raw[:, 0], 0, width)
    raw[:, 1] = np.clip(raw[:, 1], 0, height)
    return raw.tolist()


def _ocr_child(connection):
    # Explicit local paths prohibit model downloads; device=cpu and the systemd
    # device sandbox prohibit this auxiliary worker from consuming A2 VRAM.
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            import importlib.metadata
            import paddle
            from paddleocr import PaddleOCR
            if paddle.__version__ != '3.2.2' or paddle.is_compiled_with_cuda():
                raise ValueError('unexpected_cpu_runtime')
            if importlib.metadata.version('paddleocr') != '3.7.0' or importlib.metadata.version('paddlex') != '3.7.2':
                raise ValueError('cpu_ocr_dependency_version_changed')
            model = PaddleOCR(device='cpu', enable_mkldnn=True, cpu_threads=6,
                text_detection_model_name='PP-OCRv5_server_det',
                text_detection_model_dir=str(MODEL_ROOT/'PP-OCRv5_server_det'),
                text_recognition_model_name='PP-OCRv5_server_rec',
                text_recognition_model_dir=str(MODEL_ROOT/'PP-OCRv5_server_rec'),
                doc_orientation_classify_model_name='PP-LCNet_x1_0_doc_ori',
                doc_orientation_classify_model_dir=str(MODEL_ROOT/'PP-LCNet_x1_0_doc_ori'),
                use_doc_orientation_classify=True, use_doc_unwarping=False,
                use_textline_orientation=False, text_recognition_batch_size=2)
            connection.send({'ready': True})
        except Exception as error:
            # Constructor has no document input, so this message cannot contain
            # OCR contents. Keep it private for deployment diagnosis only.
            connection.send({'error': type(error).__name__, 'startup_detail': str(error)[:300]})
            return
        while True:
            try:
                path = connection.recv()
                if path is None:
                    return
                result = model.predict(path)
                from PIL import Image
                with Image.open(path) as source_image:
                    width, height = source_image.size
                lines = []
                for page in result:
                    data = page.json.get('res', page.json)
                    texts = data.get('rec_texts', [])
                    scores = data.get('rec_scores', [])
                    boxes = data.get('rec_polys', [])
                    angle = data.get('doc_preprocessor_res', {}).get('angle', 0)
                    if not len(texts) == len(scores) == len(boxes):
                        raise ValueError('ocr_result_lengths_disagree')
                    for text, score, box in zip(texts, scores, boxes):
                        lines.append({'text': str(text), 'score': float(score),
                            'polygon': unrotate_polygon(box, width, height, angle), 'orientation_degrees': angle})
                connection.send({'lines': lines})
            except EOFError:
                return
            except Exception as error:
                connection.send({'error': type(error).__name__, 'code': str(error) if re.fullmatch('[a-z_]+', str(error)) else None})


class CPUOCR:
    def __init__(self):
        self.process = None
        self.connection = None

    def close(self):
        if self.process:
            try:
                if self.connection:
                    self.connection.send(None)
            except (BrokenPipeError, EOFError, OSError):
                pass
            self.process.join(5)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(5)
        if self.connection:
            self.connection.close()
        self.process = self.connection = None

    def run(self, png):
        if self.process is None or not self.process.is_alive():
            self.close()
            context = multiprocessing.get_context('spawn')
            self.connection, child = context.Pipe()
            self.process = context.Process(target=_ocr_child, args=(child,), daemon=True)
            self.process.start()
            child.close()
            if not self.connection.poll(180):
                self.close()
                raise RepairInfrastructureError('cpu_ocr_model_start_timeout')
            ready = self.connection.recv()
            if not ready.get('ready'):
                self.close()
                print(json.dumps({'model_start_error': ready.get('error'), 'detail': ready.get('startup_detail')}), flush=True)
                raise RepairInfrastructureError('cpu_ocr_model_start_failed')
        self.connection.send(str(png))
        if not self.connection.poll(180):
            self.close()
            raise ValueError('cpu_ocr_page_timeout')
        response = self.connection.recv()
        if 'error' in response:
            raise ValueError(response.get('code') or 'cpu_ocr_inference_failed_'+response['error'].lower())
        return response['lines']


class Repair:
    def __init__(self, sync):
        self.sync = sync
        self.root = sync.archive/'image-repair'
        self.root.mkdir(exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.root/'state.db', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('pragma journal_mode=WAL')
        self.db.execute('pragma synchronous=FULL')
        self.db.execute('create table if not exists jobs(revision text primary key,state text,reason text,updated real,processed integer default 0)')
        self.db.commit()
        self.ocr = CPUOCR()

    def close(self):
        self.ocr.close()
        self.db.close()

    def mark(self, revision, state, reason=None, count=0):
        self.db.execute('insert into jobs values(?,?,?,?,?) on conflict(revision) do update set state=excluded.state,reason=excluded.reason,updated=excluded.updated,processed=jobs.processed+excluded.processed',
            (revision, state, reason, time.time(), count))
        self.db.commit()
        # Only numeric statistics/codes/opaque IDs. Never OCR text or source names.
        print(json.dumps({'repair': revision, 'state': state, 'reason': reason, 'processed': count}), flush=True)

    def quarantine_secret(self, rev):
        # Newly discovered credentials must update the authoritative flags used
        # by catalogue/raw-file permissions, not just this auxiliary task ledger.
        self.sync.db.execute('begin immediate')
        try:
            self.sync.db.execute("update revisions set error='sensitive_content_admin_review' where id=? and state='review' and error=?", (rev['id'], FLAG))
            self.sync.db.execute("update sources set error='sensitive_content_admin_review' where sha=? and state='review' and error=?", (rev['sha'], FLAG))
            self.sync.db.commit()
        except Exception:
            self.sync.db.rollback()
            raise
        self.sync.event('image_repair_credential_quarantined', rev['id'], {'published': False})

    def candidates(self, limit, include_validated=False):
        rows = self.sync.db.execute("select * from revisions where state='review' and error=? order by created desc", (FLAG,)).fetchall()
        selected = []
        for row in rows:
            status = self.db.execute('select state from jobs where revision=?', (row['id'],)).fetchone()
            if status and (status['state'] in ('review', 'promoted') or
                    (status['state'] == 'validated' and not include_validated)):
                continue
            selected.append(row)
            if len(selected) >= limit:
                break
        return selected

    def source(self, rev):
        for row in self.sync.db.execute("select * from sources where sha=? and missing=0 and state='review' and error=? order by modified desc", (rev['sha'], FLAG)):
            path = row['path']
            if sensitive(path) or self.sync.excluded(path) or self.sync.target_kb(path) != rev['kb']:
                continue
            if not any(path == root.rstrip('/') or path.startswith(root.rstrip('/')+'/') for root in self.sync.cfg['roots']):
                continue
            archive = self.sync.archive/'objects'/rev['sha'][:2]/rev['sha']
            if row['archive'] != str(archive) or not archive.is_file() or archive.stat().st_size != row['size']:
                continue
            return row, archive
        raise ValueError('repair_source_unavailable')

    def repair_pdf_page(self, source, original, draft, number):
        from PIL import Image
        from render_cpu import render_page
        draft.mkdir(parents=True, exist_ok=True)
        q = json.loads((original/'quality.json').read_text())
        md = (original/'clean.md').read_text()
        structured = json.loads((original/'result.json').read_text())
        blocks = [b for p in structured.get('pages', []) for b in p.get('blocks', [])]
        if any(b.get('type') == 'table' for b in blocks):
            # Text-only OCR must never reinterpret tables as unstructured prose.
            raise ValueError('image_page_contains_table_review')
        # This specific flag means the WHOLE page contains <100 text characters.
        # Image blocks can be merely seals/logos while the actual missed text is
        # elsewhere or rotated. Full-page independent OCR verifies the page;
        # OCRing only the seal would falsely report "no text" for certificates.
        boxes = [[0, 0, 1, 1]]
        results = []
        png = self.sync.ram/(draft.parent.name+'-repair-page.png')
        crop_path = png.with_name(png.stem+'-crop.png')
        try:
            self.sync.guard()
            render_page(str(source), number-1, str(png))
            with Image.open(png) as image:
                width, height = image.size
                if width*height > 4_000_000:
                    raise ValueError('repair_large_image_requires_tiling')
                for box in boxes:
                    self.sync.guard()
                    pixels = (math.floor(box[0]*width), math.floor(box[1]*height),
                        math.ceil(box[2]*width), math.ceil(box[3]*height))
                    with image.crop(pixels) as crop:
                        if looks_like_grid(crop):
                            raise ValueError('image_table_requires_structured_repair')
                        crop.convert('RGB').save(crop_path)
                        lines = self.ocr.run(crop_path)
                        actual_box = [pixels[0]/width, pixels[1]/height, pixels[2]/width, pixels[3]/height]
                        for line in lines:
                            line['page_polygon'] = map_polygon(line['polygon'], actual_box, crop.width, crop.height)
                    failure = qualify(lines)
                    if failure == 'sensitive_content_admin_review' or sensitive(md):
                        raise ValueError('sensitive_content_admin_review')
                    atomic(draft/'review-ocr.json', {'policy': POLICY, 'bbox': actual_box,
                        'coordinate_unit': 'normalized_page', 'lines': lines})
                    if failure:
                        raise ValueError(failure)
                    results.append({'bbox': actual_box, 'coordinate_unit': 'normalized_page', 'lines': lines})
            return self.write_page(original, draft, md, q, results, structured)
        finally:
            png.unlink(missing_ok=True)
            crop_path.unlink(missing_ok=True)

    def write_page(self, original, draft, md, q, results, structured=None):
        text = '\n\n'.join('\n'.join(line['text'] for line in region['lines']) for region in results)
        if sensitive(md+'\n'+text):
            raise ValueError('sensitive_content_admin_review')
        md = md.replace(OFFICE_PLACEHOLDER, '[已执行图片文字补充识别，原图保留于 NAS]')
        md = md.replace(PLACEHOLDER, '[已执行整页文字补充识别，原图保留于 NAS]')
        md += '\n\n### 图片文字补充识别\n\n'+text
        errors = [e for e in q.get('errors', []) if e != FLAG]
        errors.extend(quality(md, [{'type': 'text', 'content': text}], '', False))
        if errors:
            raise ValueError('repair_other_quality_failed')
        q.update(errors=[], image_repair_policy=POLICY, image_repair_regions=len(results))
        if structured:
            pages = structured.get('pages', [])
            if len(pages) != 1:
                raise ValueError('repair_page_structure_ambiguous')
            # Retain original block coordinates; supplemental coordinates live
            # separately with an explicit unit instead of mixing coordinate systems.
            structured['image_ocr_supplement'] = results
            atomic(draft/'result.json', structured)
        elif (original/'result.json').exists():
            shutil.copy2(original/'result.json', draft/'result.json')
        atomic(draft/'image-ocr.json', {'policy': POLICY, 'regions': results})
        (draft/'clean.md').write_text(md, encoding='utf-8')
        atomic(draft/'quality.json', q)  # Last: completed-page checkpoint.
        return len(results)

    def repair_office(self, rev, base, draft, budget):
        from PIL import Image
        output = Path('/data/archive/derived/pilot-private')/rev['id']/'document.json'
        if not output.is_file() or output.stat().st_size > 128*1024**2:
            raise ValueError('office_image_assets_unavailable_or_large')
        assets = json.loads(output.read_text()).get('Assets', [])
        if not assets or len(assets) > 256:
            raise ValueError('office_image_asset_count_budget')
        asset_dir = draft/'assets'
        asset_dir.mkdir(exist_ok=True)
        processed = 0
        for index, asset in enumerate(assets):
            done = asset_dir/f'{index:04}.json'
            if done.exists():
                continue
            if processed >= budget:
                return False, processed
            self.sync.guard()
            media = asset.get('MediaType')
            if media not in ('image/png', 'image/jpeg', 'image/jpg', 'image/bmp', 'image/tiff'):
                raise ValueError('office_image_format_needs_conversion')
            encoded = asset.get('Data', '')
            if not isinstance(encoded, str) or len(encoded) > 32*1024**2:
                raise ValueError('office_image_size_budget')
            raw = base64.b64decode(encoded, validate=True)
            path = self.sync.ram/(rev['id']+'-repair-asset.png')
            try:
                from io import BytesIO
                with Image.open(BytesIO(raw)) as image:
                    if image.width*image.height > 4_000_000 or getattr(image, 'n_frames', 1) != 1:
                        raise ValueError('office_image_requires_tiling_or_split')
                    if looks_like_grid(image):
                        raise ValueError('image_table_requires_structured_repair')
                    image.convert('RGB').save(path)
                    lines = self.ocr.run(path)
                    for line in lines:
                        line['asset_polygon'] = map_polygon(line['polygon'], [0, 0, 1, 1], image.width, image.height)
                failure = qualify(lines)
                if failure:
                    if failure != 'sensitive_content_admin_review':
                        atomic(asset_dir/f'{index:04}-review.json', {'asset_id': asset.get('ID'),
                            'origin_part': asset.get('OriginPart'), 'page': None, 'lines': lines, 'reason': failure})
                    raise ValueError(failure)
                atomic(done, {'asset_id': asset.get('ID'), 'origin_part': asset.get('OriginPart'),
                    'coordinate_unit': 'normalized_image', 'page': None, 'lines': lines})
                processed += 1
            finally:
                path.unlink(missing_ok=True)
        original = base/'page-000001'
        target = draft/'page-000001'
        target.mkdir(exist_ok=True)
        self.write_page(original, target, (original/'clean.md').read_text(),
            json.loads((original/'quality.json').read_text()),
            [json.loads((asset_dir/f'{i:04}.json').read_text()) for i in range(len(assets))])
        return True, processed

    def run(self, rev, budget=4, promote=False):
        self.sync.guard()
        source_row, source = self.source(rev)
        lock_dir = self.sync.state/'content-locks'
        lock_dir.mkdir(exist_ok=True)
        with (lock_dir/(hashlib.sha256(rev['sha'].encode()).hexdigest()+'.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
            base = self.sync.archive/'derived'/rev['id']
            manifest = json.loads((base/'manifest.json').read_text())
            if set(manifest['errors']) != {FLAG} or not 0 < manifest['pages'] <= 20000:
                raise ValueError('repair_other_quality_retained')
            draft = self.root/rev['id']
            draft.mkdir(exist_ok=True, mode=0o700)
            complete = True
            processed = 0
            if manifest.get('office'):
                complete, processed = self.repair_office(rev, base, draft, budget)
            else:
                for n in range(1, manifest['pages']+1):
                    original = base/f'page-{n:06}'
                    q = json.loads((original/'quality.json').read_text())
                    md = (original/'clean.md').read_text()
                    if sensitive(md):
                        raise ValueError('sensitive_content_admin_review')
                    if set(q.get('errors', [])) - {FLAG}:
                        raise ValueError('repair_other_quality_retained')
                    if not flagged(md, q) or (draft/original.name/'quality.json').exists():
                        continue
                    if processed >= budget:
                        complete = False
                        break
                    self.repair_pdf_page(source, original, draft/original.name, n)
                    processed += 1
            if not complete:
                self.mark(rev['id'], 'working', count=processed)
                return
            # Recheck all original/good and repaired pages, never publish only
            # the successful subset of a secret-containing/failed document.
            for n in range(1, manifest['pages']+1):
                folder = draft/f'page-{n:06}'
                if not folder.exists():
                    folder = base/f'page-{n:06}'
                q = json.loads((folder/'quality.json').read_text())
                md = (folder/'clean.md').read_text()
                if sensitive(md):
                    raise ValueError('sensitive_content_admin_review')
                if q.get('errors') or flagged(md, q):
                    raise ValueError('repair_final_quality_failed')
            manifest.update(errors=[], image_repair_policy=POLICY,
                image_ocr_model='PP-OCRv5_server', image_ocr_version='3.7.0',
                image_ocr_runtime='paddle-cpu-3.2.2')
            atomic(draft/'manifest.json', manifest)
            if promote:
                self.promote(rev, source_row, draft, base)
            self.mark(rev['id'], 'promoted' if promote else 'validated', count=processed)

    def promote(self, rev, source_row, draft, base):
        # Source validation and route checks precede the short publisher fence.
        self.sync.validate_source(source_row)
        self.sync.request('GET', '/knowledge-bases/'+rev['kb'])
        with (self.sync.state/'publisher.lock').open('a') as lock:
            deadline = time.monotonic()+300
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    self.sync.guard()
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.5)
            self.sync.guard()
            self.sync.validate_source(source_row)  # Recheck after any fence wait.
            current = self.sync.db.execute('select * from sources where path=?', (source_row['path'],)).fetchone()
            old = self.sync.db.execute('select state,error from revisions where id=?', (rev['id'],)).fetchone()
            if not current or current['missing'] or current['state'] != 'review' or current['error'] != FLAG or current['sha'] != rev['sha']:
                raise ValueError('repair_source_state_changed')
            if any(current[k] != source_row[k] for k in ('etag', 'modified', 'size')) or self.sync.excluded(current['path']) or self.sync.target_kb(current['path']) != rev['kb']:
                raise ValueError('repair_source_route_changed')
            if not old or tuple(old) != ('review', FLAG):
                raise ValueError('repair_revision_state_changed')
            if self.sync.db.execute('select count(*) from parts where revision=?', (rev['id'],)).fetchone()[0]:
                raise ValueError('repair_existing_publish_intent_review')
            backup = self.root/'backups'/rev['id']
            backup.mkdir(parents=True, exist_ok=True, mode=0o700)
            for page in sorted(draft.glob('page-*')):
                target = base/page.name
                for name in ('quality.json', 'clean.md', 'result.json', 'image-ocr.json'):
                    incoming = page/name
                    if not incoming.exists():
                        continue
                    saved = backup/page.name/name
                    saved.parent.mkdir(exist_ok=True)
                    if (target/name).exists() and not saved.exists():
                        shutil.copy2(target/name, saved)
                    tmp = target/(name+'.repair-tmp')
                    shutil.copy2(incoming, tmp)
                    os.replace(tmp, target/name)
            if not (backup/'manifest.json').exists():
                shutil.copy2(base/'manifest.json', backup/'manifest.json')
            shutil.copy2(draft/'manifest.json', base/'manifest.repair-tmp')
            os.replace(base/'manifest.repair-tmp', base/'manifest.json')
            self.sync.db.execute('begin immediate')
            try:
                self.sync.db.execute("update revisions set state='validated',error=null,manifest=? where id=? and state='review' and error=?", (str(base/'manifest.json'), rev['id'], FLAG))
                for row in self.sync.db.execute("select * from sources where sha=? and missing=0 and state='review' and error=?", (rev['sha'], FLAG)).fetchall():
                    if not sensitive(row['path']) and not self.sync.excluded(row['path']) and self.sync.target_kb(row['path']) == rev['kb']:
                        self.sync.db.execute("update sources set state='validated',error=null,attempts=0,next_attempt=0 where path=?", (row['path'],))
                self.sync.db.commit()
            except Exception:
                self.sync.db.rollback()
                raise
            self.sync.event('image_repair_validated', rev['id'], {'policy': POLICY, 'pages': json.loads((base/'manifest.json').read_text())['pages'], 'cloud_calls': 0})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/etc/weknora-nas-sync/config.json')
    parser.add_argument('--limit', type=int, default=2)
    parser.add_argument('--page-budget', type=int, default=4)
    parser.add_argument('--promote', action='store_true')
    parser.add_argument('--revision', action='append', help='Explicit opaque revision ID for acceptance/promotion')
    parser.add_argument('--daemon', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.limit <= 10 or not 1 <= args.page_budget <= 12:
        raise ValueError('repair_budget_invalid')
    sync = Sync(args.config)
    sync.activity = {'role': 'parser'}  # Honor OCR_PAUSED and shutdown checkpoint.
    sync.stop_event = threading.Event()
    repair = Repair(sync)
    stop = False
    def stopping(*_):
        nonlocal stop
        stop = True
        sync.stop_event.set()
        repair.ocr.close()
    signal.signal(signal.SIGTERM, stopping)
    try:
        with (sync.state/'image-repair.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
            while not stop:
                try:
                    if args.revision:
                        if len(args.revision) > 10 or any(not re.fullmatch('[a-f0-9]{64}', r) for r in args.revision):
                            raise ValueError('invalid_revision')
                        rows = [row for revision in args.revision for row in sync.db.execute("select * from revisions where id=? and state='review' and error=?", (revision, FLAG)).fetchall()]
                    else:
                        rows = repair.candidates(args.limit, include_validated=args.promote)
                    for rev in rows:
                        if stop:
                            break
                        try:
                            repair.run(rev, args.page_budget, args.promote)
                        except BlockingIOError:
                            continue  # Content/publisher busy, retry next bounded cycle.
                        except Halt:
                            break
                        except RepairInfrastructureError as error:
                            atomic(sync.state/'image-repair-health.json', {'at': time.time(),
                                'state': 'runtime_unavailable', 'reason': str(error), 'ordinary_queues_paused': False})
                            raise
                        except ValueError as error:
                            if stop:
                                break
                            code = str(error) if re.fullmatch('[a-z_]+', str(error)) else 'repair_invalid_data'
                            if code == 'sensitive_content_admin_review':
                                repair.quarantine_secret(rev)
                            repair.mark(rev['id'], 'review', code)
                        except Exception as error:
                            if stop:
                                break
                            repair.mark(rev['id'], 'review', type(error).__name__)
                except Halt:
                    pass
                if not args.daemon:
                    break
                for _ in range(60):
                    if stop:
                        break
                    time.sleep(1)
    finally:
        repair.close()
        sync.close()


if __name__ == '__main__':
    main()
