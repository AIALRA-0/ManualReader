"""Run one STEP-07 V2 request for every frozen STEP-04/06 task."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib.resources import files
import argparse
import json
import os
from pathlib import Path
import subprocess
from time import perf_counter, time

from ...transport import PROFILE as BASE_PROFILE, executable
from ..step04.writer_transport import MarkdownTransport
from ..step05.contracts import arr, digest, obj, TEXT
from manualreader.artifacts import read_json as read, write_json as write
from ..step03.cache import atomic


PROFILE = {**BASE_PROFILE, 'effort': 'low'}
PROMPT_NAME = 'STEP07_V2_EDITOR.md'
PROMPT_SHA = '2a8bcf289eaca494faee4fa06157b098c569e6601dde96a254972c1f31f43a76'
POLICY_SHA = 'c71c10d7348401cf49b9fcc38dd3af2023378fd81d0775852f79ba9af5cec31f'
NULLABLE = {'type':['string','null']}
REVISION = obj(original_exact=TEXT, replacement=TEXT)
SCHEMA = obj(
    supplement_decisions=arr(obj(overlay_id=TEXT, action={'type':'string','enum':[
        'keep_inline','move_inline','defer','omit_duplicate']}, new_anchor_quote=TEXT,
        insert_side={'type':'string','enum':['before','after','']},
        text_revisions=arr(REVISION), reason=TEXT)),
    c3_edits=arr(obj(original_exact=TEXT, replacement=TEXT, source_ids=arr(TEXT),
                     issue_type=TEXT, reason=TEXT)),
    transition_edits=arr(obj(overlay_id=NULLABLE, side={'type':'string','enum':['before','after','between']},
                             operation={'type':'string','enum':['insert','replace']},
                             anchor_quote=TEXT, original_exact=NULLABLE, replacement=TEXT, reason=TEXT)),
)


class FusionTransport(MarkdownTransport):
    def prepare(self, body):
        if body.get('reasoning', {}).get('effort') != PROFILE['effort']:
            raise ValueError('STEP-07 requires low reasoning effort')
        # The inherited request validator checks the earlier stage's fixed profile.
        canonical = {**body, 'reasoning': {**body['reasoning'], 'effort': BASE_PROFILE['effort']}}
        request, hashes = super().prepare(canonical)
        request['reasoning'] = {**request['reasoning'], 'effort': PROFILE['effort']}
        request['text'] = {'format': {'type':'json_schema','name':'reader_step07_v2',
                                      'strict':True,'schema':SCHEMA}}
        write(self.folder/'dispatch.json', {'profile': PROFILE, 'image_sha256': hashes,
                                            'prompt_sha256':PROMPT_SHA,'policy_sha256':POLICY_SHA,
                                            'schema_sha256':digest(json.dumps(SCHEMA,sort_keys=True))})
        return request, hashes


def input_files(root, sample):
    inputs, previous = root/'inputs'/sample, root/'previous'/sample
    run = read(inputs/'step04-run.json')
    if run['output_file'] not in ('chapter.md', 'answer.md'):
        raise ValueError('Unexpected STEP-04 output filename')
    body = (inputs/run['output_file']).read_bytes().decode('utf8')
    if digest(body.encode()) != run['output_hash']:
        raise ValueError('STEP-04 body hash differs from its receipt')
    card = read(inputs/'task-card.json')
    source = read(inputs/'source-index.json')
    return inputs, previous, run, body, card, source


def prepare(root, sample):
    _, previous, run, body, card, source = input_files(root, sample)
    anchors = read(previous/'anchors.json')
    overlays = read(previous/'overlays.json')
    mapping = read(previous/'step06-merged-mapping.json')
    coverage = read(previous/'step06-goal-question-coverage.json')
    positions = {a['anchor_id']: i for i,a in enumerate(anchors)}
    nearby = defaultdict(set)
    for edge in mapping['links']:
        nearby[edge['body_anchor_id']].add(edge['source_id'])
    evidence = {item for patch in overlays for item in patch['source_ids']}
    used_assets = {item for patch in overlays for item in patch['asset_refs']}
    panels = []
    for patch in overlays:
        pos = positions[patch['anchor_id']]
        context = anchors[max(0,pos-1):pos+2]
        panels.append({**patch,'overlay_id':patch['patch_id'],
                       'context':[{k:a[k] for k in ('anchor_id','text')} for a in context],
                       'related_overlays':[x['patch_id'] for x in overlays
                                           if x is not patch and (positions[x['anchor_id']]==pos
                                               or set(x['source_ids']).intersection(patch['source_ids']))]})
    focused = [{k:x[k] for k in ('source_id','position','text','asset_refs')}
               for x in source['sources'] if x['source_id'] in evidence]
    assets = source['assets']
    lookup = assets if isinstance(assets,dict) else {a['asset_id']:a for a in assets}
    media = {key:lookup[key] for key in sorted(used_assets) if key in lookup}
    raw_policy=files('manualreader.stages.step02').joinpath('reader_step02_policies.json').read_bytes()
    if digest(raw_policy)!=POLICY_SHA:
        raise ValueError('STEP-02 frozen writing rules hash mismatch')
    return {'frozen_writing_policies':raw_policy.decode('utf8'),
            'task_id':run['task_id'],'mode':run['mode'],'chapter':card.get('chapter') or card['question'],
            'c3_markdown':body,'overlays':panels,'source_context':focused,'assets':media,
            'source_ids_by_anchor':{k:sorted(v) for k,v in sorted(nearby.items())},
            'goal_items':coverage['goal_items'],'question_items':coverage['question_items'],
            'question_raw_units':coverage['question_raw_units']}, overlays


def invoke(folder, payload, cli, submitted):
    outcome = folder/'outcome.json'
    if outcome.exists():
        return read(outcome)
    if (folder/'dispatch.json').exists():
        return {'state':'interrupted_after_dispatch'}
    folder.mkdir(parents=True, exist_ok=True)
    system = files(__package__).joinpath(PROMPT_NAME).read_bytes()
    if digest(system) != PROMPT_SHA:
        raise ValueError('STEP-07 frozen prompt hash mismatch')
    prompt_path = folder/'prompt.md'
    atomic(prompt_path, system)
    raw = json.dumps(payload, ensure_ascii=False, separators=(',',':'))
    atomic(folder/'request.json', raw.encode())
    if not cli:
        raise RuntimeError('Codex executable not found')
    began = perf_counter()
    started_at = time()
    # Fusion edits text and moves intact media, so the request carries image references.
    proxy = FusionTransport(folder,raw,system.decode('utf8'),{}).start()
    out = folder/'first-message.json'
    args = [cli,'exec','--ignore-user-config','--ignore-rules','--skip-git-repo-check',
            '--json','--color','never','-m',PROFILE['model'],
            '-c','model_reasoning_effort="low"','-c','service_tier="default"',
            '-c','web_search="disabled"','-c','approval_policy="never"','-s','read-only',
            '-C',str(folder),'-c','model_instructions_file='+json.dumps(str(prompt_path)),
            '-c','openai_base_url='+json.dumps(f'http://127.0.0.1:{proxy.server.server_port}'),
            '-o',str(out)]
    for flag in ('apps','plugins','multi_agent','shell_tool','browser_use','computer_use',
                 'workspace_dependencies','hooks','fast_mode','image_generation'):
        args += ['--disable',flag]
    env = {k:v for k,v in os.environ.items() if k not in ('CODEX_THREAD_ID','CODEX_SESSION_ID','PYTHONPATH')
           and not k.startswith(('MANUALREADER_TOKEN','MANUAL_READER_TOKEN','MANUAL_READER_PAIRING'))}
    error = None
    try:
        with (folder/'events.jsonl').open('wb') as events, (folder/'stderr.txt').open('wb') as errors:
            result = subprocess.run(args+['-'],input=raw.encode(),stdout=events,stderr=errors,
                                    env=env,timeout=900,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:
                error = f'CLI {result.returncode}'
    except (OSError,subprocess.TimeoutExpired) as exc:
        error = type(exc).__name__
    finally:
        proxy.stop()
    response = next((r['response'] for r in proxy.receipts if r.get('response')), {})
    actual = proxy.first_text or (out.read_bytes() if out.exists() else b'') or ''.join(proxy.partial_text).encode()
    atomic(folder/'raw-response.txt',actual)
    try:
        reply = json.loads(actual)
    except (ValueError,UnicodeError):
        reply = {}
    good = (response.get('status')=='completed' and response.get('model')==PROFILE['model']
            and response.get('service_tier') in (None,'default') and isinstance(reply,dict)
            and set(reply)==set(SCHEMA['required']) and all(isinstance(v,list) for v in reply.values()))
    identifier = None
    events = folder/'events.jsonl'
    if events.exists():
        for line in events.read_bytes().splitlines():
            try:
                event = json.loads(line)
                if event.get('type')=='thread.started':
                    identifier = event.get('thread_id')
                    break
            except ValueError:
                continue
    result = {'state':'complete' if good else 'model_error', 'reply':reply if good else {},
              'provider_responses':proxy.receipts,'response':response,'cli_thread_id':identifier,
              'profile':PROFILE,'prompt_sha256':PROMPT_SHA,'policy_sha256':POLICY_SHA,'cli_error':error,
              'started_at':started_at,'ended_at':time(),'queue_seconds':max(0,started_at-submitted),
              'wall_seconds':perf_counter()-began,'cost':'unknown','image_bytes':0}
    write(outcome,result)
    return result


def verify_freeze(root, samples):
    manifest=root/'model-input-freeze.json'
    paths=set()
    for sample in samples:
        for folder in (root/'inputs'/sample,root/'previous'/sample):
            paths.update(p for p in folder.glob('*') if p.is_file())
    workspace=root/'workspace'
    if workspace.is_dir():
        paths.update(p for p in (workspace/'src/manualreader').rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts)
        paths.add(workspace/'pyproject.toml')
    value={'prompt_sha256':PROMPT_SHA,'policy_sha256':POLICY_SHA,
           'schema_sha256':digest(json.dumps(SCHEMA,sort_keys=True)),
           'files':{p.relative_to(root).as_posix():digest(p.read_bytes()) for p in sorted(paths)}}
    if manifest.exists() and read(manifest)!=value:
        raise ValueError('STEP-07 V2 input or code changed after freeze')
    write(manifest,value)
    write(root/'model-schema.json',SCHEMA)


def batch(root, parallel, cli):
    by_mode = defaultdict(list)
    for path in sorted((root/'previous').iterdir()):
        by_mode[read(root/'inputs'/path.name/'step04-run.json')['mode']].append(path.name)
    if set(by_mode)!={'NORMAL','INTRO','FINALE','ANSWER'} or any(len(v)!=10 for v in by_mode.values()):
        raise ValueError('Expected 40 real STEP-04/06 runs, ten per mode')

    def task(sample, submitted):
        model = root/'jobs'/sample/'model'
        model.mkdir(parents=True,exist_ok=True)
        try:
            payload, _ = prepare(root,sample)
            result = invoke(model,payload,cli,submitted)
        except (OSError,ValueError,RuntimeError,KeyError) as exc:
            result = {'state':'input_error','error':str(exc),'provider_responses':[]}
            if not (model/'outcome.json').exists():
                write(model/'outcome.json',result)
        print(sample,result['state'],flush=True)
        return result

    began, started = perf_counter(), time()
    sample_order = [sample for seq in by_mode.values() for sample in seq]
    verify_freeze(root,sample_order)
    executed = {}
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        pending = {pool.submit(task,s,started):s for s in sample_order}
        for future in as_completed(pending):
            executed[pending[future]] = future.result()
    write(root/'model-batch.json',{'state':'complete','started_at':started,'ended_at':time(),
          'wall_seconds':perf_counter()-began,'worker_count':parallel,
          'tasks':{k:v['state'] for k,v in executed.items()}})
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('root',type=Path)
    p.add_argument('--parallel',type=int,default=40)
    p.add_argument('--codex',default=executable())
    args = p.parse_args()
    return batch(args.root.resolve(),args.parallel,args.codex)


if __name__=='__main__':
    raise SystemExit(main())
