def openclaw_summary_snapshot():
 try:return json.loads((STATE/'openclaw-summary-progress.json').read_text())
 except Exception:return {}

"""Loopback operational API. Every request reuses WeKnora's admin authorization."""
import json,os,sqlite3,threading,time,subprocess,shutil
from catalog_api import query_files,original,AccessError
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,quote
import requests
from hardware_history import HardwareHistory

STATE=Path('/data/weknora/nas-sync');PREFIX='/api/v1/system/admin/nas-monitor'
hardware={};hardware_lock=threading.Lock();request_slots=threading.BoundedSemaphore(12)
hardware_history=HardwareHistory()
download_slots=threading.BoundedSemaphore(2)
def collect():
 previous=None
 while True:
  try:
   cpu=list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:9]));total=sum(cpu);idle=cpu[3]+cpu[4]
   percent=round(100*(1-(idle-previous[1])/(total-previous[0])),1) if previous and total>previous[0] else None;previous=(total,idle)
   mem={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()}
   disks=[{'path':p,'total':shutil.disk_usage(p).total,'free':shutil.disk_usage(p).free} for p in ['/','/data/archive','/run/weknora-work']]
   try:
    values=subprocess.check_output(['nvidia-smi','--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,utilization.memory','--format=csv,noheader,nounits'],text=True,timeout=3).strip().splitlines()[0].split(',')
    def gpu_number(index):
     try:return float(values[index])
     except (ValueError,IndexError):return None
    gpu={'name':values[0].strip(),'utilization':gpu_number(1),'used_mib':gpu_number(2),'total_mib':gpu_number(3),'temperature':gpu_number(4),'memory_utilization':gpu_number(5)}
   except Exception:gpu=None
   value={'sampled_at':time.time(),'cpu_percent':percent,'cpu_count':os.cpu_count(),'cpu_load1':os.getloadavg()[0],'memory_total':mem['MemTotal'],'memory_available':mem['MemAvailable'],'gpu':gpu,'disks':disks}
   hardware_history.append(value)
   with hardware_lock:hardware.update(value)
  except Exception:pass
  time.sleep(5)

def llm_cost_snapshot():
 from datetime import datetime,timezone,timedelta
 d=Path('/data/archive/llm-cost');now=datetime.now(timezone(timedelta(hours=8)));day=now.strftime('%Y-%m-%d');month=day[:7]
 try:
  policy=json.loads((d/'policy.json').read_text());state=json.loads((d/'usage.json').read_text()) if (d/'usage.json').exists() else {}
  rows={};daily=monthly=reserved=0
  for key,value in state.get('stats',{}).items():
   date,task,model=key.split('|',2);used=value.get('actual_tokens',0)+value.get('estimated_tokens',0)
   if date.startswith(month):monthly+=used
   if date==day:
    daily+=used;r=rows.setdefault(task,{'task':task,'calls':0,'actual_tokens':0,'estimated_tokens':0,'cache_hits':0,'errors':0,'input_tokens':0,'output_tokens':0})
    for field in r:
     if field!='task':r[field]+=value.get(field,0)
  for value in state.get('pending',{}).values():
   if value['day'].startswith(month):monthly+=value['tokens']
   if value['day']==day:daily+=value['tokens'];reserved+=value['tokens']
  return {'available':True,'day':day,'daily_used':daily,'monthly_used':monthly,'reserved':reserved,'daily_limit':policy['daily_tokens'],'monthly_limit':policy['monthly_tokens'],'rows':list(rows.values()),'started_at':policy.get('started_at'),'cooling_models':sum(v>time.time() for v in state.get('cooldown',{}).values())}
 except Exception:return {'available':False}

def maintenance_snapshot():
 try:return json.loads((STATE/'maintenance.json').read_text())
 except Exception:return None

def summary_progress_snapshot():
 try:return json.loads((STATE/'summary-progress.json').read_text())
 except Exception:return None

def queue_observability_snapshot():
 try:
  value=json.loads((STATE/'queue-observability.json').read_text())
  value['stale']=time.time()-value.get('sampled_at',0)>90
  return value
 except (OSError,ValueError):return None

