import os,shutil
from pathlib import Path
import urllib.request, subprocess,ssl,threading,json,hashlib,base64,struct,datetime,http.server
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
root=Path(__file__).resolve().parents[1];build=root/'build/contract-tests';build.mkdir(parents=True,exist_ok=True)
tools=root.parent/'android-build-tools';jdk=Path(os.environ['JAVA_HOME'])/'bin' if os.environ.get('JAVA_HOME') else Path(shutil.which('java') or 'java').resolve().parent
jar=build/'json.jar'
if not jar.exists():urllib.request.urlretrieve('https://repo.maven.apache.org/maven2/org/json/json/20240303/json-20240303.jar',jar)
key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]);now=datetime.datetime.now(datetime.timezone.utc)
cert=x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1)).not_valid_after(now+datetime.timedelta(days=2)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),False).sign(key,hashes.SHA256())
(build/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM));(build/'test-key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
trust=build/'test-trust.jks'
if trust.exists():trust.unlink()
subprocess.run([str(jdk/('keytool'+('.exe' if os.name=='nt' else ''))),'-importcert','-noprompt','-alias','synthetic','-file',str(build/'cert.pem'),'-keystore',str(trust),'-storepass','synthetic-test-only'],check=True,capture_output=True)
issues=[];requests=[]
class Handler(http.server.BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def response(self,status,data,kind='application/json',headers={}):
  body=data.encode();self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
  for k,v in headers.items():self.send_header(k,v)
  self.end_headers();self.wfile.write(body)
 def frame(self,opcode,data,fin=True):
  b=data.encode() if isinstance(data,str) else data
  self.wfile.write(bytes([(128 if fin else 0)|opcode,len(b)])+b);self.wfile.flush()
 def incoming(self):
  first,second=self.rfile.read(2);size=second&127
  if size==126:size=struct.unpack('!H',self.rfile.read(2))[0]
  elif size==127:size=struct.unpack('!Q',self.rfile.read(8))[0]
  if not second&128:issues.append('Client frame not masked')
  mask=self.rfile.read(4);data=self.rfile.read(size)
  return first&15,bytes(x^mask[i%4] for i,x in enumerate(data))
 def do_GET(self):
  requests.append(self.path.split('?')[0])
  if self.headers.get('Upgrade','').lower()=='websocket':
   digest=base64.b64encode(hashlib.sha1((self.headers['Sec-WebSocket-Key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
   self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',digest);self.end_headers()
   opcode,data=self.incoming()
   if self.path.startswith('/api/agents/'):
    frame=json.loads(data)
    if frame.get('session_key')!='mobile:synthetic-session' or frame.get('type')!='user_turn' or frame.get('text')!='工作问题' or frame.get('conversation_mode')!='ask':issues.append('Octop turn contract mismatch')
    self.frame(1,json.dumps({'type':'token','content':'Octop回答'},ensure_ascii=False));self.frame(1,json.dumps({'type':'done'}))
   else:
    if data.decode()!='测试消息':issues.append('WebSocket outgoing UTF8 mismatch')
    self.frame(9,b'ping');op,pong=self.incoming()
    if op!=10 or pong!=b'ping':issues.append('Pong mismatch')
    self.frame(1,'分片',False);self.frame(0,'回答',True)
   self.close_connection=True;return
  if self.path.endswith('/headers'):self.response(200,json.dumps({'authorization':self.headers.get('Authorization',''),'tenant':self.headers.get('X-Tenant-ID','')}));return
  if self.path=='/redirect':self.response(302,'{}',headers={'Location':'https://example.invalid/credential-target'});return
  if self.path=='/html':self.response(200,'<html>login</html>','text/html');return
  if self.path=='/unauthorized':self.response(401,'{}');return
  if self.path=='/forbidden':self.response(403,'{}');return
  self.response(404,'{}')
 def do_POST(self):
  raw=self.rfile.read(int(self.headers.get('Content-Length',0)));requests.append(self.path)
  if self.path=='/kb-stream':self.response(200,'data: '+json.dumps({'response_type':'answer','content':'知识库'},ensure_ascii=False)+'\n\ndata: '+json.dumps({'response_type':'answer','content':'回答','done':True},ensure_ascii=False)+'\n\n','text/event-stream');return
  if self.path=='/v1/chat/completions':self.response(200,'data: '+json.dumps({'choices':[{'delta':{'content':'模型回答'}}]},ensure_ascii=False)+'\n\ndata: [DONE]\n\n','text/event-stream');return
  if self.path=='/truncated':self.response(200,'data: {"response_type":"answer","content":"partial"}\n\n','text/event-stream');return
  if self.path=='/v1/error-stream':self.response(200,'data: {"error":{"message":"synthetic"}}\n\n','text/event-stream');return
  self.response(404,'{}')
server=http.server.ThreadingHTTPServer(('localhost',0),Handler);tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(build/'cert.pem',build/'test-key.pem');server.socket=tls.wrap_socket(server.socket,server_side=True);threading.Thread(target=server.serve_forever,daemon=True).start()
src=root/'src/com/weilan/knowledge';files=[src/(n+'.java') for n in ['ApiSettings','ApiClient','StreamProtocol','NativeSocket','AccountChat','FileReferences','ShareFiles']]+[root/'tests/NativeContractTest.java']
subprocess.run([str(jdk/('javac'+('.exe' if os.name=='nt' else ''))),'-encoding','UTF-8','--release','8','-cp',str(jar),'-d',str(build),*[str(p) for p in files]],check=True)
result=subprocess.run([str(jdk/('java'+('.exe' if os.name=='nt' else ''))),f'-Djavax.net.ssl.trustStore={trust}','-Djavax.net.ssl.trustStorePassword=synthetic-test-only','-cp',str(build)+os.pathsep+str(jar),'com.weilan.knowledge.NativeContractTest',f'https://localhost:{server.server_port}'],capture_output=True,text=True,encoding='utf-8',timeout=45)
server.shutdown()
if result.returncode:print(result.stderr);raise SystemExit(result.returncode)
assert not issues,issues
assert '/credential-target' not in requests
print(result.stdout.strip())
(build/'validation.json').write_text(json.dumps({'result':result.stdout.strip(),'synthetic_server_issues':issues,'redirect_not_followed':True,'test_environment':'JVM and synthetic loopback HTTPS/WSS server, not Android device'},ensure_ascii=False,indent=2),encoding='utf-8')
