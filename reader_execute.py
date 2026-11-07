"""用户在本地手动触发后，由脚本并行运行独立写作组，网页不启动模型。"""
import base64
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from urllib.request import Request,urlopen
from urllib.parse import urlsplit,urljoin
import datetime,json,os,re,shutil,subprocess,time,sys

MODEL='gpt-6.1-terra'
from contextvars import ContextVar
CLOSED_WRITER_PORT=ContextVar('reader_closed_writer_port',default=None)
from reader_prompts import STYLE as WRITING_STYLE, TEACHING_METHOD, WRITER_INSTRUCTIONS, NAMING_RULE, WRITING_VERSION
from reader_media import ensure_visible_sources
from reader_quality import answer_parts
from reader_generation import normalize_output,VERSION as FRAMEWORK_VERSION

def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def save(path,value):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.next')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf8');temporary.replace(path)

def build_prompt(payload,reference,style=WRITING_STYLE):
    payload=dict(payload);payload['writing_version']=WRITING_VERSION
    for key in ('output_file','publish_command','reference_file'):payload.pop(key,None)
    files={s.get('file_id') for s in payload['sources'] if s.get('content_kind')!='explanation'}
    files.update(l.get('file_id') for s in payload['sources'] for l in s.get('source_links',[]))
    originals=[p for p in reference.get('passages',[]) if p.get('file_id') in files]
    # 原件完整保留；同一原文已经出现在任务附近时，只保留一处，并继续使用原编号。
    supplied={s['id']:[s.get('original','')] for s in payload['sources']}
    for block in payload.get('reference_blocks',[]):supplied.setdefault(block['id'],[]).append(block.get('text',''))
    payload['full_original_reference']=[p for p in originals if p.get('text','') not in supplied.get(p['id'],[])]
    payload['instructions']=[s for s in payload['instructions'] if not any(k in s for k in ('reference.json','result JSON 文件','本地 JSON','本地文件','最终只返回文件路径','文件路径和条数','问题、原句和规则已写入'))]
    if TEACHING_METHOD not in payload['instructions']:payload['instructions'].append(TEACHING_METHOD)
    payload['instructions']+=['原文、必要写作规则和可用原图已经放入这一次输入，不再为了读取它们调用工具',
      '直接输出最终JSON对象，包含本组全部回答、术语和本组选择题；客户端负责保存，不写文件，不输出收尾消息',
      '理解原句所必需的前置必须在回答里讲清；简单问题简洁，复杂问题充分，不限制回答字数',
      '公式中的多字母程序标识使用LaTeX的mathtt或text保留整个名称，并说明其含义；数学下标使用完整花括号。程序标识与数学下标要区分，不把程序名称擅自改成另一种变量，也不把多字母名称当成几个字母相乘',
      '图片已按 sources.media 中的编号顺序随输入附上；必须实际读图，对估计值和不确定标记保持原有含义',
      NAMING_RULE]
    payload['instructions'].append('必要时可使用可用的图片生成工具，一组最多生成一张新插图，优先已有原图或Mermaid。通过generated_assets返回实际工具产生的path、alt、question_ids；内置工具默认写入本次会话的generated_images目录，脚本会核对会话编号，复制到本组目录并上传，再插入对应回答，不要编造已保存路径。没有实际生成成功时不要写虚构地址。空间形状或结构需要直观看时配图；简单词义不配图。')
    payload['instructions'].append('同次输出 plan 及其 unit_ids，每道练习用 placement 声明插入哪个回答 part 后；正文、术语和练习之外不输出重复成稿')
    payload['instructions'].append('新术语只提交名称、定义、直观解释和问题编号，不自行编造term_id；只有复用registered_terms中已有条目时才返回其真实term_id，新编号由脚本分配')
    if not payload.get('registered_terms'):
        payload['result_contract']={**payload['result_contract'],'terms':[{k:v for k,v in t.items() if k!='term_id'} for t in payload['result_contract']['terms']]}
    payload['instructions']=list(dict.fromkeys(payload['instructions']))
    return '请用 Terra high 完成本组阅读回答，直接返回结果JSON\n'+style+'\n任务与原材料：\n'+json.dumps(payload,ensure_ascii=False,separators=(',',':'))