def pipeline_snapshot(db):
 import statistics
 rows=db.execute("select detail from events where kind='page_completed' and at>? order by id desc limit 10000",(time.time()-3600,)).fetchall()
 routes={};timings={};success=flags=0
 for row in rows:
  try:d=json.loads(row[0])
  except Exception:continue
  route=d.get('route','legacy_gpu');routes[route]=routes.get(route,0)+1
  if d.get('issues',d.get('errors',[])):flags+=1
  else:success+=1
  for key,value in d.get('timings',{}).items():
   if isinstance(value,(int,float)):timings.setdefault(key,[]).append(value)
 profile=STATE/'pipeline-runtime.json'
 runtime=json.loads(profile.read_text()) if profile.exists() else {}
 from page_router import POLICY
 return {'observability':queue_observability_snapshot(),'backend':runtime.get('backend','persistent-python-q8'),'gpu_slots':runtime.get('gpu_slots',1),'document_lanes':runtime.get('document_lanes',{'large':0,'small':4}),'render_workers':8,'lookahead_pages':16,'ram_budget_mib':2048,'routing_policy':POLICY,'routes_hour':routes,'successful_pages_hour':success,'flagged_pages_hour':flags,'successful_pages_minute':round(success/60,2),'timings_mean':{k:round(statistics.mean(v),3) for k,v in timings.items()},'mixed_region_ocr':False}

