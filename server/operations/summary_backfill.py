"""Serial, resumable manual-summary backfill. No automatic enrichment enabled."""
import argparse,contextlib,fcntl,hashlib,json,os,secrets,sqlite3,sys,time
from pathlib import Path
import requests
sys.path.insert(0,'/opt/weknora-organization')
from organize import sql,lit
ROOT=Path('/data/archive/summary-backfill')

def dbopen():
 ROOT.mkdir(mode=0o700,exist_ok=True)
 d=sqlite3.connect(ROOT/'state.db');d.row_factory=sqlite3.Row
 d.executescript("create table if not exists jobs(id text primary key,kb text,state text default 'pending',attempts int default 0,next_at real default 0,submitted real,error text,original_task text); create table if not exists events(at real,id text,state text);")
 if 'model_id' not in [x[1] for x in d.execute('pragma table_info(jobs)')]:d.execute('alter table jobs add column model_id text')
 d.execute('create table if not exists control(name text primary key,value text)');d.commit()
 return d

def change(d,kid,state,error=None,delay=0):
 with d:
  d.execute('update jobs set state=?,error=?,next_at=? where id=?',(state,error,time.time()+delay,kid))
  d.execute('insert into events values(?,?,?)',(time.time(),kid,state))

def publish_progress(d):
 counts=dict(d.execute('select state,count(*) from jobs group by state'))
 models={}
 for model,state,n in d.execute('select model_id,state,count(*) from jobs group by model_id,state'):
  models.setdefault(model or 'legacy_unrecorded',{})[state]=n
 mode=d.execute("select value from control where name='mode'").fetchone()
 value={'time':time.time(),'counts':counts,'total':sum(counts.values()),'paused':(ROOT/'PAUSED').exists(),'models':models,'mode':mode[0] if mode else 'single_inflight'}
 p=Path('/data/weknora/nas-sync/summary-progress.json');tmp=p.with_suffix('.tmp')
 tmp.write_text(json.dumps(value));tmp.chmod(0o644);tmp.replace(p)

def seed(d):
 # Execute the established read-only Redis decoder without its aggregate footer.
 ns={};source=Path('/opt/weknora-nas-sync/summary_retry_redis_base.py').read_text()
 exec(source[:source.index("rows=call('ZRANGE'")],ns)
 rows=ns['call']('ZRANGE','asynq:{summary}:archived',0,-1)
 allowed=json.loads(Path('/opt/octop-pilot/private/api-bridge.json').read_text())['knowledge_base_ids']
 values=sql("select coalesce(json_agg(json_build_object('id',id,'kb',knowledge_base_id,'title',title)),'[]') from knowledges where tenant_id=10000 and deleted_at is null and parse_status='completed' and summary_status='failed' and knowledge_base_id in ("+','.join(lit(x) for x in allowed)+")")
 eligible={x['id']:x for x in json.loads(values)}
 added=0
 try:
  for task in rows:
   raw=ns['call']('HGET',b'asynq:{summary}:t:'+task,'msg')
   if not raw:continue
   msg=ns['decode'](raw);error=msg.get(7,b'').decode('utf-8','replace')
   # Seed every archived summary-generation failure, not just old rate limits.
   if msg.get(1,b'').decode('utf-8','replace') != 'summary:generation':continue
   payload=json.loads(msg.get(2,b'{}'));kid=payload.get('knowledge_id')
   item=eligible.get(kid)
   if not item or any(s in (item['title'] or '') for s in ('审计','财务','密码','口令','保险箱')):continue
   added+=d.execute('insert or ignore into jobs(id,kb,original_task) values(?,?,?)',(kid,item['kb'],task.decode())).rowcount
  d.commit()
 finally:ns['stream'].close();ns['sock'].close()
 print(json.dumps({'archived_seen':len(rows),'eligible_unique_added':added}))

def budget_gate(model_id=None):
 # Fail closed; the native model wrapper remains the authoritative governor.
 root=Path('/data/archive/llm-cost');policy=json.loads((root/'policy.json').read_text());usage=json.loads((root/'usage.json').read_text())
 now=time.time()
 if usage.get('cooldown',{}).get(model_id,0)>now:return 'provider_cooldown'
 if any(v.get('model')==model_id and (not k.split('-',1)[0].isdigit() or Path('/proc/'+k.split('-',1)[0]).exists()) for k,v in usage.get('pending',{}).items()):return 'other_llm_in_flight'
 import datetime
 day=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d')
 daily=monthly=0
 for k,v in usage.get('stats',{}).items():
  if k.rsplit('|',1)[-1] in policy.get('unlimited_token_models',[]):continue
  n=v.get('actual_tokens',0)+v.get('estimated_tokens',0)
  if k.startswith(day+'|'):daily+=n
  if k.startswith(day[:7]):monthly+=n
 if model_id not in policy.get('unlimited_token_models',[]) and (daily+100000>policy['daily_tokens'] or monthly+100000>policy['monthly_tokens']):return 'budget_reserve'
 return None

