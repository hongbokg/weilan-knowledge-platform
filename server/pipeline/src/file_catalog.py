"""Source-file discovery and ordinary permission-bound catalogue knowledge.

This is a filename/path index, not a company-certificate database. Document
contents, accounts and passwords are never copied into catalogue entries.
"""
import hashlib,json,re,time,unicodedata
from pathlib import PurePosixPath

IMAGE_EXTENSIONS=('.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff')
CATALOG_SCHEMA='''
create table if not exists file_catalog(path text primary key,kb text not null,ext text,size integer,modified text,seen text,missing integer default 0,md5 text);
create index if not exists file_catalog_kb_path on file_catalog(kb,path);
create table if not exists catalogue_publications(id text primary key,kb text,parent text,batch integer,content_hash text,knowledge_id text,state text,updated real);
'''

def public_file(path):
    parts=PurePosixPath(path).parts
    return not any(p.startswith('.') or p.startswith('~$') for p in parts) and PurePosixPath(path).suffix.lower() not in ('.key','.pem','.pfx','.crt','.env','.db','.sql','.log')

def merge_catalog(sync,scan):
    sync.db.execute('insert into file_catalog(path,kb,ext,size,modified,seen,missing) select path,kb,ext,size,modified,?,0 from discovered_catalog where true on conflict(path) do update set md5=case when file_catalog.size=excluded.size and file_catalog.modified=excluded.modified then file_catalog.md5 else null end,kb=excluded.kb,ext=excluded.ext,size=excluded.size,modified=excluded.modified,seen=excluded.seen,missing=0',(scan,))
    sync.db.execute('update file_catalog set missing=missing+1 where seen!=?',(scan,))

def catalogue_groups(sync):
    rows=sync.db.execute("select c.path,c.kb,c.size,c.modified,s.state from file_catalog c left join sources s on s.path=c.path where c.missing=0 and coalesce(s.error,'') not like '%sensitive%' order by c.kb,c.path").fetchall()
    groups={}
    for row in rows:
        path=row['path']
        if sync.excluded(path) or not public_file(path):continue
        if re.search(r'密码|口令|凭据|credentials|secrets?',path,re.I) and row['state']!='indexed':continue
        groups.setdefault((row['kb'],str(PurePosixPath(path).parent)),[]).append(dict(row))
    focus=sync.cfg.get("priority_keywords",[])
    ordered=sorted(groups.items(),key=lambda item:(not any(x in item[0][1] for x in focus if isinstance(x,str)),item[0]))
    for (kb,parent),files in ordered:
        for offset in range(0,len(files),30):
            batch=offset//30
            ident=hashlib.sha256(json.dumps([kb,parent,batch],ensure_ascii=False).encode()).hexdigest()
            marker='nas-catalog-'+ident[:20]
            lines=['# NAS 源文件目录清单','这是实际发现的源文件目录，不是文件正文、OCR 结果或证照真实性证明。',
                   '文件出现在此清单，表示 NAS 扫描发现过它；正文可能待解析、处理中或隔离。查不到正文不能认定 NAS 没有原件。',
                   '当前状态与原图请打开文件目录页面 /nas-files.html，按文件名查询。',f'目录：{parent}']
            for file in files[offset:offset+30]:
                lines += [f"\n## 文件：{PurePosixPath(file['path']).name}",f"NAS 原始路径：{file['path']}",
                          f"大小：{file['size']} 字节；NAS 修改时间：{file['modified']}"]
            body='\n'.join(lines)
            yield {'id':ident,'kb':kb,'parent':parent,'batch':batch,'marker':marker,'body':body,
                   'hash':hashlib.sha256(body.encode()).hexdigest(),'title':f'NAS 文件目录 · {PurePosixPath(parent).name} [{marker}]'}

def publish_catalog(sync,limit=8):
    handled=0
    for item in catalogue_groups(sync):
        row=sync.db.execute('select * from catalogue_publications where id=?',(item['id'],)).fetchone()
        if row and row['content_hash']==item['hash'] and row['state']=='indexed':continue
        if handled>=limit:break
        sync.guard();handled+=1
        kid=row['knowledge_id'] if row else None
        if row and row['state'] in ('creating','ambiguous') and not kid:
            # Reconcile a possibly accepted POST before deciding whether to retry.
            matches=[];page=1
            while True:
                result=sync.request('GET',f"/knowledge-bases/{item['kb']}/knowledge?page={page}&page_size=100")
                entries=result if isinstance(result,list) else result.get('data',result.get('items',[]))
                matches.extend(k['id'] for k in entries if '['+item['marker']+']' in k.get('title',''))
                if len(entries)<100:break
                page+=1
            if len(matches)!=1:continue
            kid=matches[0]
            sync.db.execute("update catalogue_publications set knowledge_id=?,state='created' where id=?",(kid,item['id']));sync.db.commit()
        if not row:
            sync.db.execute('insert into catalogue_publications values(?,?,?,?,?,?,?,?)',(item['id'],item['kb'],item['parent'],item['batch'],None,None,'planned',time.time()));sync.db.commit()
        try:
            if not kid:
                sync.db.execute("update catalogue_publications set state='creating' where id=?",(item['id'],));sync.db.commit()
                actual=sync.request('POST',f"/knowledge-bases/{item['kb']}/knowledge/manual",{'title':item['title'],'content':item['body'],'status':'draft','channel':'nas-catalog'})
                kid=actual['id']
                sync.db.execute("update catalogue_publications set knowledge_id=?,state='created' where id=?",(kid,item['id']));sync.db.commit()
            actual=sync.request('GET',f'/knowledge/{kid}')
            if not row or row['content_hash']!=item['hash'] or actual.get('parse_status') not in ('completed','pending','processing','finalizing'):
                sync.request('PUT',f'/knowledge/{kid}',{'custom_metadata':{'source':'NAS 文件目录','catalog_only':True,'nas_directory':item['parent'],'catalogue_owner':sync.cfg['owner'],'catalogue_id':item['id'],'snapshot_scan':sync.db.execute('select seen from file_catalog where kb=? and path like ? limit 1',(item['kb'],item['parent']+'/%')).fetchone()[0]}})
                sync.request('PUT',f'/knowledge/manual/{kid}',{'title':item['title'],'content':item['body'],'status':'publish'})
                sync.db.execute("update catalogue_publications set content_hash=?,state='indexing',updated=? where id=?",(item['hash'],time.time(),item['id']));sync.db.commit()
            deadline=time.time()+90
            while time.time()<deadline:
                sync.guard();actual=sync.request('GET',f'/knowledge/{kid}')
                if sync.search_ready(kid,actual):
                    sync.db.execute("update catalogue_publications set state='indexed',updated=? where id=?",(time.time(),item['id']));sync.db.commit();break
                if actual.get('parse_status')=='failed':raise RuntimeError('catalogue_index_failed')
                time.sleep(1)
        except Exception:
            # Preserve the durable POST intent; never blindly duplicate knowledge.
            sync.db.execute("update catalogue_publications set state=case when knowledge_id is null then 'ambiguous' else 'retry' end,updated=? where id=?",(time.time(),item['id']));sync.db.commit()
    return handled
