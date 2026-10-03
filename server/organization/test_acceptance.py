import io,unittest
import openpyxl
from products import workbook,table_records,markdown_tables,norm
from classification import classify
class TestMeaning(unittest.TestCase):
 def test_cells_price_and_origin(self):
  b=openpyxl.Workbook();s=b.active;s.title='报价';s.append(['名称','品牌型号','规格','单位','含税单价（元）']);s.append(['摄像机','海康威视\nDS-2CD1234','400万像素','台',217]);x=io.BytesIO();b.save(x);sheet,rows=next(workbook(x.getvalue(),'.xlsx'));r=table_records(rows,sheet,'/报价.xlsx','kb','md5','date')[0]
  self.assertEqual((r['brand'],r['model'],r['price'],r['tax'],r['currency'],r['row']),('海康威视','DS-2CD1234','217','含税','CNY',2));self.assertEqual(r['category'],'摄像头')
 def test_sensitive_not_published(self):
  with self.assertRaises(ValueError):table_records([['名称','型号','单价'],['password: abc','ABC',10]],'s','p','k','m','d')
 def test_total_never_unit_price(self):
  r=table_records([['名称','型号','总价'],['摄像头','DS-1',3000]],'s','p','k','m','d')[0];self.assertEqual(r['price'],'')
 def test_ambiguous_folder_not_project(self):
  r=classify('/nas/各零散项目2023-8-30/青岛蔚蓝信科通信服务有限公司/a.pdf');self.assertEqual(r['kind'],'company');self.assertEqual(r['project'],'公司通用资料')
 def test_embedded_project_company(self):
  r=classify('/nas/监控摄像头采购项目+2026-TEST-W4002+青岛蔚蓝信科通信服务有限公司/a.pdf');self.assertEqual(r['company'],'青岛蔚蓝信科通信服务有限公司');self.assertEqual(r['kind'],'project')
 def test_merged_table(self):
  t=list(markdown_tables('<table><tr><th rowspan="2">名称</th><th>型号</th></tr><tr><td>DS-1</td></tr></table>'))[0][1];self.assertEqual(t[1],['名称','DS-1'])
 def test_model_normalization(self):self.assertEqual(norm('ＤＳ－１２３'),norm('ds-123'))
unittest.main()
