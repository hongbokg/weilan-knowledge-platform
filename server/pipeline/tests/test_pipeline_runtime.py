import concurrent.futures
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pipeline_runtime as runtime


class ImmediatePool:
    def submit(self, fn, *args):
        future = concurrent.futures.Future()
        try:
            future.set_result(fn(*args))
        except BaseException as error:
            future.set_exception(error)
        return future


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.s = SimpleNamespace(archive=root/'archive', ram=root/'ram',
            render_pool=ImmediatePool(), cfg={'mineru_version': 'unchanged'},
            guard=lambda: None, stage=lambda *a, **k: None, event=lambda *a: None)
        self.events = []
        self.s.event = lambda *a: self.events.append(a)
        self.s.mineru = lambda *a: self.fail('Verified native pages must not use GPU')

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def native(source, index, png):
        text = 'Reliable original text for page ' + str(index+1)
        return {'route': {'route': 'cpu', 'reason': 'native_verified', 'native': text,
                'markdown': text, 'blocks': [{'type': 'text', 'content': text, 'bbox': [1, 2, 3, 4]}],
                'size_pt': [600, 800]}, 'native': text, 'blank': False,
                'render_seconds': .01, 'dpi': None, 'render_revision': runtime.POLICY}

    def test_long_pdf_checkpoint_does_not_repeat_completed_pages(self):
        from sync import PageYield
        visited = []
        def prepare(*args):
            visited.append(args[1])
            return self.native(*args)
        with patch.object(runtime, 'page_count', return_value=23), patch.object(runtime, 'prepare_page', side_effect=prepare):
            for _ in range(2):
                with self.assertRaises(PageYield):
                    runtime.parse(self.s, 'source', 'revision')
            chunks, base = runtime.parse(self.s, 'source', 'revision')
        self.assertEqual(sorted(visited), list(range(23)))
        self.assertEqual(len(visited), 23)
        self.assertEqual(len(self.events), 23)
        self.assertIn('原文件第 23 页', chunks[-1])
        self.assertEqual(json.loads((base/'cursor-v1.json').read_text())['next'], 23)

    def test_sensitive_native_page_stays_quarantined(self):
        from sync import Review
        def prepare(*args):
            info = self.native(*args)
            route = info['route']
            route['markdown'] = 'password: ExampleSecret123'
            return info
        with patch.object(runtime, 'page_count', return_value=1), patch.object(runtime, 'prepare_page', side_effect=prepare):
            with self.assertRaisesRegex(Review, 'sensitive_content_admin_review'):
                runtime.parse(self.s, 'source', 'sensitive')

    def test_oversized_physical_page_does_not_retry_as_unknown_error(self):
        from sync import Review
        with patch.object(runtime,'page_count',return_value=1),patch.object(runtime,'prepare_page',side_effect=ValueError('oversize_page_requires_tiling')):
            with self.assertRaisesRegex(Review,'oversize_page_requires_tiling'):
                runtime.parse(self.s,'source','oversize')

    def test_missing_checkpoint_is_not_silently_published(self):
        from sync import PageYield
        base = self.s.archive/'derived'/'gap'
        base.mkdir(parents=True)
        (base/'cursor-v1.json').write_text('{"total":3,"next":3}')
        with self.assertRaises(PageYield):
            runtime.parse(self.s, 'source', 'gap')
        self.assertEqual(json.loads((base/'cursor-v1.json').read_text())['next'], 0)

    def test_native_parser_failure_falls_back_to_renderer(self):
        with patch.object(runtime, 'analyze_page', side_effect=RuntimeError), patch.object(runtime, 'render_page', return_value={'native':'','blank':False}) as render:
            result = runtime.prepare_page('source', 0, 'image')
        render.assert_called_once()
        self.assertEqual(result['route']['route'], 'gpu')

    def test_download_occurs_after_gpu_admission_release(self):
        from sync import Sync
        slot = threading.BoundedSemaphore(1)
        self.s.gpu_slot, self.s.state = slot, Path(self.temp.name)
        self.s._mineru = lambda *a: ({'status':'completed'}, 'base')
        def download(*args):
            self.assertTrue(slot.acquire(blocking=False))
            slot.release()
            return {'markdown':'m','structured_content':'s'}
        self.s._download_mineru = download
        self.assertIn('markdown', Sync.mineru(self.s, 'image', 'out'))


if __name__ == '__main__':
    unittest.main()
