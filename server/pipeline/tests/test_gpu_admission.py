import multiprocessing
import tempfile
import time
import unittest
from gpu_admission import admit


def work(state,slots,active,peak,lock,barrier):
    barrier.wait(8)
    with admit(state,slots,lambda:None):
        with lock:
            active.value+=1
            peak.value=max(peak.value,active.value)
        time.sleep(.12)
        with lock:active.value-=1


class AdmissionTests(unittest.TestCase):
    def run_processes(self,slots):
        with tempfile.TemporaryDirectory() as state:
            ctx=multiprocessing.get_context('spawn')
            active,peak,lock=ctx.Value('i',0),ctx.Value('i',0),ctx.Lock()
            barrier=ctx.Barrier(6)
            processes=[ctx.Process(target=work,args=(state,slots,active,peak,lock,barrier)) for _ in range(6)]
            try:
                for p in processes:p.start()
                for p in processes:p.join(10)
                self.assertTrue(all(p.exitcode==0 for p in processes))
                self.assertEqual(active.value,0)
                self.assertEqual(peak.value,slots)
            finally:
                for p in processes:
                    if p.is_alive():p.terminate();p.join()
    def test_legacy_processes_remain_serial(self):self.run_processes(1)
    def test_native_processes_share_two_slots(self):self.run_processes(2)
    def test_exception_releases_slot_and_fence(self):
        with tempfile.TemporaryDirectory() as state:
            with self.assertRaises(RuntimeError):
                with admit(state,2,lambda:None):raise RuntimeError('test')
            with admit(state,1,lambda:None):pass
    def test_invalid_budget_is_rejected(self):
        with tempfile.TemporaryDirectory() as state:
            with self.assertRaises(ValueError):
                with admit(state,3,lambda:None):pass


if __name__=='__main__':unittest.main()
