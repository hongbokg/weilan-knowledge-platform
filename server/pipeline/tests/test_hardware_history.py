import unittest
from hardware_history import HardwareHistory, sample


class HardwareTests(unittest.TestCase):
    def hardware(self, at):
        return {'sampled_at': at, 'cpu_percent': 0, 'memory_total': 8*1024**3,
                'memory_available': 6*1024**3, 'gpu': {'utilization': 100, 'used_mib': 4096}}

    def test_missing_is_not_zero(self):
        row = sample({'sampled_at': 100, 'gpu': None, 'cpu_percent': float('nan')})
        self.assertIsNone(row['cpu_percent'])
        self.assertIsNone(row['gpu_percent'])
        self.assertIsNone(row['memory_used_gib'])
        self.assertIsNone(row['vram_used_gib'])

    def test_real_zero_and_units(self):
        row = sample(self.hardware(100))
        self.assertEqual(row['cpu_percent'], 0)
        self.assertEqual(row['memory_used_gib'], 2)
        self.assertEqual(row['memory_percent'], 25)
        self.assertEqual(row['vram_used_gib'], 4)

    def test_bounded_duplicate_and_elapsed_window(self):
        history = HardwareHistory(3)
        for at in (10, 15, 20, 20, 25):
            history.append(self.hardware(at))
        self.assertEqual([r['at'] for r in history.snapshot(25)['samples']], [15, 20, 25])
        self.assertEqual([r['at'] for r in history.snapshot(30, 8)['samples']], [25])
        self.assertFalse(history.snapshot(1000)['samples'])


if __name__ == '__main__': unittest.main()
