"""Read-only workload observations; no model calls, private paths or credentials."""
import collections
import datetime
import json
import os
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

STATE = Path('/data/weknora/nas-sync')
LOGS = Path('/data/archive/nas-sync/observability')


def duration_summary(values):
    values = sorted(v for v in values if isinstance(v, (int, float)) and 0 <= v < 86400)
    if not values:
        return {'count': 0, 'mean_seconds': None, 'p95_seconds': None}
    import math
    return {'count': len(values), 'mean_seconds': round(sum(values)/len(values), 3),
            'p95_seconds': round(values[max(0, math.ceil(len(values)*.95)-1)], 3)}


def page_stats(db, now):
    # A page retry or version re-publication must not inflate page throughput.
    rows = db.execute("SELECT subject,at,detail FROM events WHERE kind='page_completed' AND at>? ORDER BY id DESC LIMIT 20000", (now-3600,)).fetchall()
    unique = {}
    for subject, at, detail in rows:
        try:
            value = json.loads(detail)
            key = (subject, value['page'])
            if key not in unique:
                unique[key] = (at, value)
        except (ValueError, KeyError, TypeError):
            continue
    result = {}
    for minutes in (5, 60):
        recent = [d for at, d in unique.values() if at >= now-minutes*60]
        good = sum(not d.get('issues', d.get('errors', [])) for d in recent)
        result[str(minutes)] = {'completed': len(recent), 'quality_passed': good,
            'flagged': len(recent)-good, 'quality_pages_per_minute': round(good/minutes, 2)}
    timings = collections.defaultdict(list)
    for _, d in unique.values():
        for field in ('admission_seconds', 'job_wait_seconds', 'download_seconds'):
            value = d.get('timings', {}).get(field)
            if value is not None:
                timings[field].append(value)
        timings['page_processing_seconds'].append(d.get('inference_seconds'))
    result['timings'] = {k: duration_summary(v) for k, v in timings.items()}
    result['event_limit_reached'] = len(rows) == 20000
    result['embedding_only_seconds'] = None
    result['file_enqueue_wait_seconds'] = None
    return result


def sustained(previous, now, key, condition):
    # Do not count monitor downtime or reboot gaps as sustained saturation.
    if not condition:
        return None
    recent = 0 < now-previous.get('sampled_at', 0) <= 90
    return previous.get(key, now) if recent and previous.get(key) is not None else now


def alarms(snapshot, previous):
    now = snapshot['sampled_at']
    pool = snapshot.get('ocr_pool')
    gpu = snapshot.get('gpu') or {}
    full = bool(pool and pool['waiting'] >= pool.get('waiting_limit', 8))
    snapshot['queue_full_since'] = sustained(previous, now, 'queue_full_since', full)
    snapshot['gpu_busy_since'] = sustained(previous, now, 'gpu_busy_since', gpu.get('utilization', 0) >= 98)
    snapshot['gpu_busy_sampled_seconds'] = round(now-snapshot['gpu_busy_since']) if snapshot['gpu_busy_since'] else 0
    high = gpu.get('temperature', 0) >= 80
    snapshot['temperature_high_since'] = sustained(previous, now, 'temperature_high_since', high)
    warnings = []
    if snapshot['queue_full_since'] and now-snapshot['queue_full_since'] >= 300:
        warnings.append('ocr_request_queue_sustained_full')
    if snapshot['temperature_high_since'] and now-snapshot['temperature_high_since'] >= 90:
        warnings.append('gpu_temperature_sustained_high')
    if pool is None:
        warnings.append('ocr_pool_telemetry_unavailable')
    old_pool = previous.get('ocr_pool') or {}
    same_instance = pool and pool.get('metrics', {}).get('started_at') == old_pool.get('metrics', {}).get('started_at')
    if same_instance:
        old = old_pool.get('metrics', {}).get('counts', {})
        counts = pool.get('metrics', {}).get('counts', {})
        if counts.get('timeouts', 0) > old.get('timeouts', 0):
            warnings.append('ocr_request_timeout')
        if counts.get('queue_rejected', 0) > old.get('queue_rejected', 0):
            warnings.append('ocr_request_backpressure')
    snapshot['warnings'] = warnings
    return snapshot


