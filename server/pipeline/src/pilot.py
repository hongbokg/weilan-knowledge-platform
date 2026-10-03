"""Seed qualification metadata only; process two business licence images."""
import json,time,uuid,fcntl
from pathlib import Path
from sync import Sync
from file_catalog import IMAGE_EXTENSIONS,public_file,publish_catalog

s=Sync('/etc/weknora-nas-sync/config.json')
root='/团队文件-蔚蓝飞牛nas/蔚蓝信科/郭静/A公司资质信息'
queue=[root];seen=set();selected=[];count=0
scan=s.db.execute('select id from scans where complete=1 order by started desc limit 1').fetchone()
seen_id=scan[0] if scan else 'qualification-pilot'
while queue:
    parent=queue.pop()
    if parent in seen:continue
    seen.add(parent)
    for row in s.listing(parent):
        path=row['path']
        if s.excluded(path):continue
        if row['directory']:
            if path.rstrip('/')!=parent.rstrip('/'):queue.append(path)
            continue
        if not public_file(path):continue
        ext=Path(path).suffix.lower();kb=s.target_kb(path)
        s.db.execute('insert into file_catalog(path,kb,ext,size,modified,seen,missing) values(?,?,?,?,?,?,0) on conflict(path) do update set kb=excluded.kb,ext=excluded.ext,size=excluded.size,modified=excluded.modified,seen=excluded.seen,missing=0',(path,kb,ext,row['size'],row['modified'],seen_id));count+=1
        if ext in IMAGE_EXTENSIONS:
            s.db.execute('insert into sources(path,etag,size,modified,seen,state) values(?,?,?,?,?,\'pending\') on conflict(path) do nothing',(path,row['etag'],row['size'],row['modified'],seen_id))
            if '金瑞世达营业执照' in path and '金瑞世达/' in path and len(selected)<2:selected.append(path)
    s.db.commit()
print(json.dumps({'catalogue_seed':count,'image_pilot_selected':len(selected)},ensure_ascii=False),flush=True)
with (s.state/'pilot.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    s.work(len(selected),prepare_only=True,paths=selected)
print(json.dumps({'pilot_states':[dict(s.db.execute('select state,error from sources where path=?',(p,)).fetchone()) for p in selected]},ensure_ascii=False),flush=True)
s.db.close()
