"""One reserved large-file lane, three small-file lanes, with idle stealing."""
import queue

LARGE_BYTES = 100 * 1024 * 1024

class DocumentLanes:
    def __init__(self, rows):
        self.small, self.large = queue.Queue(), queue.Queue()
        for row in rows:
            self.put(row['path'], (row['size'] or 0) >= LARGE_BYTES)

    def put(self, path, large):
        (self.large if large else self.small).put(path)

    def take(self, lane):
        preferred = (self.large, self.small) if lane == 0 else (self.small, self.large)
        for bucket in preferred:
            try:
                return bucket.get_nowait(), bucket is self.large
            except queue.Empty:
                pass
        raise queue.Empty

    def qsize(self):
        return self.small.qsize() + self.large.qsize()

    def empty(self):
        return self.qsize() == 0
