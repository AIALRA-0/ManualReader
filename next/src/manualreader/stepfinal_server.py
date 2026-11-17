"""Run the established Reader front end over isolated Next chapter projections."""

from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape
from pathlib import Path
from threading import Thread
from time import perf_counter, sleep
from urllib.parse import parse_qs, urlsplit
import json
import os
import re
import shutil
import sys

from .stages.finish.preview import SAMPLE, digest, optional, project_native, setup
from manualreader.artifacts import write_json as write


def server_handler(backend, script, run=None, *, production_runs=None):
    from .stages.finish.commit import DELIVERY_PROGRESS_BASIS, commit, prepare, stable_hash
    from .stages.finish.review import next_calls, next_run

    class Handler(backend.ProductionHandler):
        def next_access(self, method):
            if production_runs is not None and self.begin_remote(method):
                return False
            self.context()
            return True

        def next_project(self):
            material=backend.mid()
            sample=material.removeprefix('next-final-')
            return sample!=material and SAMPLE.fullmatch(sample) and self.project_available(material)

        def next_run_root(self):
            if production_runs is None:
                return run
            material=backend.mid()
            return production_runs/material if self.next_project() else None

        def do_GET(self):
            path=urlsplit(self.path).path
            if production_runs is not None and path=='/healthz':
                return self.json({'application':'codex-manual-reader','version':'Next 0.8.1',
                                  'status':'ready','authentication':'authentik'})
            if path=='/acceptance' and run is not None:
                self.context()
                ready=optional(run,'reader-service-ready.json',{}).get('samples',{})
                case=optional(run,'whiteboard-inputs.json',{}).get('samples',{})
                entries=[]
                for name,item in case.items():
                    card=optional(run/'inputs'/name,'task-card.json',{})
                    title=(card.get('chapter') or {}).get('title') or name
                    url=ready.get(name)
                    label=escape(item['mode']+' · '+title)
                    entries.append(f'<li><a href="{escape(url)}">{label}</a></li>' if url else f'<li>{label} · 等待正文</li>')
                body=('<!doctype html><meta charset=utf-8><title>Reader 隔离验收</title>'
                      '<main><h1>Reader 隔离验收</h1><ul>'+''.join(entries)+'</ul></main>').encode()
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if path=='/reader-finish-math.js':
                if not self.next_access('GET'):
                    return
                return self.file(backend.STATIC/path[1:])
            if path=='/vendor/reader-text.js':
                if not self.next_access('GET'):
                    return
                return self.file(backend.STATIC/'reader-next-text.js','text/javascript; charset=utf-8')
            if path == '/reader-postprocess-preview.js':
                if not self.next_access('GET'):
                    return
                self.send_response(200)
                self.send_header('Content-Type','text/javascript; charset=utf-8')
                self.send_header('Content-Length',str(len(script)))
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                self.wfile.write(script)
                return
            if production_runs is not None and (path=='/api/postprocess-preview' or
                    path.startswith('/postprocess-preview-asset/')):
                if not self.next_access('GET'):
                    return
                if not self.next_project():
                    return self.send_error(404)
                folder=backend.mpath().parent/'postprocess-preview'
                if path=='/api/postprocess-preview':
                    target=folder/'manifest.json'
                    return self.file(target,'application/json; charset=utf-8') if target.is_file() else self.send_error(404)
                name=path.removeprefix('/postprocess-preview-asset/')
                if not re.fullmatch(r'[a-f0-9]{64}\.(?:png|jpe?g|webp|gif|svg)',name):
                    return self.send_error(404)
                folder=(folder/'assets').resolve()
                target=(folder/name).resolve()
                if not target.is_relative_to(folder) or not target.is_file():
                    return self.send_error(404)
                return self.project_file_response(target)
            if path == '/api/next-finish/resource-context':
                if not self.next_access('GET'):
                    return
                if not self.authenticated():
                    return self.json({},403)
                if not self.next_project():
                    return self.json({'error':'找不到这份隔离预览'},404)
                sid=parse_qs(urlsplit(self.path).query).get('id',[None])[0]
                folder=backend.mpath().parent/'postprocess-preview'
                doc=optional(folder,'manifest.json',{})
                matches=[item for items in doc.get('resources',{}).values() for item in items if item['resource_id']==sid]
                return self.json(matches[0] if matches else {'error':'这份预览没有这个资源'},200 if matches else 404)
            if (run is not None or production_runs is not None) and path in (
                    '/api/next-finish/review','/api/quality-review/calls','/api/next-finish/delivery'):
                if not self.next_access('GET'):
                    return
                material=backend.mid()
                review_root=self.next_run_root()
                if review_root is not None and self.next_project():
                    if not self.authenticated():
                        return self.json({},403)
                    if path=='/api/quality-review/calls':
                        data=next_calls(review_root,material)
                        if data is not None:
                            return self.json(data)
                    else:
                        state=backend.core.read_state()
                        receipt=state.get('next_save_receipts',{}).get(state.get('next_current_delivery'),{})
                        manifest=optional(backend.mpath().parent/'postprocess-preview','manifest.json',{})
                        current=(receipt.get('status')=='applied' and
                                 receipt.get('progress_basis')==DELIVERY_PROGRESS_BASIS and
                                 receipt.get('manifest_sha256')==stable_hash(manifest))
                        progress=receipt.get('progress',{}).get('manifest',{}).get('progress')
                        delivery={'version':receipt['explanation_version'],'at':receipt['saved_at'],
                                  'progress':progress} if current else None
                        if path=='/api/next-finish/delivery':
                            return self.json({'delivery':delivery})
                        data=next_run(review_root,material)
                        if data is not None:
                            return self.json({**data,'delivery':delivery})
            return super().do_GET()

        def do_POST(self):
            path=urlsplit(self.path).path
            if path.startswith(('/api/writing-handoff/','/api/chatgpt-handoff/')):
                if not self.next_access('POST'):
                    return
                return self.json({'error':'这项文件交接入口已移除'},404)
            routes={'/api/next-finish/simulate/prepare':(False,False),
                    '/api/next-finish/simulate/commit':(False,True),
                    '/api/next-finish/delivery/prepare':(True,False),
                    '/api/next-finish/delivery/commit':(True,True)}
            if path not in routes:
                return super().do_POST()
            if not self.next_access('POST'):
                return
            material=backend.mid()
            if not self.safe_origin() or not self.authenticated():
                return self.json({},403)
            publish,saving=routes[path]
            if not self.next_project() or (production_runs is not None and not publish):
                return self.json({'error':'这个项目没有可提交的 Next 草稿'},404)
            channel='reader' if production_runs is not None else 'isolated-test'
            if self.headers.get('X-Reader-Finish') != channel or self.headers.get_content_type()!='application/json':
                return self.json({'error':'请从当前讲解提交交稿'},403)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 2<=size<=65536:
                    raise ValueError('无效的保存模拟参数')
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):
                    raise ValueError('保存模拟参数必须是 JSON 对象')
                result=commit(backend,data,publish=publish) if saving else prepare(backend,publish=publish)
                return self.json(result)
            except (ValueError,KeyError,TypeError) as exc:
                return self.json({'error':str(exc)},409)

    return Handler