def command(executable,folder,target,images,writer=True):
    args=[executable,'exec','--ignore-user-config','--skip-git-repo-check','--json','--color','never','-m',MODEL,'-c','model_reasoning_effort="high"','-c','service_tier="default"','-c','web_search="live"','-c','approval_policy="never"','-s','workspace-write','-C',str(folder),'-o',str(target)]
    if writer:
        writer_instructions=Path(folder)/'writer-instructions.txt'
        writer_instructions.write_text(WRITER_INSTRUCTIONS,'utf8')
        args+=['-c','model_instructions_file='+json.dumps(str(writer_instructions.resolve()))]
    for flag in ('apps','plugins','multi_agent','shell_tool','browser_use','computer_use','workspace_dependencies','hooks','fast_mode'):
        args+=['--disable',flag]
    args+=['--enable','image_generation']
    for image in images:args+=['-i',str(image)]
    if writer and CLOSED_WRITER_PORT.get():
        args[args.index('--enable')]='--disable'
        args+=['-c','openai_base_url='+json.dumps('http://127.0.0.1:'+str(CLOSED_WRITER_PORT.get()))]
    return args+['-']


def store_generated_assets(bridge,value,folder,session_id=None):
    """只上传本组实际生成的图片，不接受指向其他目录的文件名。"""
    import io,hashlib
    from PIL import Image
    issues=[];known={a['question_id']:a for a in value.get('answers',[])};assets=value.get('generated_assets') or []
    if not isinstance(assets,list):return ['插图清单格式不正确，正文已保留']
    for item in assets[:1]:
        if not isinstance(item,dict) or not isinstance(item.get('path'),str) or not isinstance(item.get('question_ids'),list):
            issues.append('插图缺少实际文件或关联问题，正文已保留');continue
        path=Path(item['path'].replace('\\\\','\\') if os.name=='nt' else item['path']);path=path if path.is_absolute() else folder/path
        built_in=False
        if session_id and path.is_file():
            homes=[Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'generated_images',Path.home()/'.codex/generated_images']
            built_in=any(path.resolve().is_relative_to(home.resolve()) and path.resolve().relative_to(home.resolve()).parts[0]==session_id for home in homes)
        if (not path.resolve().is_relative_to(folder.resolve()) and not built_in) or not path.is_file():
            issues.append('插图文件不在本组输出目录，正文已保留');continue
        if path.stat().st_size>12*1024*1024:issues.append('插图超过12MB，正文已保留');continue
        if built_in:
            copied=folder/('generated-'+hashlib.sha256(path.read_bytes()).hexdigest()[:20]+path.suffix.lower());shutil.copy2(path,copied);path=copied;item['path']=copied.name
        try:
            with Image.open(path) as original:
                if original.width*original.height>24_000_000:raise ValueError('插图像素过多')
                image=original.convert('RGB');output=io.BytesIO();image.save(output,format='PNG');raw=output.getvalue()
            identity='illustration-'+hashlib.sha256(raw).hexdigest()[:20]
            receipt=bridge.request('/api/content/files',{'file_id':identity,'filename':identity+'.png','content_base64':base64.b64encode(raw).decode()})
            url=receipt['original_url'];alt=re.sub(r'[\[\]\r\n]',' ',str(item.get('alt') or '讲解示意图'))
            for qid in item.get('question_ids',[]):
                if qid in known:known[qid]['text']+='\n\n!['+alt+']('+url+')'
        except (OSError,ValueError,KeyError,Image.DecompressionBombError) as error:issues.append('插图未保存：'+str(error))
    return issues

