import sqlite3
import unittest
from repair_planner import classify, inventory


class PlannerTests(unittest.TestCase):
    def test_credentials_take_precedence_over_format_and_submission_errors(self):
        for state in ('review', 'ambiguous'):
            self.assertEqual(classify(state, 'image_assets_require_review,sensitive_content_admin_review'), 'credential')

    def test_ip_or_unknown_reason_is_not_automatically_sensitive(self):
        self.assertEqual(classify('review', 'ip_address'), 'other')
        self.assertEqual(classify('review', 'anydoc_conversion_failed'), 'office')

    def test_inventory_avoids_private_paths_and_never_enqueues(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE sources(path,state,error,missing)')
        db.executemany('INSERT INTO sources VALUES(?,?,?,?)', [('/private/abc.pdf', 'review', 'empty_table', 0),
            ('/secret/file.pdf', 'indexed', '', 0), ('/deleted.doc', 'review', 'empty_table', 1)])
        result = inventory(db, 100)
        self.assertEqual(result['total'], 1)
        self.assertNotIn('/private', str(result))
        self.assertFalse(result['limits']['production_enqueue'])
        self.assertEqual(db.execute('SELECT state FROM sources WHERE path=?', ('/private/abc.pdf',)).fetchone()[0], 'review')


if __name__ == '__main__': unittest.main()