def run(d):
 if (ROOT/'PAUSED').exists():return 'paused'
 cfg=json.loads(Path('/etc/weknora-nas-sync/config.json').read_text());allowed=[r[0] for r in d.execute('select distinct kb from jobs')]
 if not allowed:return 'empty'
 t='sk-'+secrets.token_urlsafe(32)
 out=sql("insert into tenant_api_keys(tenant_id,scope_type,name,key_hash,api_key,full_access,knowledge_base_ids,capabilities,created_at,updated_at,expires_at) values(10000,'tenant','Serial summary backfill',"+lit(hashlib.sha256(t.encode()).hexdigest())+",'',false,"+lit(json.dumps(allowed))+"::jsonb,'[\"retrieve\",\"ingest\"]',now(),now(),now()+interval '15 minutes') returning id;")
 token_id=next(int(x) for x in out.splitlines() if x.isdigit())
 s=requests.Session();s.trust_env=False;s.headers['X-API-Key']=t
 def api(method,path):
  r=s.request(method,cfg['api_url'].rstrip('/')+path,timeout=(10,60))
  if r.status_code!=200:raise RuntimeError('business_http_'+str(r.status_code))
  return r.json()['data']
 try:
  active=d.execute("select * from jobs where state='running' limit 1").fetchone()
  if active:
   doc=api('GET','/knowledge/'+active['id']);status=doc.get('summary_status')
   if status=='completed':change(d,active['id'],'completed');return 'completed_one'
   if status=='failed':
    delay=900*2**max(0,active['attempts']-1)
    change(d,active['id'],'failed' if active['attempts']>=3 else 'pending','summary_failed',delay)
    return 'cooldown_after_failure'
   if time.time()-active['submitted']>3600:
    change(d,active['id'],'blocked','awaiting_task_reconciliation');return 'uncertain_task_stopped'
   return 'awaiting_one'
  # A submission with uncertain outcome must be reconciled before any more.
  if d.execute("select count(*) from jobs where state='blocked'").fetchone()[0]:return 'blocked_reconciliation'
  from summary_routing import pick_summary_job
  usage=json.loads(Path('/data/archive/llm-cost/usage.json').read_text())
  last=d.execute("select value from control where name='last_model'").fetchone()
  candidates=d.execute("select * from jobs where state='pending' and next_at<=? order by id",(time.time(),)).fetchall()
  row=pick_summary_job(candidates,last[0] if last else None,usage.get('cooldown',{}),time.time())
  if not row:return 'nothing_due_or_models_cooling'
  gate=budget_gate(row['model_id'])
  if gate:return gate
  doc=api('GET','/knowledge/'+row['id'])
  if doc.get('knowledge_base_id')!=row['kb'] or doc.get('parse_status')!='completed':change(d,row['id'],'skipped','source_not_ready');return 'source_not_ready'
  if doc.get('summary_status')=='completed':change(d,row['id'],'completed');return 'already_complete'
  if doc.get('summary_status') in ('pending','processing'):
   change(d,row['id'],'blocked','already_has_summary_task');return 'existing_task_needs_reconciliation'
  # Durable intent first; an uncertain HTTP timeout is never blindly retried.
  with d:
   d.execute("update jobs set state='running',attempts=attempts+1,submitted=? where id=?",(time.time(),row['id']))
   if row['model_id']:d.execute("insert or replace into control(name,value) values('last_model',?)",(row['model_id'],))
  try:api('POST','/knowledge/'+row['id']+'/regenerate-summary')
  except Exception:
   change(d,row['id'],'blocked','submission_outcome_uncertain');return 'uncertain_submission_stopped'
  return 'submitted_one'
 finally:sql('update tenant_api_keys set revoked_at=now() where id='+str(token_id));s.close()

def main():
 os.umask(0o077);p=argparse.ArgumentParser();p.add_argument('--seed',action='store_true');p.add_argument('--drain-seconds',type=int,default=0);args=p.parse_args();d=dbopen()
 with (ROOT/'worker.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if args.seed:seed(d)
  else:
   deadline=time.monotonic()+min(max(args.drain_seconds,0),60)
   iterations=0
   while True:
    state=run(d);iterations+=1;publish_progress(d)
    if time.monotonic()>=deadline or state not in ('submitted_one','awaiting_one','completed_one','already_complete','source_not_ready'):break
    time.sleep(3 if state in ('submitted_one','awaiting_one') else .1)
   print(json.dumps({'state':state,'iterations':iterations}))
  print(json.dumps({'jobs':dict(d.execute('select state,count(*) from jobs group by state').fetchall())}))
 d.close()
if __name__=='__main__':main()
