import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import codec_probe


class CodecProbeTests(unittest.TestCase):
    def test_rgb_pixels_survive_both_paths_without_retaining_buffers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sample.png'
            image = Image.new('RGBA', (20, 13), (7, 99, 203, 125))
            image.save(path)
            image.close()
            report = codec_probe.codec_sample(path, repeats=2)
            self.assertTrue(report['pixel_identity'])
            self.assertEqual(report['raw_rgb_bytes'], 20 * 13 * 3)
            self.assertEqual(list(Path(tmp).iterdir()), [path])
            self.assertNotIn(str(path), str(report))

    def test_size_budget_rejects_image_before_decoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'large.png'
            path.write_bytes(b'12345')
            with patch.object(codec_probe, 'MAX_BYTES', 4):
                with self.assertRaisesRegex(ValueError, 'page_image_budget'):
                    codec_probe.codec_sample(path)

    def test_pixel_budget_rejects_before_loading_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sample.png'
            image = Image.new('RGB', (10, 10))
            image.save(path)
            image.close()
            with patch.object(codec_probe, 'MAX_PIXELS', 99):
                with self.assertRaisesRegex(ValueError, 'page_pixel_budget'):
                    codec_probe.codec_sample(path)


if __name__ == '__main__':
    unittest.main()
