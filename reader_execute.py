"""用户在本地手动触发后，由脚本并行运行独立写作组，网页不启动模型。"""
import base64
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from urllib.request import Request,urlopen
from urllib.parse import urlsplit,urljoin
import datetime,json,os,re,shutil,subprocess,time,sys

from reader_writer_profiles import writer_profile,writer_label,public_profiles
MODEL=writer_profile('answer')['model']
EFFORT=writer_profile('answer')['effort']
from contextvars import ContextVar
WRITER_ROLE=ContextVar('reader_writer_role',default='answer')
from reader_prompts import WRITER_INSTRUCTIONS, WRITING_VERSION
from reader_task_prompts import DAILY_QA_SUMMARY, READING_ANALYSIS, TERM_NAME_CORRECTION, section, section_body
from reader_media import check_visuals,ensure_visible_sources,publish_coordinate_figures
from reader_quality import answer_parts
from reader_generation import answer_evidence_sources,normalize_output,VERSION as FRAMEWORK_VERSION

def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def save(path,value):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.next')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf8');temporary.replace(path)

def writer_task(payload):
    """直接调用时，系统指令已提供正式写作规则。"""
    task = dict(payload)
    instructions = task.get('instructions', [])
    task['instructions'] = [rule for rule in instructions if rule != WRITER_INSTRUCTIONS]
    return task


def build_prompt(payload, reference):
    task = writer_task(payload)
    task['writing_version'] = WRITING_VERSION
    # 这些字段供手动派发使用。直接调用的写作者只返回成稿。
    for key in ('output_file', 'publish_command', 'reference_file', 'full_original_reference',
                'result_contract', 'writer', 'schema_version'):
        task.pop(key, None)
    task['instructions'] = [rule for rule in task['instructions'] if not any(
        marker in rule for marker in ('reference.json', 'result JSON 文件', '本地 JSON', '本地文件',
                                      '最终只返回文件路径', '文件路径和条数', '问题、原句和规则已写入'))]
    visual_kinds = ('image','visual','page_image','table','formula','equation')
    needs_visual = any(q.get('visual_requirement',{}).get('explicit') for q in task.get('questions',[])) or any(
        source.get('source_kind',source.get('kind')) in visual_kinds for source in task['sources'])
    if needs_visual:
        task['instructions'].append('你回答这组问题前要查看附上的原图，保留图中的估计值和标记\n图打不开时，你从原文页找能核对的版本，同一失败地址只请求一次')
    else:
        task['instructions'].append('你先读当前提问，只有这次的结论需要图中证据时才查看相关原图，上一轮的配图按本题需要取用')
    task['instructions'].append('你直接返回最终 JSON，新图用 generated_assets 返回真实 path、alt 和 question_ids，客户端负责核对并保存')
    return ('你用 ' + writer_label('answer') + ' 回答本组问题，直接返回结果 JSON\n\n'
            + json.dumps(task, ensure_ascii=False, separators=(',', ':')))


def command(executable,folder,target,images,writer=True,role=None,allow_image_generation=True,system_instructions=WRITER_INSTRUCTIONS,research=True):
    profile=writer_profile(role or(WRITER_ROLE.get()if writer else'coordinator'))
    args=[executable,'exec','--ignore-user-config','--ignore-rules','--skip-git-repo-check','--json','--color','never','-m',profile['model'],'-c','model_reasoning_effort='+json.dumps(profile['effort']),'-c','service_tier="default"','-c','web_search='+json.dumps('live' if research else 'disabled'),'-c','approval_policy="never"','-s','workspace-write','-C',str(folder),'-o',str(target)]
    if writer:
        writer_instructions=Path(folder)/'writer-instructions.txt'
        writer_instructions.write_bytes(system_instructions.encode('utf8'))
        args+=['-c','model_instructions_file='+json.dumps(str(writer_instructions.resolve()))]
    for flag in ('apps','plugins','multi_agent','shell_tool','browser_use','computer_use','workspace_dependencies','hooks','fast_mode'):
        args+=['--disable',flag]
    args+=['--enable'if allow_image_generation else'--disable','image_generation']
    for image in images:args+=['--image='+str(image)]
    return args+['-']


