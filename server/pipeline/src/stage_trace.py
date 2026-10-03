"""Bounded numeric spans for isolated SDK profiling; never capture call data."""
import contextvars
import functools
import inspect
import math
import threading
import time

STAGES = frozenset(('page_load_rgb', 'layout_prepare', 'layout_parse',
    'extract_prepare', 'crop_resize', 'png_encode', 'base64_pack',
    'request_build', 'model_http_wall', 'result_postprocess'))


class Trace:
    def __init__(self, limit=2048):
        self.limit = limit
        self.spans = []
        self.dropped = 0
        self.lock = threading.Lock()
        self.parent = contextvars.ContextVar('numeric_trace_parent', default=None)
        self.sequence = 0
        self.origin = time.perf_counter()

    def begin(self):
        with self.lock:
            self.sequence += 1
            identity = self.sequence
        parent = self.parent.get()
        token = self.parent.set(identity)
        return identity, parent, token, time.perf_counter()

    def end(self, stage, state, failed):
        identity, parent, token, start = state
        finish = time.perf_counter()
        self.parent.reset(token)
        row = {'id': identity, 'parent': parent, 'stage': stage,
               'start_seconds': round(start-self.origin, 6),
               'end_seconds': round(finish-self.origin, 6),
               'wall_seconds': round(finish-start, 6), 'failed': failed}
        with self.lock:
            if len(self.spans) < self.limit:
                self.spans.append(row)
            else:
                self.dropped += 1

    def wrap(self, stage, fn):
        if stage not in STAGES:
            raise ValueError('unknown_numeric_stage')
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_call(*args, **kwargs):
                state = self.begin()
                failed = True
                try:
                    result = await fn(*args, **kwargs)
                    failed = False
                    return result
                finally:
                    self.end(stage, state, failed)
            return async_call
        @functools.wraps(fn)
        def call(*args, **kwargs):
            state = self.begin()
            failed = True
            try:
                result = fn(*args, **kwargs)
                failed = False
                return result
            finally:
                self.end(stage, state, failed)
        return call

    def summary(self):
        rows = list(self.spans)
        result = {}
        for stage in sorted(STAGES):
            values = sorted(x['wall_seconds'] for x in rows if x['stage'] == stage)
            if values:
                result[stage] = {'count': len(values),
                    'mean_seconds': round(sum(values)/len(values), 6),
                    'p95_seconds': values[math.ceil(len(values)*.95)-1],
                    'sum_inclusive_seconds': round(sum(values), 6)}
        return {'stages': result, 'spans': rows, 'dropped': self.dropped,
                'note': 'Inclusive nested and concurrent spans must not be summed as page elapsed.'}
