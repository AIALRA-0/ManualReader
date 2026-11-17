"""Run the ten-material, four-mode acceptance matrix on a freshly installed service."""
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import sys
import os
from threading import Lock
from time import perf_counter,sleep
from hashlib import sha256
from urllib.request import urlopen
from zipfile import ZipFile


def read(path):
    return json.loads(path.read_bytes())


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def deploy(root, report, cold=False):
    config=root/'instance'
    settings=report/'deployment.json'
    if settings.exists():
        ready=read(settings)
        return ready
    env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','CODEX_THREAD_ID','CODEX_SESSION_ID')}
    env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    ready_path=config/'service-ready.json'
    token=config/'reader-token.txt'
    if ready_path.exists():
        raise ValueError('实例已经有部署记录；请使用前一次部署回执')
    opts={'creationflags':subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
    start=perf_counter()
    with (report/'server.log').open('w',encoding='utf8') as log:
        proc=subprocess.Popen([sys.executable,'-I','-B','-m','manualreader.server','--data',str(config/'data'),
                              '--port','0','--token-file',str(token),'--ready-file',str(ready_path)],
                              cwd=config,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,**opts)
    while True:
        try:
            ready=read(ready_path)
            with urlopen(ready['base']+'/health',timeout=3) as response:
                health=json.load(response)
                assert health['version']==ready['version']
                ready.update(based_on='fresh isolated venv install',pid=proc.pid,
                             cold_start_seconds=perf_counter()-start,health=health)
                save(settings,ready)
                return ready
        except (ValueError,OSError,KeyError):
            if proc.poll() is not None or perf_counter()-start>20:
                if proc.poll() is None:proc.terminate()
                raise RuntimeError('独立环境启动失败；查看 server.log')
            sleep(.05)


def client(base, report):
    folder=report/'downloaded-client'
    first=not folder.exists()
    folder.mkdir(exist_ok=True)
    with urlopen(base+'/v1/client-hashes',timeout=15) as r:
        hashes=json.load(r)
    if first:
        archive=report/'manualreader-next-client.zip'
        with urlopen(base+'/v1/client',timeout=15) as r:
            archive.write_bytes(r.read())
        with ZipFile(archive) as z:
            assert z.testzip() is None
            for n in z.namelist():
                target=folder/n
                assert target.resolve().is_relative_to(folder.resolve())
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(z.read(n))
    actual={}
    for name,want in hashes.items():
        data=(folder/name).read_bytes()
        actual[name]={'sha256':sha256(data).hexdigest(),'bytes':len(data)}
        assert actual[name]==want
    save(report/'client-hashes.json',actual)
    return folder


def main():
    p=ArgumentParser()
    p.add_argument('--root',required=True,type=Path)
    p.add_argument('--report',required=True,type=Path)
    p.add_argument('--question-archive',required=True,type=Path)
    p.add_argument('--pilot',action='store_true')
    p.add_argument('--only',action='append',default=[])
    p.add_argument('--retry-dir')
    a=p.parse_args()
    root,report=a.root.resolve(),a.report.resolve()
    report.mkdir(parents=True,exist_ok=True)
    home=deploy(root,report)
    downloaded=client(home['base'],report)
    sys.path.insert(0,str(downloaded))
    from manualreader.client import API
    from manualreader.planning import plan
    import manualreader
    assert Path(manualreader.__file__).is_relative_to(downloaded)
    stamp=read(root/'instance/data/import-report.json')
    qs=read(a.question_archive)['questions']
    assert len(qs)==len(set(qs))==10
    exam=stamp['07-exam2']
    assert all(q in exam['recorded_questions'] for q in qs)
    specs=[]
    for sid,row in sorted(stamp.items()):
        for mode in ('NORMAL','INTRO','FINALE'):
            specs.append((f'{sid}-{mode.lower()}',row['project_id'],mode,None))
    specs.extend(('answer-'+q,exam['project_id'],'ANSWER',q) for q in qs)
    assert len(specs)==40
    batch='pilot' if a.pilot else a.retry_dir or 'runs'
    if a.pilot:
        specs=[x for x in specs if x[0]=='03-ibm-normal']
    if a.only:
        specs=[x for x in specs if x[0] in a.only]
    if not specs:
        raise ValueError('没有匹配的验收任务')
    token=(root/'instance/reader-token.txt').read_text('utf8')
    lock=Lock()
    slots={'cur':0,'peak':0}
    memory={}
    results={}
    def run(job):
        name,pid,mode,q=job
        path=report/batch/name
        if (path/'run.json').exists():
            return read(path/'run.json')
        with lock:
            slots['cur']+=1
            slots['peak']=max(slots['peak'],slots['cur'])
        try:
            return plan(API(home['base'],token,pid),path,mode,question_id=q,explicit_finale=(mode=='FINALE'),
                        cache=report/'cache',original_cache=memory)
        finally:
            with lock:slots['cur']-=1
    began=perf_counter()
    print('service',home['base'],'starting',len(specs),'jobs using',downloaded,flush=True)
    with ThreadPoolExecutor(max_workers=len(specs)) as pool:
        futures={pool.submit(run,x):x for x in specs}
        for job in as_completed(futures):
            spec=futures[job]
            try:row=job.result()
            except Exception as error:row={'status':'failed','mode':spec[2],'error':str(error)}
            results[spec[0]]=row
            print(spec[0],row['status'],row.get('error',''),flush=True)
    save(report/(batch+'-summary.json'),{'wall_seconds':perf_counter()-began,'peak_jobs':slots['peak'],'results':results})
    return 0 if all(x['status']=='planned' for x in results.values()) else 2


if __name__=='__main__':
    raise SystemExit(main())
