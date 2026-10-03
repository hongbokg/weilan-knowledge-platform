package com.weilan.knowledge;
import javax.net.ssl.*;import java.net.*;import java.io.*;import java.security.*;import java.util.*;
/** Small RFC6455 client: verified TLS, masked client frames, bounded server messages. */
final class NativeSocket {
 private final SSLSocket socket;private final InputStream in;private final OutputStream out;
 private final SecureRandom random=new SecureRandom();
 NativeSocket(URI uri) throws Exception {
  if(!"wss".equals(uri.getScheme())||uri.getHost()==null||uri.getUserInfo()!=null)throw new IOException("WSS required");
  int port=uri.getPort()>0?uri.getPort():443;java.net.Socket raw=new java.net.Socket();
  try {
   raw.connect(new InetSocketAddress(uri.getHost(),port),15000);
   socket=(SSLSocket)((SSLSocketFactory)SSLSocketFactory.getDefault()).createSocket(raw,uri.getHost(),port,true);
   SSLParameters params=socket.getSSLParameters();params.setEndpointIdentificationAlgorithm("HTTPS");socket.setSSLParameters(params);
   socket.setSoTimeout(90000);socket.startHandshake();in=socket.getInputStream();out=socket.getOutputStream();
   byte[] nonce=new byte[16];random.nextBytes(nonce);String key=Base64.getEncoder().encodeToString(nonce);
   String path=uri.getRawPath()+(uri.getRawQuery()==null?"":"?"+uri.getRawQuery());
   String host=uri.getHost()+(port==443?"":":"+port);
   out.write(("GET "+path+" HTTP/1.1\r\nHost: "+host+"\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: "+key+"\r\nSec-WebSocket-Version: 13\r\n\r\n").getBytes("US-ASCII"));out.flush();
   ByteArrayOutputStream header=new ByteArrayOutputStream();int a=0,b=0,c=0,x;
   while((x=in.read())!=-1){header.write(x);if(header.size()>16384)throw new IOException("Oversize handshake");if(a==13&&b==10&&c==13&&x==10)break;a=b;b=c;c=x;}
   String[] lines=header.toString("US-ASCII").split("\r\n");
   if(lines.length==0||!lines[0].matches("HTTP/1\\.[01] 101(?: .*|)"))throw new IOException("Octop WebSocket 握手失败，请检查地址和登录");
   Map<String,String> fields=new HashMap<>();for(int i=1;i<lines.length;i++){int colon=lines[i].indexOf(':');if(colon>0)fields.put(lines[i].substring(0,colon).toLowerCase(Locale.ROOT),lines[i].substring(colon+1).trim());}
   String accept=Base64.getEncoder().encodeToString(MessageDigest.getInstance("SHA-1").digest((key+"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").getBytes("US-ASCII")));
   if(!accept.equals(fields.get("sec-websocket-accept"))||!"websocket".equalsIgnoreCase(fields.get("upgrade"))||!fields.getOrDefault("connection","").toLowerCase(Locale.ROOT).contains("upgrade"))throw new IOException("Invalid WebSocket handshake");
  }catch(Exception e){raw.close();throw e;}
 }
 synchronized void send(String message) throws Exception {write(1,message.getBytes("UTF-8"));}
 private synchronized void write(int opcode,byte[] data) throws Exception {
  if(data.length>2*1024*1024)throw new IOException("Oversize frame");out.write(128|opcode);
  int n=data.length;if(n<126)out.write(128|n);else if(n<=65535){out.write(128|126);out.write(n>>>8);out.write(n);}else{out.write(128|127);for(int i=7;i>=0;i--)out.write((int)(((long)n>>>(i*8))&255));}
  byte[] mask=new byte[4];random.nextBytes(mask);out.write(mask);byte[] payload=new byte[n];for(int i=0;i<n;i++)payload[i]=(byte)(data[i]^mask[i%4]);out.write(payload);out.flush();
 }
 private int octet() throws IOException {int b=in.read();if(b==-1)throw new EOFException("Octop 连接中断");return b;}
 String receive() throws Exception {
  ByteArrayOutputStream message=new ByteArrayOutputStream();boolean started=false;
  while(true){int first=octet(),second=octet(),opcode=first&15;boolean fin=(first&128)!=0;
   if((first&112)!=0||(second&128)!=0)throw new IOException("Invalid server frame");long n=second&127;
   if(n==126)n=((long)octet()<<8)|octet();else if(n==127){n=0;for(int i=0;i<8;i++){int part=octet();if(i==0&&(part&128)!=0)throw new IOException("Invalid length");n=(n<<8)|part;if(n>2*1024*1024)throw new IOException("Oversize frame");}}
   if(n>2*1024*1024||message.size()+n>2*1024*1024)throw new IOException("Oversize message");
   if(opcode>=8&&(!fin||n>125))throw new IOException("Invalid control frame");
   byte[] data=new byte[(int)n];int offset=0;while(offset<data.length){int count=in.read(data,offset,data.length-offset);if(count==-1)throw new EOFException();offset+=count;}
   if(opcode==8)throw new IOException("Octop 会话已关闭，请重新登录或重试");
   if(opcode==9){write(10,data);continue;}if(opcode==10)continue;
   if(opcode==1){if(started)throw new IOException("Unexpected text frame");started=true;}else if(opcode!=0||!started)throw new IOException("Unexpected opcode");
   message.write(data);if(fin)return new String(message.toByteArray(),"UTF-8");
  }
 }
 void close(){try{socket.close();}catch(Exception ignored){}}
}