def store_generated_assets(bridge,value,folder,session_id=None,*,supplement_session=None):
    """只上传本组实际生成的图片，不接受指向其他目录的文件名。"""
    import io,hashlib
    from PIL import Image
    issues=[];known={a['question_id']:a for a in value.get('answers',[])};assets=value.get('generated_assets') or []
    if not isinstance(assets,list):return ['插图清单格式不正确，正文已保留']
    for item in assets:
        if not isinstance(item,dict) or not isinstance(item.get('path'),str) or not isinstance(item.get('question_ids'),list):
            issues.append('插图缺少实际文件或关联问题，正文已保留');continue
        path=Path(item['path'].replace('\\\\','\\') if os.name=='nt' else item['path']);path=path if path.is_absolute() else folder/path
        built_in=False
        if (session_id or supplement_session) and path.is_file():
            homes=[Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'generated_images',Path.home()/'.codex/generated_images']
            built_in=any(path.resolve().is_relative_to(home.resolve()) and path.resolve().relative_to(home.resolve()).parts[0] in (session_id,supplement_session) for home in homes)
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
            receipt=bridge.request('/api/content/files',{'file_id':identity,'filename':identity+'.png','purpose':'illustration','content_base64':base64.b64encode(raw).decode()})
            url=receipt['original_url'];alt=re.sub(r'[\[\]\r\n]',' ',str(item.get('alt') or '讲解示意图'))
            markdown='!['+alt+']('+url+')'
            for qid in item.get('question_ids',[]):
                if qid not in known:continue
                answer=known[qid];parts=answer.get('answer_parts')or answer.get('parts')or[]
                part=next((p for p in parts if p.get('id')==item.get('after_part_id')),None)or(parts[-1]if parts else None)
                if part and markdown not in part.get('markdown',''):part['markdown']=part.get('markdown','')+'\n\n'+markdown
                if markdown not in answer.get('text',''):answer['text']=answer.get('text','')+'\n\n'+markdown
            parts=value.get('explanation',{}).get('parts',[])
            if parts:
                part=next((p for p in parts if p.get('id')==item.get('after_part_id')),None)or parts[-1]
                if markdown not in part['markdown']:part['markdown']+='\n\n'+markdown
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
    entries=[(m,False) for source in payload['sources'] if payload.get('mode')=='lesson' or source.get('id') in needed or source.get('source_kind',source.get('kind')) in ('image','visual','page_image','table','formula','equation') for m in (source.get('media_items') or [source.get('media') or ({'src':source['src']}if source.get('src')else{})])]
    nearby=[row for row in payload.get('reference_blocks',[]) if row.get('src')]
    for media, optional in entries+[(row,True) for row in nearby[:2]]:
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
        def download():
            with opener(Request(url,headers=headers),timeout=8 if optional else 60) as response:
                kind=response.headers.get_content_type();suffix={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp','image/gif':'.gif'}.get(kind)
                if not suffix:raise ValueError('原图地址没有返回可读取的图片')
                raw=response.read(30*1024*1024+1)
                if len(raw)>30*1024*1024:raise ValueError('原图超过30MB，请提供对应区域的原始图文件')
                return suffix,raw
        try:
            suffix,raw=bridge.source_image(url,download) if hasattr(bridge,'source_image') else download()
        except (OSError,ValueError):
            if optional:continue
            raise
        path=path.with_suffix(suffix);path.write_bytes(raw);images.append(path)
    return images

def result_json(raw,group,payload=None,reference=None,source_aliases=None):
    raw=re.sub(r'^```(?:json)?\s*|\s*```$','',raw.strip());value=json.loads(raw)
    if source_aliases:value=__import__('reader_writer_input').restore_output(value,source_aliases)
    answers=value.get('answers',[]);expected=set(group['question_ids'])
    if len(answers)!=len(expected) or {a.get('question_id') for a in answers}!=expected:raise ValueError('回答编号存在缺项或重复，原始输出已保留')
    if not isinstance(value.get('terms'),list) or not isinstance(value.get('quizzes'),list):raise ValueError('术语和练习需要使用列表，原始输出已保留')
    if payload is not None:
        sources=answer_evidence_sources(payload,(reference or{}).get('passages',[]))
        value=normalize_output(value,questions=payload['questions'],sources=sources,
                               registered_terms=payload.get('registered_terms',[]))
    return value

def normalize_terms(value,payload):
    from reader_term_quality import validate_asset_term
    known=payload.get('registered_terms',[]);changes=[]
    for term in value['terms']:
        old=validate_asset_term(term,known)
        if old and (term.get('term_id'),term.get('name'))!=(old['id'],old['name']):
            term.update(term_id=old['id'],name=old['name'])
            changes.append({'term_id':old['id'],'name':old['name']})
    return changes

def invoke_writer(folder,prompt,images,executable,system_override=None,allow_image_generation=True,research=True):
    """记录真实输入、模型输出、检索和用量；不读取维护聊天或连接凭证。"""
    from reader_worker import runtime_usage,external_tool_usage
    folder=Path(folder);folder.mkdir(exist_ok=True,parents=True);target=folder/'final.txt';events=folder/'events.jsonl'
    if events.exists():raise ValueError('本次调用已有日志，请读取原任务，不重复生成')
    system=section_body(TERM_NAME_CORRECTION, 1) if WRITER_ROLE.get()=='term_repair' else WRITER_INSTRUCTIONS
    if system_override is not None:system=system_override
    (folder/'input.txt').write_text(prompt,'utf8')
    args=command(executable,folder,target,images,allow_image_generation=allow_image_generation,system_instructions=system,research=research)
    if(folder/'output-schema.json').exists():args[-1:-1]=['--output-schema',str(folder/'output-schema.json')]
    from reader_writer_transport import Gate
    gate=Gate(writer_profile(WRITER_ROLE.get()),prompt,system,folder,research=research,
              allow_image_generation=allow_image_generation and WRITER_ROLE.get()!='term_repair').start()
    args[-1:-1]=['-c','openai_base_url='+json.dumps('http://127.0.0.1:'+str(gate.port))]
    save(folder/'invocation.json',{'arguments':args,**writer_profile(WRITER_ROLE.get()),'images':[str(p)for p in images],'prompt_characters':len(prompt),'framework_version':FRAMEWORK_VERSION})
    environment=dict(os.environ)
    for key in ('CODEX_THREAD_ID','CODEX_SESSION_ID','MANUAL_READER_TOKEN','MANUAL_READER_PAIRING_CODE'):environment.pop(key,None)
    session_id=None;started=time.perf_counter();failure=None;code=None
    try:
        with events.open('w',encoding='utf8')as log,(folder/'stderr.txt').open('w',encoding='utf8')as err:
            process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,text=True,encoding='utf8',env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));process.stdin.write(prompt);process.stdin.close()
            for line in process.stdout:
                log.write(line);log.flush()
                try:event=json.loads(line)
                except ValueError:continue
                if not isinstance(event,dict):continue
                if event.get('type')=='thread.started':session_id=event.get('thread_id')
            code=process.wait()
    except (OSError, UnicodeError) as error:
        failure=error
    finally:gate.stop()
    from reader_writer_transport import measured_usage
    usage=measured_usage(gate.receipts,writer_profile(WRITER_ROLE.get()),session_id)or runtime_usage({'session_id':session_id,'configured_service_tier':'default'})
    if usage:
        usage.update(seconds=round(time.perf_counter()-started,3),writing_version=WRITING_VERSION,
                     tool_usage=external_tool_usage(events.read_text('utf8').splitlines()),
                     research_requests=gate.search_requests,
                     research_model_calls=sum(row.get('research_roundtrip',False) for row in gate.receipts),
                     transport_policy='current-task-web-and-images-v1' if research else 'current-task-analysis-v1',
                     actual_request_receipts='transport.json')
        save(folder/'usage.json',usage)
    if gate.output_stalled:
        raise RuntimeError('写作服务持续返回空白，客户端已停止等待。部分草稿和收到的用量回执已保留；未写回阅读器。')
    if failure is not None:
        raise RuntimeError('客户端读取写作流时中断，草稿及已收到的用量回执已保留') from failure
    if code or not target.exists():raise RuntimeError('本次写作未完成，已生成的内容、实际用量和日志保留，继续核对原任务')
    expected=writer_profile(WRITER_ROLE.get())
    if not usage or(usage.get('model'),usage.get('effort'),usage.get('configured_service_tier'))!=(expected['model'],expected['effort'],'default'):raise RuntimeError('实际模型或档位与当前写作配置不同，原始结果保留，请更新客户端后继续原任务')
    return target.read_text('utf8'),usage,session_id

