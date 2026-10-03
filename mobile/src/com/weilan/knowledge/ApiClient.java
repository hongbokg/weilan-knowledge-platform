package com.weilan.knowledge;
import javax.net.ssl.HttpsURLConnection;
import java.net.*;import java.io.*;import java.util.*;
import org.json.*;
final class ApiClient {
 static final String DEFAULT_KB="https://kb.example.com";
 final ApiSettings store;
 volatile HttpsURLConnection active;
 volatile NativeSocket socket;
 volatile boolean cancelled;
 ApiClient(ApiSettings s){store=s;}
 String get(String k,String f) throws Exception{return store.get(k,f);}
 static String base(String input) throws Exception {
  URI u=new URI(input.trim());
  if(!"https".equalsIgnoreCase(u.getScheme())||u.getHost()==null||u.getUserInfo()!=null||u.getQuery()!=null||u.getFragment()!=null)throw new Exception("请输入有效的 HTTPS 地址");
  if(u.getPort()==0||u.getPort() < -1||u.getPort()>65535)throw new Exception("端口无效");
  String s=u.toASCIIString();while(s.endsWith("/"))s=s.substring(0,s.length()-1);return s;
 }
 String endpoint(String service) throws Exception {String b=get(service+"_url",service.equals("kb")?DEFAULT_KB:"");if(b.isEmpty())throw new Exception("请先配置 "+service.toUpperCase()+" 连接地址");return base(b);}
 String token(String service) throws Exception {String t=get(service+"_token","");if(t.isEmpty())throw new Exception(service.equals("octop")?"Octop 统一登录尚未接通，请先使用知识库和 GLM Flash。":"请先使用账号和密码登录。");return t;}
 HttpsURLConnection open(String service,String path,String method,JSONObject body,boolean auth) throws Exception {
  if(!path.startsWith("/")||path.startsWith("//")||path.indexOf('\r')>=0||path.indexOf('\n')>=0)throw new Exception("无效接口路径");
  URL u=new URL(endpoint(service)+path);HttpsURLConnection c=(HttpsURLConnection)u.openConnection();
  c.setInstanceFollowRedirects(false);c.setConnectTimeout(15000);c.setReadTimeout(90000);c.setRequestMethod(method);
  c.setRequestProperty("Accept","application/json, text/event-stream");c.setRequestProperty("User-Agent","WeilanNative/2.0.0");
  if(auth){c.setRequestProperty("Authorization","Bearer "+token(service));if(service.equals("kb")){String tenant=get("kb_tenant","");if(!tenant.isEmpty())c.setRequestProperty("X-Tenant-ID",tenant);}}
  if(body!=null){c.setDoOutput(true);c.setRequestProperty("Content-Type","application/json; charset=utf-8");byte[] b=body.toString().getBytes("UTF-8");c.setFixedLengthStreamingMode(b.length);try(OutputStream out=c.getOutputStream()){out.write(b);}}
  return c;
 }
 static void check(HttpsURLConnection c) throws Exception {
  int status=c.getResponseCode();if(status>=200&&status<300)return;
  if(status==401)throw new Exception("登录已失效，请重新登录或更新密钥（401）");
  if(status==403)throw new Exception("当前账号没有访问权限（403）");
  if(status>=300&&status<400)throw new Exception("服务返回重定向，已停止发送鉴权信息");
  throw new Exception("接口请求失败（HTTP "+status+"），请检查服务配置");
 }
 Object request(String svc,String path,String method,JSONObject body,boolean auth) throws Exception {
  HttpsURLConnection c=open(svc,path,method,body,auth);
  try{check(c);if(c.getResponseCode()==204)return new JSONObject();String type=c.getContentType();if(type==null||!type.toLowerCase(Locale.ROOT).contains("json"))throw new Exception("接口返回的不是 JSON，请确认 API 地址");
   String raw=new String(bytes(c.getInputStream(),4*1024*1024),"UTF-8");return new JSONTokener(raw).nextValue();
  }finally{c.disconnect();}
 }
 static byte[] bytes(InputStream in,int limit) throws Exception {
  ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] b=new byte[32768];int n;
  while((n=in.read(b))!=-1){if(out.size()+n>limit)throw new Exception("数据超过手机预览限制");out.write(b,0,n);}return out.toByteArray();
 }
 interface Listener {void text(String text);void meta(JSONObject data);}
 void stream(String svc,String path,JSONObject body,Listener l) throws Exception {
  if(cancelled)throw new Exception("已停止");
  active=open(svc,path,"POST",body,true);HttpsURLConnection c=active;
  try{check(c);if(cancelled)throw new Exception("已停止");String type=c.getContentType();
   if(type!=null&&type.toLowerCase(Locale.ROOT).contains("text/event-stream")) {
    final boolean[] finished={false};
    StreamProtocol.read(c.getInputStream(),(event,data)->{
     if(cancelled)return false;
     if(data.equals("[DONE]")){finished[0]=true;return false;}
     JSONObject o=new JSONObject(data);if(o.has("error"))throw new Exception("模型服务返回错误，请检查模型或配额");
     if(svc.equals("llm")) {JSONArray choices=o.optJSONArray("choices");if(choices!=null&&choices.length()>0){JSONObject choice=choices.getJSONObject(0),delta=choice.optJSONObject("delta");if(delta!=null)l.text(delta.optString("content",""));if(!choice.isNull("finish_reason")&&choice.has("finish_reason"))finished[0]=true;}}
     else {String kind=o.optString("response_type",o.optString("type",event));if(kind.equals("error"))throw new Exception("知识库问答失败，请检查服务状态");if(kind.equals("answer"))l.text(o.optString("content",""));l.meta(o);if((o.optBoolean("done")&&kind.equals("answer"))||kind.equals("complete"))finished[0]=true;if(kind.equals("tool_call"))finished[0]=false;}
     return !finished[0];
    });
    if(!finished[0]&&!cancelled)throw new Exception("连接中断，回答可能不完整");
   } else if(svc.equals("llm")&&type!=null&&type.contains("json")) {
    JSONObject o=new JSONObject(new String(bytes(c.getInputStream(),4*1024*1024),"UTF-8"));l.text(o.getJSONArray("choices").getJSONObject(0).getJSONObject("message").getString("content"));
   } else throw new Exception("接口没有返回流式回答");
  }finally{c.disconnect();if(active==c)active=null;}
 }
 static JSONArray array(Object o,String...names) throws Exception {
  if(o instanceof JSONArray)return (JSONArray)o;
  if(o instanceof JSONObject){JSONObject j=(JSONObject)o;for(String name:names){Object item=j.opt(name);if(item instanceof JSONArray)return (JSONArray)item;}Object data=j.opt("data");if(data instanceof JSONArray)return (JSONArray)data;if(data instanceof JSONObject)return array(data,names);}
  throw new Exception("接口列表格式与当前版本不匹配");
 }
 static JSONObject object(Object o) throws Exception {if(!(o instanceof JSONObject))throw new Exception("接口对象格式不匹配");JSONObject j=(JSONObject)o;return j.optJSONObject("data")!=null?j.getJSONObject("data"):j;}
 void start(){cancelled=false;}
 void cancel(){cancelled=true;HttpsURLConnection c=active;if(c!=null)c.disconnect();NativeSocket s=socket;if(s!=null)s.close();}
 void octop(String agent,String thread,String query,Listener l) throws Exception {
  String root=endpoint("octop");String path="/api/agents/"+URLEncoder.encode(agent,"UTF-8")+"/chat/ws?token="+URLEncoder.encode(token("octop"),"UTF-8");
  socket=new NativeSocket(new URI(root.replaceFirst("^https:","wss:")+path));NativeSocket s=socket;
  try{if(cancelled)throw new Exception("已停止");JSONObject frame=new JSONObject().put("type","user_turn").put("text",query).put("conversation_mode",get("octop_mode","ask"));
   if(thread.startsWith("mobile:"))frame.put("session_key",thread);else if(!thread.isEmpty())frame.put("thread_id",thread);else throw new Exception("Octop 会话标识缺失");
   s.send(frame.toString());while(!cancelled){JSONObject o=new JSONObject(s.receive());String type=o.optString("type");l.meta(o);if(type.equals("error"))throw new Exception("Octop 返回错误，请检查模型、权限和任务配置");if(type.equals("done"))return;
    if(type.equals("token")||type.equals("text_delta"))l.text(o.optString("content",o.optString("text","")));
   }
  }finally{s.close();if(socket==s)socket=null;}
 }
}
