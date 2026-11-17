"""Bounded planning transport that reuses the locally authenticated Codex runtime."""
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import base64
import json
import os
import re
import shutil
import subprocess
from threading import RLock, Thread
from time import perf_counter, time

import requests
import zstandard

from .stages.step02.read_tool import TOOL, read

PROFILE={'model':'gpt-6.1-sol','effort':'high','service_tier':'default'}
UPSTREAM='https://chatgpt.com/backend-api/codex'
FUNCTION='mcp__reader_original__read'
MAX_REQUESTS=4
MAX_TOOL_ROUNDS=1


def save(path,value):
    path=Path(path)
    stage=path.with_suffix('.partial')
    stage.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    os.replace(stage,path)


def executable():
    if value:=os.environ.get('MANUALREADER_CODEX'):
        return value
    candidates=[]
    if local:=os.environ.get('LOCALAPPDATA'):
        candidates.extend((Path(local)/'OpenAI/Codex/bin').glob('*/codex.exe'))
    if ordinary:=shutil.which('codex'):
        candidates.append(Path(ordinary))
    ranked=[]
    for path in candidates:
        try:
            ver=subprocess.run([str(path),'--version'],capture_output=True,text=True,encoding='utf8',timeout=7,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if ver.returncode==0 and (m:=re.search(r'(\d+)\.(\d+)\.(\d+)',ver.stdout)):
                ranked.append((tuple(map(int,m.groups())),str(path)))
        except (OSError,subprocess.TimeoutExpired):
            continue
    return max(ranked)[1] if ranked else None


class Transport:
    def __init__(self, folder, text, system, store, inline, images):
        self.folder,self.text,self.system=Path(folder),text,system
        self.store=store
        self.inline={fid:set(nums) for fid,nums in inline.items()}
        self.sent_images={row[0] for row in images}
        self.expected_images=[row[1] for row in images]
        self.receipts=[]
        self.resolved={}
        self.lock=RLock()

    def persist(self):
        with self.lock:
            try:
                save(self.folder/'transport.json',self.receipts)
            except OSError:
                pass

    def original_reply(self, item):
        cid=item['call_id']
        with self.lock:
            if cid in self.resolved:
                return self.resolved[cid]
            if len(self.resolved)>=MAX_TOOL_ROUNDS:
                return '本轮已经返回一批原件补读结果。请使用已提供的真实内容整理任务卡。'
            out=[]
            try:
                args=json.loads(item.get('arguments') or '{}')
                content=read(self.store,args,self.folder.parent/'original_model_read_trace.jsonl')['content']
                image_requests=iter(x for x in args.get('requests',[]) if x.get('visual'))
                image_index=next(image_requests,None)
                for block in content:
                    if block['type']=='image':
                        if image_index:
                            fid,n=image_index['file_id'],image_index['start']
                            image_index=next(image_requests,None)
                            if (fid,n) in self.sent_images:
                                continue
                            self.sent_images.add((fid,n))
                        out.append({'type':'input_image','image_url':'data:image/png;base64,'+block['data']})
                        continue
                    excerpt=block['text']
                    try:
                        data=json.loads(excerpt)
                        if looked:=data.get('rows'):
                            have=self.inline.setdefault(data['file_id'],set())
                            missing=[row for row in looked if row['n'] not in have]
                            data['already_sent_count']=len(looked)-len(missing)
                            data['rows']=missing
                            for row in missing:
                                have.add(row['n'])
                            excerpt=json.dumps(data,ensure_ascii=False,separators=(',',':'))
                    except (ValueError,AttributeError):
                        pass
                    out.append({'type':'input_text','text':excerpt})
            except (ValueError,KeyError,TypeError,OSError,AttributeError) as error:
                out=[{'type':'input_text','text':'access_blocked: '+str(error)}]
            self.resolved[cid]=out if any(x['type']=='input_image' for x in out) else '\n'.join(x['text'] for x in out)
            return self.resolved[cid]

    def prepare(self, body):
        if (body.get('model')!=PROFILE['model'] or body.get('reasoning',{}).get('effort')!=PROFILE['effort']
                or body.get('service_tier','default')!='default'):
            raise ValueError('实际模型或服务档位与已冻结配置不一致')
        messages=body.get('input',[])
        target=next((i for i,m in enumerate(messages) if m.get('role')=='user' and any(
                    d.get('type')=='input_text' and d.get('text','').strip()==self.text.strip()
                    for d in m.get('content',[]))),None)
        if target is None:
            raise ValueError('本轮响应没有携带当前任务')
        initial={'role':'user','content':[{'type':'input_text','text':self.text}]}
        images={}
        for msg in messages[:target+1]:
            for item in msg.get('content',[]):
                if item.get('type')=='input_image':
                    url=item.get('image_url','')
                    if not url.startswith('data:image/') or ',' not in url:
                        raise ValueError('只允许本机已验证的原件图像')
                    h=sha256(base64.b64decode(url.split(',',1)[1],validate=True)).hexdigest()
                    images.setdefault(h,item)
        if set(images)!=set(self.expected_images):
            raise ValueError('多模态请求中的原图字节与本轮实际原件图像不符')
        initial['content'].extend(images.values())
        tail=[]
        calls={x.get('call_id'):x for x in messages[target+1:]
               if x.get('type')=='function_call' and x.get('name')==FUNCTION and x.get('call_id')}
        for item in messages[target+1:]:
            if item.get('type')=='function_call_output' and item.get('call_id') in calls:
                tail.append({**item,'output':self.original_reply(calls[item['call_id']])})
            elif item.get('type') in ('reasoning','function_call','message') or item.get('role')=='assistant':
                tail.append(item)
        allow_tool=bool(self.store) and len(self.resolved)<MAX_TOOL_ROUNDS
        tools=([{'type':'function','name':FUNCTION,'description':TOOL['description'],
                'parameters':TOOL['inputSchema'],'strict':False}] if allow_tool else [])
        answer={**body,'instructions':self.system,'input':[initial]+tail,
                'tools':tools,'tool_choice':'auto' if allow_tool else 'none',
                'parallel_tool_calls':False,'service_tier':'default'}
        answer.pop('previous_response_id',None)
        return answer,list(images)

    def start(self):
        outer=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*_):pass
            def clean(self):
                blocked={'host','content-length','content-encoding','connection','upgrade','accept-encoding'}
                return {k:v for k,v in self.headers.items() if k.lower() not in blocked}
            def send_bytes(self,status,data,kind='application/json'):
                self.send_response(status)
                self.send_header('Content-Type',kind)
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def do_GET(self):
                if self.path.split('?',1)[0]!='/models':
                    self.send_bytes(426,b'{}')
                    return
                try:
                    with requests.get(UPSTREAM+self.path,headers=self.clean(),timeout=25,allow_redirects=False) as response:
                        self.send_bytes(response.status_code,response.content)
                except requests.RequestException:
                    self.send_bytes(502,b'{}')
            def do_POST(self):
                if self.path!='/responses':
                    self.send_bytes(403,b'{}')
                    return
                began=perf_counter()
                with outer.lock:
                    if len(outer.receipts)>=MAX_REQUESTS:
                        self.send_bytes(409,b'{"error":{"message":"ManualReader Next request limit reached"}}')
                        return
                    r={'started_at':time()}
                    outer.receipts.append(r)
                opened=False
                reply=None
                try:
                    count=int(self.headers.get('Content-Length','0'))
                    if not 0<count<48*1024*1024:raise ValueError('模型请求大小不合法')
                    raw=self.rfile.read(count)
                    if self.headers.get('Content-Encoding')=='zstd':
                        raw=zstandard.ZstdDecompressor().decompress(raw,max_output_size=48*1024*1024)
                    data,hashes=outer.prepare(json.loads(raw))
                    r.update(image_sha256=hashes,tool_rounds=len(outer.resolved),
                             tool_enabled=data['tool_choice']=='auto',request_bytes=len(json.dumps(data).encode()))
                    headers={**self.clean(),'Content-Type':'application/json','Accept-Encoding':'identity'}
                    reply=requests.post(UPSTREAM+'/responses',json=data,headers=headers,
                                        stream=True,allow_redirects=False,timeout=(25,90))
                    r.update(status=reply.status_code,retry_after=reply.headers.get('Retry-After'))
                    self.send_response(reply.status_code)
                    self.send_header('Content-Type',reply.headers.get('Content-Type','application/json'))
                    if reply.headers.get('Retry-After'):self.send_header('Retry-After',reply.headers['Retry-After'])
                    self.send_header('Connection','close')
                    self.end_headers()
                    self.close_connection=True
                    opened=True
                    for line in reply.iter_lines(chunk_size=512):
                        self.wfile.write(line+b'\n')
                        self.wfile.flush()
                        if not line.startswith(b'data: '):continue
                        try:event=json.loads(line[6:])
                        except ValueError:continue
                        kind=event.get('type')
                        if kind in ('response.completed','response.incomplete','response.failed'):
                            resp=event.get('response',{})
                            r['response']={key:resp.get(key) for key in ('model','service_tier','status','usage','error','incomplete_details')}
                        if kind=='response.output_item.done' and event.get('item',{}).get('type')=='function_call':
                            i=event['item']
                            r.setdefault('tool_calls',[]).append({k:i.get(k) for k in ('name','arguments','call_id')})
                        if kind=='error':
                            r['error']={k:event.get(k) for k in ('code','message')}
                except (ValueError,OSError,requests.RequestException,KeyError) as error:
                    r['transport_error']=f'{type(error).__name__}: {error}'
                    if not opened:
                        try:self.send_bytes(502,b'{"error":{"message":"ManualReader Next transport unavailable"}}')
                        except OSError:pass
                finally:
                    if reply is not None:reply.close()
                    r['seconds']=perf_counter()-began
                    outer.persist()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        Thread(target=self.server.serve_forever,daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def invoke(folder,system,payload,images,store,inline):
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    exe=executable()
    if not exe:raise RuntimeError('未找到已登录的本机 Codex 命令')
    raw=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
    system_path=folder/'planner-prompt.txt'
    system_path.write_bytes(system)
    output=folder/'first-response.json'
    paths=[];witness=[]
    seen=set()
    for fid,n,image in images:
        h=sha256(image).hexdigest()
        if h in seen:continue
        path=folder/f'original-{len(paths):02}.png'
        path.write_bytes(image)
        paths.append(path)
        witness.append(((fid,n),h))
        seen.add(h)
    transport=Transport(folder,raw,system.decode('utf8'),store,inline,witness).start()
    args=[exe,'exec','--ignore-user-config','--ignore-rules','--skip-git-repo-check','--json','--color','never',
          '-m',PROFILE['model'],'-c','model_reasoning_effort="high"','-c','service_tier="default"',
          '-c','web_search="disabled"','-c','approval_policy="never"','-s','read-only','-C',str(folder),
          '-c','model_instructions_file='+json.dumps(str(system_path)),
          '-c','openai_base_url='+json.dumps('http://127.0.0.1:'+str(transport.server.server_port)),
          '-o',str(output),'--output-schema',str(folder/'output-schema.json')]
    for flag in ('apps','plugins','multi_agent','shell_tool','browser_use','computer_use','workspace_dependencies','hooks','fast_mode','image_generation'):
        args+=['--disable',flag]
    for path in paths:args.append('--image='+str(path))
    env={k:v for k,v in os.environ.items() if k not in ('CODEX_THREAD_ID','CODEX_SESSION_ID','PYTHONPATH')
         and not k.startswith(('MANUALREADER_TOKEN','MANUAL_READER_TOKEN','MANUAL_READER_PAIRING'))}
    try:
        with (folder/'stderr.txt').open('w',encoding='utf8') as errors:
            r=subprocess.run(args+['-'],input=raw,stdout=subprocess.PIPE,stderr=errors,text=True,
                             encoding='utf8',env=env,timeout=210,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        events=[]
        for line in r.stdout.splitlines():
            try:event=json.loads(line)
            except ValueError:continue
            if (event.get('item') or {}).get('type')!='reasoning':events.append(event)
        save(folder/'events.json',events)
        if r.returncode or not output.is_file():
            raise RuntimeError('模型未交出完整首稿；请核对本轮传输回执')
        if not any((x.get('response') or {}).get('usage') for x in transport.receipts):
            raise RuntimeError('模型返回了内容，但服务未提供可审计的用量回执')
        for x in transport.receipts:
            resp=x.get('response') or {}
            if resp.get('usage') and (resp.get('model')!=PROFILE['model'] or resp.get('service_tier') not in (None,'default')):
                raise RuntimeError('模型实际返回的身份与本轮配置不符')
        return json.loads(output.read_bytes())
    finally:
        transport.stop()