def analyze_task(folder, packet, executable):
    from reader_analysis import analysis_packet, analysis_schema, reading_plan_markdown, validate_analysis
    analysis_dir=Path(folder)/'analysis'
    analysis_dir.mkdir()
    task=analysis_packet(packet)
    save(analysis_dir/'task.json',task)
    save(analysis_dir/'output-schema.json',analysis_schema(task))
    raw,usage,_=invoke_writer(analysis_dir,json.dumps(task,ensure_ascii=False,separators=(',',':')),[],executable,
                             system_override=READING_ANALYSIS,allow_image_generation=False,research=False)
    result=validate_analysis(task,json.loads(raw))
    save(analysis_dir/'result.json',result)
    (analysis_dir/'plan.md').write_text(reading_plan_markdown(task,result),'utf8')
    return {**packet,'reader_analysis':result},usage


def with_analysis_evidence(validate, task):
    from reader_analysis import writing_evidence_issues
    from reader_term_quality import cover_planned_terms
    def checked(raw):
        value=json.loads(raw)
        problems=[]
        for check in (
            lambda:cover_planned_terms(value,task['reader_analysis'],task.get('registered_terms',[])),
            lambda:writing_evidence_issues(value,task['reader_analysis']),
            lambda:check_visuals(value,task['reader_analysis'],[*task.get('sources',[]),*task.get('reference_blocks',[])]),
        ):
            try:problems.extend(check() or [])
            except (ValueError,KeyError,TypeError,AttributeError) as error:problems.append(str(error))
        try:result=validate(json.dumps(value,ensure_ascii=False))
        except (ValueError,KeyError,TypeError,AttributeError) as error:
            for issue in str(error).splitlines():
                # 术语在恢复来源编号之前已核对，避免同一条报错带着两个数组索引返回。
                if re.match(r'/terms/\d+[「/]',issue) and any(
                    re.sub(r'^/terms/\d+','/terms',issue)==re.sub(r'^/terms/\d+','/terms',previous)
                    for previous in problems):
                    continue
                problems.append(issue)
        if problems:
            raise ValueError('\n'.join(dict.fromkeys(problems)))
        return result
    return checked


