import os,shutil
from pathlib import Path
import subprocess,json,xml.etree.ElementTree as ET
root=Path(__file__).resolve().parents[1];src=root/'src/com/weilan/knowledge';build=root/'build/firewall-tests';build.mkdir(parents=True,exist_ok=True)
jdk=Path(os.environ['JAVA_HOME'])/'bin' if os.environ.get('JAVA_HOME') else Path(shutil.which('java') or 'java').resolve().parent
subprocess.run([str(jdk/('javac'+('.exe' if os.name=='nt' else ''))),'-encoding','UTF-8','--release','8','-d',str(build),str(src/'BlockPolicy.java'),str(root/'tests/FirewallPolicyTest.java')],check=True)
r=subprocess.run([str(jdk/('java'+('.exe' if os.name=='nt' else ''))),'-cp',str(build),'com.weilan.knowledge.FirewallPolicyTest'],check=True,capture_output=True,text=True)
ns='{http://schemas.android.com/apk/res/android}';manifest=ET.parse(root/'AndroidManifest.xml').getroot();svc=next(s for s in manifest.findall('application/service') if s.get(ns+'name')=='.FirewallService')
assert svc.get(ns+'permission')=='android.permission.BIND_VPN_SERVICE'
assert svc.get(ns+'foregroundServiceType')=='specialUse'
assert next(m for m in svc.findall('meta-data') if m.get(ns+'name')=='android.net.VpnService.SUPPORTS_ALWAYS_ON').get(ns+'value')=='false'
permissions={p.get(ns+'name') for p in manifest.findall('uses-permission')}
assert not permissions.intersection({'android.permission.RECORD_AUDIO','android.permission.READ_CALL_LOG','android.permission.READ_PHONE_STATE','android.permission.READ_CONTACTS','android.permission.ACCESS_FINE_LOCATION'})
source=(src/'FirewallService.java').read_text(encoding='utf-8')
for required in ['addRoute("0.0.0.0",0)','addRoute("::",0)','addAllowedApplication(pkg)','if(installed==0)','setBlocking(true)','START_NOT_STICKY']:assert required in source
for forbidden in ['allowBypass','addDisallowedApplication','new Socket','HttpsURLConnection','FileOutputStream','Log.']:assert forbidden not in source
print(r.stdout.strip());print('PASS manifest and firewall implementation boundary review')
output=build/'validation.json';output.write_text(json.dumps({'policy_tests':r.stdout.strip(),'manifest_checks':True,'ipv4_ipv6_default_routes':True,'empty_installed_list_refused':True,'no_gateway_or_packet_logging_in_service':True,'no_microphone_call_log_contacts_location_permissions':True,'android_device_tested':False,'limits':'JVM policy tests and source/manifest checks do not prove Android VPN runtime blocking.'},ensure_ascii=False,indent=2),encoding='utf-8')
