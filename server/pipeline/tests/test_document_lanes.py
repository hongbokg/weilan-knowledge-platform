import queue
import unittest
from document_lanes import DocumentLanes, LARGE_BYTES

class LaneTests(unittest.TestCase):
    def test_large_lane_does_not_starve_behind_small_files(self):
        jobs=DocumentLanes([{'path':str(i),'size':1} for i in range(1000)]+[{'path':'large','size':LARGE_BYTES}])
        self.assertEqual(jobs.take(0),('large',True))
        self.assertEqual(jobs.take(1),('0',False))

    def test_idle_lanes_steal_without_duplicate_dispatch(self):
        jobs=DocumentLanes([{'path':'large','size':LARGE_BYTES}])
        self.assertEqual(jobs.take(3),('large',True))
        with self.assertRaises(queue.Empty):jobs.take(0)
        self.assertTrue(jobs.empty())

    def test_checkpoint_requeues_same_size_class(self):
        jobs=DocumentLanes([{'path':'large','size':LARGE_BYTES},{'path':'small','size':None}])
        path,large=jobs.take(0);jobs.put(path,large)
        self.assertEqual(jobs.take(2),('small',False))
        self.assertEqual(jobs.take(0),('large',True))

if __name__=='__main__':unittest.main()