def combined_stage_usage(analysis, writing):
    from collections import Counter
    from reader_analysis import VERSION
    total=Counter(analysis['usage'])
    total.update(writing['usage'])
    return {**writing,'usage':dict(total),'requests':analysis.get('requests',[])+writing.get('requests',[]),
            'usage_complete':analysis.get('usage_complete',False) and writing.get('usage_complete',False),
            'http_requests':analysis.get('http_requests',0)+writing.get('http_requests',0),
            'model_started_at':analysis['model_started_at'],'seconds':round(analysis['seconds']+writing['seconds'],3),
            'analysis_version':VERSION,
            'stages':{name:{k:receipt[k] for k in ('session_id','seconds','usage','http_requests') if k in receipt}
                      for name,receipt in {'analysis':analysis,'writing':writing,**writing.get('extra_stages',{})}.items()}}


def parse_draft(raw):
    return json.loads(re.sub(r'^\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60$','',raw.strip()))


def load_usage(folder):
    path=Path(folder)/'usage.json'
    return json.loads(path.read_text('utf8')) if path.is_file() else None


def supplemental_patch(folder,draft,task,issues,images,executable):
    patching=__import__('reader_writer_patch')
    reference={key:value for key,value in task.items() if key not in
               ('instructions','writing','writing_tool_policy','writer','result_contract',
                'output_file','reference_file','publish_command','full_original_reference')}
    prompt=json.dumps({'validation_errors':issues,'original_draft':draft,'task_reference':reference},
                      ensure_ascii=False,separators=(',',':'))
    supplement=folder/'supplement';supplement.mkdir(exist_ok=True)
    save(supplement/'output-schema.json',patching.schema(__import__('reader_writer_schema').output_schema(task)))
    raw,usage,session=invoke_writer(supplement,prompt,images,executable,system_override=patching.INSTRUCTION)
    result=patching.apply(draft,parse_draft(raw))
    save(supplement/'merged.json',result)
    return result,usage,session


def combine_supplement(usage,extra,session):
    from collections import Counter
    total=Counter(usage['usage']);total.update(extra['usage'])
    return {**usage,'usage':dict(total),'requests':usage.get('requests',[])+extra.get('requests',[]),
            'http_requests':usage.get('http_requests',0)+extra.get('http_requests',0),
            'model_completed_at':extra['model_completed_at'],'seconds':usage['seconds']+extra['seconds'],
            'tool_usage':{'web_search_calls':usage.get('tool_usage',{}).get('web_search_calls',0)+extra.get('tool_usage',{}).get('web_search_calls',0)},
            'image_tools':usage.get('image_tools',[])+extra.get('image_tools',[]),
            'research_requests':usage.get('research_requests',[])+extra.get('research_requests',[]),
            'research_model_calls':usage.get('research_model_calls',0)+extra.get('research_model_calls',0),
            'usage_complete':usage.get('usage_complete',False)and extra.get('usage_complete',False),
            'supplements_used':1,'supplement_session_id':session,
            'extra_stages':{'writing':usage,'supplement':extra}}


def checked_writing(folder,prompt,images,executable,validate,task=None):
    raw,usage,session=invoke_writer(folder,prompt,images,executable)
    original=parse_draft(raw)
    original,format_changes=__import__('reader_writer_format').normalize(original)
    if format_changes:
        save(folder/'format-normalization.json',format_changes)
        raw=json.dumps(original,ensure_ascii=False)
    try:return validate(raw),usage,session
    except(ValueError,KeyError,TypeError)as error:
        save(folder/'validation.json',{'complete':False,'missing':str(error),'raw_preserved':True,'max_supplements':1})
        patched,extra,supplement_session=supplemental_patch(folder,original,task,str(error).splitlines(),images,executable)
        merged=combine_supplement(usage,extra,supplement_session)
        save(folder/'combined-usage.json',merged)
        raw2=json.dumps(patched,ensure_ascii=False)
        try:value=validate(raw2)
        except(ValueError,KeyError,TypeError)as again:
            save(folder/'validation.json',{'complete':False,'missing':str(again),'raw_preserved':True,'supplements_used':1,'next_action':'按记录中的准确缺项继续原任务，不重新生成已保存内容'})
            raise
        save(folder/'validation.json',{'complete':True,'supplements_used':1,'review_agents':0})
        merged['supplement_session_id']=supplement_session
        return value,merged,session