def main(argv=None):
    parser=ArgumentParser(description='把 Next 完成稿接到独立的 Reader 预览')
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--legacy',type=Path,required=True)
    parser.add_argument('--original-projects',type=Path)
    parser.add_argument('--sample',action='append')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--jobs',type=int)
    parser.add_argument('--project-only',action='store_true')
    parser.add_argument('--live',action='store_true',help='有正文或新附件后自动刷新隔离预览')
    args=parser.parse_args(argv)
    root=args.run.resolve()
    legacy=args.legacy.resolve()
    isolated=setup(root/'reader-isolated',legacy,args.port)
    static=isolated/'static'
    shutil.copytree(legacy/'static',static,dirs_exist_ok=True)
    from .stages.finish.native_ui import install
    script=install(static,legacy)
    os.environ['MANUAL_READER_STATIC']=str(static)
    sys.path.insert(0,str(legacy))
    import reader_server as backend

    backend.core.read_state()
    samples=args.sample or [p.parent.name for p in sorted((root/'jobs').glob('*/run.json'))]
    if (not samples and not args.live) or any(not SAMPLE.fullmatch(x) for x in samples) or len(set(samples))!=len(samples):
        parser.error('缺少有效的独立样本编号')
    started=perf_counter()
    rows={}
    revision_path=lambda name: root/'jobs'/name/'preview-revision.json'
    initial_revisions={name:digest(path) for name in samples
                       if (path:=revision_path(name)).is_file()}
    with ThreadPoolExecutor(max_workers=max(1,args.jobs or len(samples))) as pool:
        jobs={pool.submit(project_native,root,sample,isolated,backend,args.original_projects):sample for sample in samples}
        for job in as_completed(jobs):
            sample=jobs[job]
            try:
                rows[sample]=job.result()
            except Exception as error:
                rows[sample]={'sample_id':sample,'error':f'{type(error).__name__}: {error}'}
            print(json.dumps(rows[sample],ensure_ascii=False),flush=True)
    report={'samples':[rows[sample] for sample in samples],
            'projected':sum('material_id' in x for x in rows.values()),
            'wall_seconds':round(perf_counter()-started,6)}
    write(root/'reader-projections.json',report)
    if not report['projected'] and not args.live:
        return 1
    if args.project_only:
        return int(report['projected'] != len(samples))
    server=backend.ReaderHTTPServer(('127.0.0.1',args.port),server_handler(backend,script,root))
    url=f'http://127.0.0.1:{server.server_address[1]}'
    running={'url':url,'samples':{row['sample_id']:url+'/?material='+row['material_id']
                                  for row in rows.values() if 'material_id' in row}}
    write(root/'reader-service-ready.json',running)
    if args.live:
        def watch():
            published={name:version for name,version in initial_revisions.items()
                       if 'material_id' in rows[name]}
            active={}
            allowed=set(args.sample or ())
            with ThreadPoolExecutor(max_workers=args.jobs or 6) as pool:
                while True:
                    changed=False
                    for name,(version,future) in tuple(active.items()):
                        if not future.done():
                            continue
                        del active[name]
                        try:
                            row=future.result()
                        except (OSError,ValueError,KeyError,TypeError,RuntimeError) as exc:
                            rows[name]={'sample_id':name,'error':f'{type(exc).__name__}: {exc}'}
                        else:
                            rows[name]=row
                            published[name]=version
                            running['samples'][name]=url+'/?material='+row['material_id']
                        changed=True
                    if changed:
                        write(root/'reader-service-ready.json',running)
                        write(root/'reader-projections.json',{'samples':list(rows.values()),
                              'projected':sum('material_id' in r for r in rows.values()),
                              'wall_seconds':round(perf_counter()-started,6)})
                    for mark in (root/'jobs').glob('*/preview-revision.json'):
                        name=mark.parent.name
                        if name in active or (allowed and name not in allowed):
                            continue
                        try:
                            version=digest(mark)
                        except OSError:
                            continue
                        if published.get(name)!=version:
                            future=pool.submit(project_native,root,name,isolated,backend,args.original_projects)
                            active[name]=(version,future)
                    sleep(0.7)
        Thread(target=watch,daemon=True,name='reader-preview-publisher').start()
    print(url,flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