def snapshot(query):
 db=sqlite3.connect('file:'+str(STATE/'state.db')+'?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row
 try:
  counts=dict(db.execute('select state,count(*) from sources where missing=0 group by state').fetchall())
  scan=dict(db.execute('select * from scans order by started desc limit 1').fetchone() or {})
  # Errors are codes only; never expose upstream response bodies or credentials.
  scan.pop('error',None)
  page=max(1,min(int(query.get('page',['1'])[0]),100000));status=query.get('state',['all'])[0]
  allowed=['all','indexed','pending','validated','review','retry','ambiguous']
  if status not in allowed:raise ValueError('invalid state')
  search=query.get('q',[''])[0][:120];where='missing=0';args=[]
  if status!='all':where+=' and state=?';args.append(status)
  if search:where+=' and path like ?';args.append('%'+search+'%')
  count=db.execute('select count(*) from sources where '+where,args).fetchone()[0]
  rows=[dict(r) for r in db.execute('select path,size,state,error,last_hash from sources where '+where+' order by path limit 30 offset ?',[*args,(page-1)*30])]
  for r in rows:r['name']=Path(r['path']).name
  active=[]
  for file in STATE.glob('activity-*.json'):
   try:
    item=json.loads(file.read_text());os.kill(int(item['pid']),0)
    if item.get('active'):active.append(item)
   except (OSError,ValueError,KeyError):pass
  rates=dict(db.execute('select kind,count(*) from events where at>? group by kind',(time.time()-3600,)).fetchall())
  inventory=None
  p=STATE/'full-inventory.json'
  if p.exists():inventory=json.loads(p.read_text())
  with hardware_lock:hw=dict(hardware)
  hw['history']=hardware_history.snapshot(time.time())
  concurrency=json.loads((STATE/'concurrency.json').read_text()) if (STATE/'concurrency.json').exists() else None
  runtime=STATE/'pipeline-runtime.json'
  if runtime.exists() and concurrency is not None:
   concurrency['gpu_admission']=json.loads(runtime.read_text()).get('gpu_slots',1)
  return {'pipeline':pipeline_snapshot(db),'openclaw_summary':openclaw_summary_snapshot(),'summary_backfill':summary_progress_snapshot(),'maintenance':maintenance_snapshot(),'concurrency':concurrency,'llm_cost':llm_cost_snapshot(),'time':time.time(),'hardware':hw,'counts':counts,'total':sum(counts.values()),'scan':scan,'power':json.loads((STATE/'power-status.json').read_text()) if (STATE/'power-status.json').exists() else {},'paused':(STATE/'PAUSED').exists(),'active':active,'events_hour':rates,'files':rows,'filtered_total':count,'page':page,'inventory':inventory}
 finally:db.close()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,code,data):
  content=json.dumps(data,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
 def authorized(self):
  token=self.headers.get('Authorization','')
  if not token.startswith('Bearer ') or len(token)>16384:self.send(401,{'error':'请先登录管理员账户'});return False
  with requests.Session() as session:
   session.trust_env=False
   try:r=session.get('http://127.0.0.1:8080/api/v1/system/admin/runtime/queues',headers={'Authorization':token},timeout=8,allow_redirects=False)
   except requests.RequestException:self.send(503,{'error':'无法验证管理员权限'});return False
  if r.status_code!=200:self.send(403 if r.status_code==403 else 401,{'error':'需要系统管理员权限'});return False
  return True
 def do_GET(self):
  route=urlsplit(self.path).path
  if route in ('/api/v1/nas/files','/api/v1/nas/files/raw'):
   if not request_slots.acquire(blocking=False):self.send(429,{'error':'请稍后重试'});return
   try:
    cfg=json.loads(Path('/etc/weknora-nas-sync/config.json').read_text());query=parse_qs(urlsplit(self.path).query)
    if route.endswith('/raw'):
     if not download_slots.acquire(blocking=False):self.send(429,{'error':'原件下载繁忙，请稍后重试'});return
     try:
      data,mime,name,size=original(STATE,self.headers,query,cfg)
      with data:
       self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(size));self.send_header('Content-Disposition',('inline' if mime.startswith('image/') else 'attachment')+"; filename*=UTF-8''"+quote(name,safe=''));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
       self.connection.settimeout(30)
       while True:
        block=data.read(65536)
        if not block:break
        try:self.wfile.write(block)
        except (BrokenPipeError,ConnectionResetError,TimeoutError):break
     finally:download_slots.release()
    else:self.send(200,query_files(STATE,self.headers,query,cfg))
   except AccessError as e:self.send(e.status,{'error':e.message})
   except (ValueError,TypeError):self.send(400,{'error':'参数无效'})
   except Exception:self.send(503,{'error':'文件目录暂不可用'})
   finally:request_slots.release()
   return
  if urlsplit(self.path).path!=PREFIX:self.send(404,{'error':'not found'});return
  if not request_slots.acquire(blocking=False):self.send(429,{'error':'请稍后重试'});return
  try:
   if self.authorized():self.send(200,snapshot(parse_qs(urlsplit(self.path).query)))
  except (ValueError,TypeError):self.send(400,{'error':'参数无效'})
  except Exception:self.send(503,{'error':'同步状态暂不可用'})
  finally:request_slots.release()
 def do_POST(self):
  if urlsplit(self.path).path!=PREFIX:self.send(404,{'error':'not found'});return
  if not self.authorized():return
  # Interactive JWT only. Read-only API keys must not control the worker.
  if self.headers.get('Authorization','').split(' ',1)[-1].startswith('sk-'):self.send(403,{'error':'请使用管理员网页登录'});return
  try:
   size=int(self.headers.get('Content-Length','0'))
   if not 0<size<=1024:raise ValueError()
   payload=json.loads(self.rfile.read(size));action=payload.get('action')
   phase=json.loads((STATE/'power-status.json').read_text()).get('phase') if (STATE/'power-status.json').exists() else None
   if phase in ['draining','backing_up','ready','powering_off'] or (STATE/'POWER_REQUESTED').exists():self.send(409,{'error':'关机准备中，请勿重复操作'});return
   if action=='shutdown':
    if payload.get('confirmation')!='关闭虚拟机':raise ValueError()
    fd=os.open(STATE/'POWER_REQUESTED',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    self.send(202,{'success':True,'action':action});return
   if action=='pause':(STATE/'PAUSED').touch()
   elif action=='resume':(STATE/'PAUSED').unlink(missing_ok=True)
   elif action=='scan':(STATE/'SCAN_REQUESTED').touch()
   else:raise ValueError()
   self.send(200,{'success':True,'action':action})
  except (ValueError,TypeError):self.send(400,{'error':'操作无效'})
if __name__=='__main__':
 threading.Thread(target=collect,daemon=True).start()
 ThreadingHTTPServer(('127.0.0.1',16592),Handler).serve_forever()
