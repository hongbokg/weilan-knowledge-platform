import json,tempfile,unittest,sqlite3,time
from pathlib import Path
from unittest.mock import patch
from sync import Sync,Review,Ambiguous,quality,split_markdown,normalize_href,sensitive

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  cfg={'state_dir':str(self.root/'state'),'archive_dir':str(self.root/'archive'),'ram_dir':str(self.root/'ram'),
       'nas_user':'test','nas_password':'test','nas_url':'http://example.invalid','api_key':'test',
       'api_url':'http://127.0.0.1','roots':['/approved'],'kb_id':'kb','owner':'owner','mineru_version':'test','sync_deletions':True}
  (self.root/'config').write_text(json.dumps(cfg));self.s=Sync(self.root/'config');self.s.guard=lambda:None;self.s.event=lambda *args:None
 def tearDown(self):self.s.db.close();self.tmp.cleanup()
 def test_remote_href_boundary(self):
  for value in ['https://evil.invalid/x','/approved/%2e%2e/secret','/a%5cb']:
   with self.assertRaises(ValueError):normalize_href(value,'http://example.invalid')
 def test_size_class_query_reserves_large_files(self):
  for path,size in [('/approved/small.pdf',10),('/approved/large.pdf',104857600),('/approved/no-size.pdf',None)]:
   self.s.db.execute('insert into sources(path,size,state) values(?,?,?)',(path,size,'pending'))
  self.s.db.commit()
  self.assertEqual([r['path'] for r in self.s.work_rows(1,True,size_class='large')],['/approved/large.pdf'])
  self.assertEqual({r['path'] for r in self.s.work_rows(10,True,size_class='small')},{'/approved/small.pdf','/approved/no-size.pdf'})
 def test_longest_directory_route_and_boundary(self):
  self.s.cfg['routes']=[{'prefix':'/approved','kb_id':'root'},{'prefix':'/approved/company','kb_id':'company'}]
  self.assertEqual(self.s.target_kb('/approved/company/a.pdf'),'company')
  self.assertEqual(self.s.target_kb('/approved/company-other/a.pdf'),'root')
 def test_same_bytes_different_kb_have_distinct_identity(self):
  self.s.cfg['parser_revision']='test'
  self.assertNotEqual(self.s.revision_id('sha','kb-a'),self.s.revision_id('sha','kb-b'))
 def test_route_change_requeues_but_keeps_old_until_success(self):
  db=self.s.db
  db.execute("insert into revisions values('old','sha','old-kb','indexed',null,null,0)")
  db.execute("insert into sources(path,etag,size,modified,state,live_revision,last_hash) values('/approved/a.pdf','e',10,'m','indexed','old',?)",(time.time(),));db.commit()
  self.s.listing=lambda path:[{'path':'/approved/a.pdf','directory':False,'size':10,'etag':'e','modified':'m'}]
  self.s.scan()
  row=db.execute('select state,live_revision from sources').fetchone()
  self.assertEqual(tuple(row),('pending','old'))
 def test_publish_dedup_does_not_cross_kb(self):
  self.s.cfg['routes']=[{'prefix':'/approved/b','kb_id':'other'}]
  db=self.s.db;base=self.root/'derived';base.mkdir()
  db.execute("insert into revisions values('rev','same','kb','validated',null,null,0)")
  for path in ['/approved/a.pdf','/approved/b/b.pdf']:
   db.execute("insert into sources(path,sha,state) values(?,'same','pending')",(path,))
  self.s.publish('rev','same','md5','/approved/a.pdf',[],base)
  self.assertEqual(db.execute("select state from sources where path='/approved/a.pdf'").fetchone()[0],'indexed')
  self.assertEqual(db.execute("select state from sources where path='/approved/b/b.pdf'").fetchone()[0],'pending')
 def test_publisher_uses_validated_pages_without_gpu(self):
  self.s.cfg['parser_revision']='test';db=self.s.db
  rev=self.s.revision_id('sha','kb');base=self.s.archive/'derived'/rev;page=base/'page-000001';page.mkdir(parents=True)
  (base/'manifest.json').write_text(json.dumps({'pages':1,'errors':[]}));(page/'quality.json').write_text('{"errors":[]}');(page/'clean.md').write_text('validated text')
  db.execute('insert into revisions values(?,?,?,?,?,?,?)',(rev,'sha','kb','validated',None,None,0))
  db.execute("insert into sources(path,sha,md5,state) values('/approved/a.pdf','sha','md5','validated')");db.commit()
  calls=[];self.s.publish=lambda *args:calls.append(args);self.s.parse=lambda *args:self.fail('publisher must not call GPU')
  self.s.publish_ready(1);self.assertEqual(len(calls),1);self.assertIn('validated text',calls[0][4][0])
 def test_publisher_rejects_failed_cached_quality(self):
  self.s.cfg['parser_revision']='test';db=self.s.db;rev=self.s.revision_id('sha','kb')
  base=self.s.archive/'derived'/rev;base.mkdir(parents=True);(base/'manifest.json').write_text('{"pages":1,"errors":["empty_table"]}')
  db.execute('insert into revisions values(?,?,?,?,?,?,?)',(rev,'sha','kb','validated',None,None,0))
  db.execute("insert into sources(path,sha,state) values('/approved/a.pdf','sha','validated')");db.commit()
  self.s.publish=lambda *args:self.fail('must not publish failed quality');self.s.publish_ready(1)
  self.assertEqual(db.execute('select state from sources').fetchone()[0],'review')
 def test_office_location_not_fabricated_page(self):
  value=split_markdown([(1,'正文内容')],office=True)[0]
  self.assertIn('不代表页码',value)
  self.assertNotIn('原文件第 1 页',value)
 def test_hidden_dirs_excluded(self):
  self.assertTrue(self.s.excluded('/approved/.git/a.pdf'))
  self.assertFalse(self.s.excluded('/approved/project/a.docx'))
 def test_discovery_does_not_hold_parser_locks(self):
  import fcntl
  def listing(path):
   for name in ('worker.lock','cpu.lock','publisher.lock'):
    with (self.s.state/name).open('a') as lock:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   return [{'path':'/approved/a.pdf','directory':False,'size':10,'etag':'e','modified':'m'}]
  self.s.listing=listing
  self.s.scan()
  self.assertEqual(self.s.db.execute('select count(*) from sources').fetchone()[0],1)
  self.assertFalse((self.s.state/'SCAN_COMMIT.json').exists())
 def test_partial_discovery_not_published(self):
  def listing(path):
   if path.endswith('/child'):raise RuntimeError('offline')
   return [{'path':'/approved/a.pdf','directory':False,'size':10,'etag':'e','modified':'m'}, {'path':'/approved/child','directory':True}]
  self.s.listing=listing
  with self.assertRaises(RuntimeError):self.s.scan()
  self.assertEqual(self.s.db.execute('select count(*) from sources').fetchone()[0],0)
 def test_page_batch_yield_is_not_failure(self):
  from sync import PageYield
  self.s.cfg['parser_revision']='test'
  self.s.db.execute("insert into sources(path,size,state) values('/approved/large.pdf',100,'pending')");self.s.db.commit()
  self.s.request=lambda *args:{}
  self.s.download=lambda row:('sha','md5',self.root/'source')
  self.s.reuse_pages=lambda *args:None
  self.s.parse=lambda *args:(_ for _ in ()).throw(PageYield())
  self.s.work(1,prepare_only=True)
  row=self.s.db.execute('select state,error,attempts,next_attempt from sources').fetchone()
  self.assertEqual(row['state'],'pending');self.assertIsNone(row['error'])
  self.assertEqual(row['attempts'],0);self.assertEqual(row['next_attempt'],0)
 def test_publish_lanes_are_disjoint_and_cover_all(self):
  self.s.cfg['parser_revision']='test';db=self.s.db
  for i in range(8):
   sha='content-'+str(i);rev=self.s.revision_id(sha,'kb');base=self.s.archive/'derived'/rev;page=base/'page-000001';page.mkdir(parents=True)
   (base/'manifest.json').write_text('{"pages":1,"errors":[]}');(page/'quality.json').write_text('{"errors":[]}');(page/'clean.md').write_text('test content')
   db.execute('insert into revisions values(?,?,?,?,?,?,?)',(rev,sha,'kb','validated',None,None,i))
   db.execute('insert into sources(path,sha,state) values(?,?,?)',('/approved/'+str(i)+'.pdf',sha,'validated'))
  db.commit();seen=[]
  def publish(rev,sha,md5,path,*args):
   seen.append(path);db.execute("update sources set state='indexed' where path=?",(path,));db.commit()
  self.s.publish=publish;self.s.publish_ready(20,0);first=set(seen);seen.clear();self.s.publish_ready(20,1);second=set(seen)
  self.assertTrue(first);self.assertTrue(second);self.assertFalse(first&second);self.assertEqual(len(first|second),8)
 def test_short_certificate_not_rejected(self):
  self.assertEqual(quality('有效证书编号ABCDEFGHIJK',[{'type':'text'}],'',False),[])
 def test_empty_table_and_missing_body(self):
  self.assertIn('empty_table',quality('证书有效内容',[{'type':'table','content':'<table><td></td></table>'}],'',False))
  self.assertIn('nonblank_page_empty_or_short',quality('',[],'',False))
 def test_sensitive_and_lone_ip(self):
  self.assertTrue(sensitive('管理员密码：abc123'))
  self.assertFalse(sensitive('地址 192.168.1.1'))
 def test_chapter_split_no_truncation(self):
  chunks=split_markdown([(1,'a'*70),(2,'b'*70)],100)
  self.assertEqual(len(chunks),2)
  with self.assertRaises(Review):split_markdown([(1,'a'*101)],100)
 def test_failed_scan_does_not_mark_deleted(self):
  self.s.db.execute("insert into sources(path,seen,state) values('/approved/old.pdf','old','indexed')");self.s.db.commit()
  self.s.listing=lambda path:(_ for _ in ()).throw(RuntimeError('offline'))
  with self.assertRaises(RuntimeError):self.s.scan()
  self.assertEqual(self.s.db.execute('select missing from sources').fetchone()[0],0)
  self.assertEqual(self.s.db.execute('select complete from scans').fetchone()[0],0)
 def test_delete_requires_two_complete_scans(self):
  db=self.s.db
  db.execute("insert into scans(id,started,complete) values('ok',?,1)",(time.time(),))
  db.execute("insert into revisions values('r','s','kb','indexed',null,null,0)")
  db.execute("insert into sources(path,live_revision,missing,missing_since) values('/approved/a.pdf','r',1,0)")
  db.commit();self.s.request=lambda *args: self.fail('must not call deletion')
  self.s.retire();self.assertEqual(db.execute('select state from revisions').fetchone()[0],'indexed')
 def test_replacement_failure_keeps_old_revision(self):
  db=self.s.db
  db.execute("insert into scans(id,started,complete) values('ok',?,1)",(time.time(),))
  db.execute("insert into revisions values('old','sha-old','kb','indexed',null,null,0)")
  db.execute("insert into sources(path,sha,live_revision,state,missing) values('/approved/a.pdf','sha-new','old','review',0)")
  db.commit();self.s.request=lambda *args: self.fail('old valid version must stay')
  self.s.retire()
 def test_ambiguous_create_not_reposted(self):
  db=self.s.db;base=self.root/'derived';base.mkdir()
  marker='nas-sync-'+'r'*16+'-1'
  db.execute('insert into parts values(?,?,?,?,?,?,?)',('r'*64,1,marker,None,'ambiguous','x',None));db.commit()
  self.s.request=lambda *args: self.fail('must reconcile rather than retry POST')
  with self.assertRaises(Ambiguous):self.s.publish('r'*64,'sha','md5','/approved/a.pdf',['body'],base)

 def test_existing_manual_content_update_is_put_not_duplicate_create(self):
  db=self.s.db;base=self.root/'derived';base.mkdir()
  rev='r'*64;marker='nas-sync-'+rev[:16]+'-1'
  db.execute('insert into revisions values(?,?,?,?,?,?,?)',(rev,'sha','kb','validated',None,None,0))
  db.execute('insert into parts values(?,?,?,?,?,?,?)',(rev,1,marker,'existing-id','content_update_pending','old',None));db.commit()
  calls=[]
  def request(method,path,payload=None):
   calls.append((method,path,payload))
   if method=='POST':self.fail('updates must never create another document')
   if method=='GET':return {'parse_status':'completed','title':'title'}
   return {}
  self.s.request=request;self.s.search_ready=lambda *args:True
  self.s.publish(rev,'sha','md5','/approved/a.pdf',['corrected merged table'],base)
  writes=[v for v in calls if v[0]=='PUT' and v[1]=='/knowledge/manual/existing-id']
  self.assertEqual(len(writes),1)
  self.assertEqual(writes[0][2]['content'],'corrected merged table')
  self.assertEqual(db.execute('select state from parts').fetchone()[0],'indexed')

if __name__=='__main__':unittest.main()
