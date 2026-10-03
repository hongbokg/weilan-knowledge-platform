import unittest
from render_cpu import choose_scale
class RenderTests(unittest.TestCase):
 def test_malformed_scan_dimensions_not_upscaled(self):
  self.assertEqual(choose_scale(2480,3508,'',[{'width':2480,'height':3508,'bbox':(0,0,2480,3508)}]),1)
 def test_vector_page_keeps_200dpi(self):
  self.assertEqual(choose_scale(595,842,'正文文本'*20,[]),200/72)
 def test_mixed_page_not_downsampled(self):
  self.assertEqual(choose_scale(595,842,'',[{'width':200,'height':200,'bbox':(0,0,300,300)}]),200/72)
 def test_genuinely_oversize_still_isolated(self):
  with self.assertRaisesRegex(ValueError,'oversize'):choose_scale(10000,10000,'文字'*40,[])
if __name__=='__main__':unittest.main()
