"""Build with the user's Android SDK/JDK; never bundles signing credentials."""
import argparse,os,subprocess,shutil,zipfile
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--sdk',default=os.environ.get('ANDROID_SDK_ROOT'));p.add_argument('--platform',default='android-35');p.add_argument('--build-tools',default='35.0.0');p.add_argument('--keystore');p.add_argument('--alias',default='weilan');a=p.parse_args()
 if not a.sdk:raise ValueError('ANDROID_SDK_ROOT_required')
 root=Path(__file__).resolve().parent;sdk=Path(a.sdk);tools=sdk/'build-tools'/a.build_tools;android=sdk/'platforms'/a.platform/'android.jar'
 build=root/'build';build.mkdir(exist_ok=True);classes=build/'classes';classes.mkdir(exist_ok=True)
 # No broad recursive deletion. Clean only generated class files inside build.
 for f in classes.rglob('*.class'):f.unlink()
 def tool(name):
  return tools/(name+('.bat' if name in ('d8','apksigner') else '.exe') if os.name=='nt' else name)
 def run(args):subprocess.run([str(x) for x in args],check=True)
 run(['javac','-encoding','UTF-8','--release','8','-classpath',android,'-d',classes,*root.glob('src/**/*.java')])
 run([tool('aapt2'),'compile','--dir',root/'res','-o',build/'compiled.zip'])
 run([tool('aapt2'),'link','-o',build/'resources.apk','--manifest',root/'AndroidManifest.xml','-I',android,build/'compiled.zip'])
 run([tool('d8'),'--min-api','26','--lib',android,'--output',build,*classes.rglob('*.class')])
 unsigned=build/'unsigned.apk';shutil.copyfile(build/'resources.apk',unsigned)
 with zipfile.ZipFile(unsigned,'a',zipfile.ZIP_DEFLATED) as z:z.write(build/'classes.dex','classes.dex')
 run([tool('zipalign'),'-f','4',unsigned,build/'aligned.apk'])
 if a.keystore:
  if not os.environ.get('APK_SIGNING_PASSWORD'):raise ValueError('APK_SIGNING_PASSWORD_required')
  run([tool('apksigner'),'sign','--ks',a.keystore,'--ks-key-alias',a.alias,'--ks-pass','env:APK_SIGNING_PASSWORD','--key-pass','env:APK_SIGNING_PASSWORD','--out',build/'weilan-assistant.apk',build/'aligned.apk'])
  run([tool('apksigner'),'verify','--verbose',build/'weilan-assistant.apk'])
 else:print('Unsigned aligned APK built; use your own signing key before installation.')

if __name__=='__main__':main()
