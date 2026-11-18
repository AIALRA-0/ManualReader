"""Use the verified Reader providers, with shared deduplication and actual receipts."""

import asyncio
from contextlib import asynccontextmanager
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import subprocess
from time import perf_counter, time, monotonic

import requests

from ...transport import PROFILE as BASE_PROFILE, executable
from ..step03.cache import atomic
from ..step04.writer_transport import MarkdownTransport
from ..step05.contracts import digest
from manualreader.artifacts import read_json as read, write_json as write
from .contracts import SCHEMAS
from .kfs_slots import DEFAULT_DS_CONCURRENCY, KfsSlots, OVERLOAD_STATUSES


SOL=dict(BASE_PROFILE)
DS={'model':'deepseek-v4.1-flash','effort':'disabled','endpoint':'https://kuafushe.cc/v1/chat/completions'}
DS_CREDENTIAL='AIALRA_KUAFUSHE_DS_PRIMARY_KEY'
STAGE_ROOT=files(__package__)
PROMPT_MANIFEST=STAGE_ROOT.joinpath('prompt-manifest.json')
STEPS=files('manualreader.steps')
ALIASES=json.loads(STEPS.joinpath('compat','step_aliases.json').read_bytes())
NEXT_REQUEST_NAMES={key:entry['step']+'__'+entry['variant']
                    for key,entry in ALIASES.items() if 'step' in entry and 'variant' in entry}
FILES={k:tuple(STAGE_ROOT.joinpath('prompts').glob(k+'-*.md'))[0]
       for k in SCHEMAS if k not in ('F1','F2','SOURCE_GAP','TERM_NAME')}


