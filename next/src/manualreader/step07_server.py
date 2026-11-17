"""Serve the isolated STEP-07 preview and downloadable reversible output."""

import argparse
from hashlib import sha256
from html import escape
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from io import BytesIO
import json
import mimetypes
from pathlib import Path
import re
import secrets
from urllib.parse import parse_qs, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from .step04_server import page
from .step56_server import sdk as upstream_sdk
from manualreader.artifacts import read_json as read, write_json as write
from .stages.step07.resources import Media, verify_image


VERSION='ManualReader-Next/0.7.1'
COOKIE='manualreader_step07'
SAMPLE=re.compile(r'[a-z0-9-]+\Z')
ARTIFACTS=('run.json','body.fused.md','fusion-edits.json','anchor-remap.json',
           'resources.fused.json','deferred.json')
STAGE=files('manualreader.stages.step07')
VENDOR=STAGE.joinpath('vendor')


def sdk():
    old,hashes=upstream_sdk()
    output=BytesIO()
    with ZipFile(BytesIO(old)) as base, ZipFile(output,'w',ZIP_DEFLATED) as added:
        for name in hashes:
            added.writestr(name,base.read(name))
        name='manualreader/step07_client.py'
        raw=Path(__file__).with_name('step07_client.py').read_bytes()
        added.writestr(name,raw)
        hashes[name]={'sha256':sha256(raw).hexdigest(),'bytes':len(raw)}
        added.writestr('client-hashes.json',json.dumps(hashes,indent=2))
    return output.getvalue(),hashes


def downloads(root, sample):
    folder=root/'jobs'/sample
    source=root/'inputs'/sample
    draft=read(source/'step04-run.json')['output_file']
    output=BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as package:
        for filename in ARTIFACTS:
            package.write(folder/filename,filename)
        package.write(source/draft,'original/'+draft)
        package.write(source/'task-card.json','original/task-card.json')
        for entry in ('outcome.json','raw-response.txt'):
            if (folder/'model'/entry).is_file():
                package.write(folder/'model'/entry,'model/'+entry)
    return output.getvalue()


def tag_data(name, data):
    return f'<div hidden id="{name}">{escape(json.dumps(data,ensure_ascii=False))}</div>'


def links(keys, label='跳到讲解'):
    return ' '.join(f'<a href="#{escape(k,quote=True)}">{escape(label)} {i}</a>' for i,k in enumerate(keys,1))