def codex_executable():
    configured=os.environ.get('MANUAL_READER_CODEX')
    if configured:return configured
    candidates=[];local=os.environ.get('LOCALAPPDATA')
    if local:candidates.extend((Path(local)/'OpenAI/Codex/bin').glob('*/codex.exe'))
    ordinary=shutil.which('codex')
    if ordinary:candidates.append(Path(ordinary))
    ranked=[]
    for path in candidates:
        version=subprocess.run([str(path),'--version'],capture_output=True,text=True,encoding='utf8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        numbers=re.search(r'(\d+)\.(\d+)\.(\d+)',version.stdout)
        if version.returncode==0 and numbers:ranked.append((tuple(map(int,numbers.groups())),path))
    return str(max(ranked,key=lambda item:item[0])[1]) if ranked else None

def source_images(bridge,payload,folder):
    images=[];seen=set()
    needed={q['source_id'] for q in payload.get('questions',[]) if q.get('visual_requirement',{}).get('explicit')}
    entries=[m for source in payload['sources'] if payload.get('mode')=='lesson' or source.get('id') in needed or source.get('source_kind',source.get('kind')) in ('image','visual','page_image','table','formula','equation') for m in (source.get('media_items') or [source.get('media') or ({'src':source['src']}if source.get('src')else{})])]
    for media in entries:
        url=media.get('src')
        if url in seen:continue
        seen.add(url)
        if not url:continue
        if str(url).startswith('/'):
            url=bridge.base.rstrip('/')+url if bridge.remote and not str(url).startswith('/machine/') else urljoin(bridge.base,url)
        parsed=urlsplit(url)
        if parsed.scheme not in ('http','https'):raise ValueError('原图需要可读取的HTTP或HTTPS地址')
        if bridge.remote and parsed.netloc==urlsplit(bridge.base).netloc and parsed.path.startswith(('/content-files/','/page/','/asset/')):
            url=parsed._replace(path='/machine/v1'+parsed.path).geturl();parsed=urlsplit(url)
        headers={}
        if parsed.netloc==urlsplit(bridge.base).netloc:headers={k:v for k,v in bridge.headers.items() if k!='Content-Type'}
        path=folder/('source-'+str(len(images)+1)+'.image')
        opener=bridge.opener.open if headers else urlopen
        with opener(Request(url,headers=headers),timeout=60) as response:
            kind=response.headers.get_content_type();suffix={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp','image/gif':'.gif'}.get(kind)
            if not suffix:raise ValueError('原图地址没有返回可读取的图片')
            raw=response.read(30*1024*1024+1)
            if len(raw)>30*1024*1024:raise ValueError('原图超过30MB，请提供对应区域的原始图文件')
        path=path.with_suffix(suffix);path.write_bytes(raw);images.append(path)
    return images

def result_json(raw,group,payload=None,reference=None):
    raw=re.sub(r'^```(?:json)?\s*|\s*```$','',raw.strip());value=json.loads(raw)
    answers=value.get('answers',[]);expected=set(group['question_ids'])
    if len(answers)!=len(expected) or {a.get('question_id') for a in answers}!=expected:raise ValueError('回答编号存在缺项或重复，原始输出已保留')
    if not isinstance(value.get('terms'),list) or not isinstance(value.get('quizzes'),list):raise ValueError('术语和练习需要使用列表，原始输出已保留')
    if payload is not None:
        sources=list(payload.get('sources',[]))+list((reference or{}).get('passages',[]))
        value=normalize_output(value,questions=payload['questions'],sources=sources)
    return value

def normalize_terms(value,payload):
    known={t['id'] for t in payload.get('registered_terms',[])};changes=[]
    for term in value['terms']:
        identity=term.get('term_id')
        if identity and identity not in known:
            if not term.get('name') or not (term.get('professional_definition') or term.get('definition')) or not term.get('intuitive_explanation'):raise ValueError('术语引用不在登记表中，且缺少创建新条目所需的内容')
            changes.append({'model_proposed_id':identity,'name':term['name'],'action':'保留完整定义和直观解释，由阅读器分配真实编号'});term.pop('term_id')
    return changes

def invoke_writer(folder,prompt,images,executable):
    """记录真实输入、模型输出、检索和用量；不读取维护聊天或连接凭证。"""
    from reader_worker import runtime_usage,external_tool_usage
    folder=Path(folder);folder.mkdir(exist_ok=True,parents=True);target=folder/'final.txt';events=folder/'events.jsonl'
    if events.exists():raise ValueError('本次调用已有日志，请读取原任务，不重复生成')
    (folder/'input.txt').write_text(prompt,'utf8');args=command(executable,folder,target,images)
    save(folder/'invocation.json',{'arguments':args,'model':MODEL,'effort':'high','service_tier':'default','images':[str(p)for p in images],'prompt_characters':len(prompt),'framework_version':FRAMEWORK_VERSION})
    environment=dict(os.environ)
    for key in ('CODEX_THREAD_ID','CODEX_SESSION_ID','MANUAL_READER_TOKEN','MANUAL_READER_PAIRING_CODE'):environment.pop(key,None)
    session_id=None;started=time.perf_counter()
    with events.open('w',encoding='utf8')as log,(folder/'stderr.txt').open('w',encoding='utf8')as err:
        process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,text=True,encoding='utf8',env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));process.stdin.write(prompt);process.stdin.close()
        for line in process.stdout:
            log.write(line);log.flush()
            try:event=json.loads(line)
            except ValueError:continue
            if event.get('type')=='thread.started':session_id=event.get('thread_id')
        code=process.wait()
    if code or not target.exists():raise RuntimeError('本次写作未完成，已生成的内容和日志保留，不重新派发其他任务')
    usage=runtime_usage({'session_id':session_id,'configured_service_tier':'default'})
    if not usage or(usage.get('model'),usage.get('effort'),usage.get('configured_service_tier'))!=(MODEL,'high','default'):raise RuntimeError('实际模型或用量记录不符合 Terra high 普通服务，原始结果保留')
    usage.update(seconds=round(time.perf_counter()-started,3),writing_version=WRITING_VERSION,tool_usage=external_tool_usage(events.read_text('utf8').splitlines()))
    save(folder/'usage.json',usage);return target.read_text('utf8'),usage,session_id

