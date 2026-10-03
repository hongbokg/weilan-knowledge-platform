import concurrent.futures,fcntl,hashlib,json,re,sqlite3,time
from pathlib import Path
from urllib.parse import quote
import requests
from classification import classify
from store import connect,task,ROOT
from products import workbook,table_records,markdown_tables,SENSITIVE
CFG=Path('/etc/weknora-organization/config.json')
def fetch(cfg,path):
 with requests.Session() as s:
  s.trust_env=False
  with s.get(cfg['nas_url'].rstrip('/')+quote(path,safe='/'),auth=(cfg['nas_user'],cfg['nas_password']),verify=cfg.get('nas_ca',True),timeout=(8,60),stream=True,allow_redirects=False) as r:
   if r.status_code!=200:raise ValueError('nas_http_'+str(r.status_code))
   data=bytearray()
   for b in r.iter_content(65536):
    data.extend(b)
    if len(data)>64*1024**2:raise ValueError('workbook_over_64MiB_requires_review')
   return bytes(data)
def extract(cfg,row):
 path=row['path'];g=classify(path) or {};data=fetch(cfg,path);md5=hashlib.md5(data).hexdigest();records=[]
 for sheet,rows in workbook(data,row['ext']):records.extend(table_records(rows,sheet,path,row['kb'],md5,row['modified'],g.get('company',''),g.get('project','')))
 return row,md5,records
def commit(db,row,md5,records):
 keys=list(records[0]) if records else []
 with db:
  db.execute('delete from evidence where path=?',(row['path'],))
  if records:db.executemany('insert into evidence('+','.join(keys)+') values('+','.join('?' for _ in keys)+')',[tuple(r[k] for k in keys) for r in records])
  db.execute('insert into files(path,kb,md5,modified,size,state,rows,updated) values(?,?,?,?,?,?,?,?) on conflict(path) do update set kb=excluded.kb,md5=excluded.md5,modified=excluded.modified,size=excluded.size,state=excluded.state,error=null,rows=excluded.rows,updated=excluded.updated',(row['path'],row['kb'],md5,row['modified'],row['size'],'parsed' if records else 'no_product_table',len(records),time.time()))
def api(cfg,method,path,body=None):
 with requests.Session() as s:
  s.trust_env=False;r=s.request(method,cfg['api_url'].rstrip('/')+path,headers={'X-API-Key':cfg['api_key']},json=body,timeout=(8,45),allow_redirects=False)
  if r.status_code not in (200,201,202):raise RuntimeError('business_api_'+str(r.status_code))
  return r.json().get('data',{})
def publish(cfg,db,limit=3):
 for f in db.execute("select * from files where state in ('parsed','publishing','created') and rows>0 order by updated limit ?",(limit,)).fetchall():
  rows=db.execute('select * from evidence where path=? order by sheet,row limit 400',(f['path'],)).fetchall();marker='product-source-'+hashlib.sha256((f['path']+f['md5']).encode()).hexdigest()[:24]
  kid=f['knowledge_id']
  if f['state']=='publishing' and not kid:
   # Uncertain POST is reconciled; it is never blindly replayed.
   matches=[];page=1
   while page<=1000:
    data=api(cfg,'GET',f"/knowledge-bases/{cfg['product_kb']}/knowledge?page={page}&page_size=100");items=data if isinstance(data,list) else data.get('knowledge',data.get('items',data.get('data',[])))
    matches.extend(k['id'] for k in items if marker in k.get('title',''))
    if len(items)<100:break
    page+=1
   if len(matches)!=1:continue
   kid=matches[0];db.execute("update files set knowledge_id=?,state='created' where path=?",(kid,f['path']));db.commit()
  body=['# 产品参数与历史报价原表索引','来源：'+f['path'],'MD5：'+f['md5'],'注意：单价税口径、单位或币种未知时不参与区间统计；历史报价不是当日报价。']
  for r in rows:
   body.extend(['\n## '+(r['model'] or r['item']),f"品牌：{r['brand']}；品名：{r['item']}；类别：{r['category']}",f"原始型号：{r['model']}；规格/参数：{r['spec']}",f"原表单价：{r['price'] or '未识别'}；税口径：{r['tax']}；单位：{r['unit'] or '未知'}；币种：{r['currency']}",f"工作表/表格：{r['sheet']}；行：{r['row']}；原文件修改时间（非报价日期）：{r['modified']}"])
  text='\n'.join(body)
  if len(text)>150000:continue
  if not kid:
   db.execute("update files set state='publishing' where path=?",(f['path'],));db.commit()
   kid=api(cfg,'POST',f"/knowledge-bases/{cfg['product_kb']}/knowledge/manual",{'title':Path(f['path']).name[:90]+' ['+marker+']','content':text,'status':'draft','channel':'product-library'})['id']
   db.execute("update files set knowledge_id=?,state='created' where path=?",(kid,f['path']));db.commit()
  api(cfg,'PUT','/knowledge/'+kid,{'custom_metadata':{'source':'结构化产品库','nas_path':f['path'],'file_md5':f['md5'],'product_source_kb':f['kb'],'rows':f['rows'],'truncated':f['rows']>400}})
  api(cfg,'PUT','/knowledge/manual/'+kid,{'title':Path(f['path']).name[:90]+' ['+marker+']','content':text,'status':'publish'})
  db.execute("update files set state='indexing',updated=? where path=?",(time.time(),f['path']));db.commit()
 for f in db.execute("select path,knowledge_id from files where state='indexing' limit 20").fetchall():
  k=api(cfg,'GET','/knowledge/'+f['knowledge_id'])
  if k.get('parse_status')=='completed':db.execute("update files set state='indexed' where path=?",(f['path'],));db.commit()