def render(root, sample):
    folder=root/'jobs'/sample
    source=root/'inputs'/sample
    draft=read(source/'step04-run.json')
    card=read(source/'task-card.json')
    evidence=read(source/'source-index.json')
    report=read(folder/'run.json')
    remap=read(folder/'anchor-remap.json')
    deferred=read(folder/'deferred.json')
    resources=read(folder/'resources.fused.json')['items']
    body=(folder/'body.fused.md').read_bytes().decode('utf8')
    title=(card.get('chapter') or {}).get('title') or (card.get('question') or {}).get('text') or sample
    media={r['media_path']:r['verified_media_path'] for r in resources if r.get('verified_media_path')}
    lines={}
    for identifier, row in remap['anchors'].items():
        if row['surface']=='body':
            lines[identifier]=len(re.findall(r'\r\n|\n|\r',body[:row['start']]))
    mapping=remap['mapping']
    coverage=remap['goal_question_coverage']
    mode=escape(draft['mode'])
    fixture=' · 历史首稿测试案例' if draft['fixture_based'] else ''
    content=(f'<nav><a href="/step07">所有任务</a> · {mode}{fixture} · '
             f'<a href="/step07/{sample}/download">下载这篇</a></nav>')
    if report['state'] not in ('complete','skipped_no_overlay'):
        content+='<p>显示保留原补丁的预览。具体失败见下载包中的运行记录。</p>'
    content+=('<h2>融合预览</h2><article id="draft" data-markdown>'+escape(body)+'</article>')
    if deferred:
        content+='<h2>延伸补充</h2>'
        for identifier, row in deferred.items():
            label='查看延伸补充' if row['status']=='defer' else '查看补充原文和缺失附件记录'
            content+=(f'<details id="{escape(identifier,quote=True)}"><summary>{label}</summary>'
                      '<article data-markdown>'+escape(row['markdown'])+'</article></details>')

    reverse=mapping['explanation_to_source']
    back={ident:[{'sid':sid,'navigation':sid in mapping.get('navigation_only_source_ids',[])}
                 for sid in source_ids] for ident,source_ids in reverse.items()}
    content+='<details id="coverage"><summary>查看知识目标与子问</summary><ul>'
    for item in coverage['goal_items']+coverage['question_items']:
        label={'reader':'读者','teacher':'教师'}.get(item.get('role'),'子问')
        topic=item.get('topic',item.get('question_quote',''))
        content+=f'<li>{label} · {escape(topic)} {links(item["target_ids"])}</li>'
    content+='</ul></details>'
    if coverage['question_raw_units']:
        content+='<details id="question-fragments"><summary>查看原话片段</summary><ul>'
        for item in coverage['question_raw_units']:
            label='跳到相邻回答（仅导航）' if item.get('navigation_only') else '跳到回答'
            content+=f'<li>{escape(item["text"])} {links(item["target_ids"],label)}</li>'
        content+='</ul></details>'
    content+='<details id="sources"><summary>查看原文与对应讲解</summary>'
    navigation=set(mapping.get('navigation_only_source_ids',[]))
    media_store=Media(root/'media',draft,evidence)
    for row in evidence['sources']:
        sid=row['source_id']
        targets=mapping['source_to_explanation'].get(sid,[])
        title_link='仅导航' if sid in navigation else '跳到讲解'
        content+=(f'<details id="{escape(sid,quote=True)}"><summary>{escape(sid)}</summary>'
                  f'<p>{links(targets,title_link)}</p><pre>{escape(row["text"])}</pre>')
        for ref in row['asset_refs']:
            item=evidence['assets'].get(ref,{})
            if not item.get('sha256'):
                continue
            verified=media_store.resolve(f'assets/{item["sha256"]}.png')
            if path:=verified.get('verified_media_path'):
                content+=f'<p><a href="/step07/{sample}/{path}">打开原始资料图</a></p>'
        content+='</details>'
    content+='</details>'
    content+=tag_data('verified-media',media)+tag_data('body-anchors',lines)+tag_data('source-backlinks',back)
    content+=('<link rel="stylesheet" href="/step07/vendor/katex/katex.min.css">'
              '<script src="/step07/markdown-vendor.js"></script>'
              '<script src="/step07/vendor/katex/katex.min.js"></script>'
              '<script src="/step07/preview.js"></script>')
    return title,content


