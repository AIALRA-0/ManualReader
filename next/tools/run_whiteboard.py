"""Run the frozen Reader stages on a new copy of original inputs and questions."""

import argparse
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import lru_cache
from hashlib import file_digest
from pathlib import Path
import os
import json
import shutil
import sqlite3
import sys
from urllib.request import Request, urlopen
from uuid import uuid4
from threading import Lock, Semaphore
from time import time

from manualreader.planning import invoke as planner, plan
from manualreader.store import Project
from manualreader.transport import executable
from manualreader.stages.step03.cache import atomic, digest as digest_json
from manualreader.stages.step03.engine import Engine
from manualreader.stages.step04.jobs import Jobs
from manualreader.stages.step04.writer_transport import invoke as writer
from manualreader.stages.step05.inference import invoke as map_or_add, read, write
from manualreader.stages.step06.pipeline import JointJobs
from manualreader.stages.step07 import inference as stage07
from manualreader.stages.step07.fusion import assemble as fuse
from manualreader.stages.step05.contracts import digest
from manualreader.stages.step02.card import assemble as assemble_card, link_sources, policies, render as render_card
from manualreader.steps.events import RunJournal
from prepare_step56 import within, location_match, box_matches


def checksum(path):
    with path.open('rb') as stream:
        return file_digest(stream,'sha256').hexdigest()


def reconcile_finale_scope(api, folder, receipt):
    """Intersect a returned FINALE selection with the ranges the saved chapter actually supports."""
    context=read(folder/'context-fixed.json')
    catalog=read(folder/'original-catalog.json')
    draft=read(folder/'first-semantic-result.json')
    changes=[]
    revised=[]
    for item in draft['material_plan']:
        actual=context['allowed_ranges'].get(item['file_id'],[])
        seen={}
        for requested in item['source_ranges']:
            for covered in actual:
                if requested['type']==covered['type']:
                    start=max(requested['start'],covered['start'])
                    end=min(requested['end'],covered['end'])
                    if start<=end:
                        seen[(requested['type'],start,end)]={'type':requested['type'],'start':start,'end':end}
        ranges=list(seen.values())
        if ranges!=item['source_ranges']:
            changes.append({'file_id':item['file_id'],'before':item['source_ranges'],'after':ranges})
        if ranges:
            revised.append({**item,'source_ranges':ranges})
    if not revised or not changes:
        raise ValueError('已保存讲解没有可用于此收尾规划的交集')
    card, diagnostic=assemble_card(context,catalog,{**draft,'material_plan':revised})
    round_id=read(folder/'step01-start.json')['round_id']
    file_ids=[r['file_id'] for r in revised]
    selection=api.call('step01','select',round_id=round_id,file_ids=file_ids)
    if selection['status']!='selected':
        raise ValueError('局部收口时原件范围已经变化')
    indexes={fid:api.call('step02','index',round_id=round_id,file_id=fid) for fid in file_ids}
    card, bindings=link_sources(card,indexes,catalog)
    final=api.call('step01','reconcile',round_id=round_id)
    if final['status']!='ready':
        raise ValueError('局部收口时不能核对所选原件版本')
    if api.call('step02','context',round_id=round_id,explicit_finale=True)['state_fingerprint']!=context['state_fingerprint']:
        raise ValueError('局部收口期间已保存讲解发生变化')
    write(folder/'run-before-scope-reconciliation.json',receipt)
    write(folder/'scope-reconciliation.json',{'changes':changes,'new_model_calls':0,'source_links':bindings})
    write(folder/'step01-selection.json',selection)
    write(folder/'step01-reconcile.json',final)
    write(folder/'task-card.json',card)
    (folder/'task-card.md').write_text(render_card(card,policies()),encoding='utf-8')
    receipt={k:v for k,v in receipt.items() if k!='error'}
    receipt.update(status='planned',source_links=bindings,diagnostics=diagnostic,local_scope_reconciliation=True)
    write(folder/'run.json',receipt)
    return receipt


def project_context(entry):
    candidates=[(Path(f),h) for f,h in entry['preserved'].items() if Path(f).name=='state.sqlite3']
    if len(candidates)!=1:
        return {'glossary_status':'glossary_unavailable'}
    path,expected=candidates[0]
    if checksum(path)!=expected:
        raise ValueError('已登记的项目词库文件发生变更')
    with sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True) as db:
        row=db.execute('SELECT value FROM reader_state WHERE id=1').fetchone()
    state=json.loads(row[0]) if row else {}
    return {'glossary_status':'registered_snapshot','glossary_snapshot_sha256':expected,
            'existing_terms':[t for t in state.get('terms',[]) if t.get('id') and not t.get('duplicate_of')],
            'existing_quizzes':state.get('quizzes',[]),'available_code_runners':[],
            'sourced_from_registered_project_state':True}