def checked_writing(folder,prompt,images,executable,validate):
    raw,usage,session=invoke_writer(folder,prompt,images,executable)
    try:return validate(raw),usage,session
    except(ValueError,KeyError,TypeError)as error:
        save(folder/'validation.json',{'complete':False,'missing':str(error),'raw_preserved':True,'max_supplements':1})
        prompt+='\n前次实际输出与脚本发现的缺项：\n'+raw+'\n'+str(error)+'\n这是同一写作任务唯一的一次集中补齐，不是审稿。保留正确的内容、编号和关联，只补齐上述缺项，返回完整合同 JSON；不要缩短正文，不要改变当前章节。'
        raw2,extra,_=invoke_writer(folder/'supplement',prompt,images,executable)
        try:value=validate(raw2)
        except(ValueError,KeyError,TypeError)as again:
            save(folder/'validation.json',{'complete':False,'missing':str(again),'raw_preserved':True,'supplements_used':1,'next_action':'按记录中的准确缺项继续原任务，不重新生成已保存内容'})
            raise
        merged={**usage,'usage':{k:usage['usage'].get(k,0)+extra['usage'].get(k,0)for k in usage['usage']},'requests':usage.get('requests',[])+extra.get('requests',[]),'model_completed_at':extra['model_completed_at'],'seconds':usage['seconds']+extra['seconds'],'tool_usage':{'web_search_calls':usage.get('tool_usage',{}).get('web_search_calls',0)+extra.get('tool_usage',{}).get('web_search_calls',0)},'supplements_used':1}
        save(folder/'validation.json',{'complete':True,'supplements_used':1,'review_agents':0});return value,merged,session

def run_group(bridge,out,group,payload,reference,executable):
    identity=group['id'];folder=out/('writer-'+identity);folder.mkdir(exist_ok=True)
    prompt=build_prompt(payload,reference);(folder/'input.txt').write_text(prompt,'utf8')
    images=source_images(bridge,payload,folder)
    bridge.request('/api/runs/'+bridge.run_id+'/events',{'group_id':identity,'type':'dispatch'})
    bridge.request('/api/runs/'+bridge.run_id+'/events',{'group_id':identity,'type':'writer_started'})
    value,usage,session_id=checked_writing(folder,prompt,images,executable,lambda raw:result_json(raw,group,payload,reference))
    save(out/('worker-'+identity+'.json'),{'session_id':session_id,'configured_service_tier':'default'})
    usage.update(group_id=identity,type='usage',writing_version=WRITING_VERSION,configured_service_tier='default',model=MODEL,effort='high')
    bridge.request('/api/runs/'+bridge.run_id+'/events',usage);save(out/('usage-'+identity+'.json'),usage)
    questions={q['id']:q for q in payload['questions']}
    for answer in value['answers']:
        text,parts,issues=answer_parts(answer,questions[answer['question_id']]['text']);answer['text']=text;answer['format_audit']=issues
        if parts:answer['answer_parts']=parts;answer.pop('parts',None)
    before_answers={a['question_id']:a.get('text','') for a in value['answers']};asset_issues=store_generated_assets(bridge,value,folder,session_id);changes=normalize_terms(value,payload);media_changes=ensure_visible_sources(value,payload);save(folder/'script-processing.json',{'generated_asset_issues':asset_issues,'media_changes':media_changes,'term_id_changes':changes,'answer_text_changed':any(a.get('text','')!=before_answers[a['question_id']] for a in value['answers']),'definitions_changed':False});save(out/('result-'+identity+'.json'),value)
    bridge.request('/api/runs/'+bridge.run_id+'/events',{'group_id':identity,'type':'output_ready'})
    receipt=bridge.request('/api/runs/'+bridge.run_id+'/groups/'+identity+'/result',value);save(out/('saved-'+identity+'.json'),receipt)
    if receipt.get('issues') or receipt.get('missing_question_ids'):raise RuntimeError('部分结果未保存，准确缺项已写入本组保存记录；不重跑已完成内容')
    return {'group_id':identity,'saved':len(receipt['saved']),'session_id':session_id}

