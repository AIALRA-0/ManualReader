"""Local STEP-04 service with an authenticated draft library."""

from argparse import ArgumentParser
from base64 import b64decode, b64encode
from hashlib import sha256
from html import escape
from http.cookies import SimpleCookie, CookieError
from http.server import ThreadingHTTPServer
from importlib.resources import files
from io import BytesIO
import json
from pathlib import Path
import re
import secrets
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile, ZIP_DEFLATED

from . import step03_server
from .stages.step03.engine import Engine
from .stages.step04.jobs import Jobs
from .store import Project


VERSION = 'ManualReader-Next/0.4.0'
LIBRARY_ROUTE = '/step04'
COOKIE = 'manualreader_step04'


def client_archive():
    original, hashes = step03_server.client_archive()
    output = BytesIO()
    with ZipFile(BytesIO(original)) as base, ZipFile(output,'w',compression=ZIP_DEFLATED) as target:
        for name in hashes:
            target.writestr(name,base.read(name))
        extension = Path(__file__).with_name('step04_client.py').read_bytes()
        name = 'manualreader/step04_client.py'
        target.writestr(name,extension)
        hashes[name] = {'sha256':sha256(extension).hexdigest(),'bytes':len(extension)}
        target.writestr('client-hashes.json',json.dumps(hashes,indent=2))
    return output.getvalue(),hashes


def page(title, body):
    return (f'<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(title)}</title><style>'
            ':root{color-scheme:light dark}body{max-width:55rem;margin:3rem auto;padding:0 1.25rem;'
            'font:17px/1.7 system-ui}pre,article{overflow-wrap:anywhere;white-space:pre-wrap}'
            'nav{margin:1.5rem 0}a{color:LinkText}form{display:flex;gap:.6rem;flex-wrap:wrap}'
            'button,input{font:inherit;padding:.3em .65em}img{max-width:100%}'
            'article.rendered{white-space:normal}article p{margin:1em 0}pre{padding:.75em;'
            'border:1px solid GrayText;border-radius:.35em}table{border-collapse:collapse}'
            'td,th{border:1px solid GrayText;padding:.35em .6em}</style>'
            f'<h1>{escape(title)}</h1>{body}</html>').encode('utf8')


