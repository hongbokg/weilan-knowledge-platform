import unittest
from openclaw_output import decode
class OutputTests(unittest.TestCase):
 def test_unescaped_company_quotes_are_preserved(self):
  obj=decode('{"summary":"本项目由"示例公司"供货，保修36个月。","evidence":["保修36个月","示例公司供货"]}')
  self.assertEqual(obj['summary'],'本项目由"示例公司"供货，保修36个月。')
  self.assertEqual(obj['evidence'],['保修36个月','示例公司供货'])
 def test_valid_json_preserved(self):
  self.assertEqual(decode('{"summary":"原文摘要","evidence":["原文"]}')['summary'],'原文摘要')
 def test_missing_fields_rejected(self):
  with self.assertRaisesRegex(ValueError,'invalid_summary_schema'):decode('{"message":"未生成"}')
if __name__=='__main__':unittest.main()
