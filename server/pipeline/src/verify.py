"""Read-only runtime acceptance; no credentials or document bodies in output."""
import json
from sync import Sync
s=Sync('/etc/weknora-nas-sync/config.json')
for kb in sorted(set([s.cfg['kb_id']]+[r['kb_id'] for r in s.cfg.get('routes',[])])):
 k=s.request('GET','/knowledge-bases/'+kb)
 assert all(k['indexing_strategy'][f] for f in ('vector_enabled','keyword_enabled','wiki_enabled'))
 print(json.dumps({'target_kb_name':k.get('name'),'target_kb_id':k.get('id'),'strategy':k['indexing_strategy'],'wiki_model_id':k['wiki_config']['synthesis_model_id']},ensure_ascii=False))
r=s.api.get(s.cfg['api_url']+'/knowledge-bases/028362b6-563f-43ce-8724-0f7b93eb2726',timeout=20)
print(json.dumps({'other_kb_denied':r.status_code in (403,404),'status':r.status_code}))
print(json.dumps(s.status()))
for row in s.db.execute("select knowledge_id,state from parts where knowledge_id is not null and state!='deleted'").fetchall():
 k=s.request('GET','/knowledge/'+row['knowledge_id'])
 print(json.dumps({'knowledge_id':row['knowledge_id'],'local_state':row['state'],
                   'remote':{key:k.get(key) for key in ['parse_status','enable_status','embedding_model_id','chunk_count','summary_status','pending_subtasks']}}))
 stages=s.request('GET','/knowledge/'+row['knowledge_id']+'/stages')
 print(json.dumps({key:stages.get(key) for key in ['parse_status','current_stage','last_activity_at','stall_state']}))
 def summary(value):
  if isinstance(value,dict):
   fields={k:v for k,v in value.items() if k in ['name','stage','status','code','span_name','operation','type'] and isinstance(v,(str,int,bool))}
   if fields:print(json.dumps(fields))
   for k,v in value.items():
    if k not in ('input','output','attributes','metadata','error','last_error'):summary(v)
  elif isinstance(value,list):
   for v in value:summary(v)
 summary(stages.get('trace'))
