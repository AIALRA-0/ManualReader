"""Read verified Next events and model receipts for one isolated project."""

from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
import json

from manualreader.steps.events import REGISTRY
from .preview import IDENTITY, SAMPLE, optional


def _task(run, material):
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
    planner=run/'planners'/sample
    artifacts=[('STEP-02',planner/'model','planner-prompt.txt',planner/'model-input.json','first-response.json')]
    evidence=optional(run/'inputs'/sample,'writer-evidence.json',{})
    task_id=evidence.get('task_id','')
    if IDENTITY.fullmatch(task_id):
        writer=run/'writer-runs'/task_id
        artifacts.append(('STEP-04',writer,'writer-prompt.txt',writer/'writer-input.json','chapter.md'))
    for directory in sorted((run/'step56'/'jobs'/sample/'model').glob('*')):
        stage=directory.name.split('-')[0]
        if stage in ('05','06A','06B'):
            artifacts.append(('STEP-'+stage,directory,'prompt.txt',directory/'input.json','first-response.json'))
    for directory in sorted((run/'step07-work'/'jobs'/sample).glob('**/model')):
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
