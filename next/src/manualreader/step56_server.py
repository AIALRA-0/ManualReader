"""Serve isolated STEP-05/06 results alongside the existing local routes."""

from argparse import ArgumentParser
from base64 import b64encode
from hashlib import sha256
from html import escape
from http.cookies import SimpleCookie, CookieError
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import re
import secrets
from urllib.parse import parse_qs, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from . import step04_server
from .stages.step03.engine import Engine
from .stages.step04.jobs import Jobs
from manualreader.artifacts import read_json as read, write_json as write
from .stages.step06.pipeline import JointJobs


VERSION='ManualReader-Next/0.6.2'
KEY='manualreader_step56'


def downloads(job, source):
    out=BytesIO()
    with ZipFile(out,'w',ZIP_DEFLATED) as archive:
        for path in job.glob('*.json'):
            archive.write(path,path.name)
        for path in (job/'overlays').glob('*.md'):
            archive.write(path,'overlays/'+path.name)
        name=read(source/'step04-run.json')['output_file']
        archive.write(source/name,name)
        archive.write(source/'task-card.json','task-card.json')
    return out.getvalue()


def sdk():
    origin, hashes=step04_server.client_archive()
    out=BytesIO()
    with ZipFile(BytesIO(origin)) as source, ZipFile(out,'w',ZIP_DEFLATED) as target:
        for name in hashes:
            target.writestr(name,source.read(name))
        content=Path(__file__).with_name('step56_client.py').read_bytes()
        name='manualreader/step56_client.py'
        target.writestr(name,content)
        hashes[name]={'sha256':sha256(content).hexdigest(),'bytes':len(content)}
        target.writestr('client-hashes.json',json.dumps(hashes,indent=2))
    return out.getvalue(),hashes


