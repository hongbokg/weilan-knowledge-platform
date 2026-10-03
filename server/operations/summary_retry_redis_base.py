"""Read only: aggregate archived summary errors without logging task bodies."""
import collections,datetime,json,socket

sock=socket.create_connection(('127.0.0.1',6379),timeout=10);stream=sock.makefile('rb')
def pack(*args):
    parts=[str(a).encode() if not isinstance(a,bytes) else a for a in args]
    return b'*'+str(len(parts)).encode()+b'\r\n'+b''.join(b'$'+str(len(p)).encode()+b'\r\n'+p+b'\r\n' for p in parts)
def read():
    line=stream.readline();kind=line[:1];value=line[1:-2]
    if kind==b'+':return value
    if kind==b':':return int(value)
    if kind==b'$':
        n=int(value)
        if n<0:return None
        data=stream.read(n);stream.read(2);return data
    if kind==b'*':return [read() for _ in range(int(value))]
    raise RuntimeError('redis_read_failed')
def call(*args):sock.sendall(pack(*args));return read()
def decode(b):
    pos=0;result={}
    def varint():
        nonlocal pos
        n=shift=0
        while True:
            v=b[pos];pos+=1;n|=(v&127)<<shift
            if v<128:return n
            shift+=7
    while pos<len(b):
        tag=varint();field=tag>>3;wire=tag&7
        if wire==0:result[field]=varint()
        elif wire==2:
            size=varint();result[field]=b[pos:pos+size];pos+=size
        else:raise ValueError('unknown_protobuf_wire')
    return result

rows=call('ZRANGE','asynq:{summary}:archived',0,-1,'WITHSCORES');ids=rows[::2];times=[float(v) for v in rows[1::2]]
counts=collections.Counter();retried=collections.Counter();types=collections.Counter()
for offset in range(0,len(ids),200):
    batch=ids[offset:offset+200]
    sock.sendall(b''.join(pack('HGET',b'asynq:{summary}:t:'+id,'msg') for id in batch))
    for _ in batch:
        raw=read()
        if not raw:counts['missing_task_record']+=1;continue
        m=decode(raw);error=m.get(7,b'').decode('utf-8','replace')
        key='429_1302' if '429' in error and '1302' in error else '429_other' if '429' in error else 'other_error'
        counts[key]+=1;retried[str(m.get(6,0))]+=1
        types[m.get(1,b'').decode('utf-8','replace')]+=1
zone=datetime.timezone(datetime.timedelta(hours=8))
stamp=lambda t:datetime.datetime.fromtimestamp(t,zone).isoformat()
print(json.dumps({'archived':len(ids),'earliest_archive':stamp(min(times)),'latest_archive':stamp(max(times)),
                  'error_groups':dict(counts),'retried':dict(retried),'task_types':dict(types)},ensure_ascii=False))
stream.close();sock.close()

