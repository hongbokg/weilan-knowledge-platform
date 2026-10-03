import unittest
from production_stage_capture import done_stages, summary


class ProductionCaptureTests(unittest.TestCase):
    def test_latest_done_stage_and_private_metadata_not_exported(self):
        value = {'trace': [
            {'name': 'embedding', 'status': 'failed', 'duration_ms': 9999},
            {'name': 'embedding', 'status': 'done', 'duration_ms': 100, 'started_at': 'a'},
            {'name': 'embedding', 'status': 'done', 'duration_ms': 250, 'started_at': 'b'},
            {'metadata': {'name': 'embedding', 'status': 'done', 'duration_ms': 9000}},
            {'name': 'chunking', 'status': 'running', 'duration_ms': 20}]}
        self.assertEqual(done_stages(value), {'embedding': .25})

    def test_unknown_durations_are_not_zero(self):
        self.assertEqual(summary([None, -1, float('nan')])['mean_seconds'], None)
        self.assertEqual(summary([1,2,3])['p95_seconds'], 3)


if __name__ == '__main__':
    unittest.main()
