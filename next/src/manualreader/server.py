"""Local STEP-01/02 API. All imported projects are read-only planning inputs."""
from argparse import ArgumentParser
from hashlib import sha256
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit
from zipfile import ZipFile,ZIP_DEFLATED

from .stages.step01.lifecycle import Conflict
from .store import Project

VERSION='ManualReader-Next/0.2.2'
SDK_FILES=('__init__.py','client.py','planning.py','transport.py','stages/__init__.py',
           'stages/step02/__init__.py','stages/step02/card.py','stages/step02/renderer.py','stages/step02/read_tool.py',
           'stages/step02/repair.py',
           *('stages/step02/reader_step02_'+name for name in ('normal.txt','intro.txt','finale.txt','answer.txt','policies.json')))


def sdk():
    root=Path(__file__).resolve().parent
    records={}
    data=BytesIO()
    with ZipFile(data,'w',compression=ZIP_DEFLATED) as z:
        for name in SDK_FILES:
            path='manualreader/'+name
            b=(root/name).read_bytes()
            records[path]={'sha256':sha256(b).hexdigest(),'bytes':len(b)}
            z.writestr(path,b)
        z.writestr('client-hashes.json',json.dumps(records,indent=2))
    return data.getvalue(),records


def handler(data,key):
    package,hashes=sdk()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,fmt,*values):
            super().log_message(fmt,*values)
        def send(self,value,status=200,kind='application/json; charset=utf-8'):
            raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode('utf8')
            self.send_response(status)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(raw)
        def authorized(self):
            return secrets.compare_digest(self.headers.get('Authorization',''),'Reader '+key)
        def do_GET(self):
            path=urlsplit(self.path).path
            if path=='/health':
                return self.send({'version':VERSION,'step02':True,'formal_writeback':False,
                                  'service':'isolated','client_files':len(hashes)})
            if path=='/v1/client':
                return self.send(package,kind='application/zip')
            if path=='/v1/client-hashes':
                return self.send(hashes)
            if path=='/':
                return self.send(('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1"><title>ManualReader Next</title>'
                    '<style>body{max-width:46rem;margin:12vh auto;padding:0 1.5rem;font:17px/1.75 system-ui;'
                    'background:#191d26;color:#f4f3ef}a{color:#a8d8fa}small{color:#b9c2ce}</style>'
                    '<small>ManualReader Next · 隔离验收环境</small><h1>四种规划使用同一套原件</h1>'
                    '<p>你可以用独立客户端下载任务卡。普通章节、导读、阶段性收束和独立问答都会在规划前后核对文件版本。</p>'
                    '<p><a href="/v1/client">下载独立客户端</a>　<a href="/health">查看服务版本</a></p>'
                    '<p>这里保存规划结果的运行文件，正式 Reader 原件与学习记录不受影响。</p></html>').encode('utf8'),
                    kind='text/html; charset=utf-8')
            return self.send({'error':'没有这个入口'},404)
        def do_POST(self):
            if self.path not in ('/v1/step01','/v1/step02'):
                return self.send({'error':'没有这个入口'},404)
            if not self.authorized():
                return self.send({'error':'请使用隔离服务的访问密钥'},403)
            try:
                count=int(self.headers.get('Content-Length','0'))
                if not 0<count<=128000:
                    raise ValueError('请求大小不合法')
                packet=json.loads(self.rfile.read(count))
                if not isinstance(packet,dict):
                    raise ValueError('请求应为 JSON 对象')
                project=Project(data,packet.get('project_id'))
                if self.path=='/v1/step01':
                    value=project.lifecycle(packet)
                elif packet.get('action')=='context':
                    value=project.context(packet)
                else:
                    value=project.original(packet)
                return self.send(value)
            except Conflict as exc:
                return self.send({'error':exc.code,'affected':exc.affected},409)
            except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
                return self.send({'error':str(error)},400)
    return Handler


def main():
    p=ArgumentParser()
    p.add_argument('--data',required=True,type=Path)
    p.add_argument('--port',type=int,default=0)
    p.add_argument('--token-file',required=True,type=Path)
    p.add_argument('--ready-file',type=Path)
    opts=p.parse_args()
    if not opts.token_file.exists():
        opts.token_file.parent.mkdir(parents=True,exist_ok=True)
        try:
            with opts.token_file.open('x',encoding='utf8') as f:
                f.write(secrets.token_urlsafe(32))
        except FileExistsError:
            pass
    key=opts.token_file.read_text('utf8').strip()
    if len(key)<24:
        raise ValueError('访问密钥未正确生成')
    h=ThreadingHTTPServer(('127.0.0.1',opts.port),handler(opts.data,key))
    url=f'http://127.0.0.1:{h.server_port}'
    if opts.ready_file:
        opts.ready_file.write_text(json.dumps({'version':VERSION,'base':url},indent=2)+'\n',encoding='utf8')
    print(VERSION,url,flush=True)
    h.serve_forever()


if __name__=='__main__':
    main()
