from reader_writer_profiles import writer_profile,writer_label,public_profiles
"""整理当前项目的教学任务，累加保存术语与练习，写回后等待用户。"""
import copy,re,uuid
from reader_contract import canonical_term,writing_guide
from reader_learning import learning_payload
from reader_completion import completion_payload
from reader_terms import revise_term
from reader_term_names import term_name,term_key
from reader_coverage import translation_ready
from reader_quality import LESSON_PART_CONTRACT
from reader_translation import TRANSLATION_RULES

TEACHING_RULES=[__import__('reader_prompts').CONNECTED_METHOD,'讲解和回答统一采用本轮 writing.instructions，保留完整逻辑与自然记忆线索，不额外增加记忆小节或阅读步骤',
    '用户在 Codex 对话中手动触发任务；只完成本次指定的章节或待答问题，不自行讲下一节',
    '一次最多新增一个 X. 顶层主题章，可同时生成它下面多个 X.X 及更深单元，也可逐个新增；X. 不是整个项目的总容器，不把其他独立章节降为 X.X 来绕过限制。未讲目录通过 learning/outline/edit 按原 id 增删改或移动，已讲单元只能调整或重命名，不能删除或移除讲解归属；正文只生成当前任务章节。已有目录继续沿用，不自动重建或重排；网页登录、分享、评论与页尾通过 learning/exclusions 明确排除，保留原件，不用删除原文凑覆盖率',
    '先查看 translation_queue，连续读取当前阅读范围的上下文，再把所有非中文自然语言翻译成中文；用 /api/translations/groups 提交完整中文句组并保留原编号，不把 PDF 换行当成断句。图片先看原图，再用 /api/translations/visuals 复用或补译标签；OCR 为空不能直接算无文字',
    '保留标题、段落、列表、加粗、表格、图片和句级对应；公式、代码本体、路径和标识符保持原样，非中文注释及说明需要翻译',
    '当前范围翻译完成即可开始阅读和讲解，不等待整个项目翻译完成；中文原件直接阅读',
    '本节讲解、需要的新术语和练习在同一次写作中输出；每个讲解 part 至少一题，placement 逐个覆盖全部 part。练习遵守 practice_policy，默认选择题，需要计算、排序或代码执行时使用已有客观判分类型，不把整组变成自评填空题；术语复用已有编号，定义与直观解释共两段；练习栏只汇总这些同编号题目，不另外生成',
    '首次阅读疑问由用户提出，不预测或编造用户问题；一次读取项目问题池，问题来自译文、讲解和多轮追问，页面筛选不影响待答池',
    '术语和练习按轮次累加，不清空旧条目，不重置旧题答案；回答及笔记保留原编号',
    '通过 /api/teaching/result 写回讲解、术语和练习，继续处理当前章节的提问；不添加额外的确认步骤，不自动切章或标记已读',
    '开始讲解先读取 /api/work?mode=lesson，保留 lesson_task_id 并在写回时原样提交；页面换章不改变这份稿件的归属。409 lesson_selection_changed 是章节状态冲突，保留已有稿件，不重新生成、不要求重新配对；401 或项目权限 403 才按授权问题处理',
    '只有 current_task.completion.can_advance 为 true，才能用 /api/learning/select 进入下一节；唯一信号是用户在页面勾选完成，聊天中的继续指令不能代替',
    '讲解和问答分别遵守 writer_profiles 公布的模型与档位，普通服务最多两组并行；同次返回 plan、带稳定编号和小标题的讲解 parts、术语和声明 placement 的练习；不加审稿 Agent 或循环修复；网页保存问题不调用模型',
]

