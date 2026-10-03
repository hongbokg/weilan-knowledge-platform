package com.weilan.knowledge;
import org.json.*;import java.util.*;
/** Uses the authenticated server's models and library permissions, never a provider key in the APK. */
final class AccountChat {
 static final String AGENT="builtin-quick-answer";
 static final String LLM_AGENT="bf5a5f39-ba8e-4930-a34e-4169c50bed74";
 static boolean answering(JSONObject model){
  String type=model.optString("type");if(!type.equals("KnowledgeQA")&&!type.equals("VLLM"))return false;
  String status=model.optString("status","active");if(!status.equals("active"))return false;
  String name=model.optString("name","").toLowerCase(Locale.ROOT);JSONObject parameters=model.optJSONObject("parameters");String remote=parameters==null?"":parameters.optString("model_name","");return true;
 }
 static String selectModel(JSONArray models,String preferred) throws Exception {
  if(preferred.isEmpty())throw new Exception("请先在网页设置 APP 默认模型。");
  String selected="";
  for(int i=0;i<models.length();i++){JSONObject m=models.getJSONObject(i);if(!answering(m))continue;String id=m.optString("id","");if(id.isEmpty())continue;if(id.equals(preferred))return id;if(selected.isEmpty())selected=id;}
  if(!selected.equals(preferred))throw new Exception("网页尚未绑定可用的 APP 回答模型，请管理员在设置 → 模型管理选择 APP 默认模型。");return selected;
 }
 static JSONObject request(String query,boolean retrieval,String model,JSONArray libraries) throws Exception {
  if(model.isEmpty())throw new Exception("APP 回答模型尚未就绪");
  if(retrieval&&libraries.length()==0)throw new Exception("当前账号没有可访问的知识库，请联系管理员授权。");
  return new JSONObject().put("query",query).put("agent_id",retrieval?AGENT:LLM_AGENT).put("agent_enabled",false)
   .put("summary_model_id",model).put("knowledge_base_ids",retrieval?libraries:new JSONArray())
   .put("disable_title",true).put("web_search_enabled",false).put("channel","web");
 }
 static JSONObject prepare(ApiClient api) throws Exception {
  JSONArray models=ApiClient.array(api.request("kb","/api/v1/models","GET",null,true),"models","items");
  JSONObject agent=ApiClient.object(api.request("kb","/api/v1/agents/"+LLM_AGENT,"GET",null,true));JSONObject config=agent.optJSONObject("config");
  String model=selectModel(models,config==null?"":config.optString("model_id",""));
  JSONArray libraries=ApiClient.array(api.request("kb","/api/v1/knowledge-bases","GET",null,true),"knowledge_bases","items");JSONArray ids=new JSONArray();
  for(int i=0;i<libraries.length();i++){String id=libraries.getJSONObject(i).optString("id","");if(!id.isEmpty())ids.put(id);}
  return new JSONObject().put("model_id",model).put("knowledge_base_ids",ids);
 }
}
