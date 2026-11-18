"""用系统文件事件唤醒当前项目的浏览器；空闲时不扫描目录。"""
from pathlib import Path
from collections import deque
from contextlib import contextmanager
import json,threading,time
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

class FileEvents:
    def __init__(self):
        self.lock=threading.RLock();self.channels={};self.observer=None

    @contextmanager
    def subscribe(self,root):
        key=str(Path(root).resolve())
        with self.lock:
            if self.observer is None:self.observer=Observer();self.observer.start()
            channel=self.channels.get(key)
            if channel is None:
                channel={'condition':threading.Condition(),'version':0,'events':deque(maxlen=512),'clients':0}
                hub=self
                class Handler(FileSystemEventHandler):
                    def on_any_event(self,event):
                        if event.event_type not in ('created','deleted','modified','moved'):return
                        paths=[]
                        for name in (event.src_path,getattr(event,'dest_path','')):
                            if not name:continue
                            try:value=Path(name).relative_to(key).as_posix()
                            except ValueError:continue
                            if value=='.':value=''
                            if any(p.startswith('.reader-write-')for p in Path(value).parts):continue
                            paths.append(value)
                        if paths:hub.publish(key,{'type':event.event_type,'paths':paths,'directory':event.is_directory})
                channel['watch']=self.observer.schedule(Handler(),key,recursive=True);self.channels[key]=channel
            channel['clients']+=1
        try:yield channel
        finally:
            with self.lock:
                channel['clients']-=1
                if not channel['clients']:
                    self.observer.unschedule(channel['watch']);self.channels.pop(key,None)

    def publish(self,root,event):
        channel=self.channels.get(str(Path(root).resolve()))
        if channel:
            with channel['condition']:
                channel['version']+=1;channel['events'].append({'version':channel['version'],**event});channel['condition'].notify_all()

    def stream(self,handler,root,lifetime=240):
        with self.subscribe(root)as channel:
            handler.send_response(200);handler.send_header('Content-Type','text/event-stream; charset=utf-8');handler.send_header('Cache-Control','no-store');handler.send_header('X-Accel-Buffering','no');handler.end_headers()
            def send(data):handler.wfile.write(('data: '+json.dumps(data,ensure_ascii=False)+'\n\n').encode());handler.wfile.flush()
            try:
                with channel['condition']:version=channel['version']
                send({'type':'ready','version':version});end=time.monotonic()+lifetime
                while time.monotonic()<end:
                    with channel['condition']:
                        if channel['version']==version:channel['condition'].wait(timeout=15)
                        events=[e for e in channel['events']if e['version']>version];latest=channel['version']
                    if getattr(handler,'_grant',None):
                        from reader_remote import pairings
                        token=handler.headers.get('Authorization','')[7:]
                        if not pairings().authorize(token):send({'type':'revoked'});break
                    if events:
                        time.sleep(.08)
                        with channel['condition']:events=[e for e in channel['events']if e['version']>version];latest=channel['version']
                        send({'type':'changes','version':latest,'events':events});version=latest
                    else:handler.wfile.write(b': heartbeat\n\n');handler.wfile.flush()
            except (BrokenPipeError,ConnectionResetError,OSError):pass
            handler.close_connection=True

EVENTS=FileEvents()
