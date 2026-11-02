"""由用户手动运行：整理写作任务，或把模型已经输出的结果逐题写回阅读器。"""
from pathlib import Path
from urllib.request import Request, build_opener,HTTPRedirectHandler
from urllib.parse import urlsplit
import argparse
import json
import os,re,time,sys,uuid,subprocess
from concurrent.futures import ThreadPoolExecutor

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def feedback_file(path):
    """从本轮文件读取用户反馈，文件内容不参与登录或项目选择。"""
    if not path:return None
    raw=Path(path).read_text('utf-8-sig')
    if len(raw)>24000:raise ValueError('反馈文件过长，请只保留本轮用户指出的未懂之处')
    try:value=json.loads(raw)
    except ValueError:value=raw
    if isinstance(value,str):return {'reader_feedback_latest':{'text':value,'source':'用户在当前Codex对话中的直接反馈'}}
    if not isinstance(value,dict):raise ValueError('反馈文件需要是用户原话或JSON对象')
    if 'text' in value:return {'reader_feedback_latest':value}
    result={k:value[k] for k in ('reader_feedback_latest','confirmed_passage','confirmed_understanding') if k in value}
    if not result:raise ValueError('反馈文件没有本轮读者反馈或明确确认的内容')
    return result


class Bridge:
    def __init__(self, base=None, material=None,token=None,pairing=None,remember=False):
        url_file=Path(__file__).parent/'reader-url.txt'
        base=base or (url_file.read_text('utf8').strip() if url_file.exists() else 'http://127.0.0.1:8939')
        self.base=base.rstrip('/')
        parsed=urlsplit(self.base)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or (parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost'))):raise ValueError('请使用 HTTPS 网站地址，或本机开发地址')
        self.opener=build_opener(NoRedirect());self.remote=parsed.scheme=='https' or bool(token or pairing) or parsed.path=='/machine/v1'
        if self.remote:
            if parsed.path not in ('','/','/machine/v1'):raise ValueError('远程地址请填写网站根地址或 /machine/v1')
            self.base=parsed.scheme+'://'+parsed.netloc+'/machine/v1'
            if pairing:
                request=Request(self.base+'/pair/exchange',data=b'',headers={'Authorization':'Reader-Pairing '+pairing})
                exchanged=json.load(self.opener.open(request,timeout=30))
                if material and material!=exchanged.get('material_id'):raise ValueError('配对码对应另一个项目，未保存连接')
                token=exchanged['access_token'];material=material or exchanged.get('material_id')
            from reader_credentials import get as stored_token,put as remember_token
            self._from_vault=not bool(token)
            token=token or stored_token(self.base,material)
            if not token:raise ValueError('本机未保存这个项目的连接；请从网页配对，选择记住连接后无需每次配对')
            self.headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
            if material:self.headers['X-Reader-Material']=material
            if remember:
                if not material:raise ValueError('记住连接前需要明确项目编号')
                guide=self.request('/api/guide')
                if guide.get('material_id')!=material:raise ValueError('服务器返回的项目与连接不一致，未保存凭证')
                remember_token(self.base,material,token);self._from_vault=True
        else:
            session=json.load(self.opener.open(self.base+'/api/session',timeout=30))
            self.headers={'X-Reader-Pin':session['pin'],'X-Reader-Material':material or session['material_id'],'Content-Type':'application/json'}

    def request(self, path, data=None):
        if data is not None and path.split('?',1)[0] in ('/api/read','/read'):raise ValueError('阅读进度由用户在页面手动勾选，Agent 不写入已读状态')
        raw=json.dumps(data,ensure_ascii=False).encode() if data is not None else None
        if self.remote and path.startswith('/api/'):path=path[4:]
        headers={**self.headers}
        if getattr(self,'run_id',None):headers['X-Reader-Run']=self.run_id
        try:return json.load(self.opener.open(Request(self.base+path,data=raw,headers=headers),timeout=60))
        except __import__('urllib.error',fromlist=['HTTPError']).HTTPError as error:
            if self.remote and error.code==401:
                from reader_credentials import delete
                if getattr(self,'_from_vault',False):delete(self.base,self.headers.get('X-Reader-Material'))
                raise ValueError('这个项目的授权已失效或被撤销，请重新配对')from None
            raise

    def prepare(self, out, round_id=None, include_answered=False, lesson=False, observe=True,reader_context=None,question_ids=None):
        started=time.perf_counter()
        if not lesson:
            out=Path(out);out.mkdir(parents=True,exist_ok=True)
            if round_id:
                in_round=[q['id'] for q in self.request('/api/work?round='+round_id+('&include_answered=true' if include_answered else ''))['questions']]
                if question_ids is not None and not set(question_ids)<=set(in_round):raise ValueError('指定问题不属于这轮可处理的问题，请读取原问题状态')
                question_ids=in_round if question_ids is None else question_ids
            context={k:reader_context[k] for k in ('reader_feedback_latest','confirmed_passage','confirmed_understanding') if reader_context and k in reader_context}
            manifest=self.request('/api/runs',{'question_ids':question_ids,'include_answered':include_answered,**context});self.run_id=manifest['id']
            manifest['coordinator_session_id']=os.environ.get('CODEX_THREAD_ID') or os.environ.get('CODEX_SESSION_ID');manifest['local_script_started_at']=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
            (out/'run.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf8')
            if not manifest['question_count']:
                pool=manifest.get('question_pool',{})
                inconsistent=pool.get('pending',0)>0
                result={'run_id':self.run_id,'questions':0,'groups':0,'tasks':[],'model_calls':0,'question_pool':pool,'state':'pool_inconsistent' if inconsistent else 'empty_selection',
                        'message':'服务端仍有待答记录，但本次没有返回问题；需要核对状态，不能认定用户没有保存问题' if inconsistent else '本次没有返回待答问题；这不表示页面上的问题没有保存',
                        'next_action':'有明确问题编号时，使用 prepare --question 原问题编号；用户要求重答已有有效答案时再加 --include-answered，只处理这些编号，不扩大到全部已答问题'}
                (out/'dispatch.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
                return result
            urls=[group['input_url'] for group in manifest['groups']]+[manifest['reference_url']]
            with ThreadPoolExecutor(max_workers=min(4,len(urls))) as pool:payloads=list(pool.map(self.request,urls))
            (out/'reference.json').write_text(json.dumps(payloads[-1],ensure_ascii=False),'utf8')
            worker_script=Path(__file__).parent/'reader_worker.py'
            tasks=[]
            for group,payload in zip(manifest['groups'],payloads):
                identity=group['id'];target=out/('result-'+identity+'.json');payload['reference_file']=str((out/'reference.json').resolve());payload['output_file']=str((out/('result-'+identity+'.draft.json')).resolve())
                payload['publish_command']='使用同一Python执行 reader_worker.py finish --out '+str(out.resolve())+' --group '+identity+'，脚本只检查JSON、编号与缺项，然后一次发布结果，不重新写作'
                prompt=out/('prompt-'+identity+'.txt');prompt.write_text('使用 Terra high，只处理本组问题。先把回答讲清，再派生术语和选择题。结果只写一次到 output_file，然后执行 publish_command 交给脚本检查和发布，最终仅回复文件路径和条数，不再输出整份JSON，不增加审稿或修复循环。\n'+json.dumps(payload,ensure_ascii=False,separators=(',',':')),'utf8')
                tasks.append({'group_id':identity,'model':'gpt-6.1-terra','reasoning_effort':'high','fork_turns':'none','message':'先调用 reader_worker.py 的 input 动作读取一次指定输入（该动作会同时记录真实开始时间和本次会话编号）；直接完成写作并保存 output_file，最终只返回路径和条数。输入目录：'+str(out.resolve())+'；组号：'+identity,'input_file':str(prompt.resolve()),'output_file':str(target.resolve()),'worker_script':str(worker_script.resolve())})
            result={'run_id':self.run_id,'questions':manifest['question_count'],'groups':len(tasks),'tasks':tasks,'script_seconds':round(time.perf_counter()-started,4),'model_calls':0,'dispatch':'现在连续派发全部tasks，不在各组之间检索、写作或等待；每组使用全新历史；生成结果后运行 reader_bridge.py collect 自动写回和提取真实日志用量'}
            environment=dict(os.environ)
            if self.remote:environment['MANUAL_READER_TOKEN']=self.headers['Authorization'].removeprefix('Bearer ')
            command=[sys.executable,str(Path(__file__).resolve()),'--base',self.base,'--material',self.headers.get('X-Reader-Material',''),'watch','--out',str(out.resolve())]
            if observe:
                with (out/'observer.log').open('ab') as log:
                    observer=subprocess.Popen(command,env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                result['observer_pid']=observer.pid
            (out/'dispatch.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
            if observe:self.start_caller_observer(out)
            return result
        query=('round='+round_id+'&' if round_id else '')+('include_answered=true' if include_answered else '')
        packet=self.request('/api/work?'+('mode=lesson' if lesson else query))
        if reader_context:
            for key in ('reader_feedback_latest','confirmed_passage','confirmed_understanding'):
                if key in reader_context:packet[key]=reader_context[key]
        out=Path(out);out.mkdir(parents=True,exist_ok=True)
        (out/'work.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),'utf8')
        if lesson:
            (out/'prompt-1.txt').write_text('按下面任务只完成当前一节，输出讲解、术语和选择题的结果 JSON；写回后等待用户，不进入下一节\n'+json.dumps(packet,ensure_ascii=False),'utf8')
            return {'groups':1,'model':packet['writer'],'output_directory':str(out.resolve()),'model_calls':0}
        for i,ids in enumerate(packet['groups'],1):
            questions=[q for q in packet['questions'] if q['id'] in ids]
            from reader_work import group_packet
            shared_text=json.dumps(group_packet(packet,ids),ensure_ascii=False,separators=(',',':'))
            prompt=('使用 gpt-6.1-terra，high 思考档，直接完成下面的阅读回答任务。'
                    '共同材料和规范已在输入中，不再读文件、不操作阅读器、不发送进度说明。'
                    '回答、术语和练习一起输出为结果 JSON，由脚本负责逐项保存。'
                    '确实缺少外部事实时才检索；不加审稿 Agent，不循环修复。\n'
                    +shared_text)
            (out/f'prompt-{i}.txt').write_text(prompt,'utf8')
        return {'questions':len(packet['questions']),'groups':len(packet['groups']),
                'model':packet['writer'],'output_directory':str(out.resolve()),'script_seconds':round(time.perf_counter()-started,4),'model_calls':0,
                'dispatch':'将 prompt 文件中的正文直接交给写作者，不要只给文件路径让写作者再次读取'}

    def collect(self,out,include_run=True):
        from reader_worker import runtime_usage
        out=Path(out);manifest=json.loads((out/'run.json').read_text('utf8'));self.run_id=manifest['id'];receipts=[]
        for group in manifest['groups']:
            identity=group['id'];marker=out/('worker-'+identity+'.json');result=out/('result-'+identity+'.json');receipt=out/('saved-'+identity+'.json')
            if marker.exists():
                meta=json.loads(marker.read_text('utf8'));event=out/('started-'+identity+'.json')
                if not event.exists():
                    self.request('/api/runs/'+self.run_id+'/events',{'group_id':identity,'type':'writer_started'});event.write_text(json.dumps(meta),'utf8')
            if result.exists() and not receipt.exists():
                try:
                    raw=result.read_text('utf8').strip();raw=re.sub(r'^```(?:json)?\s*|\s*```$','',raw);payload=json.loads(raw)
                except (ValueError,OSError):continue
                self.request('/api/runs/'+self.run_id+'/events',{'group_id':identity,'type':'output_ready'})
                saved=self.request('/api/runs/'+self.run_id+'/groups/'+identity+'/result',payload);receipt.write_text(json.dumps(saved,ensure_ascii=False,indent=2),'utf8');receipts.append(saved)
            if marker.exists() and receipt.exists() and not (out/('usage-'+identity+'.json')).exists():
                meta=json.loads(marker.read_text('utf8'));usage=runtime_usage(meta,manifest.get('coordinator_session_id'))
                if usage:
                    self.request('/api/runs/'+self.run_id+'/events',{'group_id':identity,'type':'usage',**usage});(out/('usage-'+identity+'.json')).write_text(json.dumps(usage,ensure_ascii=False,indent=2),'utf8')
        return {'run_id':self.run_id,'groups_saved':len(list(out.glob('saved-*.json'))),'groups_total':len(manifest['groups']),'receipts':receipts,**({'run':self.request('/api/runs/'+self.run_id)} if include_run else {})}

    def start_caller_observer(self,out):
        out=Path(out);manifest=json.loads((out/'run.json').read_text('utf8'))
        if not manifest.get('coordinator_session_id'):return
        environment=dict(os.environ)
        if self.remote:environment['MANUAL_READER_TOKEN']=self.headers['Authorization'].removeprefix('Bearer ')
        args=[sys.executable,str(Path(__file__).resolve()),'--base',self.base,'--material',self.headers.get('X-Reader-Material',''),'watch-caller','--out',str(out.resolve())]
        with (out/'caller-observer.log').open('ab') as log:
            process=subprocess.Popen(args,env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (out/'caller-observer.json').write_text(json.dumps({'pid':process.pid,'session_id':manifest['coordinator_session_id']}),'utf8')

    def watch_caller(self,out):
        from reader_worker import conversation_usage
        out=Path(out);manifest=json.loads((out/'run.json').read_text('utf8'));self.run_id=manifest['id'];identity=manifest.get('coordinator_session_id')
        if not identity:return {'state':'not_collected','reason':'不是从可识别的Codex对话启动'}
        cursor=None;turn=None;last='';began=time.monotonic()
        while time.monotonic()-began<21600:
            try:
                measured=conversation_usage(identity,turn_id=turn,cursor=cursor,at=manifest.get('local_script_started_at'))
                if measured:
                    cursor=measured['cursor'];report=measured['report']
                    if report:
                        turn=report['turn_id'];signature=json.dumps(report,sort_keys=True)
                        if signature!=last:
                            self.request('/api/runs/'+self.run_id+'/events',{'type':'caller','caller':report});(out/'caller-usage.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8');last=signature
                        if report['state'] in ('complete','interrupted'):return report
            except Exception as error:(out/'caller-observer-status.json').write_text(json.dumps({'message':str(error),'generation_cancelled':False},ensure_ascii=False),'utf8')
            time.sleep(5)
        return {'state':'not_finalized','generation_cancelled':False}

    def watch(self,out):
        out=Path(out);manifest=json.loads((out/'run.json').read_text('utf8'));began=time.monotonic()
        while time.monotonic()-began<7200:
            try:
                result=self.collect(out,False)
                if result['groups_saved']==result['groups_total']:
                    if len(list(out.glob('usage-*.json')))==result['groups_total'] or time.time()-max((p.stat().st_mtime for p in out.glob('saved-*.json')),default=time.time())>30:return result
            except Exception as error:
                (out/'observer-status.json').write_text(json.dumps({'at':time.time(),'message':str(error),'generation_cancelled':False},ensure_ascii=False),'utf8')
            time.sleep(1)
        return {'run_id':manifest['id'],'generation_cancelled':False,'state':'results_remain_available'}

    def apply(self, result_path, out, metrics_path=None):
        started=time.perf_counter()
        text=Path(result_path).read_text('utf8').strip()
        if text.startswith('```'):text=re.sub(r'^```(?:json)?\s*|\s*```$','',text)
        result=json.loads(text)
        if 'explanation' in result:
            out=Path(out);out.mkdir(parents=True,exist_ok=True);prepared=out/'lesson-result.json'
            if prepared.exists():
                prior=json.loads(prepared.read_text('utf8'))
                if {k:v for k,v in prior.items() if k!='request_id'}=={k:v for k,v in result.items() if k!='request_id'}:result.setdefault('request_id',prior['request_id'])
            result.setdefault('request_id',uuid.uuid4().hex);prepared.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
            receipt=self.request('/api/teaching/result',result);(out/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),'utf8')
            return {**receipt,'model_calls':0,'script_seconds':round(time.perf_counter()-started,4)}
        progress=self.request('/api/progress')
        known={q['id']:(r,q) for r in progress['rounds'] for q in r['questions']}
        answers=result.get('answers',[])
        if not answers or len({a.get('question_id') for a in answers})!=len(answers):
            raise ValueError('回答列表为空，或同一问题出现多次')
        if any(a.get('question_id') not in known for a in answers):raise ValueError('结果中包含本材料不存在的问题')
        out=Path(out);out.mkdir(parents=True,exist_ok=True);saved=[];items=[];saved_terms=set()
        for answer in answers:
            qid=answer['question_id'];row,q=known[qid];rid=row['id'];round_ids={q['id'] for q in row['questions']}
            payload={'answers':[answer], 'terms':[], 'quizzes':[]}
            for field in ('terms','quizzes'):
                for item in result.get(field,[]):
                    if qid in item.get('question_ids',[]):
                        proposal={**item,'question_ids':[i for i in item['question_ids'] if i in round_ids]}
                        if field=='terms':
                            key=item.get('term_id') or item.get('name')
                            if key in saved_terms:proposal={k:v for k,v in proposal.items() if k in ('term_id','name','question_ids')}
                            else:saved_terms.add(key)
                        payload[field].append(proposal)
            # 每项先落盘，再调用写回；后续项出错时，前面已经完成的内容不会丢失
            target=out/(qid+'.json');temp=target.with_suffix('.next.json')
            temp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),'utf8');temp.replace(target)
            request_started=time.perf_counter();self.request('/api/rounds/'+rid+'/result',payload)
            saved.append(qid)
            items.append({'question_id':qid,'api_seconds':round(time.perf_counter()-request_started,4),
                          'elapsed_seconds':round(time.perf_counter()-started,4)})
            (out/'receipt.json').write_text(json.dumps({'saved':saved,'items':items},ensure_ascii=False,indent=2),'utf8')
        if metrics_path:
            metrics=json.loads(Path(metrics_path).read_text('utf8'))
            if not isinstance(metrics,dict):raise ValueError('用量记录需要是实际运行导出的 JSON 对象')
            for rid in {known[qid][0]['id'] for qid in saved}:
                self.request('/api/rounds/'+rid+'/metrics',metrics)
        return {'saved':len(saved),'model_calls':0,'script_seconds':round(time.perf_counter()-started,4),'output_directory':str(out.resolve())}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base')
    parser.add_argument('--material')
    parser.add_argument('--pairing-code-stdin',action='store_true')
    parser.add_argument('--remember',action='store_true',help='用户选择后，把当前项目连接保存到系统凭据库')
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('connect');sub.add_parser('forget')
    prepare=sub.add_parser('prepare');prepare.add_argument('--round');prepare.add_argument('--question',action='append',dest='question_ids',help='只准备指定原问题，可多次填写');prepare.add_argument('--include-answered',action='store_true');prepare.add_argument('--lesson',action='store_true');prepare.add_argument('--out',required=True);prepare.add_argument('--reader-feedback-file')
    apply=sub.add_parser('apply');apply.add_argument('result');apply.add_argument('--out',required=True);apply.add_argument('--metrics')
    collect=sub.add_parser('collect');collect.add_argument('--out',required=True)
    watch=sub.add_parser('watch');watch.add_argument('--out',required=True)
    caller=sub.add_parser('watch-caller');caller.add_argument('--out',required=True)
    for name in ('run','run-compact','daily'):
        run=sub.add_parser(name);run.add_argument('--out',required=True);run.add_argument('--include-answered',action='store_true');run.add_argument('--reader-feedback-file')
    args=parser.parse_args()
    if args.action=='forget':
        from reader_credentials import delete
        delete(args.base,args.material);print(json.dumps({'forgotten':True}));sys.exit(0)
    bridge=Bridge(args.base,args.material,os.environ.get('MANUAL_READER_TOKEN'),sys.stdin.readline().strip() if args.pairing_code_stdin else os.environ.get('MANUAL_READER_PAIRING_CODE'),remember=args.remember)
    reader_context=feedback_file(getattr(args,'reader_feedback_file',None))
    if args.action=='connect':
        guide=bridge.request('/api/guide');result={'connected':True,'material_id':guide.get('material_id'),'remembered':args.remember,'credential_source':'系统凭据库或本轮显式凭证'}
    elif args.action in ('run','run-compact','daily'):
        from reader_execute import execute,compact_result,daily
        result=daily(bridge,args.out,args.include_answered,reader_context) if args.action=='daily' else execute(bridge,args.out,args.include_answered,reader_context)
        if args.action=='run-compact':result=compact_result(result)
    else:result=bridge.prepare(args.out,args.round,args.include_answered,args.lesson,reader_context=reader_context,question_ids=args.question_ids) if args.action=='prepare' else bridge.collect(args.out) if args.action=='collect' else bridge.watch(args.out) if args.action=='watch' else bridge.watch_caller(args.out) if args.action=='watch-caller' else bridge.apply(args.result,args.out,args.metrics)
    print(json.dumps(result,ensure_ascii=False,indent=2))