def main(limit=32):
 cfg=json.loads(CFG.read_text());db=connect();state=Path(cfg['state_dir']);ledger=sqlite3.connect('file:'+str(state/'state.db')+'?mode=ro',uri=True);ledger.row_factory=sqlite3.Row
 paused=state/'PAUSED'
 if paused.exists():print('NAS checkpoint paused; products also paused');return
 with (ROOT/'worker.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);chosen=[]
  for row in ledger.execute("select c.* from file_catalog c left join sources s on s.path=c.path where c.missing=0 and c.ext in ('.xlsx','.xls','.csv') and coalesce(s.error,'') not like '%sensitive%' order by case when c.path like '%报价%' then 0 else 1 end,c.size,c.path"):
   if SENSITIVE.search(row['path']) or '/.' in row['path'] or '/~$' in row['path'] or set(Path(row['path']).parts)&set(cfg.get('blacklist',[])):continue
   old=db.execute('select * from files where path=?',(row['path'],)).fetchone()
   if old and old['modified']==row['modified'] and old['size']==row['size']:continue
   chosen.append(dict(row))
   if len(chosen)>=limit:break
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
   pending={pool.submit(extract,cfg,r):r for r in chosen}
   for future in concurrent.futures.as_completed(pending):
    row=pending[future]
    try:r,md5,records=future.result();commit(db,r,md5,records)
    except Exception as e:
     kind=str(e) if isinstance(e,ValueError) else type(e).__name__
     with db:db.execute('insert into files(path,kb,modified,size,state,error,updated) values(?,?,?,?,?,?,?) on conflict(path) do update set state=excluded.state,error=excluded.error,updated=excluded.updated',(row['path'],row['kb'],row['modified'],row['size'],'review',kind[:100],time.time()))
  # Reuse only validated, current parser outputs; no PDF is sent back for OCR.
  done=0
  for row in ledger.execute("select s.path,c.kb,c.modified,c.size,s.md5,s.live_revision from sources s join file_catalog c on c.path=s.path where s.state='indexed' and s.missing=0 and c.ext in ('.pdf','.doc','.docx','.ppt','.pptx') and (s.path like '%报价%' or s.path like '%技术指标%' or s.path like '%规格%' or s.path like '%彩页%') and coalesce(s.error,'')='' order by s.last_hash desc limit 200"):
   if done>=8:break
   old=db.execute('select 1 from files where path=? and modified=? and size=?',(row['path'],row['modified'],row['size'])).fetchone()
   if old or SENSITIVE.search(row['path']) or not row['live_revision'] or set(Path(row['path']).parts)&set(cfg.get('blacklist',[])):continue
   try:
    root=Path('/data/archive/nas-sync/derived')/row['live_revision'];records=[];g=classify(row['path']) or {}
    if not list(root.glob('part-*.md')):continue
    for md in root.glob('part-*.md'):
     text=md.read_text();
     if SENSITIVE.search(text):raise ValueError('restricted_content_requires_review')
     for sheet,table in markdown_tables(text):records.extend(table_records(table,md.name+'/'+sheet,row['path'],row['kb'],row['md5'],row['modified'],g.get('company',''),g.get('project',''),{'kind':'parsed_table','part':md.name,'page':None,'bbox':None}))
    commit(db,row,row['md5'],records);done+=1
   except Exception:continue
  publish(cfg,db);task(db,'products','completed',{'examined_files':len(chosen),'table_documents':done,'evidence_rows':db.execute('select count(*) from evidence').fetchone()[0]});print(json.dumps({'files':len(chosen),'rows':db.execute('select count(*) from evidence').fetchone()[0]}));ledger.close();db.close()
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--limit',type=int,default=32);a=p.parse_args()
 try:main(a.limit)
 except BlockingIOError:raise SystemExit(75)
