"""用响应头区分服务端处理和锁等待，不记录材料或凭据。"""
import threading,time,gc
import json
try:import orjson as _fast_json
except ImportError:_fast_json=None

def json_bytes(value):
    """仅加速 HTTP 编码；不改磁盘记录，特殊旧值仍按原编码器处理。"""
    if _fast_json is not None:
        try:return _fast_json.dumps(value,option=_fast_json.OPT_NON_STR_KEYS)
        except (TypeError,ValueError):pass
    return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf8')

from collections import deque
_gc_events=deque(maxlen=512)
_request=threading.local()
def begin_request():_request.lock_wait=0.;_request.cpu_started=time.thread_time();_request.phases={};_request.phase_depth={};_request.gc_ms=0.

def _gc_timing(phase,info):
    if phase=='start':_request.gc_started=time.perf_counter()
    elif hasattr(_request,'gc_started'):
        end=time.perf_counter();_gc_events.append((_request.gc_started,end));_request.gc_ms=getattr(_request,'gc_ms',0.)+(end-_request.gc_started)*1000;del _request.gc_started

gc.callbacks.append(_gc_timing)

def timed_call(name,function,*args,**kwargs):
    start=time.perf_counter();depth=getattr(_request,'phase_depth',None)
    if depth is None:depth={};_request.phase_depth=depth
    outer=not depth.get(name,0);depth[name]=depth.get(name,0)+1
    try:return function(*args,**kwargs)
    finally:
        depth[name]-=1
        phases=getattr(_request,'phases',None)
        if outer and phases is not None:phases[name]=phases.get(name,0.)+(time.perf_counter()-start)*1000
def server_timing(start,queued=None):
    total=max(0.,(time.perf_counter()-start)*1000);wait=getattr(_request,'lock_wait',0.)*1000
    cpu=max(0.,time.thread_time()-getattr(_request,'cpu_started',time.thread_time()))*1000
    now=time.perf_counter();pause=sum(max(0.,min(now,b)-max(queued if queued is not None else start,a))for a,b in tuple(_gc_events))*1000
    return f'app;dur={total:.2f}, lock;dur={wait:.2f}, work;dur={max(0.,total-wait):.2f}, cpu;dur={cpu:.2f}, gc;dur={getattr(_request,"gc_ms",0.):.2f}, gc_pause;dur={pause:.2f}'+''.join(f', {name};dur={duration:.2f}'for name,duration in getattr(_request,'phases',{}).items())
class TimedRLock:
    def __init__(self):self._lock=threading.RLock()
    def acquire(self,*args,**kwargs):
        start=time.perf_counter();ok=self._lock.acquire(*args,**kwargs)
        _request.lock_wait=getattr(_request,'lock_wait',0.)+time.perf_counter()-start
        return ok
    def release(self):return self._lock.release()
    def __enter__(self):self.acquire();return self
    def __exit__(self,*args):self.release()

class ScopedRLock:
    """同一项目的写入继续串行，不让其他项目一起等待。"""
    def __init__(self,scope):
        self.scope=scope;self.guard=threading.Lock();self.locks={};self.local=threading.local()
    def acquire(self,*args,**kwargs):
        lock=self.for_scope(self.scope())
        ok=lock.acquire(*args,**kwargs)
        if ok:
            if not hasattr(self.local,'stack'):self.local.stack=[]
            self.local.stack.append(lock)
        return ok
    def for_scope(self,identity):
        with self.guard:return self.locks.setdefault(identity,TimedRLock())
    def release(self):self.local.stack.pop().release()
    def __enter__(self):self.acquire();return self
    def __exit__(self,*args):self.release()


class ReadCache:
    """按输入版本复用展示结果；同一版本只准备一次，写入仍走原来的数据接口。"""
    def __init__(self,limit):
        from collections import OrderedDict
        self.limit=limit;self.rows=OrderedDict();self.guard=threading.Lock();self.pending={}
    def get(self,scope,version,build):
        key=(scope,version)
        while True:
            with self.guard:
                row=self.rows.get(scope)
                if row and row[0]==version:self.rows.move_to_end(scope);return row[1]
                event=self.pending.get(key)
                if event is None:
                    event=threading.Event();self.pending[key]=event;break
            timed_call('reuse_wait',event.wait)
        try:
            value=build()
            with self.guard:
                self.rows[scope]=(version,value);self.rows.move_to_end(scope)
                while len(self.rows)>self.limit:self.rows.popitem(last=False)
            return value
        finally:
            with self.guard:self.pending.pop(key,None);event.set()


from functools import lru_cache
import hashlib,re
@lru_cache(maxsize=128)
def _asset_digest(path,mtime,size):
    from pathlib import Path
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]

def asset_digest(path):
    stat=path.stat()
    return _asset_digest(str(path),stat.st_mtime_ns,stat.st_size)

def version_html(data,folder):
    # 页面引用随实际文件内容更新；旧页面链接仍可用，但不缓存旧版本号。
    def replace(match):
        name=match[2];relative=name.split('?',1)[0].lstrip('/')
        target=folder/('math-text.js' if relative=='vendor/math-text.js' else relative)
        if not target.is_file():return match[0]
        return match[1]+name.split('?',1)[0]+'?v='+asset_digest(target)+match[3]
    return re.sub(r'((?:src|href)=["\'])(/[^"\']+\.(?:js|css)(?:\?[^"\']*)?)(["\'])',replace,data)
