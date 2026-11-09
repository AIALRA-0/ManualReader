"""页面请求独立运行；写入和重处理转给原工作进程，接口与项目授权保持原样。"""
import http.client,os,time
from urllib.parse import urlsplit,parse_qs

_HOP={'connection','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','transfer-encoding','upgrade'}

def needs_worker(method,url):
    path=urlsplit(url).path
    if path.startswith('/machine/'):return True
    if method=='POST':return True
    if not path.startswith('/api/'):return False
    if path in('/api/session','/api/material','/api/progress','/api/settings','/api/speech/status','/api/pairings'):return False
    query=parse_qs(urlsplit(url).query)
    if path=='/api/library/tree'and query.get('view')==['names']:return False
    if path=='/api/coverage'and query.get('summary')==['true']:return False
    return True

def forward(handler,port):
    try:size=int(handler.headers.get('Content-Length','0'))
    except ValueError:return handler.json({'error':'请求长度不正确'},400)
    if not 0<=size<=64_000_000:return handler.json({'error':'请求内容过大'},400)
    body=handler.rfile.read(size)if size else None
    headers={k:v for k,v in handler.headers.items()if k.lower()not in _HOP|{'content-length'}}
    headers['Content-Length']=str(size);headers['Connection']='close'
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
    started=time.perf_counter();sent=False
    try:
        connection.connect();connection.sock.settimeout(None)
        connection.request(handler.command,handler.path,body=body,headers=headers)
        response=connection.getresponse();handler._forwarded_response=True
        handler.send_response(response.status,response.reason)
        for key,value in response.getheaders():
            if key.lower()not in _HOP|{'server','date'}:handler.send_header(key,value)
        handler.send_header('Server-Timing',f'web_proxy;dur={(time.perf_counter()-started)*1000:.2f}')
        handler.end_headers();sent=True
        while True:
            chunk=response.read1(65536)
            if not chunk:break
            handler.wfile.write(chunk);handler.wfile.flush()
    except (BrokenPipeError,ConnectionResetError):pass
    except (OSError,http.client.HTTPException):
        if not sent:
            handler._forwarded_response=False
            handler.json({'error':'后台连接正在恢复，已保存内容仍然保留，请稍后重试','code':'reader_worker_unavailable'},502)
    finally:connection.close()
    return True

def use_worker(handler,method):
    if os.environ.get('MANUAL_READER_ROLE')!='web'or not needs_worker(method,handler.path):return False
    try:port=int(os.environ['MANUAL_READER_WORK_PORT'])
    except (KeyError,ValueError):raise ValueError('页面进程需要配置本机工作端口')
    if not 1<=port<=65535 or port==handler.server.server_port:raise ValueError('工作端口需要与页面端口分开')
    forward(handler,port);return True
