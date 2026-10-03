"""Read-only numeric production sample; API credentials never enter output."""
import argparse
import collections
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import time


def summary(values):
    data = sorted(x for x in values if isinstance(x, (int, float)) and math.isfinite(x) and x >= 0)
    return {'count': len(data), 'mean_seconds': round(sum(data)/len(data), 6) if data else None,
            'p95_seconds': data[math.ceil(len(data)*.95)-1] if data else None}


def done_stages(value):
    # Only successful latest spans per named stage; don't export identifiers.
    chosen = {}
    def walk(item):
        if isinstance(item, dict):
            name = item.get('name')
            if name in ('chunking', 'embedding') and item.get('status') == 'done':
                duration = item.get('duration_ms')
                if isinstance(duration, (float, int)) and duration >= 0:
                    stamp = item.get('started_at', '')
                    if name not in chosen or stamp > chosen[name][0]:
                        chosen[name] = (stamp, duration/1000)
            for key, child in item.items():
                if key not in ('input', 'output', 'metadata', 'attributes'):
                    walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
    walk(value)
    return {key: pair[1] for key, pair in chosen.items()}


def capture(args):
    import requests
    cfg = json.loads(Path(args.config).read_text())
    conn = sqlite3.connect(Path(args.db).resolve().as_uri()+'?mode=ro', uri=True)
    collected_at = time.time()
    try:
        rows = conn.execute("select subject,detail from events where kind='page_completed' and at>? order by id desc limit 20000", (collected_at-3600,)).fetchall()
        unique = {}
        for identity, detail in rows:
            item = json.loads(detail)
            unique.setdefault((identity, item['page']), item)
        timing = collections.defaultdict(list)
        route_counts = collections.Counter()
        for item in unique.values():
            route_counts[item.get('route', 'unknown')] += 1
            if item.get('route') != 'gpu':
                continue
            timing['render'].append(item.get('render_seconds'))
            for field in ('admission_seconds', 'job_wait_seconds', 'download_seconds'):
                timing[field].append(item.get('timings', {}).get(field))
        verified = conn.execute("select detail from events where kind='index_verified' order by id desc limit 100").fetchall()
    finally:
        conn.close()
    api = requests.Session()
    api.trust_env = False
    embeddings = collections.defaultdict(list)
    seen = set()
    api_errors = 0
    for (detail,) in verified:
        identity = json.loads(detail).get('knowledge_id')
        if not identity or identity in seen:
            continue
        seen.add(identity)
        try:
            response = api.get(cfg['api_url'].rstrip('/')+'/knowledge/'+identity+'/stages',
                headers={'X-API-Key': cfg['api_key']}, timeout=(3, 10), allow_redirects=False)
            response.raise_for_status()
            for name, seconds in done_stages(response.json()).items():
                embeddings[name].append(seconds)
        except (requests.RequestException, ValueError):
            api_errors += 1
        if len(seen) >= 20:
            break
    samples = []
    previous_cpu = None
    for _ in range(args.samples):
        start = time.monotonic()
        try:
            response = api.get('http://127.0.0.1:16594/health', timeout=3)
            response.raise_for_status()
            pool = response.json()
            pool_numbers = {k: pool.get(k) for k in ('active', 'waiting', 'receiving', 'slots')}
            # Counts/time summaries only, no prompts or tasks.
            pool_numbers['metrics'] = pool.get('metrics')
        except (requests.RequestException, ValueError):
            pool_numbers = None
        try:
            output = subprocess.check_output(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,temperature.gpu',
                '--format=csv,noheader,nounits'], text=True, timeout=3)
            values = [float(x.strip()) for x in output.strip().split(',')]
            gpu = dict(zip(('utilization', 'memory_mib', 'temperature'), values))
        except (OSError, ValueError, subprocess.SubprocessError):
            gpu = None
        counters = [int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
        current_cpu = (sum(counters), counters[3]+counters[4])
        cpu_percent = None
        if previous_cpu and current_cpu[0] > previous_cpu[0]:
            cpu_percent = round(100*(1-(current_cpu[1]-previous_cpu[1])/(current_cpu[0]-previous_cpu[0])), 3)
        previous_cpu = current_cpu
        samples.append({'at': time.time(), 'cpu_percent': cpu_percent,
                        'gpu': gpu, 'pool': pool_numbers})
        time.sleep(max(0, 2-(time.monotonic()-start)))
    api.close()
    return {'schema': 1, 'sampled_at': collected_at,
        'mode': 'read_only_numeric_capture', 'production_configuration_changed': False,
        'pages_last_hour': {'count': len(unique),
                           'quality_passed': sum(not x.get('issues', x.get('errors', [])) for x in unique.values()),
                           'routes': dict(route_counts),
                           'gpu_route_durations': {k: summary(v) for k,v in timing.items()}},
        'backend_index_trace': {'attempted_knowledge_items': len(seen), 'api_errors': api_errors,
                               'stages': {k: summary(v) for k,v in embeddings.items()}},
        'hardware_pool_samples': samples, 'cpu_ready_queue_empty_share': None,
        'cuda_only_seconds': None,
        'limits': {'page_event_limit': 20000, 'index_api_items': 20},
        'notes': ['Pool waiting counts model HTTP requests, not prepared pages.',
                  'Empty waiting queue with active backends does not mean input starvation.',
                  'Backend embedding span includes orchestration and HTTP overhead, not pure kernels.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('config', 'db', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--samples', type=int, default=20, choices=range(1,21))
    args = parser.parse_args()
    report = capture(args)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(report, out, indent=2)
    print(json.dumps({'samples': len(report['hardware_pool_samples']),
        'pages_hour': report['pages_last_hour']['count'],
        'stage_items': report['backend_index_trace']['attempted_knowledge_items']}))
