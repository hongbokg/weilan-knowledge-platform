import unittest
import tempfile,os,json
from unittest.mock import patch,MagicMock
from pathlib import Path
from pool_guard import unhealthy,atomic,capture

class GuardTests(unittest.TestCase):
    def test_idle_gpu_is_healthy(self):
        self.assertIsNone(unhealthy({'healthy':True,'oom_kill':0,'memory_mib':8100}))
    def test_failures_oom_and_headroom_are_distinct(self):
        for row,reason in [({'healthy':False,'oom_kill':0,'memory_mib':8100},'pool_unavailable'),({'healthy':True,'oom_kill':1,'memory_mib':8100},'pool_oom'),({'healthy':True,'oom_kill':0,'memory_mib':12900},'pool_vram_headroom')]:
            self.assertEqual(unhealthy(row),reason)
    def test_atomic_profile_update_preserves_service_read_permissions(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'profile.json';p.write_text('{}');p.chmod(0o640)
            mask=os.umask(0o077)
            try:atomic(p,{'gpu_slots':1})
            finally:os.umask(mask)
            self.assertEqual(p.stat().st_mode & 0o777,0o640)
            self.assertEqual(json.loads(p.read_text())['gpu_slots'],1)
    def test_health_never_waits_on_model_inference_lock(self):
        def output(args,**kwargs):
            return '' if args[0]=='systemctl' else '8500\n'
        response=MagicMock();response.__enter__.return_value.status=200
        with patch('pool_guard.subprocess.run',return_value=type('Result',(),{'returncode':0})()),patch('pool_guard.subprocess.check_output',side_effect=output),patch('pool_guard.socket.create_connection') as connect,patch('pool_guard.urllib.request.urlopen',return_value=response) as http:
            self.assertTrue(capture()['healthy'])
        self.assertEqual(connect.call_count,2)
        self.assertEqual(http.call_args.args[0],'http://127.0.0.1:16594/health')

if __name__=='__main__':unittest.main()