def stage_fixture(root, corpus, original_projections):
    data=root/'original-inputs'
    sources={}
    mappings={}
    for folder in sorted((original_projections/'inputs').glob('*/step04-run.json')):
        item=read(folder)
        label=folder.parent.name
        mappings[label]={'sample_id':label,'project_id':item['project_id'],'mode':item['mode'],
                         'question_id':label.removeprefix('answer-') if item['mode']=='ANSWER' else None}
    if Counter(x['mode'] for x in mappings.values())!={'NORMAL':10,'INTRO':10,'FINALE':10,'ANSWER':10}:
        raise ValueError('白板样本清单应有四种模式，每种 10 个')
    for entry in read(corpus/'import-report.json').values():
        project=entry['project_id']
        src=corpus/'projects'/project
        target=data/'projects'/project
        target.mkdir(parents=True)
        original=read(src/'project.json')
        base={**original,'previous':{},'approved_styles':{},'saved_explanations':[],
              'fixture':None,'fixture_blocked':None}
        base['questions']={qid:{**q,'answer':None} for qid,q in base['questions'].items()}
        write(target/'project.json',base)
        shutil.copy2(src/'source-index.json',target/'source-index.json')
        shutil.copytree(src/'originals',target/'originals')
        for stored in base['files'].values():
            if checksum(target/'originals'/stored['relative_path'])!=stored['sha256']:
                raise ValueError(f"原件 {stored['relative_path']} 的哈希与登记记录不符")
        matches=[(Path(f),h) for f,h in entry['preserved'].items() if Path(f).name=='reading-workspace.json']
        if len(matches)!=1:
            raise ValueError(f'{project} 没有原始来源登记')
        path,sha=matches[0]
        if checksum(path)!=sha:
            raise ValueError(f'{project} 的来源登记已变化')
        paths=read(path)['files']
        byid={f['id']:{p['id']:{k:p.get(k) for k in (
             'id','raw_text','en','alt','refs','source_location','kind','role','section_id','source_ids')}
             for p in f['document']['passages']} for f in paths if f['id'] in base['files']}
        write(target/'original-registration.json',{'sources':byid,'registration_sha256':sha})
        write(target/'registered-context.json',project_context(entry))
        sources[project]={'verified_files':base['files'],'registration_sha256':sha,
                          'question_ids':sorted(base['questions'])}
    for row in mappings.values():
        if row['question_id'] and row['question_id'] not in sources[row['project_id']]['question_ids']:
            raise ValueError(f'{row["sample_id"]} 缺少真实问题')
    focus=['05-zen6-normal','07-exam2-normal','06-paper-intro','08-property-intro','03-ibm-finale',
           '02-laozhang-finale','answer-q-c6bfff272e144f9e','answer-q-3b967467f0384506']
    if not set(focus)<=mappings.keys():
        raise ValueError('指定的浏览器样本未出现在固定 40 任务中')
    manifest={'run_id':'reader-'+uuid4().hex,'started_at':time(),'data_root':str(data),'samples':mappings,
              'deep_browser_samples':focus,'original_projects':sources,
              'prior_run_reused':0,'initial_model_store_empty':True,
              'discarded_historical_fields':['previous','approved_styles','saved_explanations','question.answer']}
    write(root/'whiteboard-inputs.json',manifest)
    return manifest


class LocalProjectAPI:
    def __init__(self,project,timing,on_start=None):
        self.material=project
        self.project=project.identity
        self.base='whiteboard-local'
        self.timing=timing
        self.on_start=on_start

    def call(self,kind,action,**attrs):
        request={'action':action,**attrs}
        if kind=='step01':
            with self.timing(action):
                result=self.material.lifecycle(request)
            if action=='prepare' and self.on_start:
                self.on_start(result)
            return result
        if kind=='step02':
            if action=='context':
                return self.material.context(request)
            return self.material.original(request)
        raise ValueError(f'未识别读取操作 {kind}')


