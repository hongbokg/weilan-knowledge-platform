"""Native KB provisioning through business APIs, backed by revocable short-lived credentials."""
import contextlib,fcntl,hashlib,json,os,secrets,sqlite3,subprocess,time
from pathlib import Path
import requests
from classification import classify
from store import connect,task
CONFIG=Path('/etc/weknora-nas-sync/config.json')
def sql(q):
 r=subprocess.run(['runuser','-u','postgres','--','psql','-d','weknora','-At','-v','ON_ERROR_STOP=1'],input=q,text=True,capture_output=True,check=True);return r.stdout.strip()
def lit(s):return "'"+str(s).replace("'","''")+"'"
def save(path,data):
 p=Path(path);st=p.stat();tmp=p.with_suffix('.organization-new');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));os.chown(tmp,st.st_uid,st.st_gid);os.chmod(tmp,st.st_mode&0o777);tmp.replace(p)
def main():
 os.umask(0o077);cfg=json.loads(CONFIG.read_text());state=Path(cfg['state_dir']);db=connect();ledger=sqlite3.connect('file:'+str(state/'state.db')+'?mode=ro',uri=True);ledger.row_factory=sqlite3.Row
 groups={};denied=set(cfg.get('blacklist',[]))
 for row in ledger.execute("select c.path,c.kb from file_catalog c left join sources s on s.path=c.path where c.missing=0 and coalesce(s.error,'') not like '%sensitive%'"):
  if set(Path(row['path']).parts)&denied:continue
  r=classify(row['path'])
  if not r:continue
  g=groups.setdefault((r['company'],r['project']),dict(r,count=0,prefixes=set()));g['count']+=1;g['prefixes'].add(r['prefix'])
 groups=[g for g in groups.values() if g['count']>=3]
 ids=list(dict.fromkeys([cfg['kb_id']]+[r['kb_id'] for r in cfg.get('routes',[])]))
 ids+= [r[0] for r in db.execute("select kb from groups where kb is not null") if r[0] not in ids]
 token='sk-'+secrets.token_urlsafe(32);digest=hashlib.sha256(token.encode()).hexdigest()
 result=sql("insert into tenant_api_keys(tenant_id,scope_type,name,key_hash,api_key,full_access,knowledge_base_ids,capabilities,created_at,updated_at,expires_at) values (10000,'tenant','自动分库任务临时权限',"+lit(digest)+",'',false,"+lit(json.dumps(ids))+"::jsonb,'[\"manage_kbs\",\"retrieve\",\"ingest\",\"manage_channels\"]',now(),now(),now()+interval '2 hours') returning id;")
 keyid=next(int(x) for x in result.splitlines() if x.isdigit());session=requests.Session();session.trust_env=False;session.headers['X-API-Key']=token
 def scope():sql('update tenant_api_keys set knowledge_base_ids='+lit(json.dumps(ids))+'::jsonb where id='+str(keyid)+';')
 def api(method,path,body=None):
  r=session.request(method,cfg['api_url'].rstrip('/')+path,json=body,timeout=45,allow_redirects=False)
  if r.status_code not in (200,201,202,204):raise RuntimeError('business_api_'+str(r.status_code))
  return r.json().get('data',{}) if r.content else {}
 try:
  template=api('GET','/knowledge-bases/'+cfg['kb_id']);existing=api('GET','/knowledge-bases')
  if isinstance(existing,dict):existing=existing.get('knowledge_bases',existing.get('items',[]))
  created=0
  for g in groups+[dict(company='',project='产品参数与历史报价',kind='products',prefixes=set(),count=0)]:
   ident=hashlib.sha256(json.dumps([g['company'],g['project']],ensure_ascii=False).encode()).hexdigest();row=db.execute('select * from groups where id=?',(ident,)).fetchone()
   name=('公司·'+g['company'] if g['kind']=='company' else '产品库·参数与历史报价' if g['kind']=='products' else '项目·'+g['project']+' · '+g['company'])[:240]
   kb=row['kb'] if row else next((k['id'] for k in existing if k['name']==name),None)
   if not kb:
    # Durable name reconciliation prevents duplicates after uncertain POST outcomes.
    if not template.get('summary_model_id') or not template.get('embedding_model_id'):raise RuntimeError('Template KB model bindings incomplete')
    data=api('POST','/knowledge-bases',{'name':name,'type':'document','description':'自动组织；公司：'+g['company']+'；项目：'+g['project']+'。NAS 原件只读，模糊归属留在原库。', 'embedding_model_id':template['embedding_model_id'],'summary_model_id':template['summary_model_id'],'indexing_strategy':{'vector_enabled':True,'keyword_enabled':True,'wiki_enabled':False,'graph_enabled':False},'chunking_config':template['chunking_config']})
    kb=data['id'];ids.append(kb);scope();created+=1;existing.append(dict(id=kb,name=name))
   db.execute('insert into groups values(?,?,?,?,?,?,?,?,?) on conflict(id) do update set prefixes=excluded.prefixes,count=excluded.count,updated=excluded.updated',(ident,g['company'],g['project'],g['kind'],kb,json.dumps(sorted(g['prefixes']),ensure_ascii=False),g['count'],'ready',time.time()));db.commit()
  managed=[dict(prefix=p,kb_id=r['kb'],name='项目·'+r['project']+' · '+r['company'],organization=True) for r in db.execute("select * from groups where kind!='products'") for p in json.loads(r['prefixes'])]
  desired=[r for r in cfg.get('routes',[]) if not r.get('organization')]+managed
  product_kb=db.execute("select kb from groups where kind='products'").fetchone()[0]
  # Only named pre-existing integration credentials inherit ordinary managed KBs.
  for token_value in [cfg['api_key']]:
   sql('update tenant_api_keys set knowledge_base_ids='+lit(json.dumps(ids))+'::jsonb,updated_at=now() where key_hash='+lit(hashlib.sha256(token_value.encode()).hexdigest())+' and revoked_at is null;')
  bridgepath=Path('/opt/octop-pilot/private/api-bridge.json');bridge=json.loads(bridgepath.read_text())
  sql('update tenant_api_keys set knowledge_base_ids='+lit(json.dumps(ids))+'::jsonb,updated_at=now() where key_hash='+lit(hashlib.sha256(bridge['catalogue_key'].encode()).hexdigest())+' and revoked_at is null;')
  api('PUT','/mcp-endpoints/'+bridge['read_endpoint'],{'knowledge_base_ids':ids})
  if bridge['knowledge_base_ids']!=ids:
   bridge['knowledge_base_ids']=ids
   # Preserve bind-mount inode; restart only relay after fully flushed private configuration.
   with bridgepath.open('w') as f:json.dump(bridge,f);f.flush();os.fsync(f.fileno())
   subprocess.run(['docker','restart','weilan-octop-pilot-kb-relay-1'],check=True,stdout=subprocess.DEVNULL)
  op=Path('/etc/weknora-organization/config.json');data=dict(state_dir=cfg['state_dir'],product_kb=product_kb,api_url=cfg['api_url'],api_key=cfg['api_key'],nas_url=cfg['nas_url'],nas_user=cfg['nas_user'],nas_password=cfg['nas_password'],nas_ca=cfg.get('nas_ca',True),blacklist=cfg.get('blacklist',[]))
  if op.exists():save(op,data)
  else:op.parent.mkdir(mode=0o700,exist_ok=True);op.write_text(json.dumps(data));os.chown(op.parent,1000,1000);os.chown(op,1000,1000);op.chmod(0o600)
  if cfg.get('routes')!=desired:
   # Apply only at a worker checkpoint. Never rewrite live revisions under an active job.
   with contextlib.ExitStack() as stack:
    for name in ('worker','cpu','publisher','scan','catalogue'):
     lock=stack.enter_context((state/(name+'.lock')).open('a'));fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    cfg=json.loads(CONFIG.read_text());cfg['routes']=desired;save(CONFIG,cfg)
    writable=sqlite3.connect(state/'state.db',timeout=30)
    for route in sorted(managed,key=lambda r:len(r['prefix']),reverse=True):
     rows=writable.execute("select path,kb from file_catalog where missing=0 and (path=? or substr(path,1,?)=?)",(route['prefix'],len(route['prefix'])+1,route['prefix']+'/')).fetchall()
     for path,old_kb in rows:
      if old_kb==route['kb_id']:continue
      writable.execute('update file_catalog set kb=? where path=?',(route['kb_id'],path))
      # Reuse archived parse pages and create the new destination index before old retirement.
      writable.execute("update sources set state='retry',next_attempt=0,attempts=0,error=null where path=? and state in ('indexed','validated')",(path,))
    writable.commit();writable.close()
  task(db,'organization','completed',{'groups':len(groups),'created_kbs':created,'product_kb':product_kb});print(json.dumps({'groups':len(groups),'created_kbs':created,'product_kb':product_kb}))
 finally:
  sql('update tenant_api_keys set revoked_at=now(),updated_at=now() where id='+str(keyid)+';');session.close();ledger.close();db.close()
if __name__=='__main__':
 try:main()
 except BlockingIOError:print('Organization waits for normal queue checkpoint');raise SystemExit(75)
