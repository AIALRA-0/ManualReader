"""Read verified Next events and model receipts for one isolated project."""

from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
import json

from manualreader.steps.events import REGISTRY
from .preview import IDENTITY, SAMPLE, optional


def _task(run, material):
    connected=optional(run,'connected-run.json',{})
    if connected.get('project_id')==material:
        return 'chapter',{'run_id':run.name,'started_at':connected['started_at'],
                          'connected':True,'samples':{'chapter':{'project_id':material}}},run
    sample=material.removeprefix('next-final-')
    if sample==material or not SAMPLE.fullmatch(sample):
        return None,None,None
    manifest=optional(run,'whiteboard-inputs.json',{})
    if sample not in manifest.get('samples',{}):
        return None,None,None
    name=manifest.get('run_id','')
    if not SAMPLE.fullmatch(name):
        return None,None,None
    return sample,manifest,run/'runs'/name


def next_run(run, material):
    sample,manifest,journal=_task(run,material)
    if not sample:
        return None
    path=journal/'events.jsonl'
    try:
        info=path.stat()
    except FileNotFoundError:
        events={}
    else:
        events=_journal_by_task(path,info.st_size,info.st_mtime_ns).get((manifest['run_id'],sample),{})
    finished=optional(run/'jobs'/sample,'run.json',{})
    if manifest.get('connected'):
        recorded=optional(run/'metrics','connected-stages.json',{})
        events.update({row['id']:row for row in recorded.get('stages',[])})
        outputs={1:run/'inputs/chapter/source-index.json',2:run/'inputs/chapter/task-card.json',
                 3:run/'inputs/chapter/source-index.json',4:run/'inputs/chapter/chapter.md',
                 5:run/'joint/jobs/chapter/step05-mapping.json',6:run/'joint/jobs/chapter/overlays.json',
                 7:run/'upstream07/chapter/body.fused.md',8:run/'jobs/chapter/part-blocks.json',
                 9:run/'jobs/chapter/reading-groups.json',10:run/'jobs/chapter/translation-groups.json',
                 11:run/'jobs/chapter/translation-visuals.json',12:run/'jobs/chapter/resource-explanations.json',
                 13:run/'jobs/chapter/term-candidates.json',14:run/'jobs/chapter/quiz-candidates.json',
                 15:run/'jobs/chapter/chapter-guidance.json',16:run/'jobs/chapter/body.final.md',
                 17:run/'delivery.json',18:run/'jobs/chapter/alignment.json',19:run/'delivery.json'}
        for number,path in outputs.items():
            if not path.is_file() and 'STEP-'+f'{number:02d}' in events:
                continue
            events['STEP-'+f'{number:02d}']={'events':int(path.is_file()),
                 'ended_at':path.stat().st_mtime if path.is_file() else None,
                 'status':'complete' if path.is_file() else 'partial'}
        events['STEP-20']={'events':0,'ended_at':None,'status':'awaiting_user_confirmation'}
    return {'run_id':manifest['run_id'],'sample_id':sample,'started_at':manifest['started_at'],
            'stages':[{'id':step['step_id'],'name':step.get('title') or step['slug'],
                       **events.get(step['step_id'],{'events':0,'ended_at':None,'status':None})}
                      for step in REGISTRY],
            'resource_status_counts':finished.get('p2',{}),'resource_scope':finished.get('p2_scope',{}),
            'translation':finished.get('translation',{}),'parts':finished.get('parts'),
            'sources':finished.get('source_navigation',{}),
            'final_seconds':finished.get('elapsed_seconds')}


@lru_cache(maxsize=12)
def _journal_by_task(path, size, modified_ns):
    by_task=defaultdict(dict)
    with path.open('rb') as stream:
        for line in stream:
            try:
                event=json.loads(line)
            except (UnicodeDecodeError,json.JSONDecodeError):
                continue
            if not isinstance(event,dict):
                continue
            task_id=(event.get('run_id'),event.get('task_id'))
            stage_id=event.get('step_id')
            latest=by_task[task_id].setdefault(stage_id,{'events':0,'ended_at':None,'status':None})
            latest.update(events=latest['events']+1,ended_at=event.get('ended_at'),status=event.get('status'))
    return by_task


@lru_cache(maxsize=512)
def _cached_text(path, size, modified_ns, limit):
    with path.open('rb') as file:
        return file.read(limit).decode('utf-8',errors='replace')


def _text(path, limit=65000):
    try:
        info=path.stat()
        return _cached_text(path,info.st_size,info.st_mtime_ns,limit) if path.is_file() else ''
    except FileNotFoundError:
        return ''