class Whiteboard:
    def __init__(self,root,manifest,concurrency,preview_url=None,*,commit_normal=True):
        self.root=root
        self.data=root/'original-inputs'
        self.manifest=manifest
        self.preview_url=preview_url
        self.commit_normal=commit_normal
        self.concurrency=concurrency
        self.journal=RunJournal(root,manifest['run_id'],manifest['samples'])
        self.latest={(row['task_id'],int(row['step_id'][-2:])):row['event_id'] for row in self.journal.nodes.values()}
        self.engine=Engine(root/'step03-cache',workers=8)
        self.requests=Semaphore(concurrency)
        self.pool=ThreadPoolExecutor(max_workers=60, thread_name_prefix='reader-whiteboard')
        self.writers=Jobs(root/'writer-runs',self.engine,40,writer=self.slot(writer))
        self.step56=JointJobs(root/'step56',concurrency,infer=self.slot(map_or_add))
        self.cli=executable()
        self.memory={}
        self.media_guard=Lock()
        self.events=[]
        self.event_guard=Lock()
        for n in ('inputs','step56','step07-work','upstream07','media','jobs'):
            (root/n).mkdir(exist_ok=True)
        (root/'step56/inputs').mkdir(exist_ok=True)
        (root/'step56/media').mkdir(exist_ok=True)
        for n in ('inputs','previous','jobs','media'):
            (root/'step07-work'/n).mkdir(exist_ok=True)

    def slot(self,function):
        def run(*args,**kwargs):
            entered=time()
            with self.requests:
                started=time()
                try:
                    return function(*args,**kwargs)
                finally:
                    write(Path(args[0])/'dispatch-timing.json',{'ready_at':entered,'started_at':started,'ended_at':time()})
        return run

    @contextmanager
    def timed(self,name,stage,operation=None):
        began=time()
        error=None
        try:
            yield
        except BaseException as e:
            error=f'{type(e).__name__}: {e}'
            raise
        finally:
            event={'sample_id':name,'stage':stage,'operation':operation,'started_at':began,'ended_at':time(),'error':error}
            with self.event_guard:
                self.events.append(event)
                with (self.root/'upstream-events.jsonl').open('a',encoding='utf8') as stream:
                    import json
                    stream.write(json.dumps(event,ensure_ascii=False)+'\n')

    @lru_cache
    def registered(self,project):
        path=self.data/'projects'/project
        return read(path/'source-index.json'),read(path/'original-registration.json')

    def build_sources(self,label,project,card,job):
        index,register=self.registered(project)
        packet=read(job/'writer-input.json')
        docs={doc['file_id']:doc for doc in packet['original_documents']}
        originals=packet['original_images']
        all_sources=[]
        assets={}
        for asset in originals:
            assets[asset['asset_id']]=asset
        for entry in card['material_plan']:
            fid=entry['file_id']
            rows={r['id']:r for r in index[fid]['rows']}
            per_source=register['sources'][fid]
            doc=docs[fid]
            if doc['sha256']!=index[fid]['sha256']:
                raise ValueError(f'{fid} 的原件版本变了')
            by_pos={row['n']:row['text'] for row in doc['original']}
            for sid in entry['source_ids']:
                row=rows[sid]
                p=per_source[sid]
                if not any(within(loc,span) or span['type']=='whole_file'
                    for loc in doc['source_locations'][sid] for span in entry['source_ranges']):
                    raise ValueError(f'{sid} 已超出所选原文范围')
                pos=row['position']
                if pos and pos['type']=='subtitle_sid':
                    text='\n'.join(by_pos[n] for n in range(pos['start'],pos['end']+1) if n in by_pos)
                else:
                    text=p.get('raw_text') or p.get('en') or p.get('alt') or ''
                ref=p.get('refs') or (p.get('source_location') or {}).get('refs') or []
                asset_ids=[a['asset_id'] for a in originals if a['file_id']==fid and
                           location_match(pos,a['locator']) and box_matches(ref,a['locator'])]
                all_sources.append({'source_id':sid,'file_id':fid,'position':pos,'time':row['time'],
                                    'kind':p.get('kind'),'role':p.get('role'),'section_id':p.get('section_id'),
                                    'registered_unit_ids':p.get('source_ids') or [], 'text':text,'refs':ref,'asset_refs':asset_ids})
            for structure in doc['structures']:
                assets.setdefault(structure['asset_id'],{**structure,'file_id':fid})
        source={'sources':all_sources,'assets':assets,'snapshot_sha256':register['registration_sha256'],
                'source_files':{fid:v['sha256'] for fid,v in docs.items()},
                'saved_chapters':[{k:v for k,v in s.items() if k!='blocks'} for s in packet['saved_explanations']]}
        write(self.root/'inputs'/label/'source-index.json',source)
        return source

    def stage_writer(self,label,task,card,job,source):
        dst=self.root/'inputs'/label
        shutil.copy2(job/'run.json',dst/'step04-run.json')
        draft=job/task['output_file']
        shutil.copy2(draft,dst/draft.name)
        for img in source['assets'].values():
            if sha:=img.get('sha256'):
                file=job/'assets'/f'{sha}.png'
                if file.is_file():
                    self.register_media(file)
        for row in task.get('resource_references',[]):
            if row.get('available') and row.get('media_path'):
                file=job/'assets'/Path(row['media_path']).name
                if file.is_file():
                    self.register_media(file)
        for parent in (self.root/'step56',self.root/'step07-work'):
            target=parent/'inputs'/label
            target.mkdir(exist_ok=True)
            for item in dst.iterdir():
                if item.is_file():
                    shutil.copy2(item,target/item.name)
        write(dst/'writer-evidence.json',{'task_id':task['task_id'],'run':str(job/'run.json'),
                                          'new_draft_sha256':task['output_hash'],
                                          'planner_card_sha256':checksum(dst/'task-card.json')})
        return draft

    def register_media(self,file):
        if file.suffix!='.png' or checksum(file)!=file.stem:
            raise ValueError('媒体哈希与登记不一致')
        with self.media_guard:
            for folder in (self.root,self.root/'step56',self.root/'step07-work'):
                target=folder/'media'/file.name
                if not target.exists():
                    try:
                        os.link(file,target)
                    except OSError:
                        shutil.copy2(file,target)

    def event(self,name,step,event,value=None,status='complete',upstream=(),**kw):
        deps=[]
        for other in upstream:
            if self.latest.get((name,other)):
                deps.append(self.latest[(name,other)])
        deps.extend(kw.pop('depends_on_events',()))
        current=self.journal.emit(name,step,event,status,value=value,upstream=deps,**kw)
        self.latest[(name,step)]=current
        return current

    def isolated_save(self,name):
        from urllib.parse import urlsplit
        base=self.preview_url
        if not base or urlsplit(base).hostname not in ('127.0.0.1','localhost'):
            raise ValueError('FINALE 需要本轮正常章节的实际隔离保存版本')
        ready=read(self.root/'reader-service-ready.json')
        deadline=time()+60
        current=self.root/'jobs'/name/'run.json'
        manifest=self.root/'reader-isolated'/'library'/('next-final-'+name)/'postprocess-preview/manifest.json'
        while (name not in ready.get('samples',{}) or not manifest.is_file() or
               read(manifest).get('delivery_receipt_sha256')!=checksum(current)):
            if time()>deadline:
                raise ValueError('本轮普通章节预览还未发布')
            from time import sleep
            sleep(.4)
            ready=read(self.root/'reader-service-ready.json')
        with urlopen(base+'/api/session',timeout=10) as response:
            pin=json.load(response).get('pin','')
        auth={'X-Reader-Material':'next-final-'+name,'X-Reader-Pin':pin,
              'Content-Type':'application/json','X-Reader-Finish':'isolated-test'}
        def post(path,body):
            with urlopen(Request(base+'/api/next-finish/simulate/'+path,json.dumps(body).encode(),
                                 headers=auth),timeout=35) as response:
                return json.load(response)
        prepared=post('prepare',{})
        receipt=post('commit',{k:prepared[k] for k in ('preview_token','commit_id')}|{'confirmed':True})
        if receipt['status']!='applied':
            raise ValueError('隔离章节尚未成功保存')
        write(self.root/'jobs'/name/'isolated-save.json',receipt)
        return receipt

    def save_fresh_for_finale(self,project,name,card,task,draft,receipt):
        origin=project._project()
        entry={'node_id':card['chapter']['node_id'],'chapter':card['chapter']['title'],
               'explanation_id':receipt['explanation_id'],'version':receipt['explanation_version'],
               'source_ids':list(dict.fromkeys(s for item in card['material_plan'] for s in item['source_ids'])),
               'fixture_based':True,'markdown_path':'saved/whiteboard-normal.md',
               'body_sha256':checksum(draft)}
        if not entry['source_ids']:
            raise ValueError('本轮正常章节没有真实来源，收尾任务缺少输入')
        atomic(project.root/entry['markdown_path'],draft.read_bytes())
        origin.update(saved_explanations=[entry], fixture={
            'fixture_based':True,'source':'本轮 NORMAL 已成功写入隔离 Reader 的成稿',
            'sample_id':name,'body_sha256':checksum(draft),'save_commit_id':receipt['commit_id'],
            'manual_completion_added':False})
        write(project.projection,origin)

    async def upstream(self,item,normal_ready,planned,source_ready,run_blocking):
        name,mode,pid=(item[k] for k in ('sample_id','mode','project_id'))
        project=Project(self.data,pid)
        if mode=='FINALE':
            await normal_ready[pid]
        folder=self.root/'planners'/name
        options={'question_id':item['question_id'],'explicit_finale':mode=='FINALE'}
        def prewarm(result):
            for file in result['manifest']['files']:
                if file['status']=='active' and not file.get('identity_pending'):
                    self.engine.warm(project,result['round_id'],file['file_id'])
        api=LocalProjectAPI(project,lambda action:self.timed(name,'01',action),prewarm)
        with self.timed(name,'02'):
            prior=folder/'run.json'
            if mode=='FINALE' and prior.is_file():
                planned_task=read(prior)
            else:
                planned_task=await run_blocking(plan,api,folder,mode,**options,
                                                backend=self.slot(planner),original_cache=self.memory)
            if (mode=='FINALE' and planned_task.get('model_dispatches')==1
                and planned_task.get('error')=='FINALE 选取的原件范围超出能核对的已保存讲解'):
                planned_task=await run_blocking(reconcile_finale_scope,api,folder,planned_task)
            if planned_task['status']!='planned':
                raise RuntimeError(planned_task.get('error') or f'{name} 任务卡未生成')
        raw=(folder/'task-card.json').read_bytes()
        card=read(folder/'task-card.json')
        first=read(folder/'step01-start.json')
        file_event=self.event(name,1,'file_version_verified',{'round_id':first['round_id'],'files':card['material_plan']})
        self.event(name,2,'card_ready',card,upstream=(1,))
        atomic(self.root/'inputs'/name/'task-card.json',raw)
        project_dir=self.data/'projects'/pid
        context=read(project_dir/'registered-context.json')
        previous=card.get('previous',{})
        project_record=project._project()
        directory=project_record.get('directory',[])
        if isinstance(directory,dict):
            directory=directory.get('nodes',[])
        chapter=(card.get('chapter') or {}).get('node_id')
        active=next((i for i,row in enumerate(directory) if row.get('node_id',row.get('id'))==chapter),None)
        linked={}
        if item['mode']=='INTRO':
            first=next((row for row in directory if row.get('node_id',row.get('id')) and row.get('title')
                        and row['title'].strip() not in ('导读','章节导读','阶段性收束')),None)
            if first:
                linked['next']=first
        if active is not None:
            if active+1<len(directory):
                linked['next']=directory[active+1]
            if active:
                prev_id=directory[active-1].get('node_id',directory[active-1].get('id'))
                saved=next((row for row in project_record.get('saved_explanations',[]) if row.get('node_id')==prev_id),None)
                if saved:
                    linked['previous']={**saved,**previous}
        linked['saved']=project_record.get('saved_explanations',[])
        context['adjacent_chapters']=linked
        write(self.root/'inputs'/name/'reader-context.json',context)
        planned[name]=time()
        with self.timed(name,'04'):
            initial=await run_blocking(self.writers.submit,project,first['round_id'],mode,raw,mode=='FINALE')
            job=self.writers.folder(initial['task_id'])
            with self.timed(name,'03'):
                while not (job/'writer-input.json').is_file():
                    status=self.writers.status(initial['task_id'])
                    if status['status'] not in ('queued','preparing','writing'):
                        raise RuntimeError(status.get('error') or status['status'])
                    await asyncio.sleep(.20)
                source=await run_blocking(self.build_sources,name,pid,card,job)
            for row in source['assets'].values():
                if row.get('sha256'):
                    file=job/'assets'/(row['sha256']+'.png')
                    if file.is_file():
                        self.register_media(file)
            source_events=[]
            for row in source['sources']:
                version=source['source_files'][row['file_id']]
                source_events.append(self.journal.emit(name,3,'source_unit_ready','partial_ready',
                    unit_type='source_unit',unit_id=row['source_id'],source_version=version,
                    value={'file_id':row['file_id'],'position':row['position'],'original_sha256':digest(row['text'])},
                    upstream=[file_event]))
            self.event(name,3,'selected_scope_ready',{'snapshot':source['snapshot_sha256']},
                       upstream=(2,),depends_on_events=source_events)
            source_ready[name].set_result(source)
            while True:
                status=self.writers.status(initial['task_id'])
                if status['status'] not in ('queued','preparing','writing'):
                    break
                await asyncio.sleep(.35)
            if status['status']!='complete':
                raise RuntimeError(f"Writer {status['status']} {status.get('error','')}")
            await run_blocking(self.stage_writer,name,status,card,job,source)
        self.event(name,4,'draft_sealed',{'sha256':status['output_hash']},upstream=(2,3))
        with self.timed(name,'05/06'):
            self.step56.submit(name)
            while True:
                joint=self.step56.status(name)
                if joint['status'] not in ('queued','running'):
                    break
                await asyncio.sleep(.4)
            if joint['status'] not in ('complete','partial'):
                raise RuntimeError(joint.get('error') or joint['status'])
        self.event(name,5,'mapping_ready',joint,upstream=(4,))
        self.event(name,6,'additions_branch_ready',{'status':joint['status']},
                   status='complete' if joint['status']=='complete' else 'partial_ready',upstream=(4,5))
        with self.timed(name,'07'):
            stage=self.root/'step07-work'
            target=stage/'previous'/name
            target.mkdir()
            for path in (self.root/'step56/jobs'/name).glob('*.json'):
                shutil.copy2(path,target/path.name)
            p,unused=stage07.prepare(stage,name)
            model=stage/'jobs'/name/'model'
            outcome=await run_blocking(self.slot(stage07.invoke),model,p,self.cli,time())
            stage07_result=await run_blocking(fuse,stage,name)
            if outcome['state']!='complete':
                stage07_result['whiteboard_assembly_used_only_available_patches']=True
            result=self.root/'upstream07'/name
            result.mkdir()
            for path in (stage/'jobs'/name).iterdir():
                if path.is_file():
                    shutil.copy2(path,result/path.name)
            print(name,'STEP-07',outcome['state'],flush=True)
        self.event(name,7,'body_fused_sealed',{'fused_sha256':checksum(result/'body.fused.md')},upstream=(4,6))
        return source

    async def run(self,only=None,*,resume_attachments=False):
        from manualreader.stages.finish.pipeline import finish_batch,relations
        from manualreader.stages.finish.structure import reading_groups
        from manualreader.stages.finish.provider import Provider,SCHEMAS
        from manualreader.stages.finish.runner import Timings,persist
        from manualreader.stages.finish import content
        from manualreader.stages.finish.content import build_translation_catalog,translate,source_units,_key
        from functools import partial

        loop=asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=65,thread_name_prefix='finish-source'))
        async def block(function,*args,**kw):
            return await loop.run_in_executor(self.pool,partial(function,*args,**kw))

        planned_samples=self.manifest['samples']
        records={n:r for n,r in planned_samples.items() if only is None or n in only}
        normal_ready={i['project_id']:loop.create_future() for i in planned_samples.values() if i['mode']=='NORMAL'}
        if only is not None:
            for n,r in planned_samples.items():
                if r['mode']=='NORMAL' and (self.root/'jobs'/n/'isolated-save.json').is_file():
                    normal_ready[r['project_id']].set_result(True)
        source_ready={n:loop.create_future() for n in records}
        providers=Provider(self.root,sol=self.concurrency)
        timing=Timings()
        write(self.root/'model-schemas.json',SCHEMAS)
        planned={}
        existing=read(self.root/'whiteboard-result.json') if only is not None else {}
        finished=dict(existing.get('finished',{}))
        failures=dict(existing.get('failures',{}))
        translation_contexts={}

        def context_catalogue(name, snapshot):
            project=records[name]['project_id']
            terms=snapshot.get('existing_terms',[])
            key=(project,digest_json(terms))
            if key not in translation_contexts:
                translation_contexts[key]=build_translation_catalog([]),asyncio.Lock()
            return translation_contexts[key]


        async def preview(name,step,event,value,status='partial_ready'):
            stage_deps={8:(7,),10:(3,),11:(3,),12:(8,),13:(7,8),14:(8,),15:(8,),
                        16:(8,10,11,12,13,14,15),17:(8,9,16),18:(8,10)}
            token=self.event(name,step,event,value,status,upstream=stage_deps.get(step,()))
            job=self.root/'jobs'/name
            if (job/'part-blocks.json').is_file() and (job/'body.final.md').is_file():
                write(job/'preview-revision.json',{'event_id':token,'updated_at':time()})

        async def layout(name,source):
            rows=await asyncio.to_thread(reading_groups,source)
            write(self.root/'jobs'/name/'reading-groups.json',rows)
            self.event(name,9,'reading_groups_ready',{'groups':len(rows)},upstream=(3,))

        async def visual_sources(name):
            source=await source_ready[name]
            if source is None:
                return {}
            ask=providers.scope(name)
            snapshot=read(self.root/'inputs'/name/'reader-context.json')
            labels_seen={}
            async def labels(rows):
                if not rows:
                    return
                got=await content.translate_labels(self.root,name,rows,ask,snapshot.get('existing_terms',[]))
                for row in got['translations']:
                    labels_seen[(row['resource_id'],row['label_id'])]=row
                job=self.root/'jobs'/name
                write(job/'translation-visuals.json',{'translations':list(labels_seen.values())})
                await preview(name,11,'visual_labels_ready',got,
                              'partial_ready' if labels_seen else 'blocked' if got.get('issues') else 'skipped')
            await labels(content.source_labels(source))
            return await timing.run(name,'source-image-labels',content.original_labels(
                self.root,name,source,ask,content.build_visual_catalog([source]),on_labels=labels))

        async def translation(name):
            source=await source_ready[name]
            if source is None:
                return
            units=source_units(source)
            reading=asyncio.create_task(layout(name,source))
            snapshot=read(self.root/'inputs'/name/'reader-context.json')
            catalogue,catalogue_lock=context_catalogue(name,snapshot)
            async with catalogue_lock:
                pending={u['source_id'] for u in units if u['relation']=='translated' and _key(u) not in catalogue.by_source}
                fresh={**source,'sources':[r for r in source['sources'] if r['source_id'] in pending]}
                local=await asyncio.to_thread(build_translation_catalog,[fresh],glossary=snapshot.get('existing_terms',[]))
                catalogue.by_source.update(local.by_source)
                assigned={_key(u):catalogue.by_source[_key(u)] for u in units if _key(u) in catalogue.by_source}
            from manualreader.stages.finish.content import TranslationCatalog
            assigned_catalogue=TranslationCatalog(assigned,{},())
            job=self.root/'jobs'/name
            async def unit_ready(value,identities):
                for fid,sid,version,raw_sha in identities:
                    self.journal.emit(name,10,'translation_ready','partial_ready',unit_type='source_unit',
                        unit_id=sid,source_version=version,value={'file_id':fid,'original_sha256':raw_sha},
                        upstream=(self.latest[(name,3)],))
                write(job/'translation-groups.json',value['groups'])
                write(job/'source-translation-links.json',value['links'])
                if (job/'part-blocks.json').is_file():
                    write(job/'alignment.json',relations(read(job/'part-blocks.json'),value))
                await preview(name,10,'translation_batch_ready',value['counts'])
            tx=await timing.run(name,'B1',translate(self.root,name,source,providers.scope(name),
                                                    assigned_catalogue,on_partial=unit_ready))
            if tx is None:
                missing=[u['source_id'] for u in units if u['relation']=='translated']
                tx={'groups':[],'links':[],'unresolved_source_ids':missing,
                    'counts':{'needed':len(missing),'complete':0,'uncertain':0,'unresolved':len(missing)},
                    'issues':[{'stage':'B1','reason':'没有读取到本篇译文结果','source_ids':missing}]}
            write(job/'translation-initial.json',tx)
            await reading
            counts=tx['counts']
            self.event(name,10,'translation_scope_settled',counts,
                       status='partial_ready' if counts.get('unresolved') or counts.get('uncertain') else 'complete',
                       upstream=(3,))
            return tx

        async def translation_result(name):
            result=await timing.run(name,'step_10_translation_job',translation(name))
            if result is not None:
                return result
            source=await source_ready[name]
            if source is None:
                return None
            missing=[row['source_id'] for row in source_units(source) if row['relation']=='translated']
            result={'groups':[],'links':[],'unresolved_source_ids':missing,
                    'counts':{'needed':len(missing),'complete':0,'uncertain':0,'unresolved':len(missing)},
                    'issues':[{'stage':'B1','source_ids':missing,'reason':'译文任务未返回产物'}]}
            write(self.root/'jobs'/name/'translation-initial.json',result)
            self.event(name,10,'translation_job_failed',result['counts'],'failed',upstream=(3,))
            return result

        async def one(name,record):
            previous_result=self.root/'jobs'/name/'run.json'
            refresh_translation=(resume_attachments and previous_result.is_file()
                                 and read(previous_result).get('translation',{}).get('unresolved',0)>0)
            tx=asyncio.create_task(translation_result(name))
            visual=asyncio.create_task(timing.run(name,'step_11_visual_job',visual_sources(name)))
            try:
                if resume_attachments:
                    prior=self.root/'upstream07'/name
                    inputs=self.root/'inputs'/name
                    result=read(prior/'run.json')
                    writer_receipt=read(inputs/'step04-run.json')
                    source=read(inputs/'source-index.json')
                    if (checksum(prior/'body.fused.md')!=result['fused_sha256'] or
                            source['source_files']!=writer_receipt['source_file_shas']):
                        raise ValueError('已有正文或原件快照与本轮记录不符')
                    source_ready[name].set_result(source)
                else:
                    source=await self.upstream(record,normal_ready,planned,source_ready,block)
                result=await finish_batch(self.root,providers,timing,{name:source},write_result=False,
                                          early_visual=visual,translation_task=tx,on_update=preview,
                                          refresh_translation=refresh_translation)
                if name in result['failures']:
                    raise RuntimeError(result['failures'][name])
                if record['mode']=='NORMAL' and self.commit_normal:
                    receipt=await block(self.isolated_save,name)
                    card=read(self.root/'inputs'/name/'task-card.json')
                    project=Project(self.data,record['project_id'])
                    await block(self.save_fresh_for_finale,project,name,card,None,self.root/'jobs'/name/'body.final.md',receipt)
                    self.event(name,20,'commit_receipted',{'commit_id':receipt['commit_id'],
                            'progress_ids':receipt['progress']['updated_file_ids']},upstream=(17,))
                    normal_ready[record['project_id']].set_result(True)
                finished[name]=time()
                failures.pop(name,None)
                print(name,'STEP-10 ready',flush=True)
            except Exception as exc:
                failures[name]=str(exc)
                print(name,'BLOCKED',str(exc),flush=True)
                if not source_ready[name].done():
                    source_ready[name].set_result(None)
                if record['mode']=='NORMAL' and self.commit_normal and not normal_ready[record['project_id']].done():
                    normal_ready[record['project_id']].set_exception(RuntimeError('本轮 NORMAL 未完成，收尾缺少新正文'))
            finally:
                await asyncio.gather(tx,visual)
        await asyncio.gather(*(one(n,row) for n,row in records.items()))
        persist(self.root,'whiteboard' if only is None else 'whiteboard-resume',providers,timing)
        write(self.root/'finish-result.json',{'samples':sorted(finished),'ready':len(finished),
                                               'failures':failures,'ended_at':time()})
        write(self.root/'whiteboard-result.json',{'started_at':self.manifest['started_at'],
              'ended_at':time(),'finished':finished,'failures':failures,'prior_run_reused':0})
        self.pool.shutdown()
        self.writers.pool.shutdown()
        self.step56.close()
        self.engine.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('corpus',type=Path)
    parser.add_argument('sample_metadata',type=Path)
    parser.add_argument('--sol',type=int,default=40)
    parser.add_argument('--legacy',type=Path,required=True)
    parser.add_argument('--preview-url',required=True)
    a=parser.parse_args()
    root=a.root.resolve()
    if (root/'whiteboard-inputs.json').exists() or any((root/name).exists() for name in
            ('inputs','requests','jobs','planners','writer-runs','step56','step07-work','upstream07','runs')):
        raise ValueError('白板试验应使用新的结果目录')
    sys.path.insert(0,str(a.legacy.resolve(strict=True)))
    os.environ['MANUALREADER_STEP56_FORCE_FRESH']='1'
    case=stage_fixture(root,a.corpus,a.sample_metadata)
    asyncio.run(Whiteboard(root,case,a.sol,a.preview_url).run())


if __name__=='__main__':
    main()
