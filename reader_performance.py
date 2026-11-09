"""用响应头区分服务端处理和锁等待，不记录材料或凭据。"""
import threading,time,gc
_request=threading.local()
def begin_request():_request.lock_wait=0.;_request.cpu_started=time.thread_time();_request.phases={};_request.gc_ms=0.

def _gc_timing(phase,info):
    if phase=='start':_request.gc_started=time.perf_counter()
    elif hasattr(_request,'gc_started'):
        _request.gc_ms=getattr(_request,'gc_ms',0.)+(time.perf_counter()-_request.gc_started)*1000;del _request.gc_started

gc.callbacks.append(_gc_timing)

def timed_call(name,function,*args,**kwargs):
    start=time.perf_counter()
    try:return function(*args,**kwargs)
    finally:
        phases=getattr(_request,'phases',None)
        if phases is not None:phases[name]=phases.get(name,0.)+(time.perf_counter()-start)*1000
def server_timing(start):
    total=max(0.,(time.perf_counter()-start)*1000);wait=getattr(_request,'lock_wait',0.)*1000
    cpu=max(0.,time.thread_time()-getattr(_request,'cpu_started',time.thread_time()))*1000
    return f'app;dur={total:.2f}, lock;dur={wait:.2f}, work;dur={max(0.,total-wait):.2f}, cpu;dur={cpu:.2f}, gc;dur={getattr(_request,"gc_ms",0.):.2f}'+''.join(f', {name};dur={duration:.2f}'for name,duration in getattr(_request,'phases',{}).items())
class TimedRLock:
    def __init__(self):self._lock=threading.RLock()
    def acquire(self,*args,**kwargs):
        start=time.perf_counter();ok=self._lock.acquire(*args,**kwargs)
        _request.lock_wait=getattr(_request,'lock_wait',0.)+time.perf_counter()-start
        return ok
    def release(self):return self._lock.release()
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
