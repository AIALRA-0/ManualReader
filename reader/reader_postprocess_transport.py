"""Model I/O for the frozen Reader postprocessing contracts."""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import time

import requests

from reader_postprocess_core import atomic, fingerprint, read, sha

# These are the same endpoint, model, and key source used by the existing Reader translation runs.
DS_ENDPOINT='https://kuafushe.cc/v1/chat/completions'
DS_MODEL='deepseek-v4.1-flash'
DS_KEY='AIALRA_KUAFUSHE_DS_PRIMARY_KEY'


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def offload(fn,*args):
    """Start a ready task directly, with no local pool-size limit."""
    loop=asyncio.get_running_loop()
    future=loop.create_future()
    def settle(result,error):
        if not future.done():
            if error is None:
                future.set_result(result)
            else:
                future.set_exception(error)
    def run():
        try:
            result=fn(*args)
        except BaseException as error:
            loop.call_soon_threadsafe(settle,None,error)
        else:
            loop.call_soon_threadsafe(settle,result,None)
    threading.Thread(target=run,daemon=True).start()
    return future


def decoded(text):
    value=json.loads(re.sub(r'^\s*```(?:json)?\s*|\s*```\s*$', '',text.strip(),flags=re.I))
    if not isinstance(value,dict):
        raise ValueError('模型没有返回 JSON 对象')
    return value


def request_ds(folder,prompt,payload):
    key=os.getenv(DS_KEY)
    if not key:
        raise RuntimeError('本机缺少现有 DS 夸父社的翻译凭据')
    body={'model':DS_MODEL,'messages':[{'role':'system','content':prompt},
          {'role':'user','content':json.dumps(payload,ensure_ascii=False,separators=(',',':'))}],
          'thinking':{'type':'disabled'},'response_format':{'type':'json_object'},
          'max_tokens':12000,'stream':True,'stream_options':{'include_usage':True}}
    atomic(folder/'request.json',body)
    began=time.perf_counter()
    usage={}; actual=None; chunks=[]; complete=False; status=None; finish=None; record=None
    try:
        with requests.post(DS_ENDPOINT,headers={'Authorization':'Bearer '+key},json=body,
                           stream=True,timeout=(25,180)) as response:
            status=response.status_code
            if not response.ok:
                record={'status':status,'retry_after':response.headers.get('Retry-After')}
                raise RuntimeError('翻译接口 HTTP '+str(status))
            for line in response.iter_lines():
                if not line.startswith(b'data:'):
                    continue
                raw=line[5:].strip()
                if raw==b'[DONE]':
                    complete=True
                    break
                part=json.loads(raw)
                actual=part.get('model') or actual
                usage=part.get('usage') or usage
                for choice in part.get('choices',[]):
                    chunks.append(choice.get('delta',{}).get('content') or '')
                    finish=choice.get('finish_reason') or finish
        if not complete or finish not in (None,'stop'):
            raise RuntimeError('翻译流没有完整结束')
        return decoded(''.join(chunks))
    except requests.RequestException as exc:
        raise RuntimeError('夸父社连接中断：'+type(exc).__name__) from None
    finally:
        atomic(folder/'raw-output.txt',''.join(chunks).encode())
        atomic(folder/'receipt.json',{'provider':'DS 夸父社','actual_model':actual,
          'mode':'thinking_disabled','http_status':status,'usage':usage,'finish_reason':finish,
          'stream_complete':complete,'response_seconds':round(time.perf_counter()-began,4),
          'response_headers':record,'actual_provider_cost_usd':None})


def request_sol_text(folder,prompt,payload,images,role):
    from reader_execute import invoke_writer, WRITER_ROLE, codex_executable
    token=WRITER_ROLE.set(role)
    try:
        raw,_,_=invoke_writer(folder,json.dumps(payload,ensure_ascii=False,separators=(',',':')),
             [Path(p) for p in images],codex_executable(),system_override=prompt,allow_image_generation=False,
             research=False,request_limit=1)
        return raw
    finally:
        WRITER_ROLE.reset(token)


def request_sol(folder,prompt,payload,images,role):
    return decoded(request_sol_text(folder,prompt,payload,images,role))