def serialize(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def frozen_prompts():
    requested=json.loads(PROMPT_MANIFEST.read_bytes())['new_prompts']
    expected={Path(x['path']).name:x['sha256'] for x in requested}
    values={k:p.read_bytes() for k,p in FILES.items()}
    if set(expected)!={p.name for p in FILES.values()} or any(
            digest(value)!=expected[FILES[k].name] for k,value in values.items()):
        raise ValueError('本轮提示词与实施包的 SHA-256 不一致')
    for key, entry in ALIASES.items():
        if 'prompt' in entry:
            value=STEPS.joinpath(entry['prompt']).read_bytes()
            if digest(value)!=entry['sha256']:
                raise ValueError('实施包中的提示词已变更：'+entry['prompt'])
            values[key]=value
    return values


class ModelFailure(RuntimeError):
    def __init__(self, message, *, failure_kind=None):
        super().__init__(message)
        self.failure_kind = failure_kind


class Lane:
    """Limit ready requests per service and react to observed throttling."""

    def __init__(self, name, max_in_flight):
        self.name=name
        self.maximum=max_in_flight
        self.limit=max_in_flight
        self.busy=0
        self.peak=0
        self.success=0
        self.pause=0
        self.changes=[]
        self.condition=asyncio.Condition()

    @asynccontextmanager
    async def slot(self):
        async with self.condition:
            while self.busy>=self.limit or monotonic()<self.pause:
                if monotonic()<self.pause:
                    try:
                        await asyncio.wait_for(self.condition.wait(),self.pause-monotonic())
                    except TimeoutError:
                        pass
                else:
                    await self.condition.wait()
            self.busy+=1
            self.peak=max(self.peak,self.busy)
        try:
            yield
        finally:
            async with self.condition:
                self.busy-=1
                self.condition.notify_all()

    async def observed(self, attempts, good):
        rejected=[a for a in attempts if a.get('status')==429]
        if not rejected and not good:
            return
        async with self.condition:
            before=self.limit
            if rejected:
                pauses=[]
                for row in rejected:
                    try:
                        pauses.append(float(row.get('retry_after') or 0))
                    except ValueError:
                        pass
                self.pause=max(self.pause,monotonic()+max([1,*pauses]))
                self.limit=max(1,self.limit-self.limit//3)
                self.success=0
            else:
                self.success+=1
                if self.success>=max(4,self.limit) and self.limit<self.maximum:
                    self.limit+=1
                    self.success=0
            if before!=self.limit:
                self.changes.append({'at':time(),'provider':self.name,'before':before,'after':self.limit,
                                     'cause':'HTTP 429' if rejected else 'successful responses'})
            self.condition.notify_all()


class StructuredSol(MarkdownTransport):
    def __init__(self,folder,raw,system,images,stage):
        super().__init__(folder,raw,system,images)
        self.stage=stage

    def prepare(self,body):
        if body.get('reasoning',{}).get('effort')!=SOL['effort']:
            raise ValueError('NEXT 后处理写作使用 Sol High')
        outgoing,images=super().prepare(body)
        schema=SCHEMAS.get(self.stage)
        if schema:
            outgoing['text']={'format':{'type':'json_schema',
                                        'name':NEXT_REQUEST_NAMES.get(self.stage,self.stage.lower())[:64],
                                        'strict':True,'schema':schema}}
        return outgoing,images


def decode(raw,stage):
    if not raw:
        return None
    if stage not in SCHEMAS:
        return {'markdown':raw.decode('utf8')}
    try:
        cleaned=re.sub(r'^\s*'+chr(96)*3+r'(?:json)?\s*|\s*'+chr(96)*3+r'\s*$','',raw.decode('utf8').strip(),flags=re.I)
        value=json.loads(cleaned)
    except (ValueError,UnicodeError):
        return None
    if not isinstance(value,dict):
        return None
    types={'array':list,'object':dict,'string':str,'integer':int,'boolean':bool,'null':type(None)}
    for key,schema in SCHEMAS[stage]['properties'].items():
        kind=schema.get('type')
        expected=(kind,) if isinstance(kind,str) else (kind or ())
        if key not in value or (expected and type(value[key]) not in tuple(types[k] for k in expected)):
            return None
    return value


def invoke_sol(folder,stage,prompt,payload,images,cli):
    prompt_path=folder/'prompt.md'
    atomic(prompt_path,prompt)
    raw=serialize(payload)
    proxy=StructuredSol(folder,raw,prompt.decode('utf8'),images,stage).start()
    output=folder/'first-message.txt'
    args=[cli,'exec','--ignore-user-config','--ignore-rules','--skip-git-repo-check','--json',
          '--color','never','-m',SOL['model'],'-c','model_reasoning_effort='+json.dumps(SOL['effort']),
          '-c','service_tier="default"','-c','web_search="disabled"','-c','approval_policy="never"',
          '-s','read-only','-C',str(folder),'-c','model_instructions_file='+json.dumps(str(prompt_path)),
          '-c','openai_base_url='+json.dumps(f'http://127.0.0.1:{proxy.server.server_port}'),'-o',str(output)]
    for feature in ('apps','plugins','multi_agent','shell_tool','browser_use','computer_use',
                    'workspace_dependencies','hooks','fast_mode','image_generation'):
        args+=['--disable',feature]
    env={k:v for k,v in os.environ.items()
         if k not in ('CODEX_THREAD_ID','CODEX_SESSION_ID','PYTHONPATH',DS_CREDENTIAL)
         and not k.startswith(('MANUALREADER_TOKEN','MANUAL_READER_TOKEN','MANUAL_READER_PAIRING','AIALRA_KUAFUSHE_DS_'))}
    error=None
    try:
        with (folder/'events.jsonl').open('wb') as events,(folder/'stderr.txt').open('wb') as stderr:
            result=subprocess.run(args+['-'],input=raw.encode(),stdout=events,stderr=stderr,
                                  env=env,timeout=900,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:
                error=f'Codex CLI {result.returncode}'
    except (OSError,subprocess.TimeoutExpired) as exc:
        error=type(exc).__name__
    finally:
        proxy.stop()
    response=next((r['response'] for r in proxy.receipts if r.get('response')), {})
    body=proxy.first_text or (output.read_bytes() if output.is_file() else b'') or ''.join(proxy.partial_text).encode()
    atomic(folder/'raw-output.txt',body)
    attempts=[r for r in proxy.receipts if 'status' in r]
    out=decode(body,stage)
    good=(out is not None and response.get('status')=='completed' and response.get('model')==SOL['model']
          and response.get('service_tier') in ('default',None))
    return {'result':out if good else None,'attempts':attempts,'transport_events':proxy.receipts,
            'response':response,'error':None if good else error or '响应不完整、型号不一致或返回结构不合法'}


def invoke_ds(folder,stage,prompt,payload,*,stream=False):
    key=os.environ.get(DS_CREDENTIAL)
    if not key:
        return {'result':None,'attempts':[],'response':{},'error':'本机 DS 夸父社凭据未就绪'}
    params={'model':DS['model'],'messages':[{'role':'system','content':prompt.decode()},
            {'role':'user','content':serialize(payload)}],'thinking':{'type':'disabled'},
            'response_format':{'type':'json_object'},'max_tokens':12000,'stream':stream}
    if stream:
        params['stream_options']={'include_usage':True}
    write(folder/'wire-request.json',params)
    parts=[]
    usage={}
    model=None
    finished=False
    reason=None
    frames=0
    first_content=None
    attempt={'started_at':time()}
    began=perf_counter()
    error=None
    try:
        with requests.post(DS['endpoint'],headers={'Authorization':'Bearer '+key},json=params,
                           stream=True,allow_redirects=False,timeout=(25,180)) as response:
            attempt.update(status=response.status_code,retry_after=response.headers.get('Retry-After'))
            content_type=response.headers.get('Content-Type','').partition(';')[0].strip().lower()
            attempt['content_type']=content_type
            request_id=response.headers.get('X-Request-ID','')
            if re.fullmatch(r'[A-Za-z0-9_-]{1,128}',request_id):
                attempt['provider_request_id']=request_id
            if response.status_code==200:
                if content_type=='application/json' or not stream and content_type!='text/event-stream':
                    frame=response.json()
                    frames=1
                    choices=frame.get('choices',[])
                    if choices:
                        chunk=choices[0].get('message',{}).get('content') or ''
                        if not isinstance(chunk,str):
                            raise ValueError('DS response content is not text')
                        parts.append(chunk)
                        reason=choices[0].get('finish_reason')
                        if parts[0].strip():
                            first_content=perf_counter()-began
                    finished=reason=='stop'
                    model=frame.get('model')
                    usage=frame.get('usage') or {}
                else:
                    for line in response.iter_lines():
                        if not line.startswith(b'data:'):
                            continue
                        raw=line[5:].strip()
                        if raw==b'[DONE]':
                            finished=True
                            break
                        frame=json.loads(raw)
                        frames+=1
                        model=frame.get('model') or model
                        usage=frame.get('usage') or usage
                        for choice in frame.get('choices',[]):
                            chunk=choice.get('delta',{}).get('content') or ''
                            if not isinstance(chunk,str):
                                raise ValueError('DS response content is not text')
                            if first_content is None and chunk.strip():
                                first_content=perf_counter()-began
                            parts.append(chunk)
                            reason=choice.get('finish_reason') or reason
            else:
                error=f'HTTP {response.status_code}'
                try:
                    details=response.json().get('error')
                except (ValueError,AttributeError):
                    details=None
                code=details.get('code') if isinstance(details,dict) else None
                if isinstance(code,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',code):
                    attempt['provider_error_code']=code
                    error+=f' ({code})'
    except (requests.RequestException,ValueError,TypeError,AttributeError) as exc:
        error=type(exc).__name__
    attempt['seconds']=perf_counter()-began
    raw=''.join(parts).encode()
    atomic(folder/'raw-output.txt',raw)
    value=decode(raw,stage) if finished and reason in (None,'stop') and model==DS['model'] else None
    summary={'model':model,'usage':usage,'finish_reason':reason,'stream_complete':finished,
             'empty_content':not bool(raw.strip()),'response_frames':frames,
             'first_content_seconds':first_content,'requested_stream':stream}
    attempt['response']=summary
    return {'result':value,'attempts':[attempt],'response':summary,
            'error':None if value is not None else error or '响应不完整、型号不一致或返回结构不合法'}


class Provider:
    def __init__(self,root,*,sol=40,vision=12,ds=DEFAULT_DS_CONCURRENCY,cache_only=False,
                 ds_state_dir=None):
        self.root=root
        self.prompts=frozen_prompts()
        self.cli=None if cache_only else executable()
        self.cache_only=cache_only
        self.nodes={}
        self.log=[]
        self.consumer_log=[]
        credential=os.environ.get(DS_CREDENTIAL)
        ds_lane=(KfsSlots(DS['endpoint'],credential,ds,directory=ds_state_dir)
                 if credential and not cache_only else Lane('ds',ds))
        self.lanes={'sol':Lane('sol',sol),'vision':Lane('vision',vision),'ds':ds_lane}
        self.started=time()

    def scope(self,sample):
        async def ask(stage,payload,images=None):
            return await self.ask(sample,stage,payload,images)
        return ask

    async def ask(self,sample,stage,payload,images=None,prompt=None):
        images=images or {}
        if any(digest(raw)!=key or not raw.startswith(b'\x89PNG\r\n\x1a\n') for key,raw in images.items()):
            raise ModelFailure('视觉资源和已登记的 PNG 哈希不一致')
        ds=stage in ('B1','B2')
        if ds and images:
            raise ModelFailure('DS 只接收已读出的文字标签')
        prompt=prompt or self.prompts[stage]
        schema=SCHEMAS.get(stage)
        profile=DS if ds else SOL
        if images:
            payload={**payload,'input_image_order':list(images)}
        identity=digest(serialize([stage,digest(prompt),payload,profile,schema,list(images)]))
        canonical=NEXT_REQUEST_NAMES.get(stage,stage)
        base=self.root/'requests'/canonical/identity
        previous=self.root/'requests'/stage/identity
        if not (base/'result.json').is_file() and (previous/'result.json').is_file():
            base=previous
        came=time()
        present=self.nodes.get(identity)
        shared=present is not None
        disk=not shared and (base/'result.json').is_file()
        if self.cache_only and not (shared or disk):
            raise ModelFailure('回放缓存缺少对应请求')
        if not present:
            if disk:
                async def resume():
                    return read(base/'result.json')
                present=asyncio.create_task(resume())
            else:
                present=asyncio.create_task(self.run(sample,stage,payload,images,prompt,profile,ds,
                                                     identity,base,came))
            self.nodes[identity]=present
        response=await asyncio.shield(present)
        self.consumer_log.append({'sample_id':sample,'stage':canonical,'id':identity,'ready_at':came,
                                  'resolved_at':time(),'reused':shared or disk,
                                  'reuse_location':'memory' if shared else 'disk' if disk else None,
                                  'success':response['result'] is not None})
        if response['result'] is None:
            if self.nodes.get(identity) is present:
                self.nodes.pop(identity)
            last=(response.get('attempts') or [{}])[-1]
            kind=None
            if ds and response['response'].get('finish_reason')=='length':
                kind='ds_truncated'
            elif ds and (last.get('status') in {401,403} or
                         (response.get('error') or '').startswith('KfsUnavailable:')):
                kind='ds_authorization'
            elif ds and (last.get('status') in OVERLOAD_STATUSES | {429}
                         or last.get('response',{}).get('empty_content') and last.get('status')==200):
                kind='ds_temporary'
            raise ModelFailure(response.get('error') or '请求未返回可用结果',failure_kind=kind)
        return response['result']

    async def run(self,sample,stage,payload,images,prompt,profile,ds,identity,base,ready):
        lane=self.lanes['ds' if ds else 'sol']
        vision=self.lanes['vision'] if images else None
        base.mkdir(parents=True,exist_ok=True)
        canonical=NEXT_REQUEST_NAMES.get(stage,stage)
        write(base/'input.json',{'stage':canonical,'id':identity,'input':payload,'model':profile,
                                 'prompt_sha256':digest(prompt),'image_sha256':list(images),
                                 'schema_sha256':digest(serialize(SCHEMAS.get(stage)))})
        hits=[]
        retries=0
        started=None
        final_profile=profile
        allow_fallback=(ds and self.cli and os.environ.get('MANUAL_READER_DS_FALLBACK')=='sol')
        previous_receipt=base/'receipt.json'

        def empty_success(attempt):
            response=attempt.get('response') or {}
            return (attempt.get('status')==200 and response.get('model')==DS['model']
                    and response.get('stream_complete') and response.get('empty_content'))

        def waiting_for_provider(attempts):
            return bool(attempts) and (attempts[-1].get('status')==524 or
                                      len(attempts)>=2 and all(empty_success(row) for row in attempts))

        async def once(index, *, stream=False):
            nonlocal started
            async with lane.slot():
                if started is None:
                    started=time()
                folder=base/f'attempt-{index}'
                folder.mkdir(exist_ok=True)
                if ds:
                    result=await asyncio.to_thread(invoke_ds,folder,stage,prompt,payload,stream=stream)
                    await lane.observed(result['attempts'],result['result'] is not None)
                    return result
                if not self.cli:
                    return {'result':None,'attempts':[],'response':{},'error':'未找到 Reader 已验证的 Codex 客户端'}
                return await asyncio.to_thread(invoke_sol,folder,stage,prompt,payload,images,self.cli)

        async def raw_attempt(index, *, stream=False):
            if vision:
                async with vision.slot():
                    return await once(index,stream=stream)
            return await once(index,stream=stream)

        try:
            prior=read(previous_receipt) if allow_fallback and previous_receipt.is_file() else {}
            if prior.get('provider')=='ds' and waiting_for_provider(prior.get('attempts',[])):
                hits.extend(prior['attempts'])
                got={'result':None,'response':prior['response'],'error':prior['error']}
            else:
                got=await raw_attempt(1)
                hits.extend(got['attempts'])
                if not ds:
                    await lane.observed(got['attempts'],got['result'] is not None)
                retryable=hits and (hits[-1].get('status') in {None,429,502,503,504} or
                          ds and hits[-1].get('status')==200 and got['response'].get('model') in (None,DS['model'])
                          and got['response'].get('finish_reason')!='length')
                if got['result'] is None and retryable:
                    retries=1
                    if ds and hits[-1].get('status') in OVERLOAD_STATUSES:
                        await asyncio.sleep(1)
                    got=await raw_attempt(2,stream=ds and hits[-1].get('status')==200)
                    hits.extend(got['attempts'])
                    if not ds:
                        await lane.observed(got['attempts'],got['result'] is not None)
            if allow_fallback and got['result'] is None and waiting_for_provider(hits):
                final_profile=SOL
                lane=self.lanes['sol']
                async with lane.slot():
                    if started is None:
                        started=time()
                    alternate=base/'fallback-sol'
                    alternate.mkdir(exist_ok=True)
                    got=await asyncio.to_thread(invoke_sol,alternate,stage,prompt,payload,{},self.cli)
                hits.extend({**attempt,'provider':'sol'} for attempt in got['attempts'])
                await lane.observed(got['attempts'],got['result'] is not None)
        except (OSError,RuntimeError,ValueError) as exc:
            got={'result':None,'response':{},'error':f'{type(exc).__name__}: {exc}'}
        declared=ALIASES.get(stage,{})
        record={'sample_id':sample,'stage':canonical,'step_slug':declared.get('step'),'variant':declared.get('variant'),
                'prompt_sha256':digest(prompt),'id':identity,'provider':lane.name,
                'uses_image':bool(images),'image_bytes':sum(map(len,images.values())),
                'profile':final_profile,'requested_profile':profile,
                'ready_at':ready,'started_at':started or ready,'ended_at':time(),
                'queue_seconds':(started-ready) if started else None,'cost_usd':None,
                'attempts':hits,'retries':max(retries,len(hits)-1),'response':got['response'],
                'status':'complete' if got['result'] is not None else 'partial','error':got.get('error')}
        write(base/'receipt.json',record)
        if got['result'] is not None:
            write(base/'result.json',got)
        self.log.append(record)
        return got