def render(source, result, state, base):
    saved=read(source/'step04-run.json')
    card=read(source/'task-card.json')
    title=(card.get('chapter') or {}).get('title') or (card.get('question') or {}).get('text') or state['sample_id']
    draft=(source/saved['output_file']).read_text('utf8')
    body=f'<p><a href="/step56">查看所有任务</a> · {escape(saved["mode"])}'
    if saved['fixture_based']:
        body+=' · 历史首稿测试案例'
    if state['status'] not in ('queued','running'):
        body+=f' · <a href="{base}/download">下载映射和补丁</a>'
    body+='</p>'
    figures={r['path']:r['media_path'] for r in saved.get('resource_references',[])
             if r.get('available') and r.get('media_path')}
    body+=f'<h2>STEP-04 原稿</h2><article id="draft">{escape(draft)}</article>'
    body+='<div hidden id="verified-media">'+escape(json.dumps(figures))+'</div>'
    body+='<script src="/step04/markdown-vendor.js"></script><script src="/step04/preview.js"></script>'
    if not (result/'step06-merged-mapping.json').exists():
        reason=state.get('error') or '结果正在生成。'
        return title,body+f'<p>{escape(reason)}</p>'
    ledger=read(result/'step06-merged-mapping.json')
    evidence=read(source/'source-index.json')
    patches=read(result/'overlays.json')
    mapping=ledger['source_to_explanation']
    navigation_only=set(ledger.get('navigation_only_source_ids',[]))
    reverse=ledger['explanation_to_source']

    def backlinks(anchor):
        return ' '.join(f'<a href="#{escape(sid)}">{escape(sid)}'
                        f'{"（仅导航）" if sid in navigation_only else ""}</a>'
                        for sid in reverse.get(anchor,[]))

    nav=ledger.get('source_navigation') or {}
    if nav:
        body+=(f'<h2>来源导航归属（{escape(saved["mode"])})</h2><p>'
               f'{nav["linked"]}/{nav["total"]} 条可跳转。'
               f'其中 {nav["direct_semantic_count"]} 条有讲解关联，'
               f'{nav["navigation_only_count"]} 条仅供导航。</p>')
    else:
        body+=f'<h2>来源导航归属</h2><p>{len(mapping)} 条现有关系。</p>'
    if (result/'step06-goal-question-coverage.json').exists():
        coverage=read(result/'step06-goal-question-coverage.json')
        metric=coverage['goal_coverage']
        body+=(f'<h2>知识目标与子问</h2><p>{metric["covered"]}/{metric["total"]} 个知识目标'
               '有可跳转的正文或补充位置。</p>')
        if coverage['mode']=='ANSWER':
            q=coverage['question_item_coverage']
            body+=f'<p>{q["covered"]}/{q["total"]} 个识别出的子问有可跳转位置。</p>'
            raw_metric=coverage['question_raw_unit_coverage']
            body+=f'<p>{raw_metric["covered"]}/{raw_metric["total"]} 段原话有可跳转位置。</p>'
        body+='<details id="coverage-items"><summary>查看各个知识目标与子问</summary><ul>'
        for item in coverage['goal_items']+coverage['question_items']:
            labels={'reader':'读者', 'teacher':'教师'}
            label=labels[item['role']] if 'role' in item else '子问'
            topic=item.get('topic',item.get('question_quote'))
            body+=f'<li>{label} · {escape(str(topic))} '
            for i,target in enumerate(item['target_ids'],1):
                body+=f'<a href="#{escape(target)}">位置 {i}</a> '
            if not item['target_ids']:
                body+='未找到对应位置'
            body+='</li>'
        body+='</ul></details>'
        if coverage['mode']=='ANSWER':
            body+='<details id="question-fragments"><summary>查看原话片段</summary><ul>'
            for item in coverage['question_raw_units']:
                body+=f'<li>{escape(item["text"])} '
                label='跳到相邻回答（仅导航）' if item.get('navigation_only') else '跳到回答'
                body+=' '.join(f'<a href="#{escape(target)}">{label}</a>' for target in item['target_ids'])
                if not item['target_ids']:
                    body+='未找到对应位置'
                body+='</li>'
            body+='</ul></details>'
    body+='<details><summary>查看来源和对应位置</summary><ol>'
    for row in evidence['sources']:
        sid=row['source_id']
        label='仅导航' if sid in navigation_only else '讲解关联'
        targets=' '.join(f'<a href="#{escape(a)}">{label} {n}</a>'
                         for n,a in enumerate(mapping.get(sid,[]),1))
        loc=escape(json.dumps(row['position'],ensure_ascii=False))
        body+=(f'<li id="{escape(sid)}"><details><summary><code>{escape(sid)}</code> '
               f'{loc}</summary><pre>{escape(row["text"])}</pre>{targets}</details></li>')
    body+='</ol></details><details><summary>查看原稿位置</summary><ol>'
    for a in read(result/'anchors.json'):
        aid=a['anchor_id']
        body+=f'<li id="{escape(aid)}"><pre>{escape(a["text"])}</pre>{backlinks(aid)}</li>'
    body+='</ol></details><h2>06A 和 06B 的补充</h2>'
    if not patches:
        body+='<p>本次没有生成补充段落。</p>'
    for patch in patches:
        pid=patch['patch_id']
        body+=(f'<details id="overlay:{escape(pid)}" class="overlay"><summary>{patch["origin"]} · '
               f'{escape(patch["kind"])} · {escape(pid)}</summary><p>'
               f'<a href="#{escape(patch["anchor_id"])}">查看原稿位置</a></p>'
               f'<pre>{escape(patch["markdown"])}</pre>')
        for aid in patch['asset_refs']:
            asset=evidence['assets'][aid]
            if sha:=asset.get('sha256'):
                body+=f'<img loading="lazy" src="{base}/assets/{sha}.png" alt="{escape(aid)}">'
            elif asset.get('structure'):
                body+='<pre>'+escape(json.dumps(asset['structure'],ensure_ascii=False,indent=2))+'</pre>'
        body+=backlinks('overlay:'+pid)
        body+='</details>'
    return title,body


