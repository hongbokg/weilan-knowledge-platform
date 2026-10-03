package com.weilan.knowledge;
import java.io.*;import java.util.*;import java.net.URI;import org.json.*;
public final class NativeContractTest {
 static int checks=0;static void ok(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
 public static void main(String[] args) throws Exception {
  ok(ShareFiles.mime("报告.PDF").equals("application/pdf"),"PDF handler MIME");
  ok(ShareFiles.mime("报告.docx").equals("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),"Word handler MIME");
  ok(!ShareFiles.safeName("../../secret").contains("/"),"Unsafe filename sanitized");
  File shareRoot=new File(System.getProperty("java.io.tmpdir"),"synthetic-shares");String shareKey="11111111-1111-1111-1111-111111111111";
  ok(ShareFiles.resolve(shareRoot,shareKey,"文件.pdf").getParentFile().getName().equals(shareKey),"Share URI resolves isolated file");
  for(String unsafe:new String[]{"../../secret","..","nested/file"}){try{ShareFiles.resolve(shareRoot,shareKey,unsafe);throw new AssertionError("Share path escape");}catch(IOException expected){checks++;}}
  String citation="说明 <kb doc=\"营业执照.jpg [nas-sync-123]\"\n chunk_id=\"chunk-1\" kb_id=\"kb-1\" />";
  JSONArray files=FileReferences.parse(citation);
  ok(files.length()==1&&files.getJSONObject(0).getString("name").equals("营业执照.jpg"),"Multiline image citation resolves filename");
  ok(FileReferences.parse(citation+citation).length()==1,"Duplicate chunks produce one card");
  ok(!FileReferences.display(citation).contains("chunk_id"),"Raw citation hidden");
  ok(FileReferences.display("说明 <kb doc=\"unfinished").equals("说明 "),"Partial stream citation hidden");
  ok(FileReferences.parse("<kb doc='文件.pdf' chunk_id='two' />").getJSONObject(0).getString("name").equals("文件.pdf"),"Single quoted document citation");
  ok(FileReferences.parse("<kb doc=\"无标识.jpg\" />").length()==0,"No fabricated file reference");
  JSONArray models=new JSONArray().put(new JSONObject().put("id","embedding").put("name","hy4-preview").put("type","Embedding")).put(new JSONObject().put("id","flash").put("name","hy3").put("type","KnowledgeQA"));
  ok(AccountChat.selectModel(models,"flash").equals("flash"),"Web configured answering model");
  try{AccountChat.selectModel(models,"embedding");throw new AssertionError("Invalid preference accepted");}catch(Exception expected){checks++;}
  ok(AccountChat.selectModel(new JSONArray().put(new JSONObject().put("id","hy4").put("name","hy4-preview").put("type","KnowledgeQA")),"hy4").equals("hy4"),"HY4 selectable for APP");
  JSONObject rag=AccountChat.request("query",true,"flash",new JSONArray().put("library"));
  ok(!rag.getBoolean("agent_enabled")&&rag.getString("agent_id").equals("builtin-quick-answer"),"Prevent missing agent HTTP400");
  ok(rag.getString("summary_model_id").equals("flash")&&rag.getJSONArray("knowledge_base_ids").length()==1,"Server model and retrieval");
  ok(AccountChat.request("query",false,"flash",new JSONArray().put("library")).getJSONArray("knowledge_base_ids").length()==0,"LLM mode disables retrieval");
  try{AccountChat.selectModel(new JSONArray(),"");throw new AssertionError("Missing model accepted");}catch(Exception expected){checks++;}
  try{AccountChat.request("query",true,"flash",new JSONArray());throw new AssertionError("Unauthorized library accepted");}catch(Exception expected){checks++;}
  List<String> events=new ArrayList<>();StreamProtocol.read(new ByteArrayInputStream(":keepalive\r\nevent: answer\r\ndata: 第一行\r\ndata: 第二行\r\n\r\ndata: last".getBytes("UTF-8")),(event,data)->{events.add(event+"|"+data);return true;});
  ok(events.size()==2,"SSE multiline + final event");ok(events.get(0).equals("answer|第一行\n第二行"),"SSE Unicode");ok(events.get(1).equals("|last"),"SSE trailing dispatch");
  events.clear();StreamProtocol.read(new ByteArrayInputStream("data: 1\n\ndata: 2\n\n".getBytes("UTF-8")),(e,d)->{events.add(d);return false;});ok(events.size()==1,"SSE stop");
  for(String bad:new String[]{"http://localhost","https://user:secret@localhost","https://localhost?key=secret","https://localhost/#x","https://localhost:65536"}){try{ApiClient.base(bad);throw new AssertionError("Unsafe base accepted");}catch(Exception expected){checks++;}}
  ok(ApiClient.base("https://localhost:443/v1/").equals("https://localhost:443/v1"),"base path normalization");
  Map<String,String> settings=new HashMap<>();settings.put("kb_url",args[0]);settings.put("kb_token","synthetic-kb-token");settings.put("kb_tenant","9");settings.put("llm_url",args[0]+"/v1");settings.put("llm_token","synthetic-llm-token");settings.put("octop_url",args[0]);settings.put("octop_token","synthetic-octop-token");
  ApiClient api=new ApiClient((key,fallback)->settings.getOrDefault(key,fallback));
  JSONObject headers=ApiClient.object(api.request("kb","/headers","GET",null,true));ok(headers.getString("authorization").equals("Bearer synthetic-kb-token"),"KB bearer");ok(headers.getString("tenant").equals("9"),"KB tenant");
  JSONObject llm=ApiClient.object(api.request("llm","/headers","GET",null,true));ok(llm.getString("authorization").equals("Bearer synthetic-llm-token"),"LLM separate bearer");ok(llm.getString("tenant").equals(""),"tenant not leaked to LLM");
  for(String path:new String[]{"/unauthorized","/forbidden","/redirect","/html"}){try{api.request("kb",path,"GET",null,true);throw new AssertionError("Expected rejection "+path);}catch(Exception expected){checks++;}}
  StringBuilder output=new StringBuilder();ApiClient.Listener listener=new ApiClient.Listener(){public void text(String s){output.append(s);}public void meta(JSONObject o){}};
  api.start();api.stream("kb","/kb-stream",new JSONObject().put("query","合成问题"),listener);ok(output.toString().equals("知识库回答"),"KB streaming");
  output.setLength(0);api.start();api.stream("llm","/chat/completions",new JSONObject().put("stream",true),listener);ok(output.toString().equals("模型回答"),"LLM streaming");
  api.start();try{api.stream("kb","/truncated",new JSONObject(),listener);throw new AssertionError("truncated SSE accepted");}catch(Exception expected){checks++;}
  api.start();try{api.stream("llm","/error-stream",new JSONObject(),listener);throw new AssertionError("error SSE accepted");}catch(Exception expected){checks++;}
  api.cancel();try{api.stream("kb","/kb-stream",new JSONObject(),listener);throw new AssertionError("cancelled stream restarted");}catch(Exception expected){checks++;}
  ok(ApiClient.array(new JSONObject("{\"data\":{\"items\":[1,2]}}"),"items").length()==2,"nested response array");
  NativeSocket socket=new NativeSocket(new URI(args[0].replaceFirst("https:","wss:")+"/socket"));socket.send("测试消息");ok(socket.receive().equals("分片回答"),"fragmented UTF8 WebSocket text and ping/pong");socket.close();
  output.setLength(0);api.start();api.octop("test-agent","mobile:synthetic-session","工作问题",listener);ok(output.toString().equals("Octop回答"),"Octop user_turn and persistent session key");
  try{new NativeSocket(new URI(args[0].replace("localhost","127.0.0.1").replaceFirst("https:","wss:")+"/socket"));throw new AssertionError("Hostname mismatch accepted");}catch(javax.net.ssl.SSLHandshakeException expected){checks++;}
  System.out.println("PASS "+checks+" protocol, authentication and TLS checks");
 }
}