def redis_counts():
    # Namespaced read-only commands; never enumerate or read task bodies.
    names = ('default', 'summary', 'low', 'wiki', 'chat_attachment', 'postprocess', 'multimodal', 'graph', 'question', 'memory', 'datasource')
    result = {}
    import socket
    for name in names:
        values = {}
        for suffix, operation in (('pending', 'LLEN'), ('active', 'LLEN'), ('retry', 'ZCARD'), ('archived', 'ZCARD')):
            key = 'asynq:{'+name+'}:'+suffix
            args = (operation, key)
            encoded = ('*2\r\n'+''.join('$'+str(len(a.encode()))+'\r\n'+a+'\r\n' for a in args)).encode()
            try:
                with socket.create_connection(('127.0.0.1', 6379), timeout=1) as connection:
                    connection.sendall(encoded)
                    response = connection.makefile('rb').readline(1024)
                if response.startswith(b':'):
                    values[suffix] = int(response[1:])
                else:
                    return {'available': False}
            except (OSError, ValueError):
                return {'available': False}
        if any(values.values()):
            result[name] = values
    return {'available': True, 'queues': result, 'tracked_queues': names, 'note': 'weknora_internal_asynq_only'}


def capture():
    now = time.time()
    db = sqlite3.connect('file:'+str(STATE/'state.db')+'?mode=ro', uri=True, timeout=3)
    try:
        files = dict(db.execute('SELECT state,count(*) FROM sources WHERE missing=0 GROUP BY state'))
        cooling = db.execute("SELECT count(*) FROM sources WHERE missing=0 AND state IN ('retry','validated') AND next_attempt>?", (now,)).fetchone()[0]
        pages = page_stats(db, now)
        recent = dict(db.execute("SELECT kind,count(*) FROM events WHERE at>? AND kind IN ('document_attention','publish_attention','index_verified','publish_completed') GROUP BY kind", (now-3600,)))
        attempt_counts = dict(db.execute("SELECT CAST(attempts AS INTEGER),count(*) FROM sources WHERE missing=0 AND state='retry' GROUP BY CAST(attempts AS INTEGER)"))
    finally:
        db.close()
    try:
        request = urllib.request.Request('http://127.0.0.1:16594/health')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=3) as response:
            pool = json.loads(response.read(65536))
    except (OSError, ValueError):
        pool = None
    try:
        value = subprocess.check_output(['nvidia-smi','--query-gpu=utilization.gpu,memory.used,temperature.gpu','--format=csv,noheader,nounits'], text=True, timeout=4).strip().split(',')
        gpu = dict(zip(('utilization','memory_mib','temperature'), map(float, value)))
    except (OSError, subprocess.SubprocessError, ValueError):
        gpu = None
    return {'sampled_at': now, 'interval_seconds': 30, 'files': files, 'cooling_files': cooling,
            'retry_attempt_counts': attempt_counts, 'pages': pages, 'events_hour': recent,
            'ocr_pool': pool, 'gpu': gpu, 'redis': redis_counts(),
            'paused': (STATE/'PAUSED').exists() or (STATE/'OCR_PAUSED').exists()}


def atomic(path, value):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(value, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def record(snapshot):
    path = STATE/'queue-observability.json'
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = {}
    snapshot = alarms(snapshot, previous)
    atomic(path, snapshot)
    LOGS.mkdir(parents=True, exist_ok=True)
    today = datetime.datetime.fromtimestamp(snapshot['sampled_at'], datetime.timezone(datetime.timedelta(hours=8))).date()
    target = LOGS/(str(today)+'.jsonl')
    # Bound disk use even if a timer is accidentally configured too frequently.
    if not target.exists() or target.stat().st_size < 20*1024**2:
        with target.open('a') as f:
            f.write(json.dumps(snapshot, ensure_ascii=False)+'\n')
        target.chmod(0o600)
    for file in LOGS.glob('????-??-??.jsonl'):
        try:
            day = datetime.date.fromisoformat(file.stem)
        except ValueError:
            continue
        if (today-day).days >= 7 and file.is_file() and not file.is_symlink():
            file.unlink()
    print(json.dumps({'sampled_at': snapshot['sampled_at'], 'warnings': snapshot['warnings'],
                      'quality_pages_per_minute': snapshot['pages']['5']['quality_pages_per_minute']}))
    return snapshot


if __name__ == '__main__':
    os.umask(0o077)
    record(capture())
