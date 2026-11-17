"""Execute, persist and replay independent Writer jobs."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import re
from threading import Lock, RLock
from time import perf_counter, sleep, time

from ...transport import PROFILE, executable, save
from . import PROMPT_HASHES, prompt
from .draft_assets import check_references
from .sources import SourceUnavailable, assemble, context_for
from .writer_transport import invoke


def digest(raw):
    return sha256(raw).hexdigest()


def inspect_output(body, receipt):
    response = receipt.get('response', {})
    return_type = response.get('status')
    if return_type == 'incomplete':
        return 'incomplete'
    if return_type != 'completed' or response.get('model') != PROFILE['model']:
        return 'transport_failure'
    if response.get('service_tier') not in (None, PROFILE['service_tier']):
        return 'transport_failure'
    return 'complete' if body.strip() else 'incomplete'


class Jobs:
    def __init__(self, root, engine, workers, *, writer=invoke, cli=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.engine = engine
        self.cli = cli or executable()
        self.writer = writer
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='step04-writer')
        self.guard = Lock()
        self.state_guard = RLock()
        self.active = 0
        self.peak = 0

    def folder(self, job_id):
        if not isinstance(job_id,str) or not re.fullmatch(r'[a-f0-9]{32}',job_id):
            raise ValueError('Writer 任务编号无效')
        return self.root/job_id

    def status(self, job_id, project_id=None):
        folder = self.folder(job_id)
        with self.state_guard:
            view = json.loads((folder/'run.json').read_bytes())
        if project_id is not None and view['project_id'] != project_id:
            raise ValueError('Writer 任务不属于该项目')
        return view

    def save_state(self, folder, view):
        # A Windows reader or indexer can briefly keep the old file open.
        with self.state_guard:
            for delay in (.02,.05,.1,.25,.5):
                try:
                    save(folder/'run.json',view)
                    return
                except PermissionError:
                    sleep(delay)
            save(folder/'run.json',view)

    def submit(self, project, round_id, mode, raw, confirm_finale=False, revise=False, resume=False):
        system = prompt(mode)
        card = json.loads(raw)
        if revise and mode != 'ANSWER':
            raise ValueError('复答选项仅用于 ANSWER')
        state, context = context_for(project, round_id, mode, card, confirm_finale, revise)
        identity = [project.identity,round_id,mode,digest(raw),PROMPT_HASHES[mode],PROFILE,revise]
        job_id = digest(json.dumps(identity,sort_keys=True).encode())[:32]
        folder = self.folder(job_id)
        resumed = False
        with self.guard:
            if (folder/'run.json').exists():
                run = self.status(job_id)
                attempts = len(list(folder.glob('attempt-before-dispatch*')))
                prior = folder/('attempt-before-dispatch' if attempts==0 else f'attempt-before-dispatch-{attempts+1}')
                receipt_path = folder/'model-receipt.json'
                if not (resume and run['status']=='transport_failure' and not run['output_file']
                        and run['request_attempts']==0 and attempts<3 and receipt_path.exists()):
                    return run
                if json.loads(receipt_path.read_bytes()).get('provider_responses'):
                    return run
                with self.state_guard:
                    prior.mkdir()
                    for name in ('run.json','model-receipt.json','stderr.txt','model-events.jsonl','writer-input.json',
                                 'writer-prompt.txt','original-read-trace.jsonl','transport.json'):
                        path=folder/name
                        if path.is_file():
                            path.replace(prior/name)
                resumed = True
            folder.mkdir(exist_ok=True)
            (folder/'task-card.json').write_bytes(raw)
            meta = {'task_id':job_id, 'round_id':round_id, 'project_id':project.identity,
                    'mode':mode, 'task_card_ref':'task-card.json', 'task_card_sha256':digest(raw),
                    'source_set':card['material_plan'], 'writer_prompt_sha256':PROMPT_HASHES[mode],
                    'model_profile':PROFILE, 'fixture_based':bool(context['fixture']),
                    'user_context_ref':'writer-input.json', 'status':'queued', 'queued_at':time(),
                    'resumed_before_provider_dispatch':resumed}
            self.save_state(folder,meta)
            self.pool.submit(self.run,folder,meta,project,state,context,card,system,perf_counter())
        return meta

    def run(self, folder, meta, project, state, context, card, system, queued):
        began = perf_counter()
        meta.update(status='preparing',queue_seconds=began-queued)
        self.save_state(folder,meta)
        payload = {}
        documents = []
        receipt = {}
        output = b''
        mode = meta['mode']
        try:
            payload, documents, images = assemble(
                self.engine,project,meta['round_id'],mode,card,state,context)
            raw = json.dumps(payload,ensure_ascii=False,separators=(',',':'))
            (folder/'writer-input.json').write_bytes(raw.encode('utf8'))
            with (folder/'original-read-trace.jsonl').open('w',encoding='utf8') as trace:
                for doc in documents:
                    row = {k:v for k,v in doc.items() if k not in ('structures','original','boundary_context')}
                    row['positions'] = [r['n'] for r in doc['original']]
                    row['boundary_positions'] = [r['n'] for r in doc['boundary_context']]
                    trace.write(json.dumps(row,ensure_ascii=False)+'\n')
                for image in payload['original_images']:
                    trace.write(json.dumps(image,ensure_ascii=False)+'\n')
            meta.update(status='writing')
            self.save_state(folder,meta)
            with self.guard:
                self.active += 1
                self.peak = max(self.peak,self.active)
            start = perf_counter()
            try:
                output,receipt = self.writer(folder,system,raw,images,self.cli)
            finally:
                meta['writer_seconds'] = perf_counter()-start
                with self.guard:
                    self.active -= 1
            status = inspect_output(output,receipt)
        except SourceUnavailable as error:
            status = 'source_unavailable'
            meta['error'] = str(error)
        except Exception as error:
            status = 'transport_failure'
            meta['error'] = f'{type(error).__name__}: {error}'
        response = receipt.get('response') or {}
        usage = response.get('usage') or {}
        requests = receipt.get('provider_responses',[])
        output_name = 'answer.md' if mode=='ANSWER' else 'chapter.md'
        if output:
            (folder/output_name).write_bytes(output)
        references = check_references(folder,output,documents,self.engine,project,meta['round_id'])
        if status=='complete' and any(not row['available'] for row in references):
            status = 'incomplete'
        if not (folder/'model-receipt.json').exists():
            save(folder/'model-receipt.json',receipt)
        meta.update(
            status=status, wall_seconds=perf_counter()-queued, ended_at=time(),
            calls=sum(row.get('status') is not None for row in requests),
            request_attempts=len(requests), output_file=output_name if output else None,
            output_hash=digest(output) if output else None, output_chars=len(output.decode('utf8')),
            model=response.get('model'), effort=PROFILE['effort'] if requests else None,
            service_tier=response.get('service_tier'),
            source_version={d['file_id']:d['file_revision'] for d in documents},
            source_file_shas={d['file_id']:d['sha256'] for d in documents},
            source_read_trace='original-read-trace.jsonl' if payload else None,
            original_positions=sum(len(d['original']) for d in documents),
            local_cache=[hit for d in documents for hit in d['local_cache']],
            image_sha256=list(dict.fromkeys(h for row in requests for h in row.get('image_sha256',[]))),
            input_tokens=usage.get('input_tokens'),
            cached_input_tokens=(usage.get('input_tokens_details') or {}).get('cached_tokens'),
            output_tokens=usage.get('output_tokens'),
            reasoning_tokens=(usage.get('output_tokens_details') or {}).get('reasoning_tokens'),
            provider_cost=None, incomplete_details=response.get('incomplete_details'),
            retry_reason=[row.get('status') for row in requests[:-1]],
            resource_references=references, provider_status=[row.get('status') for row in requests],
        )
        self.save_state(folder,meta)
