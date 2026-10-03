"""Read-only WebDAV -> durable ledger -> MinerU -> WeKnora manual API.

No WeKnora source modifications. Each network mutation has a durable intent.
Ambiguous creates stop for reconciliation rather than blindly retrying POST.
"""
import argparse, contextlib, datetime, fcntl, hashlib, json, os, re, shutil
import sqlite3, sys, time, uuid, subprocess, errno, threading, queue
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, wait
import multiprocessing
from render_cpu import render_page, page_count
from file_catalog import CATALOG_SCHEMA, IMAGE_EXTENSIONS, merge_catalog, publish_catalog
from image_ingest import parse_image,visible_text
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, unquote, quote
import requests
from xml.etree import ElementTree as ET

DAV = '{DAV:}'
SCHEMA = '''
create table if not exists scans(id text primary key,started real,finished real,complete integer default 0,count integer default 0,error text);
create table if not exists sources(path text primary key,etag text,size integer,modified text,seen text,missing integer default 0,missing_since real,state text default 'pending',sha text,md5 text,archive text,last_hash real,error text,live_revision text);
create table if not exists revisions(id text primary key,sha text,kb text,state text,manifest text,error text,created real);
create table if not exists parts(revision text,part integer,marker text unique,knowledge_id text,state text,md_path text,error text,primary key(revision,part));
create table if not exists events(id integer primary key,at real,kind text,subject text,detail text);
'''

class Halt(Exception): pass
class Review(Exception): pass
class Ambiguous(Exception): pass
class PageYield(Exception): pass

def atomic(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False); f.flush(); os.fsync(f.fileno())
    os.replace(temp,path)

def office_format(source, extension):
    # Filenames in historical NAS archives can disagree with the container.
    # Select only within the same document family, without altering originals.
    with Path(source).open('rb') as f:
        magic=f.read(8)
    if magic==bytes.fromhex('d0cf11e0a1b11ae1'):
        return {'docx':'doc','pptx':'ppt'}.get(extension,extension)
    if magic[:4] in (b'PK\x03\x04',b'PK\x05\x06',b'PK\x07\x08'):
        return {'doc':'docx','ppt':'pptx'}.get(extension,extension)
    return extension

def under(path, root):
    return path == root.rstrip('/') or path.startswith(root.rstrip('/') + '/')

def normalize_href(href, base):
    u=urlsplit(href)
    if u.netloc and (u.scheme,u.netloc)!=(urlsplit(base).scheme,urlsplit(base).netloc):
        raise ValueError('cross-host WebDAV href')
    p=unquote(u.path)
    if not p.startswith('/') or '\\' in p or '\x00' in p or '..' in p.split('/'):
        raise ValueError('unsafe WebDAV path')
    return str(PurePosixPath(p))

def sensitive(text):
    from credential_policy import contains_credentials
    return contains_credentials(text)

def split_markdown(pages, limit=180000, office=False):
    # Preserve whole pages/tables. An oversized individual page requires review.
    chunks=[];current=''
    for number, text in pages:
        item=f'\n\n## 原文件文本段 {number}（不代表页码）\n\n{text}' if office else f'\n\n## 原文件第 {number} 页\n\n{text}'
        if len(item)>limit:raise Review('single_page_exceeds_manual_limit')
        if current and len(current)+len(item)>limit:chunks.append(current);current=''
        current+=item
    if current.strip():chunks.append(current)
    return chunks

def quality(md, blocks, native, blank):
    errors=[]
    plain=re.sub(r'<[^>]*>','',md).strip()
    if not blank and (not blocks or len(plain)<10):errors.append('nonblank_page_empty_or_short')
    for b in blocks:
        if b.get('type')=='table':
            content=re.sub(r'<[^>]*>','',str(b.get('content','')))
            if not re.search(r'[\w\u4e00-\u9fff]',content):errors.append('empty_table')
    native_plain=re.sub(r'\s','',native)
    if len(native_plain)>100 and len(re.sub(r'\s','',plain))<len(native_plain)*.45:
        errors.append('native_text_coverage_low')
    # Short certificates are allowed; there is no arbitrary 300-character gate.
    if sensitive(md):errors.append('sensitive_content_admin_review')
    return sorted(set(errors))

