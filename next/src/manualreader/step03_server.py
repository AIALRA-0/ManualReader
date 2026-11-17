"""Serve STEP-03 next to the unchanged STEP-01 / STEP-02 routes."""

from argparse import ArgumentParser
from hashlib import sha256
from html import escape
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from . import server
from .stages.step03.engine import Engine, VersionChanged, flat
from .store import Project

VERSION='ManualReader-Next/0.3.0'


def client_archive():
    base,expected=server.sdk()
    extension=Path(__file__).with_name('step03_client.py').read_bytes()
    out=BytesIO()
    with ZipFile(BytesIO(base)) as original, ZipFile(out,'w',compression=ZIP_DEFLATED) as combined:
        for file in expected:
            combined.writestr(file,original.read(file))
        name='manualreader/step03_client.py'
        combined.writestr(name,extension)
        expected[name]={'sha256':sha256(extension).hexdigest(),'bytes':len(extension)}
        combined.writestr('client-hashes.json',json.dumps(expected,indent=2))
    return out.getvalue(),expected


def preview(manifest, illustrations):
    title=escape(manifest['relative_path'])
    content=[]
    for asset,source in illustrations:
        point=escape(json.dumps(asset['locator'],ensure_ascii=False))
        content.append(f'<figure id="{escape(asset["asset_id"])}"><img src="{source}" alt="原件裁图">'
                       f'<figcaption>{escape(asset["kind"])} <code>{point}</code></figcaption></figure>')
    paragraphs=[]
    for asset in flat(manifest)[:300]:
        if asset.get('text') or asset.get('reason'):
            caption=escape(asset.get('text') or asset['reason'])
            pos=escape(json.dumps(asset['locator'],ensure_ascii=False))
            paragraphs.append(f'<li><code>{pos}</code><p>{caption}</p></li>')
    if manifest.get('pages'):
        navigation=' '.join(f'<li>第 {p["number"]} 页</li>' for p in manifest['pages'])
    else:
        navigation=''
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>STEP-03 原件预览</title>'
            '<style>body{max-width:1080px;margin:auto;padding:2rem;background:#111923;color:#e9f2ff;'
            'font:16px/1.6 system-ui}figure{background:#202d3c;padding:1rem}img{max-width:100%;display:block}'
            'code{font-size:.78rem;overflow-wrap:anywhere}li p{white-space:pre-wrap}</style>'
            f'<h1>{title}</h1><p>原件 SHA-256 <code>{manifest["content_sha256"]}</code></p>'
            f'<ul>{navigation}</ul>{"".join(content)}<h2>原文位置</h2><ul>{"".join(paragraphs)}</ul></html>')


def handler(data,key,engine):
    previous=server.handler(data,key)
    bundle,hashes=client_archive()
    class Handler(previous):
        def do_GET(self):
            name=urlsplit(self.path).path
            if name=='/health':
                return self.send({'version':VERSION,'step02':True,'step03':True,'formal_writeback':False,
                                  'service':'isolated','client_files':len(hashes)})
            if name=='/v1/client':
                return self.send(bundle,kind='application/zip')
            if name=='/v1/client-hashes':
                return self.send(hashes)
            if name=='/step03':
                return self.send(('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                    '<title>STEP-03 原件读取</title><h1>STEP-03 原件读取</h1>'
                    '<p>下载客户端后，使用当前项目和轮次查看原件清单或导出本机原件预览。</p>'
                    '<p><a href="/v1/client">下载客户端</a>　<a href="/health">查看服务状态</a></p></html>').encode(),
                    kind='text/html; charset=utf-8')
            return super().do_GET()
        def do_POST(self):
            if self.path!='/v1/step03':
                return super().do_POST()
            if not self.authorized():
                return self.send({'error':'请使用隔离服务的访问密钥'},403)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=4_000_000:
                    raise ValueError('原件读取请求大小无效')
                req=json.loads(self.rfile.read(size))
                project=Project(data,req['project_id'])
                rid=req['round_id']
                action=req.get('action')
                fid=req.get('file_id')
                if action=='inspect':
                    return self.send(engine.inspect(project,rid,fid,req.get('pages')))
                if action=='warm':
                    return self.send({'submitted':engine.warm(project,rid,fid)},202)
                if action=='asset':
                    value=engine.consume(project,rid,fid,req['asset_id'],req['capability'],
                                         req.get('page'),req.get('source_id'))
                    return self.send(value)
                if action=='ocr':
                    from .stages.step03.ocr import scan_text
                    value=engine.consume(project,rid,fid,req['asset_id'],'inspect_visual',req.get('page'))
                    with engine.ocr_slots:
                        return self.send(scan_text(value))
                if action=='runtime':
                    return self.send({'peak_file_parsers':engine.peak})
                if action=='locate':
                    return self.send(engine.exact_locator(project,rid,fid,req['source_id'],req['quote'],req.get('page')))
                if action=='bind':
                    return self.send(engine.binding(project,rid,req['task_card']))
                if action=='preview':
                    doc=engine.inspect(project,rid,fid,req.get('pages'))
                    figures=[]
                    unread=[]
                    for a in [a for a in flat(doc) if 'inspect_visual' in a['capabilities']][:8]:
                        try:
                            block=engine.consume(project,rid,fid,a['asset_id'],'inspect_visual',
                                                 a['locator']['number'] if doc['type']=='pdf' else None)
                        except (OSError,ValueError,RuntimeError):
                            unread.append(a['asset_id'])
                            continue
                        figures.append((a,block['content']['image_url']))
                    return self.send({'html':preview(doc,figures),'file_sha256':doc['content_sha256'],
                                      'image_count':len(figures),'unread_asset_ids':unread})
                raise ValueError('原件读取请求未指定可用的操作')
            except VersionChanged as error:
                return self.send({'error':str(error),'code':'version_conflict'},409)
            except (OSError,ValueError,TypeError,KeyError,RuntimeError) as error:
                return self.send({'error':str(error)},400)
    return Handler


def main():
    cli=ArgumentParser()
    cli.add_argument('--data',required=True,type=Path)
    cli.add_argument('--cache',required=True,type=Path)
    cli.add_argument('--token-file',required=True,type=Path)
    cli.add_argument('--port',type=int,default=0)
    cli.add_argument('--workers',type=int,default=6)
    cli.add_argument('--ready-file',type=Path)
    opts=cli.parse_args()
    if not 1<=opts.workers<=16:
        cli.error('请选择 1 到 16 个解析进程')
    if not opts.token_file.exists():
        opts.token_file.parent.mkdir(parents=True,exist_ok=True)
        with opts.token_file.open('x',encoding='utf8') as f:
            f.write(secrets.token_urlsafe(32))
    key=opts.token_file.read_text('utf8').strip()
    if len(key)<24:
        cli.error('访问密钥未正确生成')
    engine=Engine(opts.cache,opts.workers)
    with ThreadingHTTPServer(('127.0.0.1',opts.port),handler(opts.data,key,engine)) as http:
        url=f'http://127.0.0.1:{http.server_port}'
        if opts.ready_file:
            opts.ready_file.write_text(json.dumps({'version':VERSION,'base':url},indent=2)+'\n',encoding='utf8')
        print(VERSION,url,flush=True)
        try:
            http.serve_forever()
        finally:
            engine.close()


if __name__=='__main__':
    main()
