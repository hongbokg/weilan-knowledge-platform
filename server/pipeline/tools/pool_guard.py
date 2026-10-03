"""Server-side pool watch; conservative checkpoint rollback, never restore a DB."""
import json
import os
import subprocess
import socket
import time
import urllib.request
from pathlib import Path

STATE=Path('/data/weknora/nas-sync')
PROFILE=STATE/'pipeline-runtime.json'
STATUS=STATE/'gpu-pool-watch.json'
UNITS=('weknora-vlm.service','weknora-vlm-secondary.service','weknora-vlm-pool.service','mineru-api.service')

def atomic(path,data):
    previous=path.stat() if path.exists() else None
    tmp=path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(data,f);f.flush();os.fsync(f.fileno())
    if previous:
        os.chmod(tmp,previous.st_mode & 0o777)
        os.chown(tmp,previous.st_uid,previous.st_gid)
    tmp.replace(path)

def unhealthy(snapshot):
    if not snapshot['healthy']:return 'pool_unavailable'
    if snapshot['oom_kill']:return 'pool_oom'
    if snapshot['memory_mib']>12800:return 'pool_vram_headroom'
    return None

def capture():
    healthy=True;oom=0
    for unit in UNITS:
        if subprocess.run(['systemctl','is-active','--quiet',unit]).returncode:healthy=False
        group=subprocess.check_output(['systemctl','show',unit,'-p','ControlGroup','--value'],text=True).strip()
        event=Path('/sys/fs/cgroup'+group+'/memory.events')
        if group and event.exists():oom+=int(dict(line.split() for line in event.read_text().splitlines()).get('oom_kill',0))
    try:
        # llama-cpp-python's /v1/models shares the inference context lock.
        # A busy decoder is not an unavailable model. Probe socket listeners
        # and the relay's lock-independent health endpoint instead.
        for port in (16590,16591):
            with socket.create_connection(('127.0.0.1',port),timeout=3):pass
        with urllib.request.urlopen('http://127.0.0.1:16594/health',timeout=3) as r:
            healthy=healthy and r.status==200
    except Exception:healthy=False
    memory=int(subprocess.check_output(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True,timeout=5).strip())
    return {'healthy':healthy,'oom_kill':oom,'memory_mib':memory}

def rollback(reason):
    marker=STATE/'OCR_PAUSED';owner='gpu-pool-watch'
    if (STATE/'PAUSED').exists() or marker.exists():return False
    # Exclusive creation ensures this watcher cannot overwrite another operator.
    with marker.open('x') as f:f.write(owner)
    active=subprocess.run(['systemctl','is-active','--quiet','weknora-nas-sync-worker.timer']).returncode==0
    try:
        subprocess.run(['systemctl','stop','weknora-nas-sync-worker.timer'],check=True)
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            pid=subprocess.check_output(['systemctl','show','weknora-nas-sync-worker.service','-p','MainPID','--value'],text=True).strip()
            if pid=='0':break
            time.sleep(2)
        else:return False
        dropin=Path('/etc/systemd/system/mineru-api.service.d/80-python-pool.conf')
        if dropin.exists():dropin.rename(dropin.with_suffix('.disabled'))
        runtime=json.loads(PROFILE.read_text())
        runtime.update(gpu_slots=1,backend='persistent-python-q8',rolled_back_at=time.time(),rollback_reason=reason)
        atomic(PROFILE,runtime)
        subprocess.run(['systemctl','daemon-reload'],check=True)
        subprocess.run(['systemctl','restart','mineru-api.service'],check=True)
        subprocess.run(['systemctl','stop','weknora-vlm-pool.service','weknora-vlm-secondary.service'],check=True)
        return True
    finally:
        if marker.exists() and marker.read_text()==owner:
            marker.unlink()
            if active:subprocess.run(['systemctl','start','weknora-nas-sync-worker.timer'],check=True)

def main():
    os.umask(0o077)
    if not PROFILE.exists() or json.loads(PROFILE.read_text()).get('gpu_slots')!=2:return
    previous=json.loads(STATUS.read_text()) if STATUS.exists() else {}
    snapshot=capture();reason=unhealthy(snapshot)
    bad=previous.get('bad',0)+1 if reason else 0
    row={'at':time.time(),'snapshot':snapshot,'bad':bad,'reason':reason,'rolled_back':False}
    if bad>=3:row['rolled_back']=rollback(reason)
    atomic(STATUS,row)

if __name__=='__main__':main()