def translation_queue(material,context):
    current=set(context.get('passage_ids',[]));files={};pending=[];visuals=[];excluded=set(material.get('learning_excluded_source_ids',[]))
    for p in material['passages']:
        if p['id']in excluded:continue
        if p.get('content_kind')!='explanation' and p.get('kind') in ('image','visual','page_image','figure','scan'):
            if not translation_ready(p):
                fid=p.get('file_id','primary');row={'source_id':p['id'],'file_id':fid,'kind':p['kind'],'current':p['id'] in current,'src':p.get('src',''),'refs':p.get('refs',[])}
                visuals.append(row);files.setdefault(fid,[]).append(p['id'])
            continue
        if p.get('content_kind')=='explanation' or p.get('explanation_id') or p.get('translatable') is False or p.get('heading') and not (p.get('en') or '').strip():continue
        if p.get('duplicate_of') or p.get('role') in ('page_furniture','whitespace') or p.get('display_role') in ('figure_label','decoration'):continue
        original=p.get('en') or ''
        # 删除代码、公式和网址后，检查非中文的自然语言；不要把数学符号当成待译正文。
        prose=re.sub(r'```[\s\S]*?```|`[^`]*`|\$\$[\s\S]*?\$\$|\$[^$\n]+\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|https?://\S+','',original)
        letters=[c for c in prose if c.isalpha()];han=sum('\u3400'<=c<='\u9fff' for c in letters)
        foreign=any(c.isalpha() and not '\u3400'<=c<='\u9fff' for c in prose)
        kana=bool(re.search('[\u3040-\u30ff\uac00-\ud7af]',prose))
        translated=p.get('zh','').strip()
        if not foreign or (han and han/max(1,len(letters))>.6 and not kana) or translation_ready(p):continue
        fid=p.get('file_id','primary');row={'source_id':p['id'],'file_id':fid,'kind':p.get('kind'),'current':p['id'] in current}
        pending.append(row);files.setdefault(fid,[]).append(p['id'])
    return {'pending_count':len(pending)+len(visuals),'current_source_ids':[p['source_id'] for p in pending+visuals if p['current']],
            'units':pending,'visual_units':visuals,'files':[{'file_id':fid,'source_ids':ids,'read_url':'/api/translations?file_id='+fid} for fid,ids in files.items()],
            'instruction':TRANSLATION_RULES}

def teaching_context(material,stored,state):
    plan=learning_payload(material,stored,state)
    completion=completion_payload(material,stored,state)
    pending=[{'file_id':row['id'],'name':row.get('name'),'status':row.get('import_status'),'pending':row.get('pending',[])} for row in stored.get('files',[]) if row.get('pending') or row.get('import_status')=='needs_browser_snapshot']
    return {'ingestion_queue':{'files':pending,'instruction':'原件已保留；按每项 pending.action 补充浏览器快照、可读取的正文或图片说明，再继续翻译；不要把解析缺失冒充已经完成'},'current_task':{'completion':completion,'learning_node_id':plan['selected_id'],'state':state.get('teaching_state',{}).get('state','ready'),
                            'next_action':'may_advance' if completion['can_advance'] else 'handle_current_section'},
            'translation_queue':translation_queue(material,plan['context']),'instructions':TEACHING_RULES}

def recent_reader_questions(state,limit=4,char_budget=2400):
    """只带最近的真实提问，不携带整段回答或聊天历史，不额外调用模型。"""
    rows=[];seen=set();size=0
    for round_ in reversed(state.get('rounds',[])):
        for question in reversed(round_.get('questions',[])):
            text=question.get('text','').strip();identity=question.get('id')
            if not identity or identity in seen or not text or question.get('deleted_at') or not question.get('answer'):continue
            seen.add(identity)
            if size+len(text)>char_budget:continue
            rows.append({'question_id':identity,'learning_node_id':question.get('learning_node_id'),'text':text})
            size+=len(text)
            if len(rows)>=limit:return list(reversed(rows))
    return list(reversed(rows))

