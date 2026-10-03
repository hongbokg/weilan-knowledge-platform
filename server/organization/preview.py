import json,sqlite3,collections
from pathlib import Path
from classification import classify
c=json.loads(Path('/etc/weknora-nas-sync/config.json').read_text());db=sqlite3.connect('file:'+str(Path(c['state_dir'])/'state.db')+'?mode=ro',uri=True)
groups={}
for path,kb,ext in db.execute('select path,kb,ext from file_catalog where missing=0'):
 r=classify(path)
 if r:
  g=groups.setdefault((r['company'],r['project']),{'company':r['company'],'project':r['project'],'kind':r['kind'],'prefixes':set(),'count':0,'kbs':set()});g['prefixes'].add(r['prefix']);g['kbs'].add(kb);g['count']+=1
out=[dict(g,prefixes=sorted(g['prefixes']),kbs=sorted(g['kbs'])) for g in groups.values() if g['count']>=3]
Path('/home/weilan/organization-plan.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print('GROUPS',len(out));print(json.dumps(sorted(out,key=lambda r:r['count'],reverse=True)[:15],ensure_ascii=False));print('EXCEL',db.execute("select ext,count(*) from file_catalog where missing=0 and ext in ('.xlsx','.xls','.csv') group by ext").fetchall())
