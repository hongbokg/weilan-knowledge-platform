"""Read-only routing and Wiki acceptance, excluding source bodies and credentials."""
import collections,json
from sync import Sync
s=Sync('/etc/weknora-nas-sync/config.json')
counts=collections.Counter(s.target_kb(r['path']) for r in s.db.execute('select path from sources where missing=0'))
names={r['kb_id']:r['name'] for r in s.cfg['routes']}
for kb,count in counts.items():
 stats=s.request('GET',f'/knowledgebase/{kb}/wiki/stats')
 print(json.dumps({'kb':names[kb],'queued_pdf':count,'wiki_stats':stats},ensure_ascii=False))
for r in s.db.execute("select sources.path,parts.knowledge_id,revisions.kb from sources join revisions on revisions.id=sources.live_revision join parts on parts.revision=revisions.id where sources.state='indexed' and parts.state='indexed'"):
 actual=s.request('GET','/knowledge/'+r['knowledge_id'])
 assert actual['knowledge_base_id']==s.target_kb(r['path'])==r['kb']
 assert s.search_ready(r['knowledge_id'],actual)
 print(json.dumps({'routed_and_searchable':True,'kb':names[r['kb']],'knowledge_id':r['knowledge_id'],'status':actual['parse_status']},ensure_ascii=False))