def repair_term_names(bridge,out,question_ids,executable):
    from reader_worker import runtime_usage,external_tool_usage
    normalized=bridge.request('/api/terms/normalize',{});pending=[p for p in bridge.request('/api/terms/audit')['pending'] if set(p.get('question_ids',[])).intersection(question_ids)]
    report={'script_normalized':len(normalized['changed']),'duplicate_redirects':normalized['duplicate_redirects'],'pending_before':len(pending),'model_calls':0}
    if not pending:return report
    folder=out/'term-name-repair';folder.mkdir(exist_ok=True);target=folder/'final.txt'
    prompt='只修正下面术语名称，返回JSON：{"updates":[{"term_id":"真实编号","name":"中文全称（English Full Name）"}],"unresolved":[]}。有缩写时使用“缩写 中文全称（English Full Name）”，中文全称也需展开缩写的含义，不得只把缩写挪到中文部分。不修改定义、问题、编号或其他内容。根据给出的定义确定中英文对应，括号里只保留英文全称，除明确标记的例外外每个英文单词首字母大写。不得重复中文、颠倒中英文、编造缩写展开。无法确认的编号放入unresolved，不猜测。只做这一次批量处理，不再添加审稿或修复循环。必要时可以检索名称，已有规则无需读取文件。\n'+json.dumps(pending,ensure_ascii=False,separators=(',',':'))
    (folder/'input.txt').write_text(prompt,'utf8');args=command(executable,folder,target,[],writer=False);environment=dict(os.environ)
    for key in ('CODEX_THREAD_ID','CODEX_SESSION_ID','MANUAL_READER_TOKEN','MANUAL_READER_PAIRING_CODE'):environment.pop(key,None)
    session=None;started=stamp();metadata=None
    try:
        with (folder/'events.jsonl').open('w',encoding='utf8') as log,(folder/'stderr.txt').open('w',encoding='utf8') as err:
            process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,text=True,encoding='utf8',env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));process.stdin.write(prompt);process.stdin.close()
            for line in process.stdout:
                log.write(line);log.flush()
                try:event=json.loads(line)
                except ValueError:continue
                if event.get('type')=='thread.started':session=event.get('thread_id')
            code=process.wait()
        metadata=runtime_usage({'session_id':session,'configured_service_tier':'default'})
        if metadata:
            metadata.update(state='generated',started_at=started,completed_at=stamp(),tool_usage=external_tool_usage((folder/'events.jsonl').read_text('utf8').splitlines()));bridge.request('/api/runs/'+bridge.run_id+'/events',{'type':'term_repair','repair':metadata});save(folder/'usage.json',metadata)
            if metadata.get('model')!=MODEL or metadata.get('effort')!='high' or metadata.get('configured_service_tier')!='default':raise ValueError('术语补齐的实际配置与Terra high普通服务不一致，原始记录已保留')
        if code or not target.exists():raise ValueError('一次术语名称补齐没有完成，原始记录保留，回答不受影响')
        parsed=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',target.read_text('utf8').strip()));known={p['term_id']:p for p in pending};updates=[]
        from reader_term_names import analyze_name
        for row in parsed.get('updates',[]):
            if row.get('term_id') not in known:continue
            old=known[row['term_id']];markers={k:old[k] for k in ('case_exceptions','english_case_exception') if k in old}
            fixed=analyze_name(row.get('name',''),markers.get('case_exceptions',[]),bool(markers.get('english_case_exception')))
            if fixed['issues']:continue
            updates.append({'term_id':row['term_id'],'expected_name':old['name'],'name':fixed['name'],**markers})
        if updates:bridge.request('/api/terms',{'updates':updates})
        report.update(model_calls=(metadata or {}).get('usage',{}).get('model_calls'),repaired=len(updates),unresolved=[p['term_id'] for p in pending if p['term_id'] not in {u['term_id'] for u in updates}])
    except Exception as error:report.update(state='needs_attention',message=str(error),raw_output_preserved=True)
    save(folder/'script-processing.json',report);return report

def _execute(bridge,out,include_answered=False,reader_context=None):
    began=stamp();clock=time.perf_counter();cpu=time.process_time();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    if (out/'run.json').exists():raise ValueError('这个目录已经有任务记录，请为新一轮选择空目录，避免覆盖原始输出')
    executable=codex_executable()
    if not executable:raise ValueError('本地没有找到Codex CLI，请安装并登录后再手动运行；网页不会代替调用模型')
    manifest=bridge.prepare(out,include_answered=include_answered,observe=False,reader_context=reader_context)
    if not manifest.get('questions'):
        report={'run':bridge.request('/api/runs/'+bridge.run_id),'coordinator':{'kind':'local_script','total_seconds':round(time.perf_counter()-clock,3)},'groups':[],'term_names':{'state':'not_needed'},'issues':[],'question_pool':manifest.get('question_pool',{}),'message':manifest.get('message')}
        save(out/'measured-run.json',report)
        return report
    bridge.start_caller_observer(out)
    run=json.loads((out/'run.json').read_text('utf8'));reference=json.loads((out/'reference.json').read_text('utf8'))
    payloads={g['id']:json.loads((out/('prompt-'+g['id']+'.txt')).read_text('utf8').split('\n',1)[1]) for g in run['groups']}
    prepare_seconds=time.perf_counter()-clock;results=[];errors=[]
    with ThreadPoolExecutor(max_workers=max(1,min(2,len(run['groups'])))) as pool:
        futures={pool.submit(run_group,bridge,out,g,payloads[g['id']],reference,executable):g['id'] for g in run['groups']}
        for future in as_completed(futures):
            try:results.append(future.result())
            except Exception as error:errors.append({'group_id':futures[future],'message':str(error)})
    term_names=repair_term_names(bridge,out,set(run['question_ids']),executable) if not errors else {'state':'waiting_for_answers'}
    coordinator={'kind':'local_script','model':None,'started_at':began,'completed_at':stamp(),'total_seconds':round(time.perf_counter()-clock,3),'script_cpu_seconds':round(time.process_time()-cpu,3),'prepare_seconds':round(prepare_seconds,3),'parallel_groups':min(2,len(run['groups'])),'usage':{'input_tokens':0,'cached_input_tokens':0,'cache_write_input_tokens':0,'output_tokens':0,'reasoning_output_tokens':0,'model_calls':0},'scope':'这里只记录本地脚本和并行写作窗口，0 token不包含Codex读取、启动、等待和最终汇报；Codex整轮由caller另记，完整费用须等待整轮日志结算'}
    bridge.request('/api/runs/'+bridge.run_id+'/events',{'type':'coordinator','coordinator':coordinator})
    report={'run':bridge.request('/api/runs/'+bridge.run_id),'coordinator':coordinator,'groups':results,'term_names':term_names,'issues':errors};save(out/'measured-run.json',report)
    if errors:raise RuntimeError('部分组需要处理，已完成结果和准确原因见 measured-run.json；没有启动模型修复循环')
    return report

