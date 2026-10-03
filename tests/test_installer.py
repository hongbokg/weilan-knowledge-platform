import importlib.util,json,tempfile,unittest,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_installer',ROOT/'installer/install.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class InstallerTests(unittest.TestCase):
 def config(self):
  c=json.loads((ROOT/'config/config.example.json').read_text(encoding='utf-8'))
  c.update(nas_url='https://nas.local',nas_user='reader',nas_password='test-only-password',api_key='test-only-key',archive_uuid='test-only-uuid')
  return c
 def test_plan_never_starts_or_downloads_models(self):
  p=mod.make_plan();self.assertFalse(p['automatic_start']);self.assertIn('model weights',p['not_installed'])
 def test_valid_config(self):self.assertTrue(mod.validate_config(self.config()))
 def test_examples_cannot_activate(self):
  with self.assertRaises(ValueError):mod.validate_config(json.loads((ROOT/'config/config.example.json').read_text(encoding='utf-8')))
 def test_credentials_require_tls(self):
  for change in [{'nas_url':'http://nas.local'},{'nas_ca':False},{'nas_url':'https://user:password@nas.local'}]:
   c=self.config();c.update(change)
   with self.assertRaises(ValueError):mod.validate_config(c)
 def test_api_stays_loopback(self):
  c=self.config();c['api_url']='http://remote.local/api/v1'
  with self.assertRaises(ValueError):mod.validate_config(c)
 def test_deletions_disabled(self):
  c=self.config();c['sync_deletions']=True
  with self.assertRaises(ValueError):mod.validate_config(c)
 def test_stage_isolated_and_refuses_overwrite(self):
  with tempfile.TemporaryDirectory() as tmp:
   result=mod.install(tmp,dependencies=False)
   self.assertFalse(result['started'])
   self.assertTrue((Path(tmp)/'opt/weknora-nas-sync/sync.py').is_file())
   self.assertFalse((Path(tmp)/'etc/weknora-nas-sync/config.json').exists())
   for unit in mod.units('weilan'):
    self.assertTrue((Path(tmp)/'etc/systemd/system'/unit).is_file())
   with self.assertRaises(ValueError):mod.install(tmp,dependencies=False)
 def test_checksum_detects_tampering(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'file').write_text('wrong')
   (p/'MANIFEST.json').write_text(json.dumps({'files':{'file':'not-the-digest'}}))
   with self.assertRaises(ValueError):mod.verify_bundle(p)
 def test_unlisted_source_cannot_be_installed(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'MANIFEST.json').write_text(json.dumps({'files':{}}))
   (p/'extra.py').write_text('unlisted')
   with self.assertRaisesRegex(ValueError,'unlisted'):mod.verify_bundle(p)

if __name__=='__main__':unittest.main()
