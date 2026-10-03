package com.weilan.knowledge;
import org.json.*;import java.util.*;import java.util.regex.*;
/** Parses server citation markup as data, never as HTML or executable URLs. */
final class FileReferences {
 static final Pattern TAG=Pattern.compile("<kb\\b[^>]*>",Pattern.CASE_INSENSITIVE|Pattern.DOTALL);
 static String attr(String tag,String key){Matcher m=Pattern.compile("\\b"+key+"\\s*=\\s*[\"']([^\"']*)[\"']",Pattern.CASE_INSENSITIVE).matcher(tag);return m.find()?m.group(1):"";}
 static JSONArray parse(String content)throws Exception{JSONArray out=new JSONArray();Set<String> seen=new HashSet<>();Matcher m=TAG.matcher(content);while(m.find()){String tag=m.group(),chunk=attr(tag,"chunk_id"),doc=attr(tag,"doc");if(chunk.isEmpty()||!seen.add(chunk))continue;out.put(new JSONObject().put("name",doc.replaceFirst("\\s*\\[nas-sync-[^\\]]*\\]$","")).put("chunk_id",chunk));}return out;}
 static String display(String content){String clean=TAG.matcher(content).replaceAll("");int partial=clean.lastIndexOf("<kb ");if(partial>=0)clean=clean.substring(0,partial);return clean.replace("**","");}
}