def _upstream_calls(run,sample):
    if (run/'connected-run.json').is_file():
        for step,name in (('STEP-02','step02.json'),('STEP-04','step04.json')):
            value=optional(run/'metrics',name,{})
            actual=value.get('model_usage',value)
            for index,usage in enumerate(actual.get('requests',[]),1):
                yield {'id':step+' · '+str(index),'metrics':{'model':actual.get('model'),
                       'usage':usage,'seconds':actual.get('seconds') if index==1 else None,'cost_usd':None},
                       'instructions':'','input':'','output':'','processing':''}
    planner=run/'planners'/sample
    artifacts=[('STEP-02',planner/'model','planner-prompt.txt',planner/'model-input.json','first-response.json')]
    evidence=optional(run/'inputs'/sample,'writer-evidence.json',{})
    task_id=evidence.get('task_id','')
    if IDENTITY.fullmatch(task_id):
        writer=run/'writer-runs'/task_id
        artifacts.append(('STEP-04',writer,'writer-prompt.txt',writer/'writer-input.json','chapter.md'))
    joint='joint' if (run/'connected-run.json').is_file() else 'step56'
    for directory in sorted((run/joint/'jobs'/sample/'model').glob('*')):
        stage=directory.name.split('-')[0]
        if stage in ('05','06A','06B'):
            artifacts.append(('STEP-'+stage,directory,'prompt.txt',directory/'input.json','first-response.json'))
    fusion='fusion' if (run/'connected-run.json').is_file() else 'step07-work'
    for directory in sorted((run/fusion/'jobs'/sample).glob('**/model')):
        artifacts.append(('STEP-07',directory,'prompt.md',directory/'request.json','raw-response.txt'))
    for stage,folder,instructions,input_path,output in artifacts:
        for index,attempt in enumerate(optional(folder,'transport.json',[]),1):
            response=attempt.get('response') or {}
            yield {'id':stage+' · '+folder.name+' · '+str(index),
                   'metrics':{'model':response.get('model'),'usage':response.get('usage') or {},
                              'seconds':attempt.get('seconds'),'cost_usd':None},
                   'instructions':_text(folder/instructions),'input':_text(input_path),
                   'output':_text(folder/output),'processing':''}


def next_calls(run, material):
    sample,manifest,_=_task(run,material)
    if not sample:
        return None
    recorded=optional(run/'metrics','connected-calls.json',{})
    if recorded.get('material_id')==material and not (run/'requests').is_dir():
        return {**recorded,'run_id':manifest['run_id']}
    known={}
    metrics=run/'metrics'
    consumer_ids={row['id'] for p in metrics.glob('*-consumers.json')
                  for row in optional(metrics,p.name,[]) if row.get('sample_id')==sample}
    for p in metrics.glob('*-requests.json'):
        for row in optional(metrics,p.name,[]):
            if row.get('sample_id')==sample or row.get('id') in consumer_ids:
                known[row['id']]=row
    # A resumed run can reuse a valid result from an earlier invocation of the same run.
    for p in (run/'requests').glob('*/*/receipt.json'):
        row=optional(p.parent,p.name,{})
        if (row.get('sample_id')==sample or row.get('id') in consumer_ids) and row.get('id'):
            known[row['id']]=row
    calls=list(_upstream_calls(run,sample))
    usage=Counter()
    for row in sorted(known.values(),key=lambda item:item.get('ready_at',0)):
        stage,identity=row['stage'],row['id']
        if not (IDENTITY.fullmatch(stage) and IDENTITY.fullmatch(identity)):
            continue
        parts=run/'requests'/stage/identity
        attempts=sorted(parts.glob('attempt-*'),key=lambda p:int(p.name.rsplit('-',1)[-1]))
        last=attempts[-1] if attempts else parts
        reported=row.get('response',{}).get('usage') or {}
        calls.append({'id':stage+' · '+identity[:12],
                      'metrics':{'model':row.get('profile',{}).get('model'),'usage':reported,
                                 'seconds':round(row['ended_at']-row['ready_at'],3),
                                 'cost_usd':row.get('cost_usd')},
                      'instructions':_text(last/'prompt.md'),
                      'input':_text(parts/'input.json'),'output':_text(last/'raw-output.txt'),'processing':''})
    for row in optional(run,'native-answer-demo.json',{}).get('answers',[]):
        if row.get('sample_id')==sample:
            calls.append({'id':'回答 · '+row['question_id'],'metrics':row.get('metrics',{}),
                          'instructions':'','input':row['question'],'output':row['answer'],'processing':''})
    counted=set()
    for call in calls:
        key=call['metrics'].get('group_id') or call['id']
        if key in counted:
            continue
        counted.add(key)
        for name,value in (call['metrics'].get('usage') or {}).items():
            if isinstance(value,(int,float)):
                usage[name]+=value
    return {'material_id':material,'run_id':manifest['run_id'],'calls':calls,'usage':dict(usage),
            'accounting':'显示该材料的实际运行回执及适用的共享请求。金额由供应商账单确认。'}