def handler(root, key):
    package, hashes=sdk()
    cookie_key=secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def send(self,value,status=200,kind='application/json; charset=utf-8'):
            raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(raw)

        def html(self,title,content,status=200):
            return self.send(page(title,content),status,'text/html; charset=utf-8')

        def authenticated(self):
            if secrets.compare_digest(self.headers.get('Authorization',''),'Reader '+key):
                return True
            try:
                token=SimpleCookie(self.headers.get('Cookie','')).get(COOKIE)
                return bool(token and secrets.compare_digest(token.value,cookie_key))
            except CookieError:
                return False

        def samples(self):
            return sorted(p.parent.name for p in (root/'jobs').glob('*/run.json'))

        def do_GET(self):
            path=urlsplit(self.path).path.rstrip('/')
            if path=='/health':
                return self.send({'version':VERSION,'stage':'07','formal_writeback':False,
                                  'samples':len(self.samples())})
            if path=='/v1/client':
                return self.send(package,kind='application/zip')
            if path=='/v1/client-hashes':
                return self.send(hashes)
            if path in ('/step07/markdown-vendor.js','/step07/preview.js'):
                filename=path.rsplit('/',1)[1]
                location=(files('manualreader.stages.step04') if filename=='markdown-vendor.js' else STAGE)
                return self.send(location.joinpath(filename).read_bytes(),kind='application/javascript')
            if path.startswith('/step07/vendor/'):
                relative=path.removeprefix('/step07/vendor/')
                if not re.fullmatch(r'mermaid\.min\.js|katex/katex\.min\.(?:js|css)|katex/fonts/[A-Za-z0-9_.-]+',relative):
                    return self.send({'error':'未知组件'},404)
                resource=VENDOR.joinpath(*relative.split('/'))
                if not resource.is_file():
                    return self.send({'error':'组件文件缺失'},404)
                return self.send(resource.read_bytes(),kind=mimetypes.guess_type(relative)[0] or 'application/octet-stream')
            if not self.authenticated():
                if path in ('','/step07') or path.startswith('/step07/'):
                    return self.html('打开 STEP-07 融合预览',
                        '<form action="/step07/login" method="post"><label>访问密钥 '
                        '<input required name="token" type="password"></label><button>打开结果</button></form>')
                return self.send({'error':'需要访问密钥'},401)
            if path in ('','/step07'):
                rows=[]
                for sample in self.samples():
                    run=read(root/'jobs'/sample/'run.json')
                    rows.append(f'<li><a href="/step07/{sample}">{escape(sample)}</a> · {escape(run["mode"])}</li>')
                return self.html('STEP-07 融合预览','<ul>'+''.join(rows)+'</ul>')
            match=re.fullmatch(r'/step07/([a-z0-9-]+)(?:/([^/]+)(?:/([^/]+))?)?',path)
            if not match or match[1] not in self.samples():
                return self.send({'error':'找不到融合任务'},404)
            sample,action,filename=match.groups()
            if action=='assets' and filename and re.fullmatch(r'[a-f0-9]{64}\.png',filename):
                item=root/'media'/filename
                if verify_image(item):
                    return self.send(item.read_bytes(),kind='image/png')
            if not action:
                return self.html(*render(root,sample))
            if action=='download' and not filename:
                return self.send(downloads(root,sample),kind='application/zip')
            return self.send({'error':'找不到资源'},404)

        def do_POST(self):
            path=urlsplit(self.path).path
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<65536:
                return self.send({'error':'请求大小不合法'},400)
            if path=='/step07/login':
                form=parse_qs(self.rfile.read(length).decode())
                if not secrets.compare_digest(form.get('token',[''])[0],key):
                    return self.html('访问密钥不正确','<a href="/step07">重新输入</a>',403)
                self.send_response(303)
                self.send_header('Set-Cookie',f'{COOKIE}={cookie_key}; Path=/step07; HttpOnly; SameSite=Strict')
                self.send_header('Location','/step07')
                self.end_headers()
                return
            if path!='/v1/step07' or not self.authenticated():
                return self.send({'error':'接口不可用或访问密钥不正确'},401)
            try:
                request=json.loads(self.rfile.read(length))
            except ValueError:
                return self.send({'error':'请求格式不正确'},400)
            action=request.get('action')
            if action=='list':
                return self.send({'samples':self.samples()})
            sample=request.get('sample_id')
            if not isinstance(sample,str) or not SAMPLE.fullmatch(sample) or sample not in self.samples():
                return self.send({'error':'找不到融合任务'},404)
            if action=='status':
                return self.send(read(root/'jobs'/sample/'run.json'))
            return self.send({'error':'此接口只读取已生成的结果'},400)
    return Handler


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--port',type=int,default=0)
    args=parser.parse_args()
    root=args.root.resolve()
    keyfile=root/'token.txt'
    if not keyfile.exists():
        with keyfile.open('x',encoding='utf8') as handle:
            handle.write(secrets.token_urlsafe(32))
    key=keyfile.read_text('utf8').strip()
    if not key:
        parser.error('隔离服务的访问密钥不能为空')
    with ThreadingHTTPServer(('127.0.0.1',args.port),handler(root,key)) as server:
        address=f'http://127.0.0.1:{server.server_port}'
        write(root/'service-ready.json',{'version':VERSION,'base':address})
        print(address,flush=True)
        server.serve_forever()


if __name__=='__main__':
    main()
