"""Build a reviewed source release with manifests; exclude all runtime data."""
import argparse,hashlib,json,re,tarfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
IGNORE={'.git','__pycache__','build','node_modules','.pytest_cache'}
META={'MANIFEST.json','SHA256SUMS'}
ALLOWED={'.py','.java','.go','.vue','.ts','.md','.json','.xml','.png','.webp','.sh','.lock','.patch','.service','.yml','.yaml','.txt','.mod','.sum',''}

def files(root=ROOT):
 result=[]
 for p in root.rglob('*'):
  if any(x in IGNORE for x in p.relative_to(root).parts):continue
  if p.is_symlink():raise ValueError('symlink_in_release')
  if p.is_file():
   if p.suffix not in ALLOWED:raise ValueError('unreviewed_extension:'+p.name)
   result.append(p)
 return sorted(result,key=lambda x:x.relative_to(root).as_posix())

def scan(root=ROOT):
 findings=[]
 patterns={'api_key':r'\bsk-[A-Za-z0-9_-]{16,}', 'private_key':r'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----',
           'credential_url':r'https?://[^\s/@\x22\x27]+:[^\s/@\x22\x27]+@',
           'provider_token':r'\b(?:ghp_|github_pat_|glpat-)[A-Za-z0-9_]{15,}'}
 for p in files(root):
  if p.suffix in {'.png','.webp'}:continue
  s=p.read_text(encoding='utf-8')
  for rule,pat in patterns.items():
   for m in re.finditer(pat,s):
    fixtures={'https://'+'user:password@','https://'+'user:secret@'}
    if rule=='credential_url' and p.name in {'NativeContractTest.java','test_installer.py'} and m.group() in fixtures:continue
    findings.append({'file':p.relative_to(root).as_posix(),'line':s[:m.start()].count('\n')+1,'rule':rule})
 if findings:raise ValueError(json.dumps(findings,ensure_ascii=False))
 return {'files_scanned':len(files(root)),'secret_findings':0,'runtime_data_included':False}

def manifest(root=ROOT):
 result=scan(root)
 hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files(root) if p.name not in META}
 (root/'MANIFEST.json').write_text(json.dumps({'version':'0.1.0','algorithm':'sha256','files':hashes},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 allhashes={**hashes,'MANIFEST.json':hashlib.sha256((root/'MANIFEST.json').read_bytes()).hexdigest()}
 (root/'SHA256SUMS').write_text(''.join(f'{digest}  {name}\n' for name,digest in sorted(allhashes.items())),encoding='utf-8')
 return result

def build(destination):
 result=manifest();dst=Path(destination).resolve();dst.mkdir(parents=True,exist_ok=True)
 if dst.is_relative_to(ROOT):raise ValueError('archives_must_be_outside_source_tree')
 name='weilan-knowledge-platform-0.1.0'
 with tarfile.open(dst/(name+'.tar.gz'),'w:gz') as tar:
  for p in files():
   info=tar.gettarinfo(str(p),arcname=name+'/'+p.relative_to(ROOT).as_posix())
   info.mode=0o755 if p.name=='install.sh' else 0o644;info.uid=info.gid=0;info.uname=info.gname=''
   with p.open('rb') as stream:tar.addfile(info,stream)
 with zipfile.ZipFile(dst/(name+'.zip'),'w',zipfile.ZIP_DEFLATED) as z:
  for p in files():z.write(p,name+'/'+p.relative_to(ROOT).as_posix())
 archives=[dst/(name+ext) for ext in ['.tar.gz','.zip']]
 (dst/'SHA256SUMS.txt').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in archives),encoding='utf-8')
 return {**result,'archives':[str(p) for p in archives]}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('command',choices=['scan','manifest','build']);p.add_argument('--output');a=p.parse_args()
 print(json.dumps(build(a.output) if a.command=='build' else manifest() if a.command=='manifest' else scan(),ensure_ascii=False))
