"""Persist the work that is ready for each Reader task as it becomes available."""

from datetime import datetime, timezone
from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path
from threading import Lock
from time import time

from manualreader.stages.step03.cache import atomic
from manualreader.artifacts import write_json as write

REGISTRY = json.loads(files(__package__).joinpath('step_registry.json').read_bytes())
SLUG = {entry['step_id']:entry['slug'] for entry in REGISTRY}
STATUSES = {'pending','running','partial_ready','complete','blocked','failed','stale','skipped'}


def fingerprint(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


class RunJournal:
    def __init__(self,root,run_id,samples):
        self.root=Path(root).resolve()/ 'runs'/run_id
        self.root.mkdir(parents=True,exist_ok=True)
        self.run_id=run_id
        self.samples=samples
        self.nodes={}
        self.lock=Lock()
        self.started=time()
        manifest=self.root/'run_manifest.json'
        if manifest.is_file():
            prior=json.loads(manifest.read_bytes())
            if prior['run_id']!=run_id or prior['tasks']!=samples:
                raise ValueError('本轮任务身份已经变化')
            self.started=prior['started_at']
            path=self.root/'events.jsonl'
            if path.is_file():
                raw=path.read_bytes()
                lines=raw.splitlines(keepends=True)
                for index,line in enumerate(lines):
                    try:
                        row=json.loads(line)
                    except (UnicodeDecodeError,json.JSONDecodeError):
                        if index+1!=len(lines) or line.endswith(b'\n'):
                            raise
                        # Repair only an interrupted append; retain all completed events.
                        atomic(path,b''.join(lines[:index]))
                        break
                    self.nodes[row['event_id']]=row
                else:
                    if raw and not raw.endswith(b'\n'):
                        atomic(path,raw+b'\n')
        else:
            write(manifest,{'run_id':run_id,'steps':REGISTRY,'tasks':samples,
                           'started_at':self.started,'first_model_directory_empty':True})

    def emit(self,task,number,event_type,status='complete',*,unit_id=None,unit_type='chapter',
             value=None,upstream=(),source_version=None,started=None,queued=None,reason=None):
        if status not in STATUSES:
            raise ValueError('未知任务状态')
        step=f'STEP-{number:02}'
        timestamp=time()
        unit_id=unit_id or task
        key=fingerprint([task,step,unit_type,unit_id,event_type])[:24]
        with self.lock:
            prior=[self.nodes[k] for k in upstream if k in self.nodes]
            input_hash=fingerprint([[p['event_id'],p['artifact']['sha256']] for p in prior]+[source_version])
            payload=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
            event_id=fingerprint([self.run_id,key,status,input_hash,sha256(payload).hexdigest()])
            if event_id in self.nodes:
                return event_id
            folder=self.root/'tasks'/task/SLUG[step]
            artifact=folder/(event_id+'.json')
            atomic(artifact,payload)
            row={'event_id':event_id,'run_id':self.run_id,'project_id':self.samples[task]['project_id'],
                 'task_id':task,'step_id':step,'unit_type':unit_type,'unit_id':unit_id,
                 'source_version':source_version,'input_fingerprint':input_hash,'schema_version':'step-artifact-v1',
                 'upstream_versions':[{'event_id':p['event_id'],'step_id':p['step_id'],
                                       'artifact_hash':p['artifact']['sha256']} for p in prior],
                 'event_type':event_type,'status':status,
                 'artifact':{'path':artifact.relative_to(self.root).as_posix(),'sha256':sha256(payload).hexdigest()},
                 'occurred_at':datetime.fromtimestamp(timestamp,timezone.utc).isoformat(),
                 'queued_at':queued,'start_at':started,'ended_at':timestamp,'failure_reason':reason}
            self.nodes[event_id]=row
            with (self.root/'events.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            return event_id

    def invalidate(self,task,versions):
        with self.lock:
            old=list(self.nodes.values())
        affected=set()
        for entry in old:
            if entry['task_id']==task and entry['status']!='stale' and (
                    entry['source_version'] in versions or
                    any(p['event_id'] in affected for p in entry['upstream_versions'])):
                affected.add(entry['event_id'])
                self.emit(task,int(entry['step_id'][-2:]),'input_invalidated','stale',
                          unit_id=entry['unit_id'],unit_type=entry['unit_type'],upstream=[entry['event_id']],
                          reason='原件文件版本已变更')
        return affected
