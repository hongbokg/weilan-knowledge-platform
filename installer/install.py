"""Install an ingestion addon without overwriting an existing deployment.

Dependencies (WeKnora/MinerU/Office API) are managed separately. No disk format,
NAS write, model download, cloud enablement, firewall or production migration.
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.1.0'
LIVE='/opt/weknora-nas-sync'
CONFIG='/etc/weknora-nas-sync/config.json'
STATE='/data/weknora/nas-sync'
ARCHIVE='/data/archive/nas-sync'
RAM='/run/weknora-work/nas-sync'
COMMANDS={'scan':('scan-due',1,1,30000),'worker':('prepare',4,4,5400),
          'cpu':('prepare-cpu',8,8,5400),'publisher':('publish-ready',8,1,5400)}

def mapped(path, staging=None):
 return Path(staging)/path.lstrip('/') if staging else Path(path)

def make_plan():
 return {'version':VERSION,'install_path':LIVE,'config':CONFIG,
         'services':[f'weknora-nas-sync-{name}.timer' for name in COMMANDS],
         'automatic_start':False,'nas_access':'GET/HEAD/PROPFIND only',
         'prerequisites':['Linux + Python 3.12','WeKnora manual API','MinerU jobs API',
                          'archive filesystem with UUID','noswap tmpfs',
                          'optional AnyDoc-compatible CPU Office API'],
         'not_installed':['third-party full applications','model weights','GPU drivers',
                          'production data','cloud credentials','live system settings']}

def verify_bundle(root=ROOT):
 manifest=root/'MANIFEST.json'
 if not manifest.exists():raise ValueError('missing_release_manifest')
 expected=json.loads(manifest.read_text(encoding='utf-8'))['files']
 ignored={'.git','__pycache__','build','node_modules','.pytest_cache'}
 present={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and not any(x in ignored for x in p.relative_to(root).parts) and p.name not in {'MANIFEST.json','SHA256SUMS'}}
 if present!=set(expected):raise ValueError('unlisted_or_missing_package_file')
 for name,digest in expected.items():
  original=root/name
  path=original.resolve()
  if not path.is_relative_to(root.resolve()) or original.is_symlink() or not path.is_file():
   raise ValueError('unsafe_or_missing_package_file')
  if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('package_checksum_failed')

def units(user):
 base=f'''[Unit]
Description=Weilan NAS ingestion: {{name}}
After=network-online.target
Wants=network-online.target
RequiresMountsFor=/data/archive /run/weknora-work
ConditionPathExists={CONFIG}
[Service]
Type=oneshot
User={user}
Group={user}
UMask=0077
WorkingDirectory={LIVE}
Environment=PIPELINE_NATIVE_ROUTING=1
Environment=OMP_NUM_THREADS=2
Environment=OPENBLAS_NUM_THREADS=1
ExecStart={LIVE}/.venv/bin/python {LIVE}/sync.py --config {CONFIG} {{command}} --limit {{limit}} --workers {{workers}}
TimeoutStartSec={{timeout}}
TimeoutStopSec=60
SuccessExitStatus=75
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={STATE} /data/archive {RAM}
MemoryMax=12G
MemorySwapMax=0
TasksMax=256
'''
 result={}
 for name,(command,limit,workers,timeout) in COMMANDS.items():
  unit='weknora-nas-sync-'+name
  result[unit+'.service']=base.format(name=name,command=command,limit=limit,workers=workers,timeout=timeout)
  # scan-due implements the durable 72h interval based on the last complete scan.
  interval='30min' if name=='scan' else '30s'
  result[unit+'.timer']=f'''[Unit]
Description=Weilan {name} scheduling
[Timer]
OnBootSec=90s
OnUnitInactiveSec={interval}
Unit={unit}.service
[Install]
WantedBy=timers.target
'''
 return result

def install(staging=None,user='weilan',dependencies=True):
 verify_bundle()
 if not staging and (sys.platform!='linux' or os.geteuid()!=0):raise ValueError('root_linux_required')
 if not staging and sys.version_info[:2]!=(3,12):raise ValueError('python_3_12_required')
 live=mapped(LIVE,staging);unitdir=mapped('/etc/systemd/system',staging)
 # Refuse to silently replace existing production sources, configuration or units.
 collisions=[live,mapped(CONFIG,staging)]+[unitdir/name for name in units(user)]
 if any(p.exists() or p.is_symlink() for p in collisions):raise ValueError('existing_installation_requires_reviewed_upgrade')
 if not staging:
  import pwd
  try:account=pwd.getpwnam(user)
  except KeyError:
   subprocess.run(['useradd','--system','--user-group','--create-home',user],check=True)
   account=pwd.getpwnam(user)
 else:account=None
 live.parent.mkdir(parents=True,exist_ok=True)
 live.mkdir(mode=0o755)
 for p in (ROOT/'server/pipeline/src').glob('*.py'):shutil.copy2(p,live/p.name)
 shutil.copy2(ROOT/'server/pipeline/requirements.lock',live/'requirements.lock')
 if dependencies and not staging:
  subprocess.run([sys.executable,'-m','venv',str(live/'.venv')],check=True)
  subprocess.run([str(live/'.venv/bin/python'),'-m','pip','install','-r',str(live/'requirements.lock')],check=True)
 for directory in [STATE,ARCHIVE,RAM,'/data/archive/objects/pilot-private','/data/archive/derived/pilot-private']:
  target=mapped(directory,staging);target.mkdir(parents=True,exist_ok=True,mode=0o700)
  if account:os.chown(target,account.pw_uid,account.pw_gid)
 cfgdir=mapped(CONFIG,staging).parent;cfgdir.mkdir(parents=True,exist_ok=True,mode=0o700)
 cfg=ROOT/'config/config.example.json'
 target=cfgdir/'config.example.json';shutil.copy2(cfg,target);target.chmod(0o600)
 if account:os.chown(cfgdir,account.pw_uid,account.pw_gid);os.chown(target,account.pw_uid,account.pw_gid)
 unitdir.mkdir(parents=True,exist_ok=True)
 for name,text in units(user).items():(unitdir/name).write_text(text)
 (live/'installation.json').write_text(json.dumps({'release':VERSION,'user':user,'started':False},indent=2)+'\n')
 if not staging:subprocess.run(['systemctl','daemon-reload'],check=True)
 return {'installed':True,'started':False,'version':VERSION,'configure':CONFIG}

def validate_config(c):
 from urllib.parse import urlsplit
 required=['nas_url','nas_user','nas_password','api_url','api_key','kb_id','owner','archive_uuid','parser_revision','mineru_url','mineru_tier','mineru_version','roots']
 if any(not c.get(k) for k in required):raise ValueError('required_configuration_missing')
 for k in required:
  if isinstance(c[k],str) and ('CHANGE_ME' in c[k] or '.example.com' in c[k] or '.invalid' in c[k]):raise ValueError('example_configuration_not_ready')
 for key,value in [('state_dir',STATE),('archive_dir',ARCHIVE),('ram_dir',RAM)]:
  if c.get(key)!=value:raise ValueError('installer_requires_standard_data_paths')
 for key in ['api_url','mineru_url']:
  u=urlsplit(c[key])
  if u.scheme not in ('http','https') or u.hostname not in ('127.0.0.1','localhost','::1') or u.username or u.password:raise ValueError('business_services_must_be_loopback')
 nas=urlsplit(c['nas_url'])
 if nas.scheme!='https' or nas.username or nas.password or c.get('nas_ca',True) is False:raise ValueError('nas_tls_verification_required')
 if not isinstance(c['roots'],list) or not c['roots'] or any(not isinstance(x,str) or not x.startswith('/') or '..' in x.split('/') for x in c['roots']):raise ValueError('nas_roots_invalid')
 for r in c.get('routes',[]):
  if not r.get('prefix','').startswith('/') or '..' in r['prefix'].split('/'):raise ValueError('route_prefix_invalid')
  uuid.UUID(r['kb_id'])
 uuid.UUID(c['kb_id'])
 if c.get('sync_deletions'):raise ValueError('initial_installation_does_not_enable_deletions')
 return True

def preflight(config=CONFIG):
 import requests
 p=Path(config);st=p.stat()
 if st.st_mode & 0o077:raise ValueError('config_requires_0600_permissions')
 c=json.loads(p.read_text());validate_config(c)
 live=Path(LIVE)
 if not (live/'.venv/bin/python').is_file():raise ValueError('python_runtime_missing')
 probe="from sync import Sync; s=Sync('/etc/weknora-nas-sync/config.json'); s.guard(); s.close()"
 # Guard verifies archive UUID, writable archive, free space and bounded noswap tmpfs.
 user=json.loads((live/'installation.json').read_text())['user']
 subprocess.run(['runuser','-u',user,'--',str(live/'.venv/bin/python'),'-c',probe],check=True,cwd=live,capture_output=True)
 with requests.Session() as s:
  s.trust_env=False
  for kb in sorted(set([c['kb_id']]+[r['kb_id'] for r in c.get('routes',[])])):
   response=s.get(c['api_url'].rstrip('/')+'/knowledge-bases/'+kb,headers={'X-API-Key':c['api_key']},timeout=(3,10),allow_redirects=False)
   if response.status_code!=200:raise ValueError('knowledge_base_access_check_failed')
  response=s.get(c['mineru_url'].rstrip('/')+'/openapi.json',timeout=(3,10),allow_redirects=False)
  if response.status_code!=200 or '/v1/parse/jobs' not in response.json().get('paths',{}):raise ValueError('mineru_jobs_contract_missing')
  for root in c['roots']:
   response=s.request('PROPFIND',c['nas_url'].rstrip('/')+requests.utils.quote(root,safe='/'),headers={'Depth':'0'},auth=(c['nas_user'],c['nas_password']),verify=c.get('nas_ca',True),timeout=(3,10),allow_redirects=False)
   if response.status_code not in (200,207):raise ValueError('nas_read_access_check_failed')
 return {'preflight':'passed','nas_writes':0,'model_cloud_calls':0,'deletion_sync':False}

def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=['plan','install','preflight','activate'])
 p.add_argument('--staging-root');p.add_argument('--user',default='weilan');args=p.parse_args()
 if not __import__('re').fullmatch('[a-z_][a-z0-9_-]{0,30}',args.user):raise ValueError('invalid_service_user')
 if args.command=='plan':result=make_plan()
 elif args.command=='install':result=install(args.staging_root,args.user)
 elif args.staging_root:raise ValueError('staging_only_supports_install')
 else:
  if os.geteuid()!=0:raise ValueError('root_required')
  result=preflight()
  if args.command=='activate':
   subprocess.run(['systemctl','enable','--now',*[f'weknora-nas-sync-{x}.timer' for x in COMMANDS]],check=True)
   result['activated']=True
 print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
 try:main()
 except Exception as error:
  # Never print provider response, request headers, NAS path or password.
  code=str(error) if isinstance(error,ValueError) else type(error).__name__
  print(json.dumps({'error':code}),file=sys.stderr);sys.exit(1)
