"""Bounded numeric telemetry; never retain prompts, paths or model output."""
import time

BUCKETS = (0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 180, float('inf'))


class Duration:
    def __init__(self):
        self.count = 0
        self.total = self.maximum = 0.0
        self.buckets = [0] * len(BUCKETS)

    def add(self, seconds):
        self.count += 1
        self.total += seconds
        self.maximum = max(self.maximum, seconds)
        for i, bound in enumerate(BUCKETS):
            if seconds <= bound:
                self.buckets[i] += 1
                break

    def snapshot(self):
        cumulative = 0
        p95 = None
        for count, bound in zip(self.buckets, BUCKETS):
            cumulative += count
            if self.count and cumulative >= self.count * .95:
                p95 = bound if bound != float('inf') else None
                break
        return {'count': self.count, 'mean_seconds': round(self.total/self.count, 3) if self.count else None,
                'max_seconds': round(self.maximum, 3), 'p95_bucket_upper_seconds': p95}


class Telemetry:
    def __init__(self):
        self.started_at = time.time()
        self.counts = dict.fromkeys(('accepted', 'completed', 'failed', 'timeouts',
                                    'queue_rejected', 'invalid_rejected', 'canceled'), 0)
        self.wait = Duration()
        self.inference = Duration()

    def snapshot(self):
        return {'started_at': self.started_at, 'counts': dict(self.counts),
                'request_wait': self.wait.snapshot(), 'request_inference': self.inference.snapshot()}
