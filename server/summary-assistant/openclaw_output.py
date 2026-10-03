"""Decode the fixed summary/evidence schema without changing summary facts."""
import json,re
def decode(text):
 text=re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip())
 try:obj=json.loads(text)
 except json.JSONDecodeError:
  # Models sometimes leave company-name quotes unescaped inside summary.
  # Split only the two expected top-level fields; repair the evidence array
  # separately so a malformed quote cannot truncate the summary.
  match=re.fullmatch(r'\s*\{\s*"summary"\s*:\s*"(.*)"\s*,\s*"evidence"\s*:\s*(\[.*\])\s*\}\s*',text,re.S)
  from json_repair import loads
  if match:
   summary=match[1].replace(r'\"','"').replace(r'\n','\n').replace(r'\\','\\')
   obj={'summary':summary,'evidence':loads(match[2])}
  else:obj=loads(text)
 if not isinstance(obj,dict) or not isinstance(obj.get('summary'),str) or not isinstance(obj.get('evidence'),list):raise ValueError('invalid_summary_schema')
 return obj
