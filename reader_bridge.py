from reader_writer_profiles import writer_profile,writer_label,public_profiles
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
    """从本轮文件读取用户的学习目标、背景和反馈。"""
    if not path:return None
    from reader_contract import reader_context_fields
    raw=Path(path).read_text('utf-8-sig')
    if len(raw)>24000:raise ValueError('反馈文件过长，请只保留这次和学习有关的用户原话')
    try:value=json.loads(raw)
    except ValueError:value=raw
    if isinstance(value,str):return reader_context_fields({'reader_feedback_latest':value})
    if not isinstance(value,dict):raise ValueError('反馈文件需要是用户原话或JSON对象')
    if 'text' in value:return reader_context_fields({'reader_feedback_latest':value})
    result=reader_context_fields(value)
    if not result:raise ValueError('反馈文件没有本轮读者反馈或明确确认的内容')
    return result


class Bridge:
    def __init__(self, base=None, material=None,token=None,pairing=None,remember=False):
        from threading import Lock
        self._source_images={}
        self._image_lock=Lock()
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
                guide=self.request('/api/guide?view=bootstrap')
                if guide.get('material_id')!=material:raise ValueError('服务器返回的项目与连接不一致，未保存凭证')
                remember_token(self.base,material,token);self._from_vault=True
                self._verified_guide = guide
        else:
            session=json.load(self.opener.open(self.base+'/api/session',timeout=30))
            self.headers={'X-Reader-Pin':session['pin'],'X-Reader-Material':material or session['material_id'],'Content-Type':'application/json'}

    def source_image(self,url,download):
        """同一个项目任务中的多个写作组只下载同一张原图一次。"""
        from concurrent.futures import Future
        if not hasattr(self,'_image_lock'):
            return download()
        with self._image_lock:
            pending=self._source_images.get(url)
            owns_download=pending is None
            if owns_download:
                pending=self._source_images[url]=Future()
        if owns_download:
            try:
                pending.set_result(download())
            except Exception as error:
                with self._image_lock:
                    self._source_images.pop(url,None)
                pending.set_exception(error)
        return pending.result()

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
            scoped=manifest.get('reference_mode')=='group_snapshot'
            urls=[group['input_url'] for group in manifest['groups']]
            if not scoped:urls.append(manifest['reference_url'])
            with ThreadPoolExecutor(max_workers=min(4,len(urls))) as pool:payloads=list(pool.map(self.request,urls))
            if scoped:
                evidence={row['id']:row for payload in payloads for row in payload.get('reference_blocks',[])}
                reference={'material_id':manifest.get('material_id'),'passages':list(evidence.values())}
            else:
                reference=payloads.pop()
            (out/'reference.json').write_text(json.dumps(reference,ensure_ascii=False),'utf8')
            worker_script=Path(__file__).parent/'reader_worker.py'
            tasks=[]
            for group,payload in zip(manifest['groups'],payloads):
                identity=group['id'];target=out/('result-'+identity+'.json');payload['reference_file']=str((out/'reference.json').resolve());payload['output_file']=str((out/('result-'+identity+'.draft.json')).resolve())
                payload['publish_command']='你用同一个 Python 执行 reader_worker.py finish --out '+str(out.resolve())+' --group '+identity+'\n脚本核对 JSON、编号和缺项，然后发布一次'
                prompt=out/('prompt-'+identity+'.txt');prompt.write_text('你用 '+writer_label('answer')+' 回答本组问题，在同一次写作里整理术语和选择题，将结果一次保存到 output_file，运行 publish_command 后只回复文件路径和条数\n'+json.dumps(payload,ensure_ascii=False,separators=(',',':')),'utf8')
                tasks.append({'group_id':identity,'model':writer_profile('answer')['model'],'reasoning_effort':writer_profile('answer')['effort'],'fork_turns':'none','message':'你先用 reader_worker.py 的 input 动作读取一次输入，脚本会同时记录真实开始时间和会话编号\n你写完后保存到 output_file，只返回路径和条数\n输入目录 '+str(out.resolve())+'\n组号 '+identity,'input_file':str(prompt.resolve()),'output_file':str(target.resolve()),'worker_script':str(worker_script.resolve())})
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
            (out/'prompt-1.txt').write_text('你只完成当前一节，返回讲解、术语和练习的 JSON，每个讲解片段至少一道题\n你按 practice_policy 选能检验目标的题型，多题组选择题过半\n你按 writing.instructions 接住 reading_context 的实际前文，参考 recent_reader_questions，在本节第一次遇到同类内容时讲得更详细\n写回后，你继续处理本章\n'+json.dumps(packet,ensure_ascii=False),'utf8')
            return {'groups':1,'model':packet['writer'],'output_directory':str(out.resolve()),'model_calls':0}

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
        completed=0
        for path in out.glob('saved-*.json'):
            saved=json.loads(path.read_text('utf8'))
            if not saved.get('missing_question_ids') and not saved.get('issues'):
                completed+=1
        return {'run_id':self.run_id,'groups_saved':completed,'groups_total':len(manifest['groups']),'receipts':receipts,**({'run':self.request('/api/runs/'+self.run_id)} if include_run else {})}

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
            work=out/'work.json'
            if work.exists():
                packet=json.loads(work.read_text('utf8'))
                if packet.get('learning_node',{}).get('id')==result.get('learning_node_id') and packet.get('lesson_task_id'):
                    result.setdefault('lesson_task_id',packet['lesson_task_id'])
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
    terms=sub.add_parser('terms');terms.add_argument('--out',required=True)
    prepare=sub.add_parser('prepare');prepare.add_argument('--round');prepare.add_argument('--question',action='append',dest='question_ids',help='只准备指定原问题，可多次填写');prepare.add_argument('--include-answered',action='store_true');prepare.add_argument('--lesson',action='store_true');prepare.add_argument('--out',required=True);prepare.add_argument('--reader-feedback-file')
    apply=sub.add_parser('apply');apply.add_argument('result');apply.add_argument('--out',required=True);apply.add_argument('--metrics')
    collect=sub.add_parser('collect');collect.add_argument('--out',required=True)
    watch=sub.add_parser('watch');watch.add_argument('--out',required=True)
    resume=sub.add_parser('resume-writeback');resume.add_argument('--out',required=True)
    caller=sub.add_parser('watch-caller');caller.add_argument('--out',required=True)
    for name in ('run','run-compact','daily','teach'):
        run=sub.add_parser(name);run.add_argument('--out',required=True);run.add_argument('--include-answered',action='store_true');run.add_argument('--reader-feedback-file')
    sub.choices['teach'].add_argument('--research-file',help='当前Codex已经搜索核对的补充事实，JSON中notes各项提供text和url')
    args=parser.parse_args()
    if args.action=='forget':
        from reader_credentials import delete
        delete(args.base,args.material);print(json.dumps({'forgotten':True}));sys.exit(0)
    bridge=Bridge(args.base,args.material,os.environ.get('MANUAL_READER_TOKEN'),sys.stdin.readline().strip() if args.pairing_code_stdin else os.environ.get('MANUAL_READER_PAIRING_CODE'),remember=args.remember)
    reader_context=feedback_file(getattr(args,'reader_feedback_file',None))
    if args.action=='connect':
        guide=getattr(bridge,'_verified_guide',None) or bridge.request('/api/guide?view=bootstrap');result={'connected':True,'material_id':guide.get('material_id'),'remembered':args.remember,'credential_source':'系统凭据库或本轮显式凭证'}
    elif args.action=='terms':
        result=__import__('reader_term_maintenance').maintain(bridge,args.out)
    elif args.action=='resume-writeback':
        result=__import__('reader_execute').resume_writeback(bridge,args.out)
    elif args.action in ('run','run-compact','daily','teach'):
        from reader_execute import execute,compact_result,daily,teach
        result=teach(bridge,args.out,reader_context,getattr(args,'research_file',None))if args.action=='teach'else daily(bridge,args.out,args.include_answered,reader_context) if args.action=='daily' else execute(bridge,args.out,args.include_answered,reader_context)
        if args.action=='run-compact':result=compact_result(result)
    else:result=bridge.prepare(args.out,args.round,args.include_answered,args.lesson,reader_context=reader_context,question_ids=args.question_ids) if args.action=='prepare' else bridge.collect(args.out) if args.action=='collect' else bridge.watch(args.out) if args.action=='watch' else bridge.watch_caller(args.out) if args.action=='watch-caller' else bridge.apply(args.result,args.out,args.metrics)
    print(json.dumps(result,ensure_ascii=False,indent=2))
