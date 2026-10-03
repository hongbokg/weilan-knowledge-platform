"""Permission-bound source catalogue. No direct browser NAS credentials."""
import json,sqlite3,hashlib,tempfile,time,re
from pathlib import Path,PurePosixPath
from urllib.parse import quote
import requests
from file_catalog import public_file

class AccessError(Exception):
    def __init__(self,status,message):self.status=status;self.message=message

ORIGINAL_LIMIT=100*1024**2
ORIGINAL_MIME={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp','.bmp':'image/bmp','.tif':'image/tiff','.tiff':'image/tiff','.pdf':'application/pdf','.doc':'application/msword','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.xls':'application/vnd.ms-excel','.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','.ppt':'application/vnd.ms-powerpoint','.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation'}

def download_reason(row):
    if row['state']!='indexed' or row.get('error') or row.get('source_missing',0):return '原件须完成解析及敏感内容检查后开放'
    if row['ext'] not in ORIGINAL_MIME:return '原件格式暂不支持，请通过 NAS 查看'
    if row['size']>ORIGINAL_LIMIT:return '原件超过100MiB下载上限，请通过 NAS 查看'
    return None

def permitted(headers):
    forwarded={k:headers[k] for k in ('Authorization','X-API-Key','X-Tenant-ID') if headers.get(k)}
    if not (forwarded.get('Authorization') or forwarded.get('X-API-Key')):raise AccessError(401,'请登录知识库')
    with requests.Session() as session:
        session.trust_env=False
        r=session.get('http://127.0.0.1:8080/api/v1/knowledge-bases',headers=forwarded,timeout=8,allow_redirects=False)
    if r.status_code!=200:raise AccessError(403 if r.status_code==403 else 401,'知识库访问权限验证失败')
    payload=r.json();rows=payload.get('data',[]) if isinstance(payload,dict) else payload
    if isinstance(rows,dict):rows=rows.get('knowledge_bases',rows.get('items',[]))
    if not isinstance(rows,list):raise AccessError(503,'知识库权限服务返回异常')
    return {str(row['id']) for row in rows if isinstance(row,dict) and row.get('id')}

def query_files(state,headers,query,cfg):
    allowed=permitted(headers)
    requested=query.get('kb',[''])[0]
    if requested:allowed &= {requested}
    if not allowed:return {'files':[],'total':0,'page':1}
    db=sqlite3.connect('file:'+str(state/'state.db')+'?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row
    denied=set(cfg.get('blacklist',[]))
    try:
        page=max(1,min(int(query.get('page',['1'])[0]),100000));text=query.get('q',[''])[0][:160]
        where="c.missing=0 and c.kb in ("+','.join('?' for _ in allowed)+") and coalesce(s.error,'') not like '%sensitive%'";args=list(allowed)
        if text:where+=' and instr(lower(c.path),lower(?))>0';args.append(text)
        # Keep bulk filtering in SQLite. A Python callback per discovered file
        # made a full NAS count expensive even for a narrow filename query.
        where+=" and c.path not like '%/.%' and c.path not like '%/~$%' and c.ext not in ('.key','.pem','.pfx','.crt','.env','.db','.sql','.log')"
        for word in ('密码','口令','凭据','credentials','secret'):
            where+=" and (s.state='indexed' or instr(lower(c.path),?)=0)";args.append(word)
        for folder in denied:
            where+=" and instr('/'||trim(c.path,'/')||'/',?)=0";args.append('/'+folder+'/')
        status=query.get('state',[''])[0]
        if status:
            if status not in ('pending','indexed','review','retry','validated','catalog_only'):raise ValueError()
            where+=" and coalesce(s.state,'catalog_only')=?";args.append(status)
        join=' from file_catalog c left join sources s on s.path=c.path where '+where
        total=db.execute('select count(*)'+join,args).fetchone()[0]
        rows=[dict(r) for r in db.execute("select c.path,c.kb,c.ext,c.size,c.modified,coalesce(s.state,'catalog_only') as state,coalesce(s.md5,c.md5) as md5,s.error,s.live_revision as version,coalesce(s.missing,1) as source_missing"+join+' order by c.path limit 30 offset ?',[*args,(page-1)*30])]
        for row in rows:
            row['name']=PurePosixPath(row['path']).name
            row['hash_status']='complete' if row['md5'] else 'pending'
            row['preview_available']=row['state']=='indexed' and row['ext'] in ('.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff') and row['size']<=30*1024**2
            row['download_unavailable_reason']=download_reason(row)
            row['download_available']=row['download_unavailable_reason'] is None
            row['download_limit_bytes']=ORIGINAL_LIMIT
        return {'files':rows,'total':total,'page':page}
    finally:db.close()

def original(state,headers,query,cfg):
    allowed=permitted(headers);path=query.get('path',[''])[0]
    if not path or '\\' in path or any(p in ('.','..','') for p in path.lstrip('/').split('/')) or re.search(r'[\x00-\x1f\x7f]',path):raise AccessError(400,'原件路径无效')
    if not public_file(path) or set(PurePosixPath(path).parts)&set(cfg.get('blacklist',[])):raise AccessError(404,'文件不可用')
    with sqlite3.connect('file:'+str(state/'state.db')+'?mode=ro',uri=True) as db:
        row=db.execute("select c.kb,c.ext,c.size,s.state,s.sha,s.error,s.missing from file_catalog c left join sources s on s.path=c.path where c.path=? and c.missing=0",(path,)).fetchone()
    if not row or row[0] not in allowed:raise AccessError(404,'文件不可用')
    if row[3]!='indexed' or row[5] or row[6] or not row[4]:raise AccessError(403,'原件须完成解析及敏感内容检查后开放')
    if row[1] not in ORIGINAL_MIME:raise AccessError(415,'原件格式暂不支持，请通过 NAS 查看')
    if row[2]>ORIGINAL_LIMIT:raise AccessError(413,'原件超过100MiB下载上限，请通过 NAS 查看')
    scratch=state/'download-tmp';scratch.mkdir(mode=0o700,exist_ok=True)
    f=tempfile.TemporaryFile(dir=scratch);digest=hashlib.sha256();count=0;started=time.monotonic()
    try:
        with requests.Session() as session:
            session.trust_env=False
            url=cfg['nas_url'].rstrip('/')+quote(path,safe='/')
            with session.get(url,auth=(cfg['nas_user'],cfg['nas_password']),verify=cfg.get('nas_ca',True),timeout=(8,30),stream=True,allow_redirects=False) as r:
                if r.status_code!=200:raise AccessError(503,'NAS 原件暂不可用')
                length=r.headers.get('Content-Length')
                if length and int(length)>ORIGINAL_LIMIT:raise AccessError(413,'原件超过100MiB下载上限')
                for block in r.iter_content(65536):
                    count+=len(block)
                    if count>ORIGINAL_LIMIT:raise AccessError(413,'原件超过100MiB下载上限')
                    if time.monotonic()-started>120:raise AccessError(504,'NAS 原件下载超时，请稍后重试')
                    digest.update(block);f.write(block)
        if count!=row[2] or digest.hexdigest()!=row[4]:raise AccessError(409,'NAS 原件已变化，须重新解析及权限检查后下载')
        # Repeat authorization/status checks before exposing buffered bytes.
        if row[0] not in permitted(headers):raise AccessError(404,'文件不可用')
        with sqlite3.connect('file:'+str(state/'state.db')+'?mode=ro',uri=True) as db:
            current=db.execute('select c.kb,c.missing,s.state,s.sha,s.error,s.missing from file_catalog c join sources s on s.path=c.path where c.path=?',(path,)).fetchone()
        if not current or tuple(current)!=(row[0],0,'indexed',row[4],None,0):
            if not current or current[0]!=row[0] or current[1] or current[2]!='indexed' or current[3]!=row[4] or current[4] or current[5]:raise AccessError(409,'原件状态已变化，请刷新后重试')
        f.seek(0);return f,ORIGINAL_MIME[row[1]],PurePosixPath(path).name,count
    except requests.Timeout:f.close();raise AccessError(504,'NAS 原件下载超时，请稍后重试')
    except requests.RequestException:f.close();raise AccessError(503,'NAS 原件暂不可用')
    except Exception:f.close();raise
