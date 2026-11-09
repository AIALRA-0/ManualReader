"""写作只接收当前任务、原图和必要工具；凭证仅转发给原Codex服务。"""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import json,re,threading,time,requests,zstandard

def measured_usage(receipts,profile,session_id):
    """直接用服务端逐请求回执计量，避免读取客户端日志时阻塞已完成正文。"""
    requests_usage=[]
    for receipt in receipts:
        response=receipt.get('response')or{};usage=response.get('usage')
        if not usage:continue
        if response.get('model')!=profile['model']or response.get('service_tier','default')!='default':raise ValueError('服务端返回的模型或服务档与本次任务不一致')
        details=usage.get('input_tokens_details')or{};output=usage.get('output_tokens_details')or{}
        requests_usage.append({'input_tokens':usage['input_tokens'],'cached_input_tokens':details.get('cached_tokens',0),'cache_write_input_tokens':details.get('cache_write_tokens',0),
                               'output_tokens':usage['output_tokens'],'reasoning_output_tokens':output.get('reasoning_tokens',0)})
    if not requests_usage:return None
    totals={k:sum(r[k]for r in requests_usage)for k in requests_usage[0]};totals['model_calls']=len(requests_usage)
    return {'usage':totals,'requests':requests_usage,'source':'actual_model_response_receipts','usage_complete':len(requests_usage)==len(receipts),'http_requests':len(receipts),
            'session_id':session_id,'model':profile['model'],'effort':profile['effort'],'configured_service_tier':'default','image_tools':[{'tool':'image_generation','attempted':True,'actual_cost_usd':None}for receipt in receipts for call in receipt.get('tool_calls',[])if re.search(r'\bimage_gen__imagegen\s*\(',str(call.get('input','')))],
            'model_started_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(receipts[0]['started_at'])),
            'model_completed_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(receipts[-1]['started_at']+receipts[-1].get('seconds',0)))}

WEB_DESCRIPTION='''Run raw JavaScript to access only the web research tool, with no file or shell access needed. Use text(await tools.web__run(args)) to return results. Example: text(await tools.web__run({search_query:[{q:"query"}],response_length:"short"})). Open sources with {open:[{ref_id:"https://..."}]} or previously returned reference ids. Find evidence with {find:[{ref_id:"...",pattern:"..."}]}. For PDF figures use {screenshot:[{ref_id:"...",pageno:0}]}, then emit each returned image block with image(block). Combine independent searches/opens in one call. Search-only calls return short discovery snippets; open the source or find a specific passage for complete evidence. Open, find and screenshots retain their requested scope. search_query accepts up to four objects with q and optional domains/recency. response_length is short, medium or long. Results contain source ids, URLs, dates and source text; open the supporting page before citing it. text(value) returns text; image(imageContent) returns an image. No tool discovery is required.'''

IMAGE_DESCRIPTION='\nFor a necessary new illustration, tools.image_gen__imagegen({prompt:"...",transparent_background:false,referenced_image_paths:["actual local source path"]}) generates or edits an image. For a new image omit referenced_image_paths and num_last_images_to_include; for edits provide one of them. Use generatedImage(result) to show a returned image, and preserve the real returned asset path when the tool provides it. For a long image call the exact FIRST line is // @exec: {"yield_time_ms":120000,"max_output_tokens":1000} followed by a newline. JSON is mandatory. After awaiting the tool use generatedImage(result); NEVER use text(result), JSON.stringify(result), Object.entries(result), or print image_url because it contains base64. If exec yields a running cell id, resume with functions.wait. Do not print base64 or invent paths; include generated_assets only when an actual asset was generated. Proactively illustrate mathematical dependencies, multi-step mechanisms and state changes beside the explanation; use an existing figure or Mermaid when suitable, and generate a bitmap for necessary visual shape or appearance. Simple definitions need no decorative picture.'

COORDINATOR_TOOLS='Run only the authorized local command using native functions.exec_command; if it returns session_id, wait with functions.write_stdin until exit_code is present. When a functions.exec wrapper is needed, text(await tools.exec_command(args)) and text(await tools.write_stdin(args)) are available. Do not search, enumerate tools, read source materials, or write answers. Inspect the compact completion record and report actual results. Native exec_command and write_stdin are preferred.'

class Gate:
    def __init__(self,spec,prompt,system,folder,research=True,allow_image_generation=True):
        self.spec,self.prompt,self.system,self.folder,self.research=spec,prompt,system,Path(folder),research
        self.receipts=[];self.tools=None;self.lock=threading.RLock();self.allow_image_generation=allow_image_generation
    def persist(self):
        """只记录传输阶段、字节数和时间，让长请求也能被观察；不保存流中的思考内容。"""
        with self.lock:
            target=self.folder/'transport.json';temporary=self.folder/'transport.next'
            temporary.write_text(json.dumps(self.receipts,ensure_ascii=False,indent=2),'utf8');temporary.replace(target)
    def prepare(self,value):
        if value.get('model')!=self.spec['model'] or value.get('reasoning',{}).get('effort')!=self.spec['effort'] or value.get('service_tier','default')!='default':raise ValueError('实际模型与本次配置不同')
        messages=value.get('input',[]);target=None;images=[]
        for i,m in enumerate(messages):
            for block in m.get('content',[]):
                if block.get('type')=='input_text' and block.get('text','').replace('\r\n','\n').strip()==self.prompt.replace('\r\n','\n').strip():target=i
                if block.get('type')=='input_image':images.append(block)
            if m.get('type')=='additional_tools' and self.tools is None:
                functions=next((t for t in m.get('tools',[])if t.get('name')=='functions'),None)
                if functions:
                    tool=next((t for t in functions.get('tools',[])if t.get('name')=='exec'),None)
                    if tool:
                        names=('wait','exec_command','write_stdin')if self.spec.get('role')=='coordinator'else('wait',)
                        waits=[t for t in functions.get('tools',[])if t.get('name')in names]
                        description=COORDINATOR_TOOLS if self.spec.get('role')=='coordinator'else WEB_DESCRIPTION+(IMAGE_DESCRIPTION if self.allow_image_generation else '')
                        self.tools={**m,'tools':[{**functions,'tools':[{**tool,'description':description},*waits]}]}
        if target is None:raise ValueError('本次请求没有当前任务原文，停止转发以避免串任务')
        first={'role':'user','content':[{'type':'input_text','text':self.prompt}]}
        unique={json.dumps(x,sort_keys=True):x for x in images};first['content'].extend(unique.values())
        # 保留本任务真实工具往返；不携带维护对话、全局技能目录和无关工具说明。
        value={**value,'instructions':self.system,'input':([self.tools]if self.research and self.tools else[])+[first]+messages[target+1:],
               'tools':[t for t in value.get('tools',[])if self.spec.get('role')=='coordinator'and t.get('name')in('exec_command','write_stdin')],'tool_choice':'auto'if self.research else'none','parallel_tool_calls':False,'service_tier':'default'}
        value.pop('previous_response_id',None)
        if self.research and not self.tools:raise ValueError('没有取得可用的检索工具声明')
        return value
    def start(self):
        self.folder.mkdir(parents=True,exist_ok=True)
        outer=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args):pass
            def do_GET(self):
                if not self.path.startswith('/models'):self.send_response(426);self.send_header('Content-Length','0');self.end_headers();return
                headers={k:v for k,v in self.headers.items()if k.lower()not in('host','content-length','connection','accept-encoding')}
                r=requests.get('https://chatgpt.com/backend-api/codex'+self.path,headers=headers,timeout=30)
                self.send_response(r.status_code);self.send_header('Content-Length',str(len(r.content)));self.end_headers();self.wfile.write(r.content)
            def do_POST(self):
                if self.path!='/responses':
                    # Codex 的检索执行也走同一服务地址；不截断这条真实工具通道。
                    raw=self.rfile.read(int(self.headers.get('Content-Length',0)))
                    raw,policy=__import__('reader_research_transport').prepare(self.path,raw,outer.spec.get('compact_search_discovery',True))
                    with(outer.folder/'tool-routes.jsonl').open('a',encoding='utf8')as log:log.write(json.dumps({'path':self.path.split('?')[0],'bytes':len(raw),'policy':policy})+'\n')
                    headers={k:v for k,v in self.headers.items()if k.lower()not in('host','content-length','connection','accept-encoding')}
                    r=requests.post('https://chatgpt.com/backend-api/codex'+self.path,data=raw,headers=headers,timeout=(30,None),allow_redirects=False)
                    self.send_response(r.status_code);self.send_header('Content-Type',r.headers.get('Content-Type','application/json'));self.send_header('Content-Length',str(len(r.content)));self.end_headers();self.wfile.write(r.content);return
                began=time.perf_counter();r=None;receipt={'started_at':time.time(),'model':outer.spec['model'],'effort':outer.spec['effort'],'service_tier':'default'}
                try:
                    raw=self.rfile.read(int(self.headers.get('Content-Length',0)))
                    if self.headers.get('Content-Encoding')=='zstd':raw=zstandard.ZstdDecompressor().decompress(raw,max_output_size=32*1024*1024)
                    original=json.loads(raw);receipt['client_tools']=[{'type':t.get('type'),'name':t.get('name'),'nested':[x.get('name')for x in t.get('tools',[])]}for t in original.get('tools',[])];value=outer.prepare(original)
                    with outer.lock:number=len(outer.receipts)+1;outer.receipts.append(receipt)
                    public={k:v for k,v in value.items()if k not in('client_metadata','access_programs')}
                    public['input']=[m if m.get('type')!='reasoning'else{'type':'reasoning','content_omitted':True}for m in value['input']]
                    (outer.folder/('sent-'+str(number)+'.json')).write_text(json.dumps(public,ensure_ascii=False,indent=2),'utf8')
                    receipt.update(phase='awaiting_headers',original_messages=len(original.get('input',[])),sent_messages=len(value['input']),request_bytes=len(json.dumps(value).encode()),tool_declarations=1 if outer.research else 0)
                    headers={k:v for k,v in self.headers.items()if k.lower()not in('host','content-length','content-encoding','connection','upgrade','accept-encoding')};headers.update({'Content-Type':'application/json','Accept-Encoding':'identity'})
                    outer.persist()
                    r=requests.post('https://chatgpt.com/backend-api/codex/responses',json=value,headers=headers,stream=True,timeout=(30,None),allow_redirects=False)
                    receipt.update(phase='streaming',status=r.status_code,headers_seconds=round(time.perf_counter()-began,3));self.send_response(r.status_code);self.send_header('Content-Type',r.headers.get('Content-Type','application/json'));self.send_header('Connection','close');self.end_headers();calls=[];last_saved=0;public_items=set()
                    outer.persist()
                    def forwarded_chunks():
                        nonlocal last_saved
                        connected=True
                        for chunk in r.iter_content(chunk_size=4096):
                            if not chunk:continue
                            receipt.setdefault('first_chunk_seconds',round(time.perf_counter()-began,3));receipt['last_progress_at']=time.time();receipt['received_bytes']=receipt.get('received_bytes',0)+len(chunk)
                            if time.time()-last_saved>=1:outer.persist();last_saved=time.time()
                            if connected:
                                try:self.wfile.write(chunk);self.wfile.flush()
                                except OSError:connected=False;receipt['client_disconnected']=True
                            yield chunk
                    for event in __import__('reader_research_transport').events(forwarded_chunks()):
                        if event.get('type')=='response.output_item.added':
                            item=event.get('item',{})
                            if item.get('type')=='message'and item.get('role')=='assistant'and item.get('channel')not in('analysis','reasoning'):
                                public_items.add(item.get('id'))
                        if event.get('type')=='response.output_text.delta'and event.get('item_id')in public_items:
                            # 只保存面向用户的正文流；检索或推理流不进入这个草稿文件。
                            delta=event.get('delta','')
                            if isinstance(delta,str):
                                with(outer.folder/('partial-output-'+str(number)+'.txt')).open('a',encoding='utf8')as draft:draft.write(delta)
                        if event.get('type')=='response.output_item.done':
                            item=event.get('item',{})
                            if item.get('type')in('custom_tool_call','function_call'):calls.append({k:item.get(k)for k in('type','name','input','arguments')})
                        if event.get('type')in('response.completed','response.failed','response.incomplete'):
                            response=event.get('response',{});receipt['response']={k:response.get(k)for k in('model','status','service_tier','usage','error','incomplete_details')}
                        if event.get('type')=='error':receipt['error']=event
                    receipt['tool_calls']=calls;receipt['phase']='complete'if receipt.get('response')else'stream_closed';r.close();self.close_connection=True
                except Exception as e:
                    receipt.update(phase='transport_error',error_type=type(e).__name__,message=str(e))
                    try:
                        if not receipt.get('status'):self.send_error(502,'Reader transport unavailable')
                        self.close_connection=True
                    except OSError:pass
                finally:
                    if r is not None:r.close()
                    receipt['seconds']=round(time.perf_counter()-began,3)
                    outer.persist()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.port=self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever,daemon=True).start();return self
    def stop(self):self.server.shutdown();self.server.server_close()