def validate_answer_draft(draft,packed,payload,reference,aliases,required,already=()):
    from reader_generation import answer_fragment,practice_owner,practice_prompt_key
    from reader_term_quality import link_draft_terms
    link_draft_terms(draft,payload.get('registered_terms',[]))
    sources=answer_evidence_sources(payload,reference.get('passages',[]))
    tasks={q['id']:q for q in payload['questions']}
    valid,issues={},{}
    seen=set(already)
    from collections import Counter
    counts=Counter(a.get('question_id') for a in draft.get('answers',[]) if isinstance(a,dict))
    for qid in required:
        try:
            if counts[qid]!=1:
                raise ValueError('写作结果必须包含当前问题的一条回答')
            fragment,analysis=answer_fragment(draft,[qid],packed['reader_analysis'])
            def normalize(raw):
                value=__import__('reader_writer_input').restore_output(json.loads(raw),aliases)
                return normalize_output(value,questions=[tasks[qid]],sources=sources,
                                        registered_terms=payload.get('registered_terms',[]),check_practice_mix=False)
            value=with_analysis_evidence(normalize,{**packed,'reader_analysis':analysis})(json.dumps(fragment,ensure_ascii=False))
            prompts={practice_prompt_key(quiz) for quiz in value['quizzes']}
            if prompts & seen:
                raise ValueError('本回答的练习与另一回答重复，请给本部分换一个实际能检验的情境')
            seen.update(prompts)
            valid[qid]=value
        except (ValueError,KeyError,TypeError,AttributeError) as error:
            issues[qid]=str(error)
    # 确保题型规则在整组成立，仅标出需要换题型的回答。
    quizzes=[q for value in valid.values() for q in value['quizzes']]
    complete_quizzes=quizzes+list(already.values()) if isinstance(already,dict) else quizzes
    policy=__import__('reader_practice_policy').practice_policy()
    total=len(complete_quizzes)
    needed=(total//2+1 if total>1 else 0)-sum(q.get('type')=='choice' for q in complete_quizzes)
    objective=(3*total+3)//4-sum(q.get('type') in policy['objective_types'] for q in complete_quizzes)
    for quiz in sorted(quizzes,key=lambda q:q.get('type') in policy['objective_types']):
        if needed<=0 and objective<=0:
            break
        qid=practice_owner(quiz)[0]
        if quiz.get('type')=='choice':
            continue
        if qid in valid:
            valid.pop(qid)
        issue=f"{practice_owner(quiz)[1]} 的练习改为能检验原能力的选择题，确保本组选择题过半且至少四分之三可客观判分"
        issues[qid]='\n'.join(filter(None,(issues.get(qid),issue)))
        needed-=1
        objective-=int(quiz.get('type') not in policy['objective_types'])
    return valid,issues


def submit_answers(bridge,folder,run_id,identity,valid,usage,session,index):
    if not valid:
        return None,{}
    fragments=list(valid.values());first=fragments[0]
    combined={key:first[key] for key in ('writing_version','framework_version','plan')}
    by_id={u['id']:u for frag in fragments for u in frag['plan']['units']}
    order=dict.fromkeys(uid for frag in fragments for uid in frag['plan']['order'])
    # 单题片段都已核对依赖。合并时让公共前置继续排在所有使用它的单元之前。
    ready=set()
    sorted_ids=[]
    def place(uid):
        if uid in ready:
            return
        for parent in by_id[uid]['needs']:
            if parent in by_id:
                place(parent)
        ready.add(uid)
        sorted_ids.append(uid)
    for uid in order:
        place(uid)
    combined['plan']={**first['plan'],'units':list(by_id.values()),'order':sorted_ids}
    for field in ('answers','terms','quizzes'):
        combined[field]=[]
    issues={}
    for qid,value in valid.items():
        try:
            publish_coordinate_figures(bridge,value)
            failures=store_generated_assets(bridge,value,folder,session,supplement_session=usage.get('supplement_session_id'))
            if failures:
                raise ValueError('；'.join(failures))
            for field in ('answers','terms','quizzes'):
                combined[field].extend(value[field])
        except (ValueError,KeyError,OSError) as error:
            issues[qid]=str(error)
    if not combined['answers']:
        return None,issues
    save(folder/f'writeback-{index}.json',combined)
    path=f'/api/runs/{run_id}/groups/{identity}/result'
    receipt=post_answer_batch(bridge,path,combined)
    save(folder/f'receipt-{index}.json',receipt)
    issues.update({row['question_id'] or 'placement':row['message'] for row in receipt.get('issues',[])})
    return receipt,issues


def post_answer_batch(bridge,path,payload):
    """重交同一份已核对的写回，使用服务端回执防止重复保存。"""
    try:
        return bridge.request(path,payload)
    except (OSError, ValueError) as error:
        from urllib.error import HTTPError
        if isinstance(error,HTTPError) and error.code<500:
            raise
        # 写回带事务回执；响应丢失时可重交原稿，已保存的回答沿用原编号。
        return bridge.request(path,payload)


def cache_saved_answer_sources(cache,folder,index,receipt,source_folder):
    if not receipt or not receipt.get('saved'):
        return
    try:
        saved=set(receipt['saved'])
        payload=json.loads((folder/f'writeback-{index}.json').read_text('utf8'))
        answers=[answer for answer in payload['answers'] if answer['question_id'] in saved]
        stats=__import__('reader_source_cache').remember(cache,source_folder,answers)
        save(folder/f'source-cache-{index}.json',stats)
    except (OSError,ValueError):
        # 回答已提交，临时缓存失败不会改变其写回结果。
        return


def resume_writeback(bridge,out):
    """响应中断时重交已落盘的批次；本次命令不会调用模型。"""
    out=Path(out)
    manifest=json.loads((out/'run.json').read_text('utf8'))
    if manifest.get('material_id')!=bridge.headers.get('X-Reader-Material'):
        raise ValueError('原任务不属于当前项目')
    bridge.run_id=manifest['id']
    report=bridge.request('/api/runs/'+bridge.run_id)
    receipts=[]
    for group in report['groups']:
        if group.get('saved_at'):
            continue
        folder=out/('writer-'+group['id'])
        path=f"/api/runs/{bridge.run_id}/groups/{group['id']}/result"
        for draft in sorted(folder.glob('writeback-*.json')):
            payload=json.loads(draft.read_text('utf8'))
            receipts.append(bridge.request(path,payload))
    run=bridge.request('/api/runs/'+bridge.run_id)
    missing={g['id']:[qid for qid in g['question_ids'] if qid not in g.get('saved_question_ids',[])]
             for g in run['groups'] if not g.get('saved_at')}
    return {'run':run,'receipts':receipts,'missing_question_ids':missing,'model_calls':0}


def run_group(bridge,out,group,payload,reference,executable):
    identity=group['id'];folder=out/('writer-'+identity);folder.mkdir(exist_ok=True)
    cache=__import__('reader_source_cache').for_bridge(bridge)
    packed,aliases=__import__('reader_writer_input').compact_packet(payload);save(folder/'source-aliases.json',aliases)
    bridge.request('/api/runs/'+bridge.run_id+'/events',{'group_id':identity,'type':'dispatch'})
    try:
        packed,analysis_usage=analyze_task(folder,packed,executable)
    except Exception:
        if analysis_usage:=load_usage(folder/'analysis'):
            total=combined_stage_usage(analysis_usage,{'usage':{},'seconds':0,'usage_complete':False})
            total.update(group_id=identity,type='usage',writing_version=WRITING_VERSION,configured_service_tier='default',model=MODEL,effort=EFFORT)
            save(out/('usage-'+identity+'.json'),total)
            bridge.request('/api/runs/'+bridge.run_id+'/events',total)
        raise
    packed=__import__('reader_source_cache').augment(packed,cache)
    save(folder/'output-schema.json',__import__('reader_writer_schema').output_schema(packed))
    prompt=build_prompt(packed,reference);(folder/'input.txt').write_text(prompt,'utf8')
    images=source_images(bridge,payload,folder)
    bridge.request('/api/runs/'+bridge.run_id+'/events',{'group_id':identity,'type':'writer_started'})
    usage=None
    saved=[]
    issues={}
    session_id=None
    try:
        raw,usage,session_id=invoke_writer(folder,prompt,images,executable)
        draft,changes=__import__('reader_writer_format').normalize(parse_draft(raw))
        if changes:
            save(folder/'format-normalization.json',changes)
        save(folder/'draft.json',draft)
        valid,issues=validate_answer_draft(draft,packed,payload,reference,aliases,group['question_ids'])
        bridge.request('/api/runs/'+bridge.run_id+'/events',{'group_id':identity,'type':'output_ready'})
        receipt,write_issues=submit_answers(bridge,folder,bridge.run_id,identity,valid,usage,session_id,1)
        issues.update(write_issues)
        saved.extend((receipt or {}).get('saved',[]))
        cache_saved_answer_sources(cache,folder,1,receipt,folder)
        if issues and set(saved)!=set(group['question_ids']):
            save(folder/'validation.json',{'saved':saved,'issues':issues})
            from reader_generation import answer_fragment,practice_prompt_key
            remaining=[qid for qid in group['question_ids'] if qid not in saved]
            fragment,analysis=answer_fragment(draft,remaining,packed['reader_analysis'])
            task=__import__('reader_writer_input').answer_repair_packet(packed,remaining,analysis)
            task=__import__('reader_source_cache').augment(task,cache)
            repair_folder=folder/'supplement';repair_folder.mkdir(exist_ok=True)
            original_scope=__import__('reader_writer_input').answer_repair_packet(payload,remaining,analysis)
            repair_images=source_images(bridge,original_scope,repair_folder)
            fragment,extra,supplement_session=supplemental_patch(folder,fragment,task,issues,repair_images,executable)
            fragment,_=__import__('reader_writer_format').normalize(fragment)
            usage=combine_supplement(usage,extra,supplement_session)
            stems={practice_prompt_key(quiz):quiz for qid in saved for quiz in valid[qid]['quizzes']}
            repaired,issues=validate_answer_draft(fragment,task,payload,reference,aliases,remaining,stems)
            receipt,write_issues=submit_answers(bridge,folder,bridge.run_id,identity,repaired,usage,session_id,2)
            issues.update(write_issues)
            saved.extend((receipt or {}).get('saved',[]))
            cache_saved_answer_sources(cache,folder,2,receipt,repair_folder)
    finally:
        if usage is None:
            usage=load_usage(folder)
        if usage and not usage.get('supplements_used') and (extra:=load_usage(folder/'supplement')):
            usage=combine_supplement(usage,extra,extra.get('session_id'))
        save(out/('saved-'+identity+'.json'),{'saved':saved,'issues':issues,
                                            'missing_question_ids':[qid for qid in group['question_ids'] if qid not in saved]})
        total=combined_stage_usage(analysis_usage,usage or {'usage':{},'seconds':0,'usage_complete':False})
        total.update(group_id=identity,type='usage',writing_version=WRITING_VERSION,configured_service_tier='default',model=MODEL,effort=EFFORT)
        save(out/('usage-'+identity+'.json'),total)
        bridge.request('/api/runs/'+bridge.run_id+'/events',total)
    if set(saved)!=set(group['question_ids']):
        raise RuntimeError('未写回的问题与准确原因已留在本组回执：'+json.dumps(issues,ensure_ascii=False))
    return {'group_id':identity,'saved':len(saved),'session_id':session_id}

def repair_term_names(bridge,out,question_ids,executable):
    from reader_worker import runtime_usage,external_tool_usage
    normalized=bridge.request('/api/terms/normalize',{});pending=[p for p in bridge.request('/api/terms/audit')['pending'] if set(p.get('question_ids',[])).intersection(question_ids)]
    report={'script_normalized':len(normalized['changed']),'duplicate_redirects':normalized['duplicate_redirects'],'pending_before':len(pending),'model_calls':0}
    if not pending:return report
    folder=out/'term-name-repair';folder.mkdir(exist_ok=True);target=folder/'final.txt'
    prompt=section(TERM_NAME_CORRECTION, 2)+'\n\n'+json.dumps(pending,ensure_ascii=False,separators=(',',':'))
    session=None;metadata=None;role_token=WRITER_ROLE.set('term_repair')
    schema={'type':'object','properties':{'updates':{'type':'array','items':{'type':'object','properties':{'term_id':{'type':'string','enum':[p['term_id']for p in pending]},'name':{'type':'string'}},'required':['term_id','name'],'additionalProperties':False}},'unresolved':{'type':'array','items':{'type':'string'}}},'required':['updates','unresolved'],'additionalProperties':False}
    save(folder/'output-schema.json',schema)
    try:
        raw,metadata,session=invoke_writer(folder,prompt,[],executable)
        bridge.request('/api/runs/'+bridge.run_id+'/events',{'type':'term_repair','repair':metadata})
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
    finally:WRITER_ROLE.reset(role_token)
    save(folder/'script-processing.json',report);return report

def _execute(bridge,out,include_answered=False,reader_context=None,*,observe_caller=True):
    began=stamp();clock=time.perf_counter();cpu=time.process_time();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    if (out/'run.json').exists():raise ValueError('这个目录已经有任务记录，请为新一轮选择空目录，避免覆盖原始输出')
    executable=codex_executable()
    if not executable:raise ValueError('本地没有找到Codex CLI，请安装并登录后再手动运行；网页不会代替调用模型')
    manifest=bridge.prepare(out,include_answered=include_answered,observe=False,reader_context=reader_context)
    if not manifest.get('questions'):
        report={'run':bridge.request('/api/runs/'+bridge.run_id),'coordinator':{'kind':'local_script','total_seconds':round(time.perf_counter()-clock,3)},'groups':[],'term_names':{'state':'not_needed'},'issues':[],'question_pool':manifest.get('question_pool',{}),'message':manifest.get('message')}
        save(out/'measured-run.json',report)
        return report
    if observe_caller:
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

def execute(bridge,out,include_answered=False,reader_context=None,*,observe_caller=True):
    """把执行进程单独登记，外层不把 Shell 提前返回当作写作结束。"""
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    if(out/'run.json').exists()or(out/'execution-state.json').exists():raise ValueError('这个目录已有任务，请等待或核对原任务；不能覆盖执行状态或重新派发')
    state={'pid':os.getpid(),'state':'running','started_at':stamp()}
    save(out/'execution-state.json',state)
    try:
        result=_execute(bridge,out,include_answered,reader_context,observe_caller=observe_caller)
        save(out/'execution-state.json',{**state,'state':'complete','completed_at':stamp()})
        return result
    except Exception as error:
        save(out/'execution-state.json',{**state,'state':'stopped','completed_at':stamp(),'message':str(error),'completed_outputs_preserved':True})
        raise

def build_lesson_prompt(payload):
    task = writer_task(payload)
    # 客户端已经核对本章能否开始，写作者只需要本章材料和成稿规则。
    for key in ('instructions', 'writing', 'ingestion_queue', 'current_task', 'translation_queue',
                'translation_writeback', 'lesson_task_id', 'writing_tool_policy', 'writer', 'result_contract'):
        task.pop(key, None)
    task['research_scope'] = '你先用本章原文和已核对的来源\n当前结论缺少依据时，合并查找需要的事实，后续章节的细节留到实际讲到时再查'
    return ('你用 ' + writer_label('lesson') + ' 普通服务完成当前章，直接返回讲解、术语和练习的 JSON\n\n'
            + json.dumps(task, ensure_ascii=False, separators=(',', ':')))


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
    packet['writing_tool_policy']='直接使用随输入提供的原文、原图和规则，不为这些已有材料枚举工具或重读文件；必要事实缺失时可搜索并打开来源，用户要求配图时沿用原图或实际工具生成的图片，不编造地址；资料足够后直接成稿'
    packet['source_groups']=__import__('reader_generation').source_groups(packet['sources'])
    cache=__import__('reader_source_cache').for_bridge(bridge)
    packed,aliases=__import__('reader_writer_input').compact_packet(packet);save(folder/'source-aliases.json',aliases)
    source_rows=[s for s in packet['sources']if s.get('content_kind')!='explanation']
    def validate(raw):
        value=__import__('reader_writer_input').restore_output(json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',raw.strip())),aliases)
        value['writing_version']=WRITING_VERSION
        value=normalize_output(value,sources=source_rows,lesson=True,required_source_ids=[s['id']for s in source_rows],
                               reading_context=packet.get('reading_context'),registered_terms=packet.get('registered_terms',[]))
        value.update(learning_node_id=node['id'],lesson_task_id=packet['lesson_task_id'],writing_version=WRITING_VERSION)
        value['explanation'].setdefault('id','lesson-'+node['id']);value['explanation'].setdefault('title',node['title'])
        return value
    role_token=WRITER_ROLE.set('lesson')
    try:
        packed,analysis_usage=analyze_task(folder,packed,executable)
        packed=__import__('reader_source_cache').augment(packed,cache)
        save(folder/'output-schema.json',__import__('reader_writer_schema').output_schema(packed))
        result,usage,session=checked_writing(folder,build_lesson_prompt(packed),images,executable,
                                             with_analysis_evidence(validate,packed),task=packed)
        usage=combined_stage_usage(analysis_usage,usage)
    finally:WRITER_ROLE.reset(role_token)
    save(folder/'source-cache.json',__import__('reader_source_cache').remember(cache,folder,result))
    publish_coordinate_figures(bridge,result)
    asset_issues=store_generated_assets(bridge,result,folder,session,supplement_session=usage.get('supplement_session_id'))
    changes=normalize_terms(result,packet);save(out/'script-processing.json',{'term_id_changes':changes,'generated_asset_issues':asset_issues,'body_changed':bool(result.get('generated_assets'))})
    save(out/'lesson-checked.json',result)
    if asset_issues:raise ValueError('这节讲解的图尚未保存：'+'；'.join(asset_issues))
    receipt=bridge.apply(out/'lesson-checked.json',out)
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

def summarize_daily(folder, summary, executable):
    """让干净的汇报会话只读统计结果，实际用量单独登记。"""
    role = WRITER_ROLE.set('coordinator')
    try:
        text, usage, _ = invoke_writer(folder,json.dumps(summary,ensure_ascii=False),[],executable,
                                        system_override=DAILY_QA_SUMMARY,allow_image_generation=False,research=False)
    finally:
        WRITER_ROLE.reset(role)
    (Path(folder)/'reply.md').write_text(text,'utf8')
    return usage


def daily(bridge,out,include_answered=False,reader_context=None):
    """本地程序启动并等待原任务，完成后再调用一次模型汇报。"""
    out = Path(out).resolve()
    began = stamp()
    clock = time.perf_counter()
    try:
        report = execute(bridge,out,include_answered,reader_context,observe_caller=False)
    except RuntimeError:
        saved = out/'measured-run.json'
        if not saved.is_file():
            raise
        report = json.loads(saved.read_text('utf8'))
        if not report.get('issues'):
            raise
    usage = summarize_daily(out/'coordinator',compact_result(report),codex_executable())
    elapsed = round(time.perf_counter()-clock,3)
    caller = {**usage,'scope':'entire_codex_turn','state':'complete','started_at':began,'completed_at':stamp(),
              'total_seconds':elapsed,'launch_to_final_seconds':elapsed,
              'scope_note':'读取、派发、等待和写回由本地程序完成，模型用量来自结束后读取统计和汇报的实际请求。'}
    bridge.run_id = report['run']['id']
    bridge.request('/api/runs/'+bridge.run_id+'/events',{'type':'caller','caller':caller})
    run = bridge.request('/api/runs/'+bridge.run_id)
    result = {'run_id':bridge.run_id,'boundary':'从启动本地调度到干净会话汇报结束，包含准备、写作、检索、写回和等待。',
              'started_at':began,'completed_at':stamp(),'launch_to_final_seconds':elapsed,'caller':caller,'run':run,
              'complete_usage':run['complete_usage'],'complete_token_cost_usd':run['complete_estimated_token_cost_usd'],
              'complete_cost_with_recorded_search_usd':run.get('complete_estimated_total_cost_usd'),
              'external_tool_cost_usd':run.get('estimated_external_tool_cost_usd'),'actual_subscription_charge':None}
    save(out/'daily-complete.json',result)
    return {'run_id':bridge.run_id,'question_count':len(run['question_ids']),'seconds':elapsed,'usage':result['complete_usage'],
            'estimated_token_cost_usd':result['complete_token_cost_usd'],'estimated_total_cost_usd':result['complete_cost_with_recorded_search_usd'],
            'detail_file':str(out/'daily-complete.json')}