def execute(bridge,out,include_answered=False,reader_context=None):
    """把执行进程单独登记，外层不把 Shell 提前返回当作写作结束。"""
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    if(out/'run.json').exists()or(out/'execution-state.json').exists():raise ValueError('这个目录已有任务，请等待或核对原任务；不能覆盖执行状态或重新派发')
    state={'pid':os.getpid(),'state':'running','started_at':stamp()}
    save(out/'execution-state.json',state)
    try:
        result=_execute(bridge,out,include_answered,reader_context)
        save(out/'execution-state.json',{**state,'state':'complete','completed_at':stamp()})
        return result
    except Exception as error:
        save(out/'execution-state.json',{**state,'state':'stopped','completed_at':stamp(),'message':str(error),'completed_outputs_preserved':True})
        raise

def teach(bridge,out,reader_context=None,research_file=None):
    """用户手动触发当前章；复用问答的模型调用和校验，不自动翻到下一章。"""
    began=stamp();clock=time.perf_counter();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    if(out/'lesson-report.json').exists()or(out/'writer/events.jsonl').exists():raise ValueError('本轮目录已有任务，请读取原稿和保存记录，不重复生成')
    bridge.prepare(out,lesson=True,observe=False,reader_context=reader_context)
    packet=json.loads((out/'work.json').read_text('utf8'));node=packet['learning_node'];missing=packet.get('translation_queue',{}).get('current_source_ids',[])
    if packet.get('writing',{}).get('version')!=WRITING_VERSION:raise ValueError('网站与本机的讲解规则版本不同，请先下载 guide.client_files 的当前客户端；尚未调用模型，已有内容不变')
    if not packet.get('registered_terms'):
        packet['result_contract']['terms']=[{k:v for k,v in t.items()if k!='term_id'}for t in packet['result_contract']['terms']]
    if missing:
        result={'state':'translations_required','missing_source_ids':missing,'next_action':'按原编号补齐本章非中文译文，再继续当前任务','model_calls':0};save(out/'lesson-report.json',result);return result
    if node.get('status')=='ready'and node.get('explanation_ids'):
        result={'state':'already_prepared','learning_node_id':node['id'],'explanation_ids':node['explanation_ids'],'next_action':'沿用已有讲解，处理本章问题；用户手动勾选完成后才能进入下一章','model_calls':0};save(out/'lesson-report.json',result);return result
    executable=codex_executable()
    if not executable:raise ValueError('请先安装并登录本地 Codex，网页不自行调用模型')
    folder=out/'writer';folder.mkdir();images=source_images(bridge,packet,folder)
    if research_file:
        research=json.loads(Path(research_file).read_text('utf8'))
        if not isinstance(research,dict)or not isinstance(research.get('notes'),list):raise ValueError('检索资料使用含notes列表的JSON，每项提供text和url')
        for row in research['notes']:
            if not isinstance(row,dict)or not isinstance(row.get('text'),str)or not isinstance(row.get('url'),str)or not row['url'].startswith(('https://','http://')):raise ValueError('每条检索补充都需要事实与实际来源网址')
        packet['researched_context']=research
    packet['writing_tool_policy']='原材料、原图与主对话按需搜索的补充资料已经随输入附齐；最终成稿不调用文件、枚举或搜索工具；保留所有来源与原图，可使用Mermaid，额外新插图由主对话生成并登记后提供'
    prompt='用 Terra high 普通服务，只完成给定当前章；先按同一次输出中的 plan 组织正文，再提取术语和练习，直接返回 result_contract 规定的 JSON。\n'+WRITING_STYLE+'\n'+json.dumps(packet,ensure_ascii=False,separators=(',',':'))
    source_rows=[s for s in packet['sources']if s.get('content_kind')!='explanation']
    def validate(raw):
        value=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',raw.strip()))
        value['writing_version']=WRITING_VERSION
        value=normalize_output(value,sources=source_rows,lesson=True,required_source_ids=[s['id']for s in source_rows],reading_context=packet.get('reading_context'))
        value.update(learning_node_id=node['id'],lesson_task_id=packet['lesson_task_id'],writing_version=WRITING_VERSION)
        value['explanation'].setdefault('id','lesson-'+node['id']);value['explanation'].setdefault('title',node['title'])
        return value
    from reader_writer_transport import Gate
    gate=Gate(out/'writer-wire-policy.jsonl',port=0).start();policy=CLOSED_WRITER_PORT.set(gate.port)
    try:result,usage,session=checked_writing(folder,prompt,images,executable,validate)
    finally:CLOSED_WRITER_PORT.reset(policy);gate.stop()
    changes=normalize_terms(result,packet);save(out/'script-processing.json',{'term_id_changes':changes,'body_changed':False})
    save(out/'lesson-checked.json',result);receipt=bridge.apply(out/'lesson-checked.json',out)
    report={'state':'complete'if receipt.get('complete')else'needs_completion','learning_node_id':node['id'],'started_at':began,'seconds':round(time.perf_counter()-clock,3),'usage':usage,'receipt':receipt,'review_agents':0,'automatic_navigation':False}
    save(out/'lesson-report.json',report);return report