def handler(root,key,engine,jobs,writer):
    previous=step04_server.handler(root/'isolated-data',key,engine,writer)
    package,hashes=sdk()
    secret=secrets.token_urlsafe(32)

    class Handler(previous):
        def auth(self):
            if self.authorized():
                return True
            try:
                cookie=SimpleCookie(self.headers.get('Cookie','')).get(KEY)
                return bool(cookie and secrets.compare_digest(cookie.value,secret))
            except CookieError:
                return False

        def do_GET(self):
            path=urlsplit(self.path).path.rstrip('/')
            if path=='/health':
                return self.send({'version':VERSION,'steps':['01','02','03','04','05','06A','06B'],
                                  'formal_writeback':False,'client_files':len(hashes)})
            if path=='/v1/client':
                return self.send(package,kind='application/zip')
            if path=='/v1/client-hashes':
                return self.send(hashes)
            if path!='/step56' and not path.startswith('/step56/'):
                return super().do_GET()
            if not self.auth():
                return self.html('打开 STEP-05 和 STEP-06 的结果',
                      '<form action="/step56/login" method="post"><label>访问密钥 '
                      '<input required name="token" type="password"></label><button>打开结果</button></form>')
            if path=='/step56':
                rows=[]
                for folder in sorted((root/'inputs').iterdir()):
                    label=escape(folder.name)
                    rows.append(f'<li><a href="/step56/{label}">{label}</a></li>')
                return self.html('STEP-05 和 STEP-06 的结果','<ol>'+''.join(rows)+'</ol>')
            p=path.split('/')
            try:
                job=jobs.folder(p[2])
                source=jobs.input(p[2])
                if len(p)==4 and p[3]=='download':
                    return self.send(downloads(job,source),kind='application/zip')
                if len(p)==5 and p[3]=='assets' and re.fullmatch(r'[a-f0-9]{64}\.png',p[4]):
                    return self.send(jobs.image(p[4][:-4]),kind='image/png')
                if len(p)==3:
                    state=jobs.status(p[2])
                    return self.html(*render(source,job,state,path))
            except (OSError,ValueError,KeyError):
                pass
            return self.send({'error':'未找到该结果'},404)

        def do_POST(self):
            if self.path=='/step56/login':
                n=int(self.headers.get('Content-Length','0'))
                token=parse_qs(self.rfile.read(n).decode('utf8')).get('token',[''])[0] if 0<n<4096 else ''
                if not secrets.compare_digest(token.encode(),key.encode()):
                    return self.html('访问密钥不匹配','<a href="/step56">返回</a>',403)
                self.send_response(303)
                self.send_header('Location','/step56')
                self.send_header('Set-Cookie',f'{KEY}={secret}; Path=/step56; HttpOnly; SameSite=Strict')
                self.send_header('Content-Length','0')
                self.end_headers()
                return
            if self.path!='/v1/step56':
                return super().do_POST()
            if not self.authorized():
                return self.send({'error':'需要本机服务的访问密钥'},403)
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<16000:
                    raise ValueError('请求过大')
                req=json.loads(self.rfile.read(n))
                action=req['action']
                if action=='list':
                    return self.send({'samples':sorted(p.name for p in (root/'inputs').iterdir())})
                sample=req['sample_id']
                if action=='submit':
                    return self.send(jobs.submit(sample,req.get('resume') is True),202)
                if action=='status':
                    return self.send(jobs.status(sample))
                if action=='cancel':
                    return self.send(jobs.cancel(sample))
                if action=='download':
                    raw=downloads(jobs.folder(sample),jobs.input(sample))
                    return self.send({'sha256':sha256(raw).hexdigest(),'zip':b64encode(raw).decode()})
                raise ValueError('未识别的操作')
            except (OSError,ValueError,KeyError,TypeError) as error:
                return self.send({'error':str(error)},400)
    return Handler


def main():
    args=ArgumentParser()
    args.add_argument('--root',required=True,type=Path)
    args.add_argument('--models',type=int,default=36)
    args.add_argument('--port',type=int,default=0)
    config=args.parse_args()
    root=config.root.resolve()
    token=root/'token.txt'
    if not token.exists():
        with token.open('x',encoding='utf8') as stream:
            stream.write(secrets.token_urlsafe(32))
    key=token.read_text('utf8').strip()
    engine=Engine(root/'cache',4)
    writer=Jobs(root/'step04-service',engine,4)
    jobs=JointJobs(root,config.models)
    try:
        with ThreadingHTTPServer(('127.0.0.1',config.port),handler(root,key,engine,jobs,writer)) as server:
            base=f'http://127.0.0.1:{server.server_port}'
            write(root/'service-ready.json',{'version':VERSION,'base':base})
            print(VERSION,base,flush=True)
            server.serve_forever()
    finally:
        jobs.close()
        writer.pool.shutdown()
        engine.close()


if __name__=='__main__':
    main()
