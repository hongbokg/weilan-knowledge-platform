import re
from pathlib import PurePosixPath
COMPANY=re.compile(r'^[\u4e00-\u9fffA-Za-z（）()·]{2,50}(?:股份有限公司|股份公司|有限责任公司|有限公司)$')
PROJECT=re.compile(r'项目|采购|改造|建设|安装|招标公告|工程(?:招标|投标)|20\d{2}[-－][A-Z0-9]+[-－][A-Z0-9]+')
GENERIC=re.compile(r'^(?:各零散项目|_?投标文件\d*|招投标|投招标|参考|资料|彩页等|附件|DC提供资料|公司资质|资质)$|各零散项目|原电脑文件|上传')
def classify(path):
 parts=PurePosixPath(path).parts[:-1]
 for i in range(len(parts)-1,-1,-1):
  piece=parts[i];company=piece.split('+')[-1]
  if not COMPANY.fullmatch(company):continue
  ancestors=list(parts[:i])
  if company!=piece:ancestors.append(piece[:-(len(company)+1)])
  for project in reversed(ancestors):
   if not GENERIC.search(project) and PROJECT.search(project) and len(project)>5 and not COMPANY.fullmatch(project):
    return {'company':company,'project':project,'prefix':str(PurePosixPath(*parts[:i+1])),'kind':'project','confidence':'directory_exact'}
  return {'company':company,'project':'公司通用资料','prefix':str(PurePosixPath(*parts[:i+1])),'kind':'company','confidence':'directory_exact'}
 return None
