"""Bounded process-wide inference admission; lock files are never deleted."""
import contextlib
import fcntl
import time
from pathlib import Path


@contextlib.contextmanager
def admit(state, slots, guard):
    if slots not in (1, 2, 4):
        raise ValueError('unsupported_gpu_slot_count')
    with contextlib.ExitStack() as held:
        fence=held.enter_context((Path(state)/'gpu-ocr.lock').open('a'))
        mode=fcntl.LOCK_EX if slots==1 else fcntl.LOCK_SH
        while True:
            guard()
            try:
                fcntl.flock(fence,mode|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(.1)
        if slots>1:
            acquired=False
            while not acquired:
                guard()
                for index in range(slots):
                    candidate=(Path(state)/f'gpu-inference-slot-{index}.lock').open('a')
                    try:
                        fcntl.flock(candidate,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError:
                        candidate.close()
                        continue
                    held.enter_context(candidate)
                    acquired=True
                    break
                if not acquired:
                    time.sleep(.1)
        guard()
        yield