def measured(folder,provider):
    if provider=='ds':
        p=folder/'receipt.json'
        d=read(p) if p.is_file() else {}
        u=d.get('usage',{})
        return {'input_tokens':u.get('prompt_tokens',0),'output_tokens':u.get('completion_tokens',0),
                'reasoning_tokens':u.get('completion_tokens_details',{}).get('reasoning_tokens',0),
                'cached_input_tokens':u.get('prompt_cache_hit_tokens',
                   u.get('prompt_tokens_details',{}).get('cached_tokens',0)),
                'model_calls':int(p.is_file()),'http_status':d.get('http_status'),
                'actual_model':d.get('actual_model'),'execution_seconds':d.get('response_seconds')}
    p=folder/'usage.json'
    u=read(p).get('usage',{}) if p.is_file() else {}
    receipts=read(folder/'transport.json') if (folder/'transport.json').is_file() else []
    return {'input_tokens':u.get('input_tokens',0),'output_tokens':u.get('output_tokens',0),
       'reasoning_tokens':u.get('reasoning_output_tokens',0),'cached_input_tokens':u.get('cached_input_tokens',0),
       'model_calls':len(receipts),'actual_model':next((r.get('model') for r in receipts),None),
       'http_status':next((r.get('status') for r in reversed(receipts)),None),
       'execution_seconds':sum(r.get('seconds',0) or 0 for r in receipts)}


class Requests:
    def __init__(self,root,spec):
        self.root=root
        self.spec=spec
        self.results=[]
        self.inflight={}

    async def ask(self, sample,stage,name,payload,*,provider='sol',role='term_repair',images=(),
                  validate=None,context=''):
        prompt=self.spec.prompts[name]
        from reader_postprocess_wire import compatible,contract_for
        contract=contract_for(self.spec,name)
        if contract:
            payload={**payload,'output_contract':contract}

        image_hashes=[sha(Path(p).read_bytes()) for p in images]
        from reader_writer_profiles import writer_profile
        model={'provider':'DS 夸父社','model':DS_MODEL,'effort':'none'} if provider=='ds' else writer_profile(role)
        key=fingerprint(['reader-postprocess-v1',name,sha(prompt),payload,image_hashes,model,context])
        cache=self.root/'cache'/f'{key}.json'
        queued=timestamp(); tick=time.perf_counter()
        record={'sample_id':sample,'stage':stage,'prompt':name,'prompt_sha256':sha(prompt),
                'input_sha256':key,'model':model,'queued_at':queued,
                'cache_hit':False,'actual_provider_cost_usd':None,'retry_count':0,'attempts':[]}
        self.results.append(record)
        folder=self.root/'samples'/sample/'requests'/f'{stage}-{name}-{key[:14]}'
        folder.mkdir(parents=True,exist_ok=True)
        atomic(folder/'input.json',payload)
        own=False
        try:
            if cache.is_file():
                result=read(cache)
                record['cache_hit']=True
            elif key in self.inflight:
                record['cache_hit']=True
                result=await asyncio.shield(self.inflight[key])
            else:
                own=True
                self.inflight[key]=asyncio.get_running_loop().create_future()
                started=time.perf_counter()
                record.update(started_at=timestamp(),queue_seconds=round(started-tick,4))
                result=None
                for i in range(3):
                    attempt=folder/f'attempt-{i+1}'
                    attempt.mkdir(exist_ok=False)
                    try:
                        if provider=='ds':
                            raw=await offload(request_ds,attempt,prompt,payload)
                        else:
                            raw=await offload(request_sol,attempt,prompt,payload,images,role)
                        atomic(attempt/'result.raw.json',raw)
                        normalized,changes=compatible(name,raw,payload)
                        atomic(attempt/'wire-adaptation.json',changes)
                        result=validate(normalized) if validate else normalized
                    except Exception as exc:
                        note=str(exc)
                        observed=measured(attempt,provider)
                        record['attempts'].append(observed|{'error':note,'attempt':i+1})
                        status=observed.get('http_status')
                        if i==2 or (status not in (429,500,502,503,504) and '连接中断' not in note):
                            raise
                        delay=2**i
                        if provider=='ds' and (attempt/'receipt.json').is_file():
                            retry=(read(attempt/'receipt.json').get('response_headers') or {}).get('retry_after')
                            if retry and str(retry).isdigit():
                                delay=max(delay,int(retry))
                        record['retry_count']+=1
                        await asyncio.sleep(delay)
                    else:
                        record['attempts'].append(measured(attempt,provider)|{'attempt':i+1})
                        break
                atomic(cache,result)
                self.inflight[key].set_result(result)
            atomic(folder/'result.json',result)
            record['status']='complete'
            return result
        except Exception as exc:
            if own and not self.inflight[key].done():
                self.inflight[key].set_result(None)
            record.update(status='failed',error=str(exc),error_kind=type(exc).__name__)
            return None
        finally:
            if own:
                self.inflight.pop(key,None)
            record['completed_at']=timestamp()
            record['wall_seconds']=round(time.perf_counter()-tick,4)
            atomic(folder/'task.json',record)
