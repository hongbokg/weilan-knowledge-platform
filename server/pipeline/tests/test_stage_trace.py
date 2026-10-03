import asyncio
import unittest
from stage_trace import Trace


class StageTraceTests(unittest.TestCase):
    def test_nested_spans_keep_results_and_do_not_capture_content(self):
        tracer = Trace()
        child = tracer.wrap('png_encode', lambda secret: secret)
        parent = tracer.wrap('layout_prepare', lambda secret: child(secret))
        self.assertEqual(parent('private-source-content'), 'private-source-content')
        rows = tracer.summary()['spans']
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['parent'], rows[1]['id'])
        self.assertNotIn('private-source-content', str(tracer.summary()))

    def test_async_siblings_do_not_become_each_others_children(self):
        tracer = Trace()
        async def work():
            await asyncio.sleep(.001)
            return 7
        wrapped = tracer.wrap('model_http_wall', work)
        async def run():
            return await asyncio.gather(wrapped(), wrapped())
        self.assertEqual(asyncio.run(run()), [7, 7])
        self.assertTrue(all(r['parent'] is None for r in tracer.spans))

    def test_failure_propagates_without_recording_exception_message(self):
        tracer = Trace()
        def failure():
            raise ValueError('private error detail')
        with self.assertRaises(ValueError):
            tracer.wrap('extract_prepare', failure)()
        self.assertTrue(tracer.spans[0]['failed'])
        self.assertNotIn('private error detail', str(tracer.summary()))

    def test_span_storage_is_bounded(self):
        tracer = Trace(limit=2)
        wrapped = tracer.wrap('png_encode', lambda: None)
        for _ in range(10):
            wrapped()
        self.assertEqual(len(tracer.spans), 2)
        self.assertEqual(tracer.dropped, 8)


if __name__ == '__main__':
    unittest.main()
