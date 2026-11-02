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
