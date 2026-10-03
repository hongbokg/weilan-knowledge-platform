import json,sqlite3,time
from pathlib import Path
ROOT=Path('/data/archive/knowledge-organization')
SCHEMA='''
create table if not exists groups(id text primary key,company text,project text,kind text,kb text,prefixes text,count integer,state text,updated real);
create table if not exists files(path text primary key,kb text,md5 text,modified text,size integer,state text,error text,rows integer default 0,knowledge_id text,updated real);
create table if not exists evidence(id text primary key,path text,kb text,md5 text,sheet text,row integer,item text,brand text,model text,model_key text,category text,spec text,parameters text,price text,tax text,unit text,currency text,modified text,company text,project text,raw text);
create index if not exists evidence_model on evidence(model_key);
create index if not exists evidence_scope on evidence(kb,path);
create table if not exists tasks(id integer primary key,at real,kind text,status text,detail text);
'''
def connect(readonly=False):
 p=ROOT/'products.db'
 if readonly:db=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True,timeout=10)
 else:
  ROOT.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(p,timeout=30);db.execute('pragma journal_mode=WAL');db.execute('pragma synchronous=FULL');db.executescript(SCHEMA)
 if not readonly:
  import os,pwd
  if os.geteuid()==0:
   user=pwd.getpwnam('weilan')
   for file in [ROOT,p,Path(str(p)+'-wal'),Path(str(p)+'-shm')]:
    if file.exists():os.chown(file,user.pw_uid,user.pw_gid)
 db.row_factory=sqlite3.Row;return db
def task(db,kind,status,detail):
 db.execute('insert into tasks(at,kind,status,detail) values(?,?,?,?)',(time.time(),kind,status,json.dumps(detail,ensure_ascii=False)));db.commit()
