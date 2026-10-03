"""Bounded RAM-only hardware series; unavailable samples stay null."""
import collections
import math
import threading

FIELDS = ('cpu_percent', 'memory_used_gib', 'memory_percent', 'gpu_percent',
          'vram_used_gib', 'gpu_memory_percent', 'gpu_temperature', 'cpu_load1')


def number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def sample(hardware):
    gpu = hardware.get('gpu') or {}
    total, available = number(hardware.get('memory_total')), number(hardware.get('memory_available'))
    used = max(0, total-available) if total is not None and available is not None else None
    vram = number(gpu.get('used_mib'))
    return {'at': hardware['sampled_at'], 'cpu_percent': number(hardware.get('cpu_percent')),
            'memory_used_gib': used/1024**3 if used is not None else None,
            'memory_percent': 100*used/total if total and used is not None else None,
            'gpu_percent': number(gpu.get('utilization')),
            'vram_used_gib': vram/1024 if vram is not None else None,
            'gpu_memory_percent': number(gpu.get('memory_utilization')),
            'gpu_temperature': number(gpu.get('temperature')),
            'cpu_load1': number(hardware.get('cpu_load1'))}


class HardwareHistory:
    def __init__(self, capacity=121):
        self.rows = collections.deque(maxlen=capacity)
        self.lock = threading.Lock()

    def append(self, hardware):
        row = sample(hardware)
        with self.lock:
            if self.rows and row['at'] <= self.rows[-1]['at']:
                return
            self.rows.append(row)

    def snapshot(self, now, seconds=600):
        with self.lock:
            rows = [dict(r) for r in self.rows if now-seconds <= r['at'] <= now]
        return {'samples': rows, 'interval_seconds': 5, 'window_seconds': seconds,
                'storage': 'memory', 'stale_after_seconds': 20}
