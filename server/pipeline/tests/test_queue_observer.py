import json
import sqlite3
import unittest
from queue_observer import alarms, page_stats, duration_summary


class ObserverTests(unittest.TestCase):
    def snapshot(self, at, waiting=8, utilization=100, temperature=73):
        return {'sampled_at': at, 'ocr_pool': {'waiting': waiting, 'waiting_limit': 8},
                'gpu': {'utilization': utilization, 'temperature': temperature}}

    def test_full_queue_requires_five_minutes_not_one_sample(self):
        previous = {}
        for at in range(1000, 1331, 30):
            previous = alarms(self.snapshot(at), previous)
            self.assertEqual('ocr_request_queue_sustained_full' in previous['warnings'], at>=1300)
        self.assertNotIn('gpu_temperature_sustained_high', previous['warnings'])

    def test_gap_and_recovery_reset_duration(self):
        previous = alarms(self.snapshot(1000), {})
        after_gap = alarms(self.snapshot(2000), previous)
        self.assertEqual(after_gap['gpu_busy_sampled_seconds'], 0)
        recovered = alarms(self.snapshot(2030, 0, 0), after_gap)
        self.assertIsNone(recovered['queue_full_since'])
        self.assertEqual(recovered['gpu_busy_sampled_seconds'], 0)

    def test_busy_gpu_alone_is_not_failure(self):
        row = alarms(self.snapshot(1000, waiting=2), {})
        for at in range(1030, 1690, 30):
            row = alarms(self.snapshot(at, waiting=2), row)
        self.assertEqual(row['warnings'], [])

    def test_pool_restart_does_not_compare_old_error_counters(self):
        old = self.snapshot(1000, 0)
        old['ocr_pool']['metrics'] = {'started_at': 1, 'counts': {'timeouts': 1}}
        new = self.snapshot(1030, 0)
        new['ocr_pool']['metrics'] = {'started_at': 2, 'counts': {'timeouts': 2}}
        self.assertNotIn('ocr_request_timeout', alarms(new, old)['warnings'])

    def test_unique_page_counts_and_flags(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,subject TEXT,at REAL,kind TEXT,detail TEXT)')
        for subject, page, errors in [('a',1,[]), ('a',1,[]), ('a',2,['empty_table']), ('b',1,[])]:
            db.execute('INSERT INTO events(subject,at,kind,detail) VALUES(?,?,?,?)',
                (subject, 990, 'page_completed', json.dumps({'page': page, 'errors': errors, 'timings': {'admission_seconds': 2}})))
        stats = page_stats(db, 1000)
        self.assertEqual(stats['5']['completed'], 3)
        self.assertEqual(stats['5']['quality_passed'], 2)
        self.assertEqual(stats['5']['flagged'], 1)
        self.assertIsNone(stats['embedding_only_seconds'])
        self.assertIsNone(stats['file_enqueue_wait_seconds'])
        self.assertEqual(stats['timings']['admission_seconds']['mean_seconds'], 2)
        db.close()

    def test_missing_duration_not_reported_as_zero(self):
        self.assertIsNone(duration_summary([])['mean_seconds'])


if __name__ == '__main__':
    unittest.main()
