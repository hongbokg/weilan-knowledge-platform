package com.weilan.knowledge;
import java.io.*;
/** SSE parser supports UTF-8, multiline data, comments and final unterminated events. */
final class StreamProtocol {
 interface Event { boolean receive(String event,String data) throws Exception; }
 static void read(InputStream in,Event callback) throws Exception {
  BufferedReader r=new BufferedReader(new InputStreamReader(in,"UTF-8"));String line,event="";StringBuilder data=new StringBuilder();
  while((line=r.readLine())!=null) {
   if(line.isEmpty()) { if(data.length()>0&&!callback.receive(event,data.substring(0,data.length()-1)))return;event="";data.setLength(0);continue; }
   if(line.startsWith(":"))continue;
   if(line.startsWith("event:"))event=value(line);
   if(line.startsWith("data:")){data.append(value(line)).append('\n');if(data.length()>2*1024*1024)throw new IOException("SSE event too large");}
  }
  if(data.length()>0)callback.receive(event,data.substring(0,data.length()-1));
 }
 private static String value(String line){String s=line.substring(line.indexOf(':')+1);return s.startsWith(" ")?s.substring(1):s;}
}