def lesson_task(material,stored,state,node_id=None):
    plan=learning_payload(material,stored,state);identity=node_id or plan['selected_id']
    node=next((n for n in plan['nodes'] if n['id']==identity),None)
    if not node:raise ValueError('请先建立学习目录并选择要讲解的章节')
    if identity!=plan['selected_id']:raise ValueError('先按用户指令选择当前章节，再取得本节讲解任务')
    ids=set(node['passage_ids'])|set(node.get('source_range',{}).get('passage_ids',[]))|{l['passage_id'] for l in node['effective_source_links']}
    task={'material_id':material['id'],'mode':'lesson','learning_node':node,'reading_context':__import__('reader_sequence').reading_context(plan['nodes'],identity,material),**teaching_context(material,stored,state),
            'sources':[{'id':p['id'],'file_id':p.get('file_id','primary'),'original':p.get('en'),'translation':'\n\n'.join(s['text'] for s in p.get('translation_segments',[])) or p.get('zh'),'kind':p.get('kind'),'content_kind':p.get('content_kind','source'),'media_items':__import__('reader_media').source_media(material,p),'src':p.get('src'),'refs':p.get('refs',[]),'page_ids':list(dict.fromkeys(r['page_id'] for r in p.get('refs',[]) if r.get('page_id'))),'source_links':p.get('source_links',[])} for p in material['passages'] if p['id'] in ids and p.get('content_kind')!='explanation' and not p.get('explanation_id') and not __import__('reader_generation').blank_source(p)],
            'registered_terms':__import__('reader_work').related_terms(state['terms'],[],[p for p in material['passages']if p['id']in ids]),'writing':writing_guide('lesson'),'writer':writer_profile('lesson'),
            'translation_writeback':{'text':'先 POST /api/translations/groups，reading_groups 中一份连贯译文完整对应 expected_source_ids','images':'先 POST /api/translations/visuals，逐图核对标签，保留真实原图指纹和定位','legacy':'下方 translations 是旧接口兼容格式，新生成优先使用上述句组接口'},
            'result_contract':{'framework_version':__import__('reader_generation').VERSION,'plan':__import__('reader_generation').PLAN_CONTRACT,'request_id':'本次任务的唯一编号','learning_node_id':identity,'translations':[{'file_id':'原文文件编号','translations':[{'source_id':'本章每个待译原文单元的编号，逐项覆盖，不可遗漏','text':'完整中文译文，保留原有结构'}]}],'explanation':{'id':'稳定讲解编号','title':node['title'],'framing':__import__('reader_sequence').FRAMING_CONTRACT,'parts':[LESSON_PART_CONTRACT]},
                               'terms':[{'term_id':'复用已有编号；明确改善时 action=revise 并传 expected_version','name':'中文全称（English Full Name）','professional_definition':'定义','intuitive_explanation':'直观解释'}],
                               'quizzes':[{'placement':__import__('reader_generation').PLACEMENT_CONTRACT['lesson'],'prompt':'选择题','options':['选项一','选项二','选项三','选项四'],'correct_index':0,'explanation':'正确答案为什么成立','option_explanations':['与原始选项逐项对应，说明每个错误选项具体错在哪里']}]}}

    task['writing_version']=__import__('reader_prompts').WRITING_VERSION
    task['practice_policy']=__import__('reader_practice_policy').practice_policy()
    task['required_source_ids']=[s['id']for s in task['sources']if not __import__('reader_generation').blank_source(s)]
    task['source_groups']=__import__('reader_generation').source_groups(task['sources'])
    task['source_mapping_rule']='source_groups逐组落实到本次正文；plan.coverage登记unit_id、part_id和实际正文短摘。来源编号由脚本恢复，不复制原文清单。source_spans可声明连续范围，外部网页使用URL。'
    task['required_facts']=__import__('reader_lesson_checks').requirements(task['sources'])
    task['result_contract']['explanation']['evidence']=__import__('reader_lesson_checks').EVIDENCE_CONTRACT
    task['result_contract']['writing_version']=__import__('reader_prompts').WRITING_VERSION
    task['recent_reader_questions']=recent_reader_questions(state)
    return task

