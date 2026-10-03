"""Actual credential content is restricted; document subject and IP/MAC are not."""
import html
import re
from html.parser import HTMLParser

LABEL = re.compile(r'^(?:(?:管理员|登录|登入|设备|摄像头|交换机|账号|管理|超级|初始|默认|admin|login|device|NVR|IPC|Web)[ _-]*){0,3}(?:密码|口令|password|passwd|pwd|secret|api[ _-]?key|access[ _-]?token)$',re.I)
PLACEHOLDERS={'','无','暂无','无密码','无需密码','未设置','未填写','未知','密码','口令','用户名','账号','IP','IP地址','MAC','备注','说明','更新时间','******','********','xxx','xxxx','待填写','请填写','请设置','已隐藏','已脱敏','none','null','n/a'}

def actual(value):
    value=value.strip().strip('"\'`')
    # Symbol-only passwords are valid credentials; masks and table rulers are not.
    return bool(value) and value.lower() not in {v.lower() for v in PLACEHOLDERS} and not re.fullmatch(r'[-*_•\s]+',value)

class Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.rows=[];self.groups=[];self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='table':self.rows=[]
        elif tag=='tr':self.row=[]
        elif tag in ('td','th') and self.row is not None:self.cell=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            self.row.append(''.join(self.cell).strip());self.cell=None
        elif tag=='tr' and self.row is not None:
            self.rows.append(self.row);self.row=None
        elif tag=='table':
            self.groups.append(self.rows);self.rows=[]

def table_credentials(rows):
    columns=set()
    for row in rows:
        for index,value in enumerate(row):
            label=re.sub(r'[（(].*?[）)]','',value).strip()
            if LABEL.fullmatch(label):
                # Two-cell key/value form.
                if len(row)==2 and index==0:
                    if actual(row[1]):return True
                    continue
                columns.add(index)
            elif index in columns and actual(value) and not re.fullmatch(r'[:\- ]+',value):
                return True
    return False

def contains_credentials(text):
    if re.search(r'-----BEGIN .*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{16,}\b',text):return True
    pattern=r'(?:密码|口令|password|passwd|pwd|secret|api[ _-]?key|access[ _-]?token)["\']?\s*[:：=]\s*["\']?([^\s"\'<>|]+)'
    if any(actual(m.group(1)) for m in re.finditer(pattern,text,re.I)):return True
    parser=Tables();parser.feed(text)
    if any(table_credentials(rows) for rows in parser.groups+[parser.rows]):return True
    rows=[]
    for line in text.splitlines():
        if line.strip().startswith('|') and line.strip().endswith('|'):
            rows.append([html.unescape(c.strip()) for c in line.strip().strip('|').split('|')])
        elif rows:
            if table_credentials(rows):return True
            rows=[]
    return table_credentials(rows)
