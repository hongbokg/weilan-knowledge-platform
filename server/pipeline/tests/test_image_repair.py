import json
import tempfile
import unittest
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from image_repair import flagged, qualify, checked_box, map_polygon, unrotate_polygon, looks_like_grid, Repair, FLAG


class ImageRepairTests(unittest.TestCase):
    def test_short_verified_certificate_can_pass(self):
        self.assertIsNone(qualify([{'text': '营业执照统一社会信用代码ABC123', 'score': .98}]))

    def test_empty_or_low_confidence_does_not_release(self):
        self.assertIn('visual_review', qualify([]))
        self.assertIn('low_confidence', qualify([{'text': '足够长的文字内容用于测试', 'score': .5}]))
        self.assertIn('low_confidence', qualify([{'text': '足够长的文字内容用于测试', 'score': float('nan')}]))

    def test_secret_in_low_confidence_line_stays_admin_only(self):
        lines = [{'text': '管理员密码: SecretExample123', 'score': .1}]
        self.assertEqual(qualify(lines), 'sensitive_content_admin_review')

    def test_lone_ip_is_not_secret(self):
        self.assertIsNone(qualify([{'text': '摄像机设备地址 192.168.10.60', 'score': .98}]))

    def test_old_missing_page_flag_is_rederived(self):
        self.assertTrue(flagged('[图片保留于原文件] 公司营业执照', {'errors': []}))
        self.assertTrue(flagged('normal body', {'errors': [FLAG]}))
        self.assertFalse(flagged('[图片文字已补充识别，原图保留于 NAS] 营业执照', {'errors': []}))

    def test_unknown_or_out_of_bounds_coordinates_stop(self):
        for box in (None, [0, 0, 100, 200], [0, 0, 0, 1], [0, 0, float('nan'), 1]):
            with self.assertRaises(ValueError):
                checked_box(box)
        with self.assertRaises(ValueError):
            map_polygon([[0, 0], [21, 0], [20, 10], [0, 10]], [0, 0, 1, 1], 20, 10)

    def test_crop_coordinates_map_to_actual_original_page(self):
        result = map_polygon([[0, 0], [100, 0], [100, 50], [0, 50]], [.2, .3, .6, .7], 100, 50)
        self.assertEqual(result[0], [.2, .3])
        self.assertAlmostEqual(result[2][0], .6)
        self.assertAlmostEqual(result[2][1], .7)

    def test_orientation_coordinates_are_inverted(self):
        import cv2
        import numpy as np
        original = np.array([[20, 10], [30, 10], [30, 20], [20, 20]], dtype=float)
        for angle in (0, 90, 180, 270):
            mat = cv2.getRotationMatrix2D((50, 25), angle, 1)
            cosine, sine = abs(mat[0, 0]), abs(mat[0, 1])
            mat[0, 2] += (int(50*sine+100*cosine)-100)/2
            mat[1, 2] += (int(50*cosine+100*sine)-50)/2
            rotated = np.c_[original, np.ones(4)] @ mat.T
            restored = unrotate_polygon(rotated.tolist(), 100, 50, angle)
            np.testing.assert_allclose(restored, original, atol=1e-8)

    def test_table_image_is_not_flattened_into_text(self):
        from PIL import Image, ImageDraw
        image = Image.new('RGB', (600, 400), 'white')
        draw = ImageDraw.Draw(image)
        for x in (20, 200, 400, 580):
            draw.line((x, 20, x, 380), fill='black', width=3)
        for y in (20, 120, 220, 380):
            draw.line((20, y, 580, y), fill='black', width=3)
        self.assertTrue(looks_like_grid(image))
        self.assertFalse(looks_like_grid(Image.new('RGB', (600, 400), 'white')))

    def test_supplement_preserves_existing_table_and_text(self):
        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp)/'original'
            draft = Path(temp)/'draft'
            original.mkdir(); draft.mkdir()
            repair = Repair.__new__(Repair)
            original_md = '# 原来正文\n<table><tr><td>设备型号ABC123</td></tr></table>\n[图片保留于原文件]'
            structured = {'pages': [{'blocks': [{'type': 'table', 'content': '设备型号ABC123', 'bbox': [.1, .1, .9, .2]}]}]}
            repair.write_page(original, draft, original_md, {'errors': [FLAG]},
                [{'lines': [{'text': '营业执照统一社会信用代码XYZ123', 'score': .99}]}], structured)
            text = (draft/'clean.md').read_text(encoding='utf-8')
            self.assertIn('<table><tr><td>设备型号ABC123</td></tr></table>', text)
            self.assertIn('营业执照统一社会信用代码XYZ123', text)
            out = json.loads((draft/'result.json').read_text())
            self.assertEqual(out['pages'], structured['pages'])

    def test_other_quality_flag_is_never_removed(self):
        repair = Repair.__new__(Repair)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, 'other_quality'):
                repair.write_page(root, root, '[图片保留于原文件]', {'errors': [FLAG, 'empty_table']},
                    [{'lines': [{'text': '有效识别的足够长正文内容', 'score': .99}]}])

    def test_new_credentials_update_permission_flags_without_publishing(self):
        db = sqlite3.connect(':memory:')
        db.executescript('create table revisions(id,state,error);create table sources(sha,state,error);')
        db.execute('insert into revisions values(?,?,?)', ('rev', 'review', FLAG))
        db.execute('insert into sources values(?,?,?)', ('sha', 'review', FLAG))
        db.execute('insert into sources values(?,?,?)', ('other', 'review', FLAG))
        db.commit()
        events = []
        repair = Repair.__new__(Repair)
        repair.sync = SimpleNamespace(db=db, event=lambda *args: events.append(args))
        repair.quarantine_secret({'id': 'rev', 'sha': 'sha'})
        self.assertEqual(db.execute("select error from sources where sha='sha'").fetchone()[0], 'sensitive_content_admin_review')
        self.assertEqual(db.execute("select error from sources where sha='other'").fetchone()[0], FLAG)
        self.assertFalse(events[0][2]['published'])
        db.close()


if __name__ == '__main__':
    unittest.main()
