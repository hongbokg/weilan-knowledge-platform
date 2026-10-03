import json,sqlite3,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
from decimal import Decimal,InvalidOperation
from store import connect
from products import norm
import sys
sys.path.insert(0,'/opt/weknora-nas-sync')
from catalog_api import permitted,AccessError
SLOTS=threading.BoundedSemaphore(12)
def db_for(headers):
 allowed=permitted(headers);db=connect(True);cfg=json.loads(Path('/etc/weknora-nas-sync/config.json').read_text())
 db.execute('attach database ? as catalogue',('file:'+str(Path(cfg['state_dir'])/'state.db')+'?mode=ro',))
 where="c.missing=0 and c.kb in ("+','.join('?' for _ in allowed)+") and coalesce(s.error,'') not like '%sensitive%'" if allowed else '0'
 args=list(allowed)
 for word in ['审计报告','财务报表','密码','口令','凭据','credentials','secret']:
  where+=' and instr(lower(c.path),?)=0';args.append(word)
 for folder in cfg.get('blacklist',[]):where+=" and instr('/'||trim(c.path,'/')||'/',?)=0";args.append('/'+folder+'/')
 where+=" and c.path not like '%/.%' and c.path not like '%/~$%'"
 return db,allowed,where,args
JOIN=' from evidence e join files f on f.path=e.path join catalogue.file_catalog c on c.path=e.path left join catalogue.sources s on s.path=c.path '
def search(headers,q):
 db,allowed,where,args=db_for(headers)
 try:
  text=q.get('q',[''])[0][:160];company=q.get('company',[''])[0][:80];project=q.get('project',[''])[0][:160];cat=q.get('category',[''])[0][:80];page=max(1,min(int(q.get('page',['1'])[0]),100000))
  where+=' and f.modified=c.modified and f.size=c.size'
  if text:where+=" and (instr(e.model_key,?)>0 or instr(lower(e.item||' '||e.brand||' '||e.spec),lower(?))>0)";args.extend([norm(text),text])
  for field,val in [('company',company),('project',project),('category',cat)]:
   if val:where+=' and instr(e.'+field+',?)>0';args.append(val)
  total=db.execute('select count(*)'+JOIN+'where '+where,args).fetchone()[0]
  rows=[dict(r) for r in db.execute('select e.*,c.kb as current_kb'+JOIN+'where '+where+' order by case when e.model_key=? then 0 else 1 end,e.model,e.path,e.sheet,e.row limit 40 offset ?',[*args,norm(text),(page-1)*40])]
  ranges={}
  for row in rows:
   row['parameters']=json.loads(row['parameters']);row['raw']=json.loads(row['raw'])
   row['price_comparable']=bool(row['model'] and row['price'] and row['tax']!='未知' and row['unit'] and row['currency']!='未知')
  # Price intervals use all matching evidence, with original MD5/table/row de-duplication.
  seen=set()
  for r in db.execute('select e.model_key,e.brand,e.price,e.tax,e.unit,e.currency,e.md5,e.sheet,e.row'+JOIN+'where '+where,args):
   if not(r['model_key'] and r['price'] and r['tax']!='未知' and r['unit'] and r['currency']!='未知'):continue
   evidence=(r['md5'],r['sheet'],r['row']);key=(r['brand'],r['model_key'],r['tax'],r['unit'],r['currency'])
   if evidence in seen:continue
   seen.add(evidence);price=Decimal(r['price']);entry=ranges.setdefault(key,{'brand':key[0],'model_key':key[1],'tax':key[2],'unit':key[3],'currency':key[4],'minimum':price,'maximum':price,'count':0});entry['minimum']=min(entry['minimum'],price);entry['maximum']=max(entry['maximum'],price);entry['count']+=1
  for r in ranges.values():r['minimum']=str(r['minimum']);r['maximum']=str(r['maximum'])
  return {'rows':rows,'total':total,'page':page,'price_ranges':list(ranges.values()),'notice':'区间按原表单价、相同品牌型号与税/单位/币种统计；修改时间不等于报价日期。'}
 finally:db.close()
def projects(headers):
 db,allowed,where,args=db_for(headers)
 try:
  out=[]
  for g in db.execute('select * from groups order by kind,company,project'):
   if g['kb'] not in allowed:continue
   count=db.execute('select count(*) from catalogue.file_catalog c left join catalogue.sources s on s.path=c.path where '+where+' and c.kb=?',[*args,g['kb']]).fetchone()[0]
   out.append({'id':g['id'],'company':g['company'],'project':g['project'],'kind':g['kind'],'knowledge_base_id':g['kb'],'source_files':count,'state':g['state']})
  return {'groups':out}
 finally:db.close()
def compare(headers,data):
 model=data.get('model','');requirements=data.get('requirements',[])
 if not isinstance(model,str) or not 1<=len(model)<=160 or not isinstance(requirements,list) or len(requirements)>25:raise ValueError()
 found=search(headers,{'q':[model]});rows=[r for r in found['rows'] if r['model_key']==norm(model)];results=[]
 for req in requirements:
  if not isinstance(req,dict) or set(req)-{'name','value','unit','op'}:raise ValueError()
  name=req.get('name','');expected=req.get('value','');unit=req.get('unit','');op=req.get('op','=')
  if not all(isinstance(x,str) and len(x)<=160 for x in [name,expected,unit,op]) or op not in ('=','>=','<='):raise ValueError()
  checks=[]
  for row in rows:
   actual=row['parameters'].get(name)
   if not actual or actual['unit']!=unit:continue
   status='缺证据'
   try:
    a=Decimal(actual['value']);b=Decimal(expected);ok=a==b if op=='=' else a>=b if op=='>=' else a<=b;status='满足' if ok else '不满足'
   except InvalidOperation:
    if op=='=':status='满足' if norm(actual['value'])==norm(expected) else '不满足'
   checks.append({'status':status,'actual':actual,'source':{'filename':Path(row['path']).name,'nas_path':row['path'],'sheet':row['sheet'],'row':row['row'],'md5':row['md5']}})
  statuses={r['status'] for r in checks};status='证据冲突' if '满足' in statuses and '不满足' in statuses else next(iter(statuses)) if statuses else '缺证据'
  results.append({'requirement':req,'status':status,'evidence':checks})
 return {'model':model,'matching_evidence_rows':len(rows),'results':results,'notice':'仅比较已有明确数值和相同单位；缺少证据不得视为满足，尚不支持自动判断全部语义条款。'}
class H(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,status,data):
  raw=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
 def dispatch(self,post=False):
  if not SLOTS.acquire(False):return self.send(429,{'error':'busy'})
  try:
   route=urlsplit(self.path).path
   if post:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=32768 or route!='/api/v1/products/compare':raise ValueError()
    result=compare(self.headers,json.loads(self.rfile.read(length)))
   elif route=='/api/v1/products':result=search(self.headers,parse_qs(urlsplit(self.path).query))
   elif route=='/api/v1/organization/projects':result=projects(self.headers)
   else:return self.send(404,{'error':'not found'})
   self.send(200,result)
  except AccessError as e:self.send(e.status,{'error':e.message})
  except (ValueError,TypeError):self.send(400,{'error':'invalid parameters'})
  except Exception:self.send(503,{'error':'产品与项目服务暂不可用'})
  finally:SLOTS.release()
 def do_GET(self):self.dispatch()
 def do_POST(self):self.dispatch(True)
if __name__=='__main__':ThreadingHTTPServer(('127.0.0.1',16593),H).serve_forever()