def append_assets(state,data,node_id,explanation_id,passage_ids,stamp):
    issues=[];saved={'terms':[],'quizzes':[]};known_q={q['id'] for r in state['rounds'] for q in r['questions']}
    for field in ('terms','quizzes'):
        rows=data.get(field)
        if not isinstance(rows,list):issues.append(field+' 未按列表提交');continue
        if field=='quizzes' and not rows:issues.append('本次讲解尚未提交练习')
        for row in rows:
            try:
                if not isinstance(row,dict):raise ValueError('条目需要使用对象')
                qids=row.get('question_ids',[])
                if not isinstance(qids,list) or any(q not in known_q for q in qids):raise ValueError('关联的问题不属于当前项目')
                if field=='terms':
                    name=term_name(row)['name'];old=next((t for t in state['terms'] if t['id']==row.get('term_id') or name and term_key(t['name'])==term_key(name)),None)
                    if row.get('term_id') and old is None:
                        if name and (row.get('professional_definition')or row.get('definition'))and row.get('intuitive_explanation'):
                            row={**row,'model_proposed_term_id':row['term_id']};row.pop('term_id')
                        else:raise ValueError('要复用的术语不存在，请补充真实编号或完整的新术语内容')
                    __import__('reader_term_quality').validate_asset_terms([row],state['terms'])
                    if old is None:
                        definition=str(row.get('professional_definition') or row.get('definition') or '').strip();intuition=str(row.get('intuitive_explanation') or '').strip()
                        if not name or not definition or not intuition:raise ValueError('新术语需要名称、定义和直观解释')
                        old={'id':'t-'+uuid.uuid4().hex[:16],'name':name,'definition':definition,'professional_definition':definition,'intuitive_explanation':intuition,'question_ids':[]};state['terms'].append(old)
                    for key in ('case_exceptions','english_case_exception','official_spelling','case_source'):
                        if key in row:old[key]=copy.deepcopy(row[key])
                    revise_term(old,row,stamp)
                    old['question_ids']=list(dict.fromkeys(old.get('question_ids',[])+qids));collection=old
                else:
                    row=__import__('reader_contract').normalize_quiz(row)
                    prompt=row.get('prompt');options=row.get('options');correct=row.get('correct_index');explanation=row.get('explanation')
                    if not isinstance(prompt,str) or not prompt.strip() or not isinstance(explanation,str) or not explanation.strip():raise ValueError('练习需要题目和解析')
                    if not isinstance(options,list) or not 2<=len(options)<=6 or any(not isinstance(o,str) or not o.strip() for o in options) or isinstance(correct,bool) or not isinstance(correct,int) or not 0<=correct<len(options):raise ValueError('练习选项或正确答案格式不正确')
                    collection=next((q for q in state['quizzes'] if q.get('prompt')==prompt.strip() and q.get('options')==options and q.get('correct_index')==correct and (node_id in q.get('learning_node_ids',[]) or explanation_id in q.get('explanation_ids',[])) and (not row.get('placement')or q.get('placement',{}).get('after_part_id')==row['placement'].get('after_part_id'))),None)
                    if collection is None:
                        collection={'id':'z-'+uuid.uuid4().hex[:16],'round_id':None,'prompt':prompt.strip(),'options':options,'correct_index':correct,'explanation':explanation.strip(),'option_explanations':__import__('reader_contract').option_explanations(row),'question_ids':qids,'selected':None,'created_at':stamp(),**__import__('reader_contract').quiz_fields(row)};state['quizzes'].append(collection)
                    else:collection['question_ids']=list(dict.fromkeys(collection.get('question_ids',[])+qids))
                for key,value in [('learning_node_ids',node_id),('explanation_ids',explanation_id)]:collection[key]=list(dict.fromkeys(collection.get(key,[])+[value]))
                if passage_ids:collection.setdefault('passage_id',passage_ids[0])
                if field=='quizzes' and isinstance(row.get('placement'),dict):
                    position=copy.deepcopy(row['placement'])
                    if position.get('kind')=='lesson':
                        bid=position.get('after_part_id');pid=explanation_id+'--'+str(bid)
                        if pid not in passage_ids:raise ValueError('练习插入位置不属于本节讲解')
                        position.update(explanation_id=explanation_id,after_passage_id=pid)
                    collection['placement']=position;collection['framework_version']=__import__('reader_generation').VERSION
                saved[field].append(collection['id'])
            except (ValueError,TypeError) as error:issues.append(field+'：'+str(error))
    return {'saved':saved,'issues':issues,'complete':not issues}

def wait_for_user(state,node_id,stamp,request_id=None):
    state['teaching_state']={'state':'awaiting_completion','learning_node_id':node_id,'updated_at':stamp(),'request_id':request_id,
                             'next_action':'handle_current_section'}