class Sync:
    def __init__(self, config):
        self.config_path=str(config)
        self.cfg=json.loads(Path(config).read_text()); c=self.cfg
        self.state=Path(c['state_dir']);self.state.mkdir(parents=True,exist_ok=True)
        profile=self.state/'pipeline-runtime.json'
        runtime=json.loads(profile.read_text()) if profile.exists() else {}
        self.gpu_admission=int(runtime.get('gpu_slots',1))
        if self.gpu_admission not in (1,2,4):raise ValueError('unsupported_gpu_slot_count')
        self.gpu_backend=runtime.get('backend','persistent-python-q8')
        self.db=sqlite3.connect(self.state/'state.db',timeout=30)
        self.db.row_factory=sqlite3.Row
        self.db.execute('pragma journal_mode=WAL');self.db.execute('pragma synchronous=FULL')
        self.db.executescript(SCHEMA+CATALOG_SCHEMA)
        columns={r[1] for r in self.db.execute('pragma table_info(sources)')}
        for column in ('attempts','next_attempt'):
            if column not in columns:self.db.execute(f'alter table sources add column {column} real default 0')
        if 'cache_signature' not in columns:self.db.execute('alter table sources add column cache_signature text')
        self.db.executescript('''
        create index if not exists sources_live_revision_idx on sources(live_revision);
        create index if not exists sources_sha_idx on sources(sha);
        create index if not exists revisions_sha_kb_idx on revisions(sha,kb,created);
        create index if not exists sources_publish_idx on sources(state,next_attempt,last_hash);
        ''')
        self.db.commit()
        self.nas=requests.Session();self.nas.auth=(c['nas_user'],c['nas_password'])
        self.nas.verify=c.get('nas_ca',True);self.nas.trust_env=False
        self.api=requests.Session();self.api.headers['X-API-Key']=c['api_key'];self.api.trust_env=False
        self.local=requests.Session();self.local.trust_env=False
        self.archive=Path(c['archive_dir']);self.ram=Path(c['ram_dir'])
        self.render_pool=None
    def close(self):
        if self.render_pool and not getattr(self,'render_pool_shared',False):self.render_pool.shutdown(wait=True,cancel_futures=True)
        self.db.close();self.api.close();self.nas.close();self.local.close()
    def stage(self,value,**detail):
        if hasattr(self,'activity'):
            self.activity.update(stage=value,updated_at=time.time(),**detail)
            atomic(self.state/self.activity_file,self.activity)
    def event(self,kind,subject,detail):
        self.db.execute('insert into events(at,kind,subject,detail) values(?,?,?,?)',(time.time(),kind,subject,json.dumps(detail)))
        self.db.commit()
        print(json.dumps({'event':kind,'subject':subject,'detail':detail},ensure_ascii=False),flush=True)
        if kind=='page_completed' and hasattr(self,'activity'):
            self.activity.update({'page':detail['page'],'pages':detail['total'],'updated_at':time.time()})
            atomic(self.state/getattr(self,'activity_file','activity-parser.json'),self.activity)
    def guard(self):
        if getattr(self,'stop_event',None) is not None and self.stop_event.is_set():raise Halt('worker_checkpoint_stop')
        if (self.state/'PAUSED').exists():raise Halt('paused')
        if getattr(self,'activity',{}).get('role')=='parser' and (self.state/'OCR_PAUSED').exists():raise Halt('ocr_checkpoint_paused')
        marker=self.state/'SCAN_COMMIT.json'
        if marker.exists():
            try:
                commit=json.loads(marker.read_text());os.kill(int(commit['pid']),0)
            except (OSError,ValueError,KeyError):pass
            else:raise Halt('inventory_snapshot_commit')
        import subprocess
        d=json.loads(subprocess.check_output(['findmnt','-J','-T',str(self.archive),'-o','UUID,OPTIONS']))['filesystems'][0]
        if d['uuid']!=self.cfg['archive_uuid'] or 'rw' not in d['options'].split(','):raise Halt('archive_mount_unavailable')
        memory=json.loads(subprocess.check_output(['findmnt','-J','-T',str(self.ram),'-o','FSTYPE,OPTIONS']))['filesystems'][0]
        if memory['fstype']!='tmpfs' or 'noswap' not in memory['options'].split(','):raise Halt('RAM_mount_unavailable')
        if shutil.disk_usage(self.archive).free<20*1024**3:raise Halt('archive_free_space_low')
    def nas_request(self,method,path,**kwargs):
        assert method in ('GET','PROPFIND','HEAD')
        self.guard()
        url=self.cfg['nas_url'].rstrip('/')+quote(path,safe='/')
        # Never follow redirects with source credentials, including same-host ones.
        r=self.nas.request(method,url,timeout=(10,60),allow_redirects=False,**kwargs)
        if r.status_code not in (200,207):r.close();raise RuntimeError('nas_http_'+str(r.status_code))
        return r
    def listing(self,path):
        with self.nas_request('PROPFIND',path,headers={'Depth':'1'},stream=True) as r:
            chunks=[];total=0
            for b in r.iter_content(65536):
                total+=len(b)
                if total>16*1024**2:raise Review('directory_listing_too_large')
                chunks.append(b)
        xml=b''.join(chunks).decode('utf-8-sig')
        if '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():raise Review('XML_DTD_not_allowed')
        tree=ET.fromstring(xml);items=[]
        for response in tree.findall(DAV+'response'):
            p=normalize_href(response.findtext(DAV+'href') or '',self.cfg['nas_url'])
            if not under(p,path):raise Review('listing_outside_root')
            prop=None
            for ps in response.findall(DAV+'propstat'):
                if ' 200 ' in (ps.findtext(DAV+'status') or ''):prop=ps.find(DAV+'prop');break
            if prop is None:raise Review('incomplete_directory_listing')
            items.append({'path':p,'directory':prop.find('.//'+DAV+'collection') is not None,
                          'size':int(prop.findtext(DAV+'getcontentlength') or 0),
                          'etag':prop.findtext(DAV+'getetag') or '',
                          'modified':prop.findtext(DAV+'getlastmodified') or ''})
        if not items:raise Review('empty_or_invalid_listing')
        return items
    def excluded(self,path):
        parts=PurePosixPath(path).parts
        return any(p.startswith('.') or p.startswith('~$') for p in parts) or bool(set(parts)&set(self.cfg.get('blacklist',[])))
    def target_kb(self,path):
        rules=sorted(self.cfg.get('routes',[]),key=lambda r:len(r['prefix']),reverse=True)
        for rule in rules:
            if under(path,rule['prefix']):return rule['kb_id']
        return self.cfg['kb_id']
    def revision_id(self,sha,kb):
        return hashlib.sha256(json.dumps([self.cfg['owner'],sha,self.cfg['parser_revision'],kb],separators=(',',':')).encode()).hexdigest()
    def reuse_pages(self,sha,revision):
        # Reuse parser checkpoints across KBs, never remote knowledge IDs.
        target=self.archive/'derived'/revision
        if target.exists():return
        for old in self.db.execute('select id from revisions where sha=? and id!=? order by created desc',(sha,revision)):
            source=self.archive/'derived'/old['id']
            if not source.is_dir():continue
            target.mkdir(parents=True,exist_ok=True)
            for page in source.glob('page-*'):
                if page.is_dir() and (page/'quality.json').exists() and (page/'clean.md').exists():
                    shutil.copytree(page,target/page.name)
            return
    def scan(self):
        # Discovery is independent of processing. Only the final snapshot merge
        # briefly stops workers at checkpoints, preserving version consistency.
        scan=uuid.uuid4().hex
        self.db.execute('insert into scans(id,started) values(?,?)',(scan,time.time()));self.db.commit()
        self.db.execute('create temp table inventory(path text primary key,etag text,size integer,modified text)')
        self.db.execute('create temp table discovered_catalog(path text primary key,kb text,ext text,size integer,modified text)')
        count=0;seen=set();stack=list(self.cfg['roots'])
        try:
            while stack:
                self.guard();root=stack.pop()
                if root in seen:continue
                seen.add(root)
                for item in self.listing(root):
                    path=item['path']
                    if self.excluded(path) or path==root.rstrip('/'):continue
                    if item['directory']:stack.append(path);continue
                    self.db.execute('insert or replace into discovered_catalog values(?,?,?,?,?)',(path,self.target_kb(path),PurePosixPath(path).suffix.lower(),item['size'],item['modified']))
                    if not path.lower().endswith(('.pdf','.doc','.docx','.ppt','.pptx')+IMAGE_EXTENSIONS):continue
                    self.db.execute('insert or replace into inventory values(?,?,?,?)',(path,item['etag'],item['size'],item['modified']));count+=1
                self.db.execute('update scans set count=? where id=?',(count,scan));self.db.commit()
            marker=self.state/'SCAN_COMMIT.json'
            atomic(marker,{'pid':os.getpid(),'at':time.time()})
            try:
                with contextlib.ExitStack() as held:
                    for name in ('worker.lock','cpu.lock','publisher.lock'):
                        lock=held.enter_context((self.state/name).open('a'))
                        fcntl.flock(lock,fcntl.LOCK_EX)
                    self._merge_inventory(scan)
            finally:marker.unlink(missing_ok=True)
            self.event('scan_completed',scan,{'files':count});return scan
        except Exception as e:
            self.db.rollback()
            self.db.execute('update scans set finished=?,count=?,error=? where id=?',(time.time(),count,type(e).__name__,scan));self.db.commit()
            raise
        finally:
            self.db.execute('drop table if exists inventory')
            self.db.execute('drop table if exists discovered_catalog')
    def _merge_inventory(self,scan):
        count=0
        for item in self.db.execute('select * from inventory').fetchall():
            path=item['path']
            old=self.db.execute('select * from sources where path=?',(path,)).fetchone()
            changed=not old or old['missing']>0 or old['state']=='retired' or any(old[k]!=item[k] for k in ('etag','size','modified'))
            if old and old['live_revision']:
                live=self.db.execute('select kb from revisions where id=?',(old['live_revision'],)).fetchone()
                if live and live['kb']!=self.target_kb(path):changed=True
            rehash=old and time.time()-(old['last_hash'] or 0)>self.cfg.get('rehash_days',30)*86400
            status='pending' if changed or rehash else old['state']
            self.db.execute('insert into sources(path,etag,size,modified,seen,state) values(?,?,?,?,?,?) on conflict(path) do update set etag=excluded.etag,size=excluded.size,modified=excluded.modified,seen=excluded.seen,missing=0,missing_since=null,state=excluded.state',
                            (path,item['etag'],item['size'],item['modified'],scan,status));count+=1
        self.db.execute('update scans set complete=1,finished=?,count=? where id=?',(time.time(),count,scan))
        self.db.execute('update sources set missing=missing+1,missing_since=coalesce(missing_since,?) where seen!=?',(time.time(),scan))
        merge_catalog(self,scan)
        self.db.commit()
    def cache_signature(self,row,source,sha):
        stat=source.stat()
        return hashlib.sha256(json.dumps([row['etag'],row['size'],row['modified'],sha,
                                         stat.st_size,stat.st_mtime_ns,stat.st_ino]).encode()).hexdigest()
    def validate_source(self,row):
        current=next((x for x in self.listing(str(PurePosixPath(row['path']).parent)) if x['path']==row['path']),None)
        if not current or any(current[k]!=row[k] for k in ('size','etag','modified')):raise Review('source_changed_during_download')
    def download(self,row):
        self.guard()
        # Page-batch resumes reuse only an immutable archive tied to the exact
        # NAS revision and local file stat. A changed source must be downloaded.
        if row['sha'] and row['archive'] and row['cache_signature']:
            expected=self.archive/'objects'/row['sha'][:2]/row['sha']
            if str(expected)==row['archive'] and expected.is_file() and expected.stat().st_size==row['size']:
                if self.cache_signature(row,expected,row['sha'])==row['cache_signature']:
                    self.validate_source(row)
                    self.event('archive_cache_hit',row['sha'],{'bytes':row['size']})
                    return row['sha'],row['md5'],expected
        self.guard();tmp=self.archive/'incoming'/uuid.uuid4().hex;tmp.parent.mkdir(parents=True,exist_ok=True)
        md5=hashlib.md5();sha=hashlib.sha256();size=0
        try:
            headers={'If-Match':row['etag']} if row['etag'] and not row['etag'].startswith('W/') else {}
            with self.nas_request('GET',row['path'],stream=True,headers=headers) as response,tmp.open('wb') as f:
                for block in response.iter_content(4*1024**2):
                    self.guard();size+=len(block)
                    if size>row['size']:raise Review('source_changed_during_download')
                    md5.update(block);sha.update(block);f.write(block)
                f.flush();os.fsync(f.fileno())
            if size!=row['size'] or not size:raise Review('source_size_mismatch')
            self.validate_source(row)
            digest=sha.hexdigest();dest=self.archive/'objects'/digest[:2]/digest
            dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():tmp.unlink()
            else:os.replace(tmp,dest)
            self.db.execute('update sources set sha=?,md5=?,archive=?,last_hash=?,cache_signature=? where path=?',
                            (digest,md5.hexdigest(),str(dest),time.time(),self.cache_signature(row,dest,digest),row['path']));self.db.commit()
            return digest,md5.hexdigest(),dest
        finally:
            if tmp.exists():tmp.unlink()
    def mineru(self,png,out):
        # CPU work overlaps between documents; only one OCR request is admitted
        # to the configured persistent backend pool. Admission waiting does not
        # consume an inference timeout; each Python instance retains one context.
        slot=getattr(self,'gpu_slot',None)
        admission_started=time.monotonic();self.page_timings=getattr(self,'page_timings',{});self.stage('gpu_wait')
        if slot:
            while not slot.acquire(timeout=.5):self.guard()
        try:
            # Native slots own independent contexts; the legacy fence remains
            # exclusive until a verified backend profile is explicitly deployed.
            from gpu_admission import admit
            with admit(self.state,getattr(self,'gpu_admission',1),self.guard):
                self.guard();self.stage('gpu_ocr')
                started=time.monotonic();job,base=self._mineru(png,out)
                self.page_timings.update(job_wait_seconds=round(time.monotonic()-started,3),admission_seconds=round(started-admission_started,3))
        finally:
            if slot:slot.release()
        self.stage('result_download');started=time.monotonic()
        result=self._download_mineru(job,base,out)
        self.page_timings['download_seconds']=round(time.monotonic()-started,3)
        return result
    def _mineru(self,png,out):
        base=self.cfg['mineru_url'];checkpoint=out/'job.json'
        if checkpoint.exists():
            jid=json.loads(checkpoint.read_text())['job_id']
        else:
            response=self.local.post(base+'/v1/parse/jobs',json={'files':[{'source':{'type':'local','path':str(png)}}],'tier':self.cfg['mineru_tier'],'ocr_mode':'ocr','output_formats':['markdown','structured_content']},timeout=(10,60))
            response.raise_for_status();jid=response.json()['job_id'];atomic(checkpoint,{'job_id':jid})
        deadline=time.time()+self.cfg.get('page_timeout',600)
        while time.time()<deadline:
            self.guard();r=self.local.get(base+'/v1/parse/jobs/'+jid,timeout=30)
            if r.status_code==404:
                checkpoint.rename(out/('lost-job-'+uuid.uuid4().hex+'.json'));raise RuntimeError('mineru_job_lost_retry_page')
            r.raise_for_status();job=r.json()
            if job['status']=='completed':break
            if job['status'] in ('failed','canceled','partial_success'):
                checkpoint.rename(out/('failed-job-'+uuid.uuid4().hex+'.json'));raise Review('mineru_page_failed')
            time.sleep(.5)
        else:
            self.local.delete(base+'/v1/parse/jobs/'+jid,timeout=15);raise Review('mineru_page_timeout')
        return job,base
    def _download_mineru(self,job,base,out):
        paths={}
        for f in job.get('files',[]):
            for kind,ref in (f.get('output_files') or {}).items():
                if kind not in ('markdown','structured_content'):continue
                dest=out/('result.md' if kind=='markdown' else 'result.json');size=0
                with self.local.get(base+'/v1/files/'+ref['file_id']+'/content',stream=True,timeout=60) as r,dest.with_suffix('.tmp').open('wb') as target:
                    r.raise_for_status()
                    for block in r.iter_content(1024**2):
                        size+=len(block)
                        if size>64*1024**2:raise Review('parser_output_exceeds_budget')
                        target.write(block)
                os.replace(dest.with_suffix('.tmp'),dest);paths[kind]=dest
        if len(paths)!=2:raise Review('missing_parser_output')
        return paths
    def parse(self,source,revision):
        base=self.archive/'derived'/revision;base.mkdir(parents=True,exist_ok=True)
        if self.render_pool is None:
            self.render_pool=ProcessPoolExecutor(max_workers=2,mp_context=multiprocessing.get_context('spawn'))
        pool=self.render_pool
        total_pages=pool.submit(page_count,str(source)).result(timeout=180)
        self.stage('cpu_render',pages=total_pages)
        pages=[];issues=[];futures={};rendered=0
        self.ram.mkdir(parents=True,exist_ok=True)
        # Two isolated CPU processes per document lane, four pages of lookahead.
        # Four lanes therefore use at most eight render processes / sixteen PNGs.
        def submit(index):
            if index>=total_pages or index in futures:return
            out=base/f'page-{index+1:06}'
            if (out/'quality.json').exists():return
            png=self.ram/(revision+f'-{index+1}.png')
            futures[index]=pool.submit(render_page,str(source),index,str(png))
        try:
            for i in range(total_pages):
                self.guard();out=base/f'page-{i+1:06}';out.mkdir(exist_ok=True);qc=out/'quality.json'
                if qc.exists():
                    q=json.loads(qc.read_text());issues.extend(q['errors'])
                    pages.append((i+1,(out/'clean.md').read_text()));continue
                self.stage('cpu_render',page=i+1)
                for ahead in range(i,min(i+4,total_pages)):submit(ahead)
                png=self.ram/(revision+f'-{i+1}.png')
                try:
                    try:info=futures.pop(i).result(timeout=180)
                    except ValueError as e:raise Review(str(e))
                    native=info.pop('native');blank=info['blank']
                    started=time.monotonic()
                    if blank:md='（原文空白页）';blocks=[]
                    else:
                        outputs=self.mineru(png,out);md=outputs['markdown'].read_text()
                        structured=json.loads(outputs['structured_content'].read_text())
                        blocks=[b for p in structured.get('pages',[]) for b in p.get('blocks',[])]
                        if re.search(r'!\[.*?\]\(',md) and len(visible_text(md))<100:issues.append('image_text_requires_ocr')
                        md=re.sub(r'!\[.*?\]\([^\n]*\)','[图片保留于原文件]',md)
                    errors=quality(md,blocks,native,blank);issues.extend(errors)
                    (out/'clean.md').write_text(md)
                    atomic(qc,{'page':i+1,'errors':errors,**info,'parser':self.cfg['mineru_version'],'blocks':len(blocks),
                               'inference_seconds':round(time.monotonic()-started,3)})
                    pages.append((i+1,md));rendered+=1
                    self.event('page_completed',revision,{'page':i+1,'total':total_pages,'issues':errors,'render_seconds':info['render_seconds'],'dpi':info['dpi'],'inference_seconds':round(time.monotonic()-started,3)})
                finally:
                    if png.exists():png.unlink()
                if rendered>=10 and i+1<total_pages:raise PageYield()
        finally:
            for future in futures.values():future.cancel()
            if futures:wait(list(futures.values()))
            for png in self.ram.glob(revision+'-*.png'):png.unlink(missing_ok=True)
        # Assets issue is re-derived on resume rather than forgotten with a checkpoint.
        for _,md in pages:
            if '[图片保留于原文件]' in md and len(visible_text(md.replace('[图片保留于原文件]','')))<100:issues.append('image_text_requires_ocr')
        if not pages or all(md=='（原文空白页）' for _,md in pages):issues.append('all_pages_blank')
        manifest={'revision':revision,'pages':len(pages),'errors':sorted(set(issues)),'parser':self.cfg['mineru_version']}
        atomic(base/'manifest.json',manifest)
        if issues:raise Review(','.join(sorted(set(issues))))
        return split_markdown(pages),base
    def parse_office(self,source,revision,extension):
        # This queue runs in a CPU-only systemd sandbox, separate from GPU work.
        if source.stat().st_size>64*1024**2:raise Review('office_oversize_requires_conversion')
        extension=office_format(source,extension)
        root=Path('/data/archive/objects/pilot-private');root.mkdir(parents=True,exist_ok=True)
        link=root/(source.name+'.'+extension)
        if not link.exists():
            try:os.link(source,link)
            except OSError as e:
                # systemd binds writable paths separately; hardlinks across
                # these mount boundaries can fail despite the same HDD.
                if e.errno!=errno.EXDEV:raise
                tmp=link.with_suffix('.tmp');shutil.copyfile(source,tmp);os.replace(tmp,link)
        output=Path('/data/archive/derived/pilot-private')/revision;output.mkdir(parents=True,exist_ok=True)
        if not (output/'document.md').exists():
            result=subprocess.run(['/opt/weknora-nas-sync/anydoc-worker-bin','--input',str(link),'--format',extension,'--output',str(output)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=180)
            if result.returncode:raise Review('anydoc_conversion_failed')
        md=(output/'document.md').read_text();errors=quality(md,[{'type':'text'}],'',False)
        has_images=bool(re.search(r'!\[.*?\]\(',md))
        if has_images and len(visible_text(md))<100:errors.append('image_text_requires_ocr')
        md=re.sub(r'!\[.*?\]\([^\n]*\)','[图片保留于原文件，图片内容尚未单独识别]',md)
        base=self.archive/'derived'/revision;base.mkdir(parents=True,exist_ok=True)
        page=base/'page-000001';page.mkdir(exist_ok=True);(page/'clean.md').write_text(md)
        atomic(page/'quality.json',{'errors':errors,'parser':'anydoc','location_kind':'text_segment'})
        atomic(base/'manifest.json',{'revision':revision,'pages':1,'errors':errors,'parser':'anydoc','office':True})
        if errors:raise Review(','.join(errors))
        return split_markdown([(1,md)],office=True),base
    def request(self,method,path,payload=None):
        self.guard()
        r=self.api.request(method,self.cfg['api_url'].rstrip('/')+path,json=payload,timeout=(10,60),allow_redirects=False)
        if r.status_code not in (200,201,202,204):raise RuntimeError('weknora_http_'+str(r.status_code))
        if r.status_code==204:return {}
        value=r.json()
        if value.get('success') is False:raise RuntimeError('weknora_unsuccessful')
        return value.get('data',value)
    def publish(self,revision,sha,md5,path,chunks,base):
        kb=self.target_kb(path)
        for index,md in enumerate(chunks,1):
            self.guard();marker='nas-sync-'+revision[:16]+f'-{index}'
            row=self.db.execute('select * from parts where revision=? and part=?',(revision,index)).fetchone()
            mdpath=base/f'part-{index:04}.md';mdpath.write_text(md)
            title=PurePosixPath(path).name[:90]+f' [{marker}]'
            if not row:
                self.db.execute('insert into parts values(?,?,?,?,?,?,?)',(revision,index,marker,None,'planned',str(mdpath),None));self.db.commit()
                row=self.db.execute('select * from parts where revision=? and part=?',(revision,index)).fetchone()
            kid=row['knowledge_id']
            if row['state'] in ('creating','ambiguous') and not kid:
                raise Ambiguous('create_outcome_unknown_reconcile_marker')
            if not kid:
                self.db.execute("update parts set state='creating' where marker=?",(marker,));self.db.commit()
                try:
                    k=self.request('POST',f'/knowledge-bases/{kb}/knowledge/manual',{'title':title,'content':md,'status':'draft','channel':'nas-sync'})
                    kid=k['id']
                    self.db.execute("update parts set knowledge_id=?,state='created' where marker=?",(kid,marker));self.db.commit()
                except Exception:
                    self.db.execute("update parts set state='ambiguous' where marker=?",(marker,));self.db.commit();raise Ambiguous('create_outcome_unknown')
            if row['state']=='indexed':continue
            metadata={'source':'飞牛NAS','nas_path':path,'file_md5':md5,'file_sha256':sha,'sync_owner':self.cfg['owner'],
                      'revision':revision,'part':index,'parts':len(chunks),'mineru_version':self.cfg['mineru_version']}
            manifest=json.loads((base/'manifest.json').read_text()) if (base/'manifest.json').exists() else {}
            metadata['parser']=manifest.get('parser',self.cfg['mineru_version'])
            for key in ('image_repair_policy','image_ocr_model','image_ocr_version','image_ocr_runtime'):
                if key in manifest:metadata[key]=manifest[key]
            metadata['location_kind']='image' if manifest.get('image') else 'text_segment' if manifest.get('office') else 'page'
            if manifest.get('image'):metadata['source_kind']='image'
            if manifest.get('office'):metadata.pop('mineru_version',None)
            self.request('PUT',f'/knowledge/{kid}',{'custom_metadata':metadata})
            # Avoid repeatedly restarting an already accepted indexing task.
            actual=self.request('GET',f'/knowledge/{kid}')
            if row['state']=='content_update_pending' or actual.get('parse_status') not in ('pending','processing','finalizing','completed'):
                self.request('PUT',f'/knowledge/manual/{kid}',{'title':title,'content':md,'status':'publish'})
            self.db.execute("update parts set state='indexing' where marker=?",(marker,));self.db.commit()
            deadline=time.time()+900
            while time.time()<deadline:
                self.guard();actual=self.request('GET',f'/knowledge/{kid}');status=actual.get('parse_status')
                if self.search_ready(kid,actual):
                    self.event('index_verified',revision,{'knowledge_id':kid,'backend_status':status});break
                if status=='failed':raise Review('weknora_index_failed')
                time.sleep(1)
            else:raise RuntimeError('weknora_index_pending')
            self.db.execute("update parts set state='indexed' where marker=?",(marker,));self.db.commit()
        self.db.execute("update revisions set state='indexed',manifest=? where id=?",(str(base/'manifest.json'),revision))
        for source in self.db.execute('select path from sources where sha=? and missing=0',(sha,)).fetchall():
            if self.target_kb(source['path'])==kb:
                self.db.execute("update sources set state='indexed',error=null,live_revision=? where path=?",(revision,source['path']))
        self.db.commit()
    def search_ready(self,kid,actual):
        if actual.get('parse_status')=='completed':return True
        if actual.get('parse_status')!='finalizing' or actual.get('enable_status')!='enabled':return False
        stages=self.request('GET',f'/knowledge/{kid}/stages')
        states={}
        def walk(value):
            if isinstance(value,dict):
                if value.get('name') in ('chunking','embedding'):states[value['name']]=value.get('status')
                for key,v in value.items():
                    if key not in ('input','output','metadata','attributes'):walk(v)
            elif isinstance(value,list):
                for v in value:walk(v)
        walk(stages.get('trace'))
        if states.get('chunking')!='done' or states.get('embedding')!='done':return False
        kb=actual.get('knowledge_base_id')
        if not kb:raise RuntimeError('missing_knowledge_base_id')
        hits=self.request('POST',f"/knowledge-bases/{kb}/hybrid-search",{'query_text':actual.get('title','文档')[:80],
             'knowledge_ids':[kid],'match_count':3,'vector_threshold':0,'keyword_threshold':0})
        return isinstance(hits,list) and any(h.get('knowledge_id')==kid for h in hits)
    def work_rows(self,limit,prepare_only=False,cpu=False,size_class=None):
        states="('pending','retry')" if prepare_only else "('pending','retry','validated')"
        ext="("+' or '.join("lower(path) like '%"+e+"'" for e in (('.doc','.docx','.ppt','.pptx') if cpu else ('.pdf',)+IMAGE_EXTENSIONS))+")"
        priority="case when path like '%公司资质%' or path like '%营业执照%' then 0 when lower(path) like '%.pdf' then 1 else 2 end"
        focus=self.cfg.get("priority_keywords",[])[:8]
        focus=[s for s in focus if isinstance(s,str) and 0<len(s)<=80]
        first=("case when "+" or ".join("instr(path,?)>0" for _ in focus)+" then 0 else 1 end,") if focus else ""
        from document_lanes import LARGE_BYTES
        size_filter=(' and size>='+str(LARGE_BYTES)) if size_class=='large' else (' and coalesce(size,0)<'+str(LARGE_BYTES)) if size_class=='small' else ''
        return self.db.execute(f"select * from sources where missing=0 and state in {states} and {ext}{size_filter} and coalesce(next_attempt,0)<=? order by {first}{priority},coalesce(attempts,0),size,path limit ?",(time.time(),*focus,limit)).fetchall()
    def prepare_parallel(self,limit,cpu=False,workers=1):
        if not 1<=workers<=(12 if cpu else 4):raise ValueError('unsafe_worker_count')
        from document_lanes import DocumentLanes
        if cpu:
            rows=self.work_rows(limit,prepare_only=True,cpu=True)
        else:
            # Query large files separately: a size-sorted LIMIT must not starve
            # them behind thousands of small PDFs. Fill unused quota by size class.
            large=self.work_rows(max(1,limit//4),prepare_only=True,size_class='large')
            small=self.work_rows(limit-len(large),prepare_only=True,size_class='small')
            if len(small)+len(large)<limit:
                large=self.work_rows(limit-len(small),prepare_only=True,size_class='large')
            rows=large+small
        jobs=DocumentLanes(rows)
        if jobs.empty():return
        stop=threading.Event();gpu_slot=threading.BoundedSemaphore(self.gpu_admission)
        render_pool=None if cpu else ProcessPoolExecutor(max_workers=8,mp_context=multiprocessing.get_context('spawn'))
        batch_started=time.monotonic()
        def run(lane):
            worker=Sync(self.config_path);worker.gpu_slot=gpu_slot;worker.stop_event=stop
            if render_pool:worker.render_pool=render_pool;worker.render_pool_shared=True
            try:
                while not stop.is_set():
                    try:path,is_large=jobs.take(lane)
                    except queue.Empty:return
                    worker.work(1,prepare_only=True,cpu=cpu,paths=[path],lane=lane)
                    if not cpu and time.monotonic()-batch_started<300:
                        row=worker.db.execute('select state,next_attempt from sources where path=?',(path,)).fetchone()
                        if row and row['state'] in ('pending','retry') and not row['next_attempt']:jobs.put(path,is_large)
            except BaseException:
                stop.set();raise
            finally:worker.close()
        # The parent retains worker.lock/cpu.lock until every child has reached
        # a checkpoint, so inventory merges and safe shutdown still wait for all.
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures=[pool.submit(run,lane) for lane in range(min(workers,jobs.qsize()))]
                for future in futures:future.result()
        finally:
            if render_pool:render_pool.shutdown(wait=True,cancel_futures=True)
    def work(self,limit,publish=False,prepare_only=False,cpu=False,paths=None,lane=None):
        rows=self.work_rows(limit,prepare_only,cpu) if paths is None else [self.db.execute('select * from sources where path=?',(p,)).fetchone() for p in paths]
        role='cpu' if cpu else 'parser'
        self.activity_file=f'activity-{role}'+('' if lane is None else f'-{lane}')+'.json'
        for row in rows:
            if row is None or row['missing'] or row['state'] not in ('pending','retry','validated') or (prepare_only and row['state']=='validated'):continue
            self.guard();revision=None
            self.activity={'active':True,'pid':os.getpid(),'role':role,'lane':lane,'stage':'download','path':row['path'],'page':0,'pages':None,'started_at':time.time()}
            atomic(self.state/self.activity_file,self.activity)
            held=contextlib.ExitStack()
            try:
                if sensitive(row['path']):raise Review('sensitive_path_admin_review')
                kb=self.target_kb(row['path'])
                self.request('GET',f'/knowledge-bases/{kb}')
                sha,md5,source=self.download(row);revision=self.revision_id(sha,kb)
                # Shared bytes may be discovered simultaneously through aliases
                # or different KB routes. Serialize checkpoint/output ownership.
                locks=self.state/'content-locks';locks.mkdir(exist_ok=True)
                lock=held.enter_context((locks/(hashlib.sha256(sha.encode()).hexdigest()+'.lock')).open('a'))
                self.stage('content_wait')
                while True:
                    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:self.guard();time.sleep(.2)
                self.guard()
                existing=self.db.execute('select * from revisions where id=?',(revision,)).fetchone()
                if existing and existing['state']=='retired':
                    candidate=self.db.execute("select * from revisions where sha=? and kb=? and state!='retired' order by created desc limit 1",(sha,kb)).fetchone()
                    revision=candidate['id'] if candidate else hashlib.sha256((revision+row['seen']).encode()).hexdigest()
                    existing=candidate
                if existing and existing['state']=='indexed':
                    self.db.execute("update sources set state='indexed',live_revision=? where path=?",(revision,row['path']));self.db.commit();continue
                if existing and existing['state']=='review':raise Review(existing['error'] or 'cached_quality_check_failed')
                if existing and existing['state']=='validated' and prepare_only:
                    self.db.execute("update sources set state='validated',attempts=0,next_attempt=0,error=null where path=?",(row['path'],));self.db.commit();continue
                self.db.execute('insert or ignore into revisions values(?,?,?,?,?,?,?)',(revision,sha,kb,'parsing',None,None,time.time()));self.db.commit()
                self.reuse_pages(sha,revision)
                self.stage('cpu_office' if cpu else 'cpu_render')
                extension=PurePosixPath(row['path']).suffix.lower()
                chunks,base=self.parse_office(source,revision,extension[1:]) if cpu else parse_image(self,source,revision) if extension in IMAGE_EXTENSIONS else self.parse(source,revision)
                self.db.execute("update revisions set state='validated' where id=?",(revision,));self.db.commit()
                if publish:self.publish(revision,sha,md5,row['path'],chunks,base)
                else:self.db.execute("update sources set state='validated',attempts=0,next_attempt=0,error=null where path=?",(row['path'],));self.db.commit()
                self.event('document_completed',revision,{'published':publish,'parts':len(chunks)})
            except PageYield:
                self.db.execute('update sources set next_attempt=? where path=?',(0,row['path']));self.db.commit()
                self.event('page_batch_yield',revision,{'page_budget':10})
            except Halt:raise
            except Exception as e:
                status='review' if isinstance(e,Review) else 'ambiguous' if isinstance(e,Ambiguous) else 'retry'
                # Never log response bodies, file contents, credentials or traceback locals.
                reason=str(e)[:160] if isinstance(e,(Review,Ambiguous)) else type(e).__name__+(':'+str(e.errno) if isinstance(e,OSError) else '')
                attempts=int(row['attempts'] or 0)+1
                if status=='retry' and attempts>=3:status='review'
                self.db.execute('update sources set state=?,error=?,attempts=?,next_attempt=? where path=?',(status,reason,attempts,time.time()+min(3600,60*2**attempts),row['path']))
                if revision:self.db.execute('update revisions set state=?,error=? where id=?',(status,reason,revision))
                self.db.commit();self.event('document_attention',revision or 'source',{'state':status,'reason':reason})
            finally:
                held.close()
                self.activity['active']=False;atomic(self.state/self.activity_file,self.activity)
    def publish_parallel(self,limit):
        def run(lane):
            worker=Sync(self.config_path)
            try:return worker.publish_ready(limit,lane)
            finally:
                worker.close()
        # Parent retains the publisher lock, so scan commits/retirement cannot
        # race either lane. Each thread owns its DB connection and HTTP session.
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(run,lane) for lane in range(2)]
            for job in jobs:job.result()
    def publish_ready(self,limit,lane=None):
        self.db.create_function('publish_lane',1,lambda sha:hashlib.sha256(str(sha).encode()).digest()[0]%2)
        clause='' if lane is None else ' and publish_lane(sha)='+str(int(lane))
        activity_file='activity-publisher.json' if lane is None else f'activity-publisher-{lane}.json'
        focus=[v for v in self.cfg.get('priority_keywords',[])[:8] if isinstance(v,str) and 0<len(v)<=80]
        priority=("case when "+" or ".join("instr(path,?)>0" for _ in focus)+" then 0 else 1 end,") if focus else ""
        rows=self.db.execute("select * from sources where missing=0 and state='validated' and coalesce(next_attempt,0)<=?"+clause+" order by "+priority+"last_hash limit ?",(time.time(),*focus,limit)).fetchall()
        for row in rows:
            self.guard()
            current=self.db.execute('select state from sources where path=?',(row['path'],)).fetchone()
            if not current or current['state']!='validated':continue
            revision=self.revision_id(row['sha'],self.target_kb(row['path']))
            activity={'active':True,'pid':os.getpid(),'role':'publisher' if lane is None else f'publisher-{lane}','path':row['path'],'started_at':time.time()}
            atomic(self.state/activity_file,activity)
            try:
                rev=self.db.execute("select * from revisions where sha=? and kb=? and state in ('validated','indexed','retry') order by created desc limit 1",(row['sha'],self.target_kb(row['path']))).fetchone()
                if not rev:raise Review('validated_revision_missing')
                revision=rev['id'];base=self.archive/'derived'/revision
                manifest=json.loads((base/'manifest.json').read_text())
                if manifest.get('errors'):raise Review('cached_quality_check_failed')
                pages=[]
                for n in range(1,manifest['pages']+1):
                    page=base/f'page-{n:06}'
                    if json.loads((page/'quality.json').read_text()).get('errors'):raise Review('cached_page_quality_failed')
                    pages.append((n,(page/'clean.md').read_text()))
                chunks=['## 原始图片识别文字（不代表 PDF 页码）\n\n'+pages[0][1]] if manifest.get('image') else split_markdown(pages,office=manifest.get('office',False))
                self.publish(revision,row['sha'],row['md5'],row['path'],chunks,base)
                self.event('publish_completed',revision,{'pages':len(pages)})
            except Halt:raise
            except Exception as e:
                attempts=int(row['attempts'] or 0)+1
                status='ambiguous' if isinstance(e,Ambiguous) else 'review' if isinstance(e,Review) or attempts>=3 else 'validated'
                reason=str(e)[:160] if isinstance(e,(Review,Ambiguous)) else type(e).__name__
                self.db.execute('update sources set state=?,error=?,attempts=?,next_attempt=? where path=?',(status,reason,attempts,time.time()+min(3600,60*2**attempts),row['path']))
                self.db.commit();self.event('publish_attention',revision,{'state':status,'reason':reason})
            finally:
                activity['active']=False;atomic(self.state/activity_file,activity)
    def reconcile(self):
        # Reconcile ambiguous POST by deterministic title marker, across all pages.
        found={}
        pending=self.db.execute("select parts.*,revisions.kb,revisions.sha from parts join revisions on revisions.id=parts.revision where parts.state in ('creating','ambiguous') and revisions.state!='retired'").fetchall()
        for kb in set(r['kb'] for r in pending):
            page=1
            while True:
                value=self.request('GET',f'/knowledge-bases/{kb}/knowledge?page={page}&page_size=100')
                items=value if isinstance(value,list) else value.get('data',value.get('items',[]))
                if not isinstance(items,list):raise RuntimeError('unexpected_list_shape')
                for item in items:
                    match=re.search(r'\[(nas-sync-[a-f0-9]+-\d+)\]',item.get('title',''))
                    if match:found.setdefault((kb,match.group(1)),[]).append(item['id'])
                if len(items)<100:break
                page+=1
        for row in pending:
            ids=found.get((row['kb'],row['marker']),[])
            if len(ids)==1:
                self.db.execute("update parts set knowledge_id=?,state='created' where marker=?",(ids[0],row['marker']))
                for source in self.db.execute('select path from sources where sha=?',(row['sha'],)).fetchall():
                    if self.target_kb(source['path'])==row['kb']:
                        self.db.execute("update sources set state='retry' where path=?",(source['path'],))
            # Zero/multiple matches require explicit investigation, never blind POST retry.
        self.db.commit()
    def status(self):
        return {'sources':dict(self.db.execute('select state,count(*) from sources group by state').fetchall()),
                'parts':dict(self.db.execute('select state,count(*) from parts group by state').fetchall()),
                'paused':(self.state/'PAUSED').exists(),
                'latest_scan':dict(self.db.execute('select * from scans order by started desc limit 1').fetchone() or {})}
    def retire(self):
        latest=self.db.execute('select complete from scans order by started desc limit 1').fetchone()
        if not latest or not latest[0]:raise Halt('retirement_requires_complete_scan')
        for rev in self.db.execute("select * from revisions where state in ('indexed','retiring')").fetchall():
            refs=self.db.execute('select * from sources where live_revision=?',(rev['id'],)).fetchall()
            if refs and not self.cfg.get('sync_deletions',False):continue
            if any(r['missing']<2 or not r['missing_since'] or time.time()-r['missing_since']<72*3600 for r in refs):continue
            # No references means all paths have successfully switched to a newer revision.
            self.db.execute("update revisions set state='retiring' where id=?",(rev['id'],));self.db.commit()
            for part in self.db.execute('select * from parts where revision=?',(rev['id'],)).fetchall():
                if part['state']=='deleted':continue
                kid=part['knowledge_id']
                if not kid:raise Review('retirement_missing_knowledge_id')
                try:actual=self.request('GET',f'/knowledge/{kid}')
                except RuntimeError as e:
                    if str(e)!='weknora_http_404':raise
                    actual=None
                if actual:
                    meta=actual.get('custom_metadata') or {}
                    if meta.get('sync_owner')!=self.cfg['owner'] or meta.get('revision')!=rev['id']:raise Review('retirement_ownership_mismatch')
                    self.request('DELETE',f'/knowledge/{kid}')
                    # Deletion is asynchronous: next cycle verifies disappearance.
                    self.db.execute("update parts set state='deleting' where revision=? and part=?",(rev['id'],part['part']))
                else:self.db.execute("update parts set state='deleted' where revision=? and part=?",(rev['id'],part['part']))
                self.db.commit()
            pending=self.db.execute("select count(*) from parts where revision=? and state!='deleted'",(rev['id'],)).fetchone()[0]
            if not pending:
                self.db.execute("update revisions set state='retired' where id=?",(rev['id'],))
                self.db.execute("update sources set state='retired' where live_revision=? and missing>=2",(rev['id'],));self.db.commit()

if os.environ.get('PIPELINE_NATIVE_ROUTING')=='1':
    from pipeline_runtime import install
    install(Sync)

def main():
    os.umask(0o077);p=argparse.ArgumentParser();p.add_argument('--config',default='/etc/weknora-nas-sync/config.json')
    p.add_argument('command',choices=['scan','scan-due','work','prepare','prepare-cpu','publish-ready','catalogue','cycle','status','pause','resume','reconcile','retire']);p.add_argument('--limit',type=int,default=3);p.add_argument('--workers',type=int,default=1);p.add_argument('--publish',action='store_true');args=p.parse_args()
    sync=Sync(args.config)
    if args.command in ('pause','resume'):
        marker=sync.state/'PAUSED'
        if args.command=='pause':marker.touch()
        else:marker.unlink(missing_ok=True)
        print(json.dumps(sync.status()));return
    if args.command=='status':print(json.dumps(sync.status()));return
    with (sync.state/('catalogue.lock' if args.command=='catalogue' else 'publisher.lock' if args.command=='publish-ready' else 'cpu.lock' if args.command=='prepare-cpu' else 'scan.lock' if args.command in ('scan','scan-due','cycle') else 'worker.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.command=='catalogue':
            print(json.dumps({'catalogue_batches':publish_catalog(sync,args.limit)}));return
        if args.command=='scan-due':
            latest=sync.db.execute('select finished from scans where complete=1 order by finished desc limit 1').fetchone()
            if not latest or time.time()-latest[0]>=72*3600 or (sync.state/'SCAN_REQUESTED').exists():
                sync.scan();(sync.state/'SCAN_REQUESTED').unlink(missing_ok=True)
        if args.command in ('scan','cycle'):sync.scan()
        if args.command=='work':sync.work(args.limit,args.publish)
        if args.command=='cycle':
            with (sync.state/'worker.lock').open('a') as work_lock:
                fcntl.flock(work_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                sync.work(args.limit,args.publish)
        if args.command=='prepare':sync.prepare_parallel(args.limit,workers=args.workers)
        if args.command=='prepare-cpu':sync.prepare_parallel(args.limit,cpu=True,workers=args.workers)
        if args.command=='publish-ready':sync.publish_parallel(args.limit);sync.retire()
        if args.command=='reconcile':sync.reconcile()
        if args.command=='retire' or (args.command in ('work','cycle') and args.publish):sync.retire()
    print(json.dumps(sync.status()))

if __name__=='__main__':
    sys.modules['sync']=sys.modules[__name__]
    try:main()
    except Halt as e:print(json.dumps({'stopped':str(e)}));sys.exit(0)
    except BlockingIOError:print(json.dumps({'stopped':'another_sync_task_is_running'}));sys.exit(75)
