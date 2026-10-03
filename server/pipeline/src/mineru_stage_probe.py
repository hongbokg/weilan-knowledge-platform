"""One archived indexed page through SDK + existing pool, no index writes.

Isolated process hooks do not change any production service. HTTP wall time
includes queue/transport/backend CPU; it is not CUDA kernel time.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from unittest.mock import patch
from urllib.request import build_opener, ProxyHandler
from stage_trace import Trace
from render_cpu import render_page


def select_page(db, archive):
    conn = sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro', uri=True)
    try:
        rows = conn.execute('''select e.detail,s.archive from events e
            join revisions r on r.id=e.subject
            join sources s on s.live_revision=r.id
            where e.kind='page_completed' and r.state='indexed'
              and s.state='indexed' and s.archive is not null
            order by e.id desc limit 400''').fetchall()
    finally:
        conn.close()
    root = Path(archive).resolve()
    for detail, value in rows:
        item = json.loads(detail)
        if item.get('route') != 'gpu' or item.get('issues') or item.get('errors'):
            continue
        source = Path(value).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            continue
        with source.open('rb') as inp:
            if inp.read(5) != b'%PDF-':
                continue
        return source, int(item['page'])-1
    raise ValueError('no_current_indexed_gpu_page')


def health():
    opener = build_opener(ProxyHandler({}))
    with opener.open('http://127.0.0.1:16594/health', timeout=5) as response:
        return json.loads(response.read(65536))


def run(args):
    from loguru import logger
    logger.remove()  # SDK exceptions/logs may include content; suppress in this isolated run.
    import httpx
    from PIL import Image
    import mineru_vl_utils.mineru_client as client_module
    import mineru_vl_utils.vlm_client.utils as utils
    import mineru_vl_utils.vlm_client.http_client as http_module
    from contextlib import ExitStack
    before = health()
    if before['waiting'] > 4:
        raise ValueError('production_pool_busy_skip_probe')
    source, index = select_page(args.db, args.archive)
    trace = Trace()
    helper = client_module.MinerUClientHelper
    with tempfile.TemporaryDirectory(prefix='mineru-stage-', dir=args.ram) as tmp:
        png = Path(tmp)/'page.png'
        render = render_page(str(source), index, str(png))
        # Hook only this interpreter and always restore, never patch installed files.
        with ExitStack() as hooks:
            bindings = [(helper, 'prepare_for_layout', 'layout_prepare'),
                (helper, 'parse_layout_output', 'layout_parse'),
                (helper, 'prepare_for_extract', 'extract_prepare'),
                (helper, 'resize_by_need', 'crop_resize'),
                (helper, 'post_process', 'result_postprocess'),
                (client_module, 'get_png_bytes', 'png_encode'),
                (utils, 'get_png_bytes', 'png_encode'),
                (http_module, 'get_image_data_url', 'base64_pack'),
                (http_module.HttpVlmClient, 'build_request_body', 'request_build'),
                (httpx.Client, 'send', 'model_http_wall'),
                (httpx.AsyncClient, 'send', 'model_http_wall')]
            for obj, name, stage in bindings:
                hooks.enter_context(patch.object(obj, name, trace.wrap(stage, getattr(obj, name))))
            def load():
                with Image.open(png) as inp:
                    inp.load()
                    return inp.convert('RGB')
            image = trace.wrap('page_load_rgb', load)()
            client = client_module.MinerUClient(backend='http-client',
                model_name='mineru-vlm', server_url='http://127.0.0.1:16594/',
                skip_model_name_checking=True, max_concurrency=2,
                http_timeout=180, max_retries=0, use_tqdm=False, debug=False)
            started = time.perf_counter()
            try:
                result = client.two_step_extract(image)
                elapsed = time.perf_counter()-started
                blocks = list(result)
                content_chars = sum(len(b.content or '') for b in blocks)
                types = {}
                for block in blocks:
                    types[block.type] = types.get(block.type, 0)+1
            finally:
                image.close()
                asyncio.run(client.aclose())
    report = {'schema': 1, 'mode': 'isolated_sdk_page_probe',
        'production_configuration_changed': False, 'index_writes': 0,
        'source_writes': 0, 'page': index+1, 'render_wall_seconds': render['render_seconds'],
        'render_stages': render.get('render_stage_seconds', {}),
        'sdk_page_wall_seconds': round(elapsed, 6),
        'layout_dimensions': list(client.helper.layout_image_size),
        'blocks': len(blocks), 'block_types': types, 'content_chars': content_chars,
        'trace': trace.summary(), 'pool_before': before, 'pool_after': health(),
        'cuda_only_seconds': None, 'embedding_only_seconds': None,
        'queue_wait_per_request_seconds': None,
        'quality_comparison_completed': False}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('db', 'archive', 'ram', 'output'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    try:
        report = run(args)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as target:
            json.dump(report, target, indent=2)
        print(json.dumps({'status': 'completed', 'blocks': report['blocks'],
                          'elapsed': report['sdk_page_wall_seconds']}))
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_class': type(error).__name__}))
        raise SystemExit(1)