def handler(data,key,engine,jobs):
    previous = step03_server.handler(data,key,engine)
    archive,hashes = client_archive()
    session = secrets.token_urlsafe(32)

    class Handler(previous):
        def html(self, title, body, status=200):
            return self.send(page(title,body),status,kind='text/html; charset=utf-8')

        def browser_authorized(self):
            if self.authorized():
                return True
            try:
                cookie = SimpleCookie(self.headers.get('Cookie',''))
                stored = cookie.get(COOKIE)
                return bool(stored and secrets.compare_digest(stored.value,session))
            except CookieError:
                return False

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/health':
                return self.send({'version':VERSION,'step02':True,'step03':True,'step04':True,
                                  'formal_writeback':False,'service':'isolated','client_files':len(hashes),
                                  'peak_writers':jobs.peak})
            if path == '/v1/client':
                return self.send(archive,kind='application/zip')
            if path == '/v1/client-hashes':
                return self.send(hashes)
            if path in ('/step04/markdown-vendor.js','/step04/preview.js'):
                return self.send(files('manualreader.stages.step04').joinpath(path.rsplit('/',1)[1]).read_bytes(),
                                 kind='application/javascript; charset=utf-8')
            if path != LIBRARY_ROUTE and not path.startswith(LIBRARY_ROUTE+'/'):
                return super().do_GET()
            if not self.browser_authorized():
                return self.html('打开 STEP-04 草稿',
                    '<p>输入隔离服务的访问密钥后，可以查看首稿和下载原始 Markdown。</p>'
                    '<form method="post" action="/step04/login"><label>访问密钥 '
                    '<input name="token" type="password" required></label><button>打开草稿</button></form>')
            parts = path.rstrip('/').split('/')
            if len(parts)==2:
                rows = []
                for folder in sorted(jobs.root.iterdir()):
                    try:
                        run = jobs.status(folder.name)
                        card = json.loads((folder/'task-card.json').read_bytes())
                    except (ValueError,OSError):
                        continue
                    title = card['question']['text'] if run['mode']=='ANSWER' else card['chapter']['title']
                    rows.append(f'<li><a href="/step04/{folder.name}">{escape(title)}</a> '
                                f'<small>{escape(run["mode"])} · {escape(run["status"])}</small></li>')
                return self.html('STEP-04 首稿',f'<ol>{"".join(rows)}</ol><a href="/v1/client">下载客户端</a>')
            try:
                run = jobs.status(parts[2])
                folder = jobs.folder(parts[2])
                name = run.get('output_file')
                if len(parts)==5 and parts[3]=='assets' and re.fullmatch(r'[a-f0-9]{64}\.png',parts[4]):
                    return self.send((folder/'assets'/parts[4]).read_bytes(),kind='image/png')
                if len(parts)==4 and parts[3]=='download' and name:
                    return self.send((folder/name).read_bytes(),kind='text/markdown; charset=utf-8')
                if len(parts)!=3:
                    return self.send({'error':'没有这个草稿资源'},404)
                card = json.loads((folder/'task-card.json').read_bytes())
                title = card['question']['text'] if run['mode']=='ANSWER' else card['chapter']['title']
                body = (f'<nav><a href="/step04">返回首稿列表</a> · {escape(run["status"])}'
                        + (' · <a href="'+path+'/download">打开原始 Markdown</a>' if name else '')+'</nav>')
                if run['fixture_based']:
                    body += '<p>这份收束首稿使用隔离测试构造的历史讲解。</p>'
                if name:
                    body += f'<article id="draft">{escape((folder/name).read_text("utf8"))}</article>'
                    media = {r['path']:r['media_path'] for r in run.get('resource_references',[])
                             if r.get('available') and r.get('media_path')}
                    # Older transport receipts may already point directly at a verified local image.
                    for reference in run.get('resource_references',[]):
                        p = reference['path']
                        if reference.get('available') and re.fullmatch(r'assets/[a-f0-9]{64}\.png',p):
                            media.setdefault(p,p)
                    body += '<div hidden id="verified-media">'+escape(json.dumps(media))+'</div>'
                    body += '<script src="/step04/markdown-vendor.js"></script><script src="/step04/preview.js"></script>'
                elif run.get('error'):
                    body += f'<p>{escape(run["error"])}</p>'
                return self.html(title,body)
            except (ValueError,OSError,KeyError):
                return self.send({'error':'没有这个草稿或资源'},404)

        def do_POST(self):
            if self.path == '/step04/login':
                n = int(self.headers.get('Content-Length','0'))
                value = parse_qs(self.rfile.read(n).decode('utf8')).get('token',[''])[0] if 0<n<4096 else ''
                if not secrets.compare_digest(value.encode('utf8'),key.encode('utf8')):
                    return self.html('访问密钥不匹配','<a href="/step04">返回</a>',403)
                self.send_response(303)
                self.send_header('Location','/step04')
                self.send_header('Set-Cookie',f'{COOKIE}={session}; Path=/step04; HttpOnly; SameSite=Strict')
                self.send_header('Content-Length','0')
                self.end_headers()
                return
            if self.path != '/v1/step04':
                return super().do_POST()
            if not self.authorized():
                return self.send({'error':'请使用隔离服务的访问密钥'},403)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=4_000_000:
                    raise ValueError('Writer 请求大小无效')
                req=json.loads(self.rfile.read(size))
                project = Project(data,req['project_id'])
                action = req['action']
                if action=='submit':
                    value = jobs.submit(project,req['round_id'],req['mode'],
                                        b64decode(req['task_card'],validate=True),
                                        req.get('explicit_finale') is True,
                                        req.get('revise_existing_answer') is True,
                                        req.get('resume_before_dispatch') is True)
                    return self.send(value,202)
                run=jobs.status(req['task_id'],project.identity)
                if action=='status':
                    return self.send(run)
                if action=='download':
                    folder=jobs.folder(req['task_id'])
                    return self.send({'run':run,'markdown':b64encode((folder/run['output_file']).read_bytes()).decode()})
                raise ValueError('Writer 支持提交、查看状态和下载首稿')
            except (ValueError,OSError,TypeError,KeyError,RuntimeError) as error:
                return self.send({'error':str(error)},400)
    return Handler


def main():
    p=ArgumentParser()
    p.add_argument('--data',required=True,type=Path)
    p.add_argument('--cache',required=True,type=Path)
    p.add_argument('--runs',required=True,type=Path)
    p.add_argument('--token-file',required=True,type=Path)
    p.add_argument('--port',type=int,default=0)
    p.add_argument('--workers',type=int,default=6)
    p.add_argument('--writers',type=int,default=8)
    p.add_argument('--ready-file',type=Path)
    opts=p.parse_args()
    if opts.writers < 1 or not 1<=opts.workers<=16:
        p.error('写作并行数须为正整数，解析进程数可选 1 到 16')
    if not opts.token_file.exists():
        opts.token_file.parent.mkdir(parents=True,exist_ok=True)
        with opts.token_file.open('x',encoding='utf8') as stream:
            stream.write(secrets.token_urlsafe(32))
    key=opts.token_file.read_text('utf8').strip()
    if len(key)<24:
        p.error('访问密钥未正确生成')
    engine=Engine(opts.cache,opts.workers)
    jobs=Jobs(opts.runs,engine,opts.writers)
    try:
        with ThreadingHTTPServer(('127.0.0.1',opts.port),handler(opts.data,key,engine,jobs)) as http:
            url=f'http://127.0.0.1:{http.server_port}'
            if opts.ready_file:
                opts.ready_file.write_text(json.dumps({'version':VERSION,'base':url})+'\n',encoding='utf8')
            print(VERSION,url,flush=True)
            http.serve_forever()
    finally:
        jobs.pool.shutdown()
        engine.close()


if __name__=='__main__':
    main()