def process_running(pid):
    if not isinstance(pid,int)or pid<=0:return False
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return False
        try:
            code=wintypes.DWORD();return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code)))and code.value==259
        finally:kernel.CloseHandle(handle)
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True


def wait_for_report(out,alive=process_running,pause=time.sleep):
    """只等已经启动的这一轮，不重新派发，也不把等待次数算作新的模型调用。"""
    out=Path(out);target=out/'measured-run.json';missing=0
    while not target.exists():
        state_file=out/'execution-state.json'
        try:state=json.loads(state_file.read_text('utf8'))if state_file.exists()else{}
        except (OSError,ValueError):state={}
        if state.get('state')=='running'and alive(state.get('pid')):
            save(out/'daily-state.json',{'state':'waiting_for_existing_execution','pid':state['pid'],'updated_at':stamp(),'redispatched':False});missing=0
        elif state.get('state')in('complete','stopped'):
            raise RuntimeError('本轮执行进程已结束，但统计文件尚未完成；已保存回答和原始输出保留，请核对 execution-state.json，不要重新派发写作')
        else:
            missing+=1
            if missing>=6:raise RuntimeError('没有找到仍在运行的本轮执行进程，也没有最终统计；请核对本轮日志，不能将此状态当作问题未回答')
        pause(1)
    return json.loads(target.read_text('utf8'))


def compact_result(report):
    """只向主调度返回运行摘要，完整输入、输出和日志继续保存在本轮目录。"""
    run=report['run']
    return {'run_id':run['id'],'question_count':len(run['question_ids']),'state':run['state'],'writer_seconds':report['coordinator']['total_seconds'],'usage':run['usage'],'estimated_token_cost_usd':run.get('estimated_token_cost_usd'),'web_search_calls':run.get('web_search_calls'),'estimated_total_cost_usd':run.get('estimated_total_cost_usd'),'issues':report['issues'],'output':'measured-run.json',**{k:report[k] for k in ('question_pool','message') if k in report}}

