"""Bounded CPU routing and checkpoint scheduling; existing authorization stays in Sync."""
import json
import multiprocessing
import re
import time
from concurrent.futures import ProcessPoolExecutor, wait
from pathlib import Path

from page_router import analyze_page, POLICY
from render_cpu import render_page, page_count


def prepare_page(source, index, png):
    start = time.monotonic()
    try:
        route = analyze_page(source, index)
    except Exception:
        # A native-parser failure must not prevent the established OCR path.
        route = {'route': 'gpu', 'reason': 'native_parser_failed'}
    if route['route'] == 'cpu':
        return {'route': route, 'native': route['native'], 'blank': False,
                'render_seconds': round(time.monotonic()-start, 3), 'dpi': None,
                'render_revision': POLICY, 'source_size_pt': route['size_pt']}
    # Region-only mixed OCR remains gated until its layout/coordinate acceptance.
    info = render_page(source, index, png)
    info['route'] = route
    info['render_seconds'] = round(time.monotonic()-start, 3)
    return info


def parse(self, source, revision):
    from sync import atomic, quality, Review, PageYield, split_markdown
    from image_ingest import visible_text
    base = self.archive/'derived'/revision
    base.mkdir(parents=True, exist_ok=True)
    if self.render_pool is None:
        self.render_pool = ProcessPoolExecutor(max_workers=2,
            mp_context=multiprocessing.get_context('spawn'))
    pool = self.render_pool
    cursor = base/'cursor-v1.json'
    if cursor.exists():
        saved = json.loads(cursor.read_text())
        total, first = int(saved['total']), int(saved['next'])
        if not 0 <= first <= total:
            raise Review('invalid_page_cursor')
    else:
        total = pool.submit(page_count, str(source)).result(timeout=180)
        first = 0
        while first < total and (base/f'page-{first+1:06}'/'quality.json').exists():
            first += 1
    budget_end = min(first+10, total)
    futures = {}
    self.ram.mkdir(parents=True, exist_ok=True)
    self.stage('cpu_render', pages=total, page=first+1)
    try:
        for i in range(first, budget_end):
            self.guard()
            out = base/f'page-{i+1:06}'
            out.mkdir(exist_ok=True)
            qc = out/'quality.json'
            if qc.exists():
                atomic(cursor, {'total': total, 'next': i+1, 'policy': POLICY})
                continue
            for ahead in range(i, min(i+4, budget_end)):
                if ahead not in futures and not (base/f'page-{ahead+1:06}'/'quality.json').exists():
                    png = self.ram/(revision+f'-{ahead+1}.png')
                    futures[ahead] = pool.submit(prepare_page, str(source), ahead, str(png))
            png = self.ram/(revision+f'-{i+1}.png')
            try:
                try:
                    info = futures.pop(i).result(timeout=180)
                except ValueError as error:
                    if str(error) in ('oversize_page_requires_tiling','page_image_budget'):
                        raise Review(str(error)) from None
                    raise
                route = info.pop('route')
                native = info.pop('native')
                blank = info['blank']
                start = time.monotonic()
                self.page_timings = {}
                blocks = []
                if route['route'] == 'cpu':
                    md, blocks = route['markdown'], route['blocks']
                    errors = quality(md, blocks, native, False)
                    if any(e != 'sensitive_content_admin_review' for e in errors):
                        info = pool.submit(render_page, str(source), i, str(png)).result(timeout=180)
                        info.pop('native', None)
                        route['route'], route['reason'] = 'gpu', 'native_quality_fallback'
                if blank:
                    md = '（原文空白页）'
                elif route['route'] != 'cpu':
                    outputs = self.mineru(png, out)
                    md = outputs['markdown'].read_text()
                    structured = json.loads(outputs['structured_content'].read_text())
                    blocks = [b for p in structured.get('pages', []) for b in p.get('blocks', [])]
                    md = re.sub(r'!\[.*?\]\([^\n]*\)', '[图片保留于原文件]', md)
                errors = quality(md, blocks, native, blank)
                if '[图片保留于原文件]' in md and len(visible_text(md)) < 100:
                    errors.append('image_text_requires_ocr')
                elapsed = round(time.monotonic()-start, 3)
                (out/'clean.md').write_text(md)
                if route['route'] == 'cpu':
                    atomic(out/'result.json', {'pages': [{'page': i+1, 'width': route['size_pt'][0],
                        'height': route['size_pt'][1], 'coordinate_unit': 'pdf_points', 'blocks': blocks}]})
                detail = {'page': i+1, 'total': total, 'errors': sorted(set(errors)),
                    **info, 'parser': self.cfg['mineru_version'], 'route': route['route'],
                    'route_reason': route['reason'], 'policy': POLICY, 'blocks': len(blocks),
                    'inference_seconds': elapsed, 'timings': self.page_timings,
                    'backend':'cpu-native' if route['route']=='cpu' else getattr(self,'gpu_backend','persistent-python-q8')}
                atomic(qc, detail)
                atomic(cursor, {'total': total, 'next': i+1, 'policy': POLICY})
                self.event('page_completed', revision, {**detail, 'issues': detail['errors']})
            finally:
                png.unlink(missing_ok=True)
    finally:
        for future in futures.values():
            future.cancel()
        if futures:
            wait(list(futures.values()))
        for png in self.ram.glob(revision+'-*.png'):
            png.unlink(missing_ok=True)
    if budget_end < total:
        raise PageYield()
    pages, issues = [], []
    for i in range(total):
        out = base/f'page-{i+1:06}'
        if not (out/'quality.json').exists():
            atomic(cursor, {'total': total, 'next': i, 'policy': POLICY})
            raise PageYield()
        q = json.loads((out/'quality.json').read_text())
        md = (out/'clean.md').read_text()
        issues.extend(q['errors'])
        pages.append((i+1, md))
        if '[图片保留于原文件]' in md and len(visible_text(md.replace('[图片保留于原文件]', ''))) < 100:
            issues.append('image_text_requires_ocr')
    if not pages or all(md == '（原文空白页）' for _, md in pages):
        issues.append('all_pages_blank')
    atomic(base/'manifest.json', {'revision': revision, 'pages': total,
        'errors': sorted(set(issues)), 'parser': self.cfg['mineru_version'], 'policy': POLICY})
    if issues:
        raise Review(','.join(sorted(set(issues))))
    return split_markdown(pages), base


def install(cls):
    cls.parse = parse
