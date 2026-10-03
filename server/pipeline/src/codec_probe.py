"""Offline codec baseline: read indexed archives; never enqueue or call a model.

This measures full-page codecs, not MinerU crop generation or CUDA time.
Report contains numbers and sample ordinals, never source paths or text.
"""
import argparse
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import statistics
import tempfile
import time

MAX_PIXELS = 24_000_000
MAX_BYTES = 32 * 1024 ** 2


def measured(fn):
    start = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - start


def decode_rgb(data):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > MAX_PIXELS:
            raise ValueError('page_pixel_budget')
        image.load()
        return image.convert('RGB')


def codec_sample(png, repeats=3):
    if Path(png).stat().st_size > MAX_BYTES:
        raise ValueError('page_image_budget')
    data = Path(png).read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError('page_image_budget')
    image = decode_rgb(data)
    rows = []
    try:
        expected = image.tobytes()
        for _ in range(repeats):
            packed, encode_s = measured(lambda: base64.b64encode(data))
            unpacked, decode_s = measured(lambda: base64.b64decode(packed, validate=True))
            decoded, png_decode_s = measured(lambda: decode_rgb(unpacked))
            try:
                if decoded.tobytes() != expected:
                    raise ValueError('pixel_identity_failed')
            finally:
                decoded.close()
            raw, raw_pack_s = measured(image.tobytes)
            # Private, temporary file on a caller-selected RAM filesystem.
            # This is a filesystem copy baseline, not a production shm protocol.
            with tempfile.TemporaryFile(dir=Path(png).parent) as target:
                _, write_s = measured(lambda: target.write(raw))
                target.seek(0)
                restored, read_s = measured(lambda: target.read(len(raw) + 1))
                if restored != expected:
                    raise ValueError('raw_identity_failed')
            rows.append({'base64_encode': encode_s, 'base64_decode': decode_s,
                         'png_decode_rgb': png_decode_s, 'raw_pack': raw_pack_s,
                         'ram_raw_write': write_s, 'ram_raw_read': read_s})
        return {'width': image.width, 'height': image.height,
                'png_bytes': len(data), 'raw_rgb_bytes': len(expected),
                'pixel_identity': True,
                'seconds': {k: round(statistics.median(r[k] for r in rows), 6)
                            for k in rows[0]}}
    finally:
        image.close()


def candidates(db, archive_root, limit):
    # A bounded walk of current indexed records; no remote NAS access.
    conn = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = conn.execute('''select distinct s.archive from sources s
            join revisions r on r.id=s.live_revision
            where s.state='indexed' and r.state='indexed'
              and s.archive is not null and s.size<=104857600
            order by r.created,s.archive limit 500''').fetchall()
    finally:
        conn.close()
    result = []
    root = Path(archive_root).resolve()
    for (value,) in rows:
        path = Path(value).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            continue
        with path.open('rb') as source:
            if source.read(5) != b'%PDF-':
                continue
        result.append(path)
        if len(result) >= limit:
            break
    return result


def run(args):
    spec = importlib.util.spec_from_file_location('probe_render', args.renderer)
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    results = []
    failed = 0
    with tempfile.TemporaryDirectory(prefix='codec-probe-', dir=args.ram) as tmp:
        for ordinal, path in enumerate(candidates(args.db, args.archive, args.documents), 1):
            count = renderer.page_count(str(path))
            for index in range(min(count, args.pages)):
                png = Path(tmp) / f'{ordinal}-{index}.png'
                try:
                    info = renderer.render_page(str(path), index, str(png))
                    results.append({'sample': ordinal, 'page': index + 1,
                                    't1_wall_seconds': info['render_seconds'],
                                    't1_stages': info.get('render_stage_seconds', {}),
                                    'codec': codec_sample(png)})
                except Exception:
                    # Do not print parser exceptions that could include source paths.
                    failed += 1
                finally:
                    png.unlink(missing_ok=True)
    return {'schema': 1, 'mode': 'offline_codec_only', 'model_calls': 0,
            'source_writes': 0, 'failed_pages': failed, 'pages': results,
            'limits': {'pixels': MAX_PIXELS, 'png_bytes': MAX_BYTES},
            'excluded_measurements': ['mineru_layout_crop', 'http_transfer',
                'model_preprocessing', 'cuda_inference', 'embedding', 'indexing']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--ram', required=True)
    parser.add_argument('--renderer', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--documents', type=int, default=3, choices=range(1, 4))
    parser.add_argument('--pages', type=int, default=2, choices=range(1, 3))
    args = parser.parse_args()
    report = run(args)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as target:
        json.dump(report, target, ensure_ascii=False, indent=2)
    print(json.dumps({'pages': len(report['pages']), 'failed': report['failed_pages'],
                      'model_calls': 0}))