def daily(bridge,out,include_answered=False,reader_context=None):
    """手动触发时另开干净的Terra high普通会话，只做本轮回答调度和汇报。"""
    from reader_worker import conversation_usage,external_tool_usage
    import shlex
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True);folder=out/'coordinator';folder.mkdir()
    executable=codex_executable();target=folder/'reply.md'
    if not executable:raise ValueError('请先安装并登录本地Codex')
    child=[sys.executable,str(Path(__file__).with_name('reader_bridge.py')),'--base',bridge.base,'--material',bridge.headers['X-Reader-Material'],'run-compact','--out',str(out)]
    if include_answered:child+=['--include-answered']
    if reader_context:
        context_file=out/'reader-feedback.json';save(context_file,reader_context)
        child+=['--reader-feedback-file',str(context_file)]
    shell=('& '+' '.join("'"+v.replace("'","''")+"'" for v in child)) if os.name=='nt' else shlex.join(child)
    prompt=('你是阅读器日常回答任务的主调度，只处理当前项目的未回答问题。立即执行下面的一条命令；程序会读取问题和材料、附上原图、按已准备的分组数量，以Terra high普通服务并行生成完整回答、必要前置、术语和练习，并逐组写回。不要开发、测试、部署、读其他文件或历史，也不要自己重复写答案。程序运行期间必须等到 measured-run.json 已落盘才可汇报，Shell 返回后台会话编号不代表任务结束；工具返回仍在运行时，用write_stdin等候，单次等候可用60000毫秒，不要频繁轮询。命令完成后，用中文简短汇报实际完成的问题数、写作窗口耗时和返回的用量，明确主调度本次会话的最终用量由启动器在你汇报结束后补入。不得改模型、推理档、服务档或并行数。\n'+shell)
    args=command(executable,folder,target,[],writer=False);args[args.index('-s')+1]='danger-full-access'
    for flag in ('shell_tool',):
        pos=args.index(flag);del args[pos-1:pos+1]
    args[args.index('web_search="live"')]='web_search="disabled"'
    env=dict(os.environ)
    for key in ('CODEX_THREAD_ID','CODEX_SESSION_ID','MANUAL_READER_PAIRING_CODE'):env.pop(key,None)
    if bridge.remote:env['MANUAL_READER_TOKEN']=bridge.headers['Authorization'].removeprefix('Bearer ')
    began=stamp();clock=time.perf_counter();(folder/'input.txt').write_text(prompt,'utf8');save(folder/'invocation.json',{'arguments':args,'model':MODEL,'effort':'high','service_tier':'default','previous_conversation_messages':0,'began_at':began})
    session=None
    with (folder/'events.jsonl').open('w',encoding='utf8') as events,(folder/'stderr.log').open('w',encoding='utf8') as error:
        process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=error,text=True,encoding='utf8',env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));process.stdin.write(prompt);process.stdin.close()
        for line in process.stdout:
            events.write(line);events.flush()
            try:event=json.loads(line)
            except ValueError:continue
            if event.get('type')=='thread.started':session=event['thread_id'];save(folder/'session.json',{'session_id':session})
        code=process.wait()
    # 主调度可能在 Shell 返回后台会话编号时结束；启动器仍等同一批写作者落盘。
    report=wait_for_report(out)
    if not session:raise RuntimeError('写作记录已保留，但主调度没有返回会话编号，暂不能结算主调度用量')
    caller=None
    for attempt in range(20):
        value=conversation_usage(session)
        if value and value['report'] and value['report']['state']=='complete':caller=value['report'];break
        time.sleep(.25)
    if not caller:raise RuntimeError('主调度已返回，但最终用量日志尚未结算，原始输出保留')
    caller['configured_service_tier']=caller.get('configured_service_tier') or 'default'
    for request in caller['requests']:request['configured_service_tier']=request.get('configured_service_tier') or 'default'
    if (caller.get('model'),caller.get('effort'),caller['configured_service_tier'])!=(MODEL,'high','default'):raise RuntimeError('主调度实际配置不符合Terra high普通服务，日志已保留')
    caller.update(launch_started_at=began,launch_completed_at=stamp(),launch_to_final_seconds=round(time.perf_counter()-clock,3),tool_usage=external_tool_usage((folder/'events.jsonl').read_text('utf8').splitlines()))
    bridge.run_id=report['run']['id'];bridge.request('/api/runs/'+bridge.run_id+'/events',{'type':'caller','caller':caller})
    run=bridge.request('/api/runs/'+bridge.run_id)
    result={'run_id':bridge.run_id,'boundary':'从启动干净主调度到它完成最终汇报；包含读取问题、程序启动、两路写作、必要检索、写回与主调度等待，开发、验收和部署在另一会话记录','started_at':began,'completed_at':stamp(),'launch_to_final_seconds':round(time.perf_counter()-clock,3),'caller':caller,'run':run,'complete_usage':run['complete_usage'],'complete_token_cost_usd':run['complete_estimated_token_cost_usd'],'complete_cost_with_recorded_search_usd':None,'actual_subscription_charge':None}
    if result['complete_token_cost_usd'] is not None:result['complete_cost_with_recorded_search_usd']=round(result['complete_token_cost_usd']+run.get('web_search_calls',0)*.01,8)
    save(out/'daily-complete.json',result)
    return {'run_id':bridge.run_id,'question_count':len(run['question_ids']),'seconds':result['launch_to_final_seconds'],'usage':result['complete_usage'],'estimated_token_cost_usd':result['complete_token_cost_usd'],'estimated_total_cost_usd':result['complete_cost_with_recorded_search_usd'],'detail_file':str(out/'daily-complete.json')}
