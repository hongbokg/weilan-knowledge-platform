"""Periodic operational evidence. No document content or credentials in output."""
import json,time,datetime,subprocess,collections
from pathlib import Path
from sync import Sync,atomic
s=Sync('/etc/weknora-nas-sync/config.json');now=time.time()
result={'at':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),'status':s.status()}
for hours in [1,12]:
 events=s.db.execute('select kind,count(*) from events where at>=? group by kind',(now-hours*3600,)).fetchall()
 result[f'events_{hours}h']=dict(events)
result['review_reasons']=dict(s.db.execute("select coalesce(error,'unknown'),count(*) from sources where state in ('review','ambiguous') group by error").fetchall())
result['gpu']=subprocess.check_output(['nvidia-smi','--query-gpu=utilization.gpu,memory.used,memory.total','--format=csv,noheader,nounits'],text=True).strip()
result['queue_archived']={q:int(subprocess.check_output(['redis-cli','ZCARD','asynq:{'+q+'}:archived'],text=True)) for q in ['default','summary','low','wiki']}
result['services']={name:subprocess.run(['systemctl','is-active',name],capture_output=True,text=True).stdout.strip() for name in ['weknora-nas-sync-worker.timer','weknora-nas-sync-publisher.timer','weknora-nas-sync-scan.timer','mineru-api.service','weknora-server.service']}
atomic(s.state/'latest-metrics.json',result)
with (s.state/'metrics.jsonl').open('a') as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')
print(json.dumps(result,ensure_ascii=False))
