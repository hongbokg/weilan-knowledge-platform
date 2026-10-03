"""Apply the tested source overlay only to a clean checkout of the pinned base."""
import argparse,json,subprocess,shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE='9114e4e4f905be71976a77c684d3f92731e6f9a6'

def apply(target,write=False):
 target=Path(target).resolve();overlay=ROOT/'server/weknora-overlay'
 head=subprocess.check_output(['git','-C',str(target),'rev-parse','HEAD'],text=True).strip()
 if head!=BASE:raise ValueError('upstream_commit_does_not_match')
 names=[str(p.relative_to(overlay)).replace('\\','/') for p in overlay.rglob('*') if p.is_file() and p.suffix in ('.go','.vue','.ts')]
 dirty=subprocess.check_output(['git','-C',str(target),'status','--porcelain','--',*names],text=True)
 if dirty.strip():raise ValueError('overlay_paths_already_modified')
 subprocess.run(['git','-C',str(target),'apply','--check',str(overlay/'upstream-changes.patch')],check=True)
 if write:
  # Copy full reviewed files because new untracked files are not in git diff.
  for name in names:
   dst=(target/name).resolve()
   if not dst.is_relative_to(target):raise ValueError('unsafe_overlay_path')
   dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(overlay/name,dst)
 return {'base':BASE,'files':len(names),'applied':write,'automatic_build':False,'service_restart':False}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('checkout');p.add_argument('--apply',action='store_true');a=p.parse_args()
 print(json.dumps(apply(a.checkout,a.apply)))
