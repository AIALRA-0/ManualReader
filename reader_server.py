"""在现有读写接口上增加材料隔离、定位历史和翻译覆盖校验。"""
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,unquote
from http.server import ThreadingHTTPServer
import base64,copy,json,mimetypes,os,re,sqlite3,threading,uuid,socket,time
import server as core
from reader_contract import normalize_question,prose_display,writing_guide,canonical_term
from source_parser import parse,apply_translations
from reader_features import settings,save_settings,default_templates,reading_progress,samples,sample_stats
from reader_work import work_packet,change_answer,compact_packet,group_packet
from reader_runs import Runs
from reader_questions import attach_source,create_batch,change_question,decorate_question,save_drafts,sync_questions,infer_learning_node
from reader_completion import completion_payload,record_completion
from reader_coverage import coverage_payload,validate_source_mapping
from reader_terms import revise_term
from reader_content import lecture_material
from reader_speech import proxy as speech_proxy
from reader_media import ensure_result_media
from reader_questions import source_for,answer_body
import reader_library
from reader_workspace import Workspace, RULES as ASSOCIATION_RULES
from reader_remote import RemoteMixin,public_base
from reader_learning import learning_payload,validate_outline
from reader_term_names import analyze_name,term_name,term_key,normalize_state,display_state
from reader_teaching import TEACHING_RULES,teaching_context,lesson_task,append_assets,wait_for_user,translation_queue

ROOT=core.ROOT;DEFAULT=core.MATERIAL['id'];CONTEXT=threading.local();CACHE={};PROJECT_CACHE={}
DEFAULT_MATERIAL=Path(os.environ.get('MANUAL_READER_MATERIAL',str(ROOT/'material.json')))
STATIC=Path(os.environ.get('MANUAL_READER_STATIC',str(ROOT/'static')))
LIBRARY=Path(os.environ.get('MANUAL_READER_LIBRARY',str(ROOT/'library')))
SETTINGS=Path(os.environ.get('MANUAL_READER_SETTINGS',str(ROOT/'reader-settings.json')))
def mid():return getattr(CONTEXT,'material_id',DEFAULT)
def mpath(identity=None):
    identity=identity or mid()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,90}',identity):raise ValueError('材料编号无效')
    return DEFAULT_MATERIAL if identity==DEFAULT else LIBRARY/identity/'material.json'
def raw_material(identity=None):
    identity=identity or mid();path=mpath(identity)
    if not path.exists():raise ValueError('没有找到这份材料')
    version=path.stat().st_mtime_ns
    with core.LOCK:
        if identity not in CACHE or CACHE[identity][0]!=version:CACHE[identity]=(version,json.loads(path.read_text('utf8')))
        return CACHE[identity][1]
def workspace(identity=None):return Workspace(mpath(identity).parent)
def material_revision(identity=None):return max(mpath(identity).stat().st_mtime_ns,workspace(identity).revision())
def material(identity=None):
    identity=identity or mid();version=material_revision(identity)
    with core.LOCK:
        if identity not in PROJECT_CACHE or PROJECT_CACHE[identity][0]!=version:
            PROJECT_CACHE[identity]=(version,workspace(identity).project(raw_material(identity)))
        return PROJECT_CACHE[identity][1]
def db():
    target=core.DB if mid()==DEFAULT else LIBRARY/mid()/'state.sqlite3'
    conn=sqlite3.connect(target,timeout=15);conn.execute('PRAGMA journal_mode=WAL');conn.execute('CREATE TABLE IF NOT EXISTS reader_state (id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL)');return conn
def default_focus():
    m=material()
    if not m['passages']:return {'passage_id':None,'sentence_id':None,'revision':1}
    if m.get('start_passage_id') in {p['id'] for p in m['passages']}:return {'passage_id':m['start_passage_id'],'sentence_id':None,'revision':1}
    pid=next((p['id'] for p in m['passages'] if m.get('reader_mode')=='authored' and not p.get('heading')),None)
    if pid:return {'passage_id':pid,'sentence_id':None,'revision':1}
    pid=next((x['passage_id'] for x in m['outline'] if x.get('title')=='摘要'),m['passages'][0]['id'])
    return {'passage_id':pid,'sentence_id':None,'revision':1}
def locate(pid,sid=None):
    p=next((p for p in material()['passages'] if p['id']==pid),None)
    if not p:raise ValueError('没有找到这个段落')
    if sid and not any(s['id']==sid for s in p['sentences']):raise ValueError('句子不属于所选段落')
    return p
def round_context(state,rid):
    m=material();r=core.find_round(state,rid);p=locate(r['passage_id']);allq={q['id']:q for row in state['rounds'] for q in row['questions']};items=[]
    for q in r['questions']:
        s=next((s for s in p['sentences'] if s['id']==q.get('sentence_id')),None);refs=(s or {}).get('refs') or p['refs'];pages=[]
        for ref in refs:
            page=m['pages'].get(ref.get('page_id'),{})
            if page.get('image'):pages.append({'url':f'{public_base()}/page/{Path(page["image"]).name}?material={mid()}','bbox':ref.get('bbox')})
        items.append({**decorate_question(m,q),'source':{'original':q.get('target',{}).get('original',(s or {}).get('en','')),'translation':q.get('target',{}).get('translation',(s or {}).get('zh') or p['zh']),'paragraph_original':p.get('aligned_original') or p['en'],'precision':(s or {}).get('precision') or p['precision'],'refs':refs,'page_images':pages},'previous_question':allq.get(q.get('parent_question_id'))})
    return {'material_id':mid(),'passage':p,'round':{**r,'questions':items},'terms':state['terms'],'writing':writing_guide()}
core.db=db;core.default_focus=default_focus;core.locate=locate;core.round_context=round_context
BASE_SAVE_RESULT=core.save_result
def save_result(state,rid,data):
    prepared=copy.deepcopy(data);original_names=[]
    ensure_result_media(material(),state,prepared)
    for answer in prepared.get('answers',[]):
        answer.update(validate_source_mapping(material(),answer))
        question=next((q for r in state['rounds'] for q in r['questions'] if q['id']==answer.get('question_id')),None)
        if question:
            source=source_for(material(),question)
            answer['text']='原文：\n\n'+source['text']+'\n\n回答：\n\n'+answer_body(answer.get('text',''),source)
    for term in prepared.get('terms',[]):
        if not term.get('definition') and term.get('professional_definition'):term['definition']=term['professional_definition']
        raw=term.get('name','');name=term_name(term)['name'];term['name']=name
        existing=next((t for t in state['terms'] if name and term_key(t['name'])==term_key(name)),None)
        if existing:term['term_id']=existing['id']
        original_names.append((name,raw))
    result=BASE_SAVE_RESULT(state,rid,prepared)
    for answer in prepared.get('answers',[]):
        question=next(q for q in result['questions'] if q['id']==answer['question_id']);question.pop('answer_deleted_at',None)
        for key in ('explained_source_ids','source_ranges'):
            if key in answer:question[key]=answer[key]
        question.setdefault('learning_node_id',infer_learning_node(material(),question))
    for term in prepared.get('terms',[]):
        item=next((t for t in state['terms'] if t['id']==term.get('term_id') or t['name']==term.get('name')),None)
        if item:
            for key in ('case_exceptions','english_case_exception','official_spelling','case_source'):
                if key in term:item[key]=copy.deepcopy(term[key])
            revise_term(item,term,core.stamp)
            questions={q['id']:q for row in state['rounds'] for q in row['questions']}
            owners=[infer_learning_node(material(),questions[qid]) for qid in term.get('question_ids',[]) if qid in questions]
            item['learning_node_ids']=list(dict.fromkeys(item.get('learning_node_ids',[])+[node for node in owners if node]))
            aliases=term.get('aliases',[])
            if not isinstance(aliases,list) or any(not isinstance(x,str) or len(x)>500 for x in aliases):raise ValueError('术语别名必须是文字列表')
            item['aliases']=list(dict.fromkeys(item.get('aliases',[])+[x.strip() for x in aliases if x.strip()]))
            item.setdefault('professional_definition',item['definition'])
            if term.get('intuitive_explanation') and not item.get('intuitive_explanation'):item['intuitive_explanation']=core.text(term['intuitive_explanation'],'直观解释',12000)
            item['needs_intuitive_explanation']=not bool(item.get('intuitive_explanation'))
    for name,raw in original_names:
        item=next((t for t in state['terms'] if canonical_term(t['name']).casefold()==name.casefold()),None)
        if item and raw and raw!=name and raw not in item.setdefault('source_names',[]):item['source_names'].append(raw)
    node=learning_payload(material(),workspace().read(),state)['selected_id']
    if node:wait_for_user(state,node,core.stamp)
    return result
core.save_result=save_result

def library_sources():
    identities=[DEFAULT]+[p.parent.name for p in LIBRARY.glob('*/material.json')]
    return [{'id':identity,'title':material(identity)['title'],'kind':material(identity).get('kind','pdf'),'reader_mode':material(identity).get('reader_mode'),'passages':len(material(identity)['passages']),'translation_status':material(identity).get('translation_status','ready' if identity==DEFAULT else 'pending')} for identity in identities]

def library_tree():return reader_library.catalog(LIBRARY/'library-index.json',library_sources())

def library():return [{**row,'title':row['name']} for row in library_tree()['materials'] if not row.get('deleted') and not row.get('archived')]

def stored_state(identity):
    target=core.DB if identity==DEFAULT else LIBRARY/identity/'state.sqlite3'
    if not target.exists():return {'rounds':[],'terms':[],'quizzes':[],'notes':{},'read_passages':{}}
    with sqlite3.connect(target.resolve().as_uri()+'?mode=ro',uri=True) as conn:
        row=conn.execute('SELECT value FROM reader_state WHERE id=1').fetchone()
    return json.loads(row[0]) if row else {'rounds':[]}

def sample_records(scope,with_revisions=False):
    identities=[item['id'] for item in library()] if scope=='all' else [mid()]
    records=[];revisions={}
    for identity in identities:
        state=stored_state(identity);records.extend(samples(material(identity),state));revisions[identity]=state.get('revision',0)
    return (records,revisions) if with_revisions else records

def progress_payload(progress=None):
    progress,_=display_state(core.read_state() if progress is None else progress)
    from reader_contract import quiz_display_orders,quiz_quality_flags
    orders=quiz_display_orders(progress.get('quizzes',[]))
    for quiz in progress.get('quizzes',[]):quiz.update(display_order=orders.get(quiz['id'],list(range(len(quiz['options'])))),quality_flags=quiz_quality_flags(quiz))
    for row in progress['rounds']:row['questions']=[decorate_question(material(),q) for q in row['questions']]
    return {**progress,'learning':learning_payload(material(),workspace().read(),progress),'material_revision':material_revision(),
            'completion':completion_payload(material(),workspace().read(),progress),
            'settings_revision':SETTINGS.stat().st_mtime_ns if SETTINGS.exists() else 0,
            'reading_progress':reading_progress(material(),progress)}

def statistics_payload(scope,include_records=False):
    entries=library() if scope=='all' else [{'id':mid(),'title':material()['title']}]
    records=[];revisions={};rows=[]
    for entry in entries:
        identity=entry['id'];document=material(identity);state=stored_state(identity)
        samples_for_document=samples(document,state);records.extend(samples_for_document)
        revisions[identity]=[state.get('revision',0),mpath(identity).stat().st_mtime_ns,entry['title']]
        reading=reading_progress(document,state)['overall'];active_questions={q['id'] for r in state.get('rounds',[]) for q in r['questions'] if not q.get('deleted_at')};quizzes=[q for q in state.get('quizzes',[]) if q.get('learning_node_ids') or any(identity in active_questions for identity in q.get('question_ids',[]))]
        answered=[q for q in quizzes if q.get('selected') is not None]
        rows.append({'id':identity,'title':entry['title'],'reading':reading,
                     'practice':{'done':len(answered),'total':len(quizzes),'correct':sum(q['selected']==q['correct_index'] for q in answered)},
                     'questions':len(samples_for_document),'answered':sum(bool(q.get('answer')) for q in samples_for_document),
                     'terms':len(state.get('terms',[])),'notes':sum(bool(v) for v in state.get('notes',{}).values())})
    def overall(key):
        result={field:sum(row[key].get(field,0) for row in rows) for field in ('done','total')}
        result['percent']=round(result['done']/result['total']*100,1) if result['total'] else 0
        return result
    result={**sample_stats(records,{'overall':overall('reading')}),'data_revisions':revisions,
            'practice':{**overall('practice'),'correct':sum(row['practice']['correct'] for row in rows)},
            'per_material':rows,'terms':sum(row['terms'] for row in rows),'notes':sum(row['notes'] for row in rows)}
    if include_records:result['records']=records
    return result

def agent_guide():
    guide={'material_id':mid(),'description':'用户在任意 Codex 对话中手动触发，阅读器只提供读取和写回接口',
      'preferred_writer':{'model':'gpt-6.1-terra','effort':'high','max_concurrent_groups':2},
      'association_contract_version':2,
      'writing':writing_guide(),
      'steps':['每次接手先读取 /api/content 与 /api/learning，确认材料编号、主学习章节、原文范围和已有讲解；原文件目录通过 /api/index 查阅',
               '需要安排学习路线时，用 /api/learning/outline 保存主学习目录；讲解写入 /api/content/explanations 时提供 learning_node_id，同时保存正文与章节归属',
               '尚无原件的项目可以先建立学习目录并写入讲解；不要把学习目录仅上传为普通 Markdown，也不要用原文 section_id 代替 learning_node_id',
               '从 /api/work 一次取得待回答问题、去重原句、完整原文和已登记术语',
               '按 groups 分组，默认最多两组 Terra high 并行；把共同材料与本组问题直接放入写作消息，不再让写作者读文件；相同原句留在同一组',
               '回答、术语和练习一起输出，不让写作者操作文件或调用写回接口',
               '由脚本逐条保存并写回；只做格式、编号和缺项检查，不增加审稿 Agent 或循环修复',
               '耗时、token 和缓存从真实运行记录取得，由脚本写入本轮 metrics，不让模型猜测用量',
               '只有问题确实需要外部事实时才检索，多个问题共用取得的证据；保留出处',
               '回答或讲解写回时保留用户当前阅读位置；引导换章调用 /api/learning/select，/api/focus 只定位原句或图片'],
      'reading_progress_rules':'父章节按自身与全部子节的可见内容去重汇总已读比例；已读状态只能由用户在阅读页面手动勾选。Agent 和测试不得改写官网进度，机器接口已禁止修改；生成讲解、回答、跳章均不标记已读。交互测试只在隔离副本进行',
      'endpoints':{'index':'GET /api/index','work':'GET /api/work?round={round_id}','progress':'GET /api/progress',
                   'library':'GET /api/library/tree','terms':'POST /api/terms',
                   'passage':'GET /api/passages/{passage_id}','round':'GET /api/rounds/{round_id}','question_batch':'POST /api/question-batches','question_edit':'POST /api/questions/{question_id}',
                   'result':'POST /api/rounds/{round_id}/result','focus':'POST /api/focus',
                   'metrics':'POST /api/rounds/{round_id}/metrics',
                   'learning':'GET /api/learning','learning_outline':'POST /api/learning/outline','learning_select':'POST /api/learning/select','normalize_source':'POST /api/content/normalize','content':'GET /api/content','files':'POST /api/content/files','explanation':'POST /api/content/explanations','presentation':'POST /api/content/presentation','project':'POST /api/projects','lecture':'POST /api/lectures','translation':'POST /api/translations'},
      'term_update_contract':{'updates':[{'term_id':'已登记的术语编号','professional_definition':'准确含义、条件和边界','intuitive_explanation':'用同一个具体对象承接并解释含义'}],'rules':'只更新指定术语，保留原编号、问题和笔记；旧解释保存到历史中，不生成重复的第三段'},
      'learning_contract':{
        'read':'GET /api/learning 返回所有学习节点、当前章节和 context；context 列出本章原文、讲解、旧问答与术语的实际编号，查看其他文件不会替换主目录',
        'outline_request':{'nodes':[{'id':'intro','parent_id':None,'title':'先读懂材料在做什么','source_links':[],'explanation_ids':[]},{'id':'detail','parent_id':'intro','title':'理解其中一个步骤','source_links':[],'explanation_ids':[]}]},
        'explanation_request':{'id':'lesson-detail','learning_node_id':'detail','title':'本节讲解','markdown':'在这里提交用户要求的讲解正文'},
        'optional_fields':{'outline.expected_revision':'使用 GET /api/content 返回的 revision 防止覆盖其他对话的修改','explanation.expected_version':'修改已有讲解时必填；沿用已有 blocks 编号保留问答定位','explanation.source_links':'首次写入可省略，自动继承本章或父章的原文范围；显式提交时使用真实的 file_id、passage_id、sentence_id'},
        'select_request':{'node_id':'detail'},
        'identity_rules':['material_id 表示整个项目，每次请求用 X-Reader-Material 指定；请求体也提供 material_id 时必须一致',
                          'file_id 表示某份原件，section_id 仅表示该原件中的目录位置；learning_node_id 表示独立学习目录中的章节',
                          '译文使用原片段的 source_id，句子使用原句编号；问题使用 passage_id 和 sentence_id，追问使用 parent_question_id',
                          '一个章节可关联多份原文和多段讲解；界面展示本章全部讲解，来源编号由接口校验；尚无讲解的章节标为待讲解',
                          '问题、回答、术语和笔记保留原编号，旧记录不因切换目录或文件而迁移；术语通过 question_ids 复用已有条目']},
      'lecture_contract':{'title':'项目名称','sections':[{'id':'intro','parent_id':None,'title':'项目在做什么','markdown':'讲解正文'},
                         {'id':'entry','parent_id':'intro','title':'请求从哪里进入','markdown':'子章节正文'}],
                         'rules':'顶层从 0 开始，子章节从 1 开始，例如 0、0.1、0.2、1；只用 parent_id 表示层级，先提交父章节和全部子章节，脚本统一生成编号；指定 material_id 可以更新已有讲解，已有原件材料不能被覆盖'},
      'translation_contract':{'source':'保持 source_id 和句级对应，保护公式、代码、网址和数值',
                              'format':'保留标题、段落、加粗和表格，所有需要翻译的编号均有译文才能完成'},
      'content_contract':{'rules':ASSOCIATION_RULES,'read':'GET /api/content 返回文件、讲解、稳定片段及当前布局；GET /api/material 返回可定位的完整内容','files':{'file_id':'稳定文件编号，可省略后自动生成','filename':'项目内文件路径，例如 notes/example.md','content_base64':'文件字节的 Base64'},'explanations':{'id':'稳定讲解编号','title':'讲解标题','markdown':'支持公式、表格和图片的正文','section_id':'可选的原文章节编号；不是学习章节编号','learning_node_id':'可选的学习章节编号；接口会同时登记讲解与章节归属','source_links':[{'file_id':'primary','passage_id':'原文段落编号','sentence_id':'可选句子编号'}],'expected_version':'修改时必须提供上一版编号；blocks 可显式指定片段 ID'},'presentation':{'layout':'parallel / lesson / three / single / custom','columns':[['original','translation'],['explanation']],'file_id':'要打开的原文文件','explanation_id':'要打开的讲解'},'questions':'POST /api/question-batches 一次保存当前整组内容的问题，每项保留 passage_id 和 sentence_id；parent_question_id 指向原问题，追问在原问题下展示；点击句子只定位，不筛掉其他问题；POST /api/questions/{id} 编辑或确认删除，历史、回答与笔记保留','learning':'主学习目录通过 POST /api/learning/outline 写入 nodes；每个节点使用稳定 id、parent_id、title、source_links、explanation_ids 或 legacy_section_ids；脚本生成从 0 开始的层级编号，尚无正文的节点显示待讲解；通过 /api/learning/select 选择当前章节，/api/focus 只定位，不替换学习目录；不要把学习目录仅作为普通 Markdown 文件上传','trigger':'所有模型调用由用户在 Codex 对话手动触发，写入不自动切换用户页面；引导阅读时再提交 presentation 与 focus'},
      'headers':{'X-Reader-Pin':'连接码','X-Reader-Material':mid()}}
    guide['steps']=TEACHING_RULES+guide['steps']
    guide['steps']=[s for s in guide['steps'] if not s.startswith('回答或讲解写回时保留用户当前阅读位置；引导换章')]
    guide['endpoints']['teaching_result']='POST /api/teaching/result'
    guide['endpoints']['lesson_task']='GET /api/work?mode=lesson'
    guide['learning_contract']['select_request']={'node_id':'下一章节编号'}
    guide['learning_contract']['advance_rule']='只有 progress.completion.can_advance 为 true 时 Agent 才能换章；这个值只由用户手动完成按钮产生，聊天指令和 Agent 提交不能代替'
    guide['endpoints'].update(coverage='GET /api/coverage',import_source='POST /api/content/import',question_sync='POST /api/questions/sync')
    guide['coverage_contract']={'explained_source_ids':'实际解释过的原文段落、句子或对象编号','source_ranges':'或提交 passage_id、可选 sentence_id、start、end；区间使用原文 Unicode 字符位置，左闭右开','rule':'source_links 只表示引用；未明确解释的引用不算讲解覆盖。覆盖率和进度由脚本计算，不由 Agent 填写'}
    guide['term_update_contract']['rules']='术语保留独立定义和直观解释两段；优先复用正文里已经讲清的解释。同名复用不重复生成；需要改善旧解释时提交 action=revise、expected_version，保留历史和已有编号'
    guide['term_name_contract']={'audit':'GET /api/terms/audit','normalize':'POST /api/terms/normalize','name_update':{'updates':[{'term_id':'已登记编号','expected_name':'读取到的当前名称','name':'缩写 中文全称（English Full Name）'}]},'case_rules':'未标记例外时，英文全称每个单词首字母大写；专有大小写使用 case_exceptions 文字列表，整条英文保留原样使用 english_case_exception=true；代码、网址和数学记号保持原样','repair_rule':'脚本先整理中英文顺序、重复中文、大小写和同名重复。原编号、原名历史、笔记和定义保留。仍有歧义时，本地客户端只把本轮相关名称及已有解释集中提交一次普通Terra high补齐，不审稿、不循环；未能确认的名称继续保留，已保存回答不受影响'}
    guide['teaching_result_contract']={'request_id':'本次任务唯一编号，重试时保持不变','learning_node_id':'当前章节编号','explanation':{'id':'稳定讲解编号','title':'讲解标题','markdown':'正文；修改已有讲解时提供 expected_version'},'terms':'复用已有术语或提交名称、定义和直观解释的列表','quizzes':'本次内容的选择题列表；可以直接关联本节，不要求先创建用户问题'}
    guide['teaching_result_contract']['translations']=[{'file_id':'原文文件编号','translations':[{'source_id':'本章每个待译原文单元的编号','text':'完整中文译文，保留原有结构'}]}]
    guide['teaching_result_contract']['translation_rule']='本章关联的所有待译原文单元必须逐项完成；接口返回缺项编号时只补译文，再提交已有讲解，不重新生成正文'
    guide.update(teaching_context(material(),workspace().read(),core.read_state()))
    guide['question_pool_rule']='GET /api/work 默认汇总当前项目全部已保存且未回答的问题，包括首次提问及追问；草稿只计数，不作为正式问题处理'
    guide['fast_answer_path']={'start':'POST /api/runs','client_command':'reader_bridge.py daily --out <本轮空目录>','read_group':'GET /api/runs/{run_id}/groups/{group_id}','save':'POST /api/runs/{run_id}/groups/{group_id}/result','statistics':'GET /api/runs','model':'gpt-6.1-terra','effort':'high','service_tier':'default','parallel_groups':2,'rule':'用户手动运行daily客户端；另开干净的Terra high普通服务主调度，读取问题、启动两路同配置写作者、等待并汇报；程序附上原图、逐组保存完整结果，最终计入主调度汇报用量；网页不调用模型，主调度不重抄答案'}
    guide['steps']=[step.replace('每次接手先读取 /api/content 与 /api/learning','首次建立学习路线或导入材料时，按需读取 /api/content 与 /api/learning').replace('从 /api/work 一次取得待回答问题、去重原句、完整原文和已登记术语','回答任务优先用 POST /api/runs 一次准备；旧 /api/work 仅供兼容') for step in guide['steps']]
    guide['steps'].insert(0,'用户要求回答问题时，优先手动运行 reader_bridge.py daily --out <本轮空目录>；另开干净的Terra high普通服务主调度会话，程序预装材料、规则与原图，两个Terra high普通服务写作者并行；主调度最终汇报结束后结算整轮。开发、验收和部署在其他会话进行，单列统计。不要把读取五个大接口作为前置，也不由主对话重抄结果')
    guide['endpoints'].update(runs='GET /api/runs',start_run='POST /api/runs')
    guide['accounting_contract']={'script':'coordinator只表示本地脚本，不能称为完整主调度0 token','codex':'本地客户端读取启动它的Codex整轮日志，包含读取、启动、等待和最终汇报；后台脚本等待task_complete后补齐caller，不调用模型','parent_model_rule':'daily入口另开全新主调度，明确指定gpt-6.1-terra、high与service_tier=default，禁用fast_mode；它与两个写作者均核对实际日志，不继承当前长对话配置；开发对话另外统计，不冒充日常回答成本','complete':'complete_usage和complete_estimated_token_cost_usd只在主对话结束且已采集时返回，未采集留空；同轮开发和其他工作原样计入，不猜测分摊，重叠时间不相加'}
    guide['media_contract']={'rule':'图、表和空间关系优先显示实际原图；显式要求看图时要读取并引用sources.media_items的真实地址，不只描述图名。简单定义不强行配图。流程可直接用Mermaid，新插图由当前Codex使用绘图工具生成后通过content/files写入，禁止引用不存在的文件。','files':'POST /api/content/files','outline':'复杂讲解可在同一次输出附teaching_outline，不另起规划或审稿调用'}
    guide['quiz_contract']={'display_order':'显示位置到原options下标的映射；选择题作答提交原下标，不能提交显示位置，历史选择不因位置均衡而改变','quality_flags':'脚本只标记明显的长度线索，不据此阻塞生成或自动循环修复','revise_unanswered':'POST /api/quizzes/{quiz_id}/options，提交expected_options、options、correct_index；只允许调整未作答题的干扰项，原正确答案、编号和记录保留'}
    guide['client_files']={'reader_prompts.py':'GET /api/agent/reader_prompts.py','reader_media.py':'GET /api/agent/reader_media.py','reader_bridge.py':'GET /api/agent/reader_bridge.py','reader_worker.py':'GET /api/agent/reader_worker.py','reader_execute.py':'GET /api/agent/reader_execute.py','reader_term_names.py':'GET /api/agent/reader_term_names.py','rule':'建立连接或开始新一轮回答时，从当前项目接口下载 client_files 列出的全部当前客户端和规则模块放到同一目录；已有旧脚本也要替换，只更新程序文件，不覆盖任务输入、结果和记录；脚本下载不需要模型阅读其正文。使用已登录的本地Codex，访问令牌只传进程内存或环境变量，不写入任务文件'}
    return guide

class Handler(core.Handler):
    def context(self):
        self._request_started=time.perf_counter();CONTEXT.material_id=self.headers.get('X-Reader-Material') or parse_qs(urlsplit(self.path).query).get('material',[DEFAULT])[0]
    def json(self,data,status=200):
        identity=self.headers.get('X-Reader-Run')
        if identity and re.fullmatch(r'run-[a-f0-9]{20}',identity) and self.authenticated() and urlsplit(self.path).path!='/api/runs/'+identity:
            Runs(mpath().parent).request(identity,self.command,urlsplit(self.path).path,(time.perf_counter()-getattr(self,'_request_started',time.perf_counter()))*1000,len(json.dumps(data,ensure_ascii=False).encode()))
        return super().json(data,status)
    def file(self,path,ctype=None):
        return super().file(path,ctype or mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
    def do_GET(self):
        self.context();path=unquote(urlsplit(self.path).path)
        try:
            if path=='/':return self.file(STATIC/'index.html','text/html; charset=utf-8')
            if path in ('/settings','/statistics'):return self.file(STATIC/(path[1:]+'.html'),'text/html; charset=utf-8')
            if path=='/vendor/math-text.js':return self.file(STATIC/'math-text.js','text/javascript; charset=utf-8')
            if path in ('/workspace-ui.js','/reader-terms.js','/reader-speech.js','/app.js','/reader-ui.js','/reader-markdown.js','/library-ui.js','/remote-ui.js','/markdown-vendor.js','/markdown-vendor.LICENSE.txt','/app.css','/settings.js','/statistics.js'):return self.file(STATIC/path[1:])
            if path.startswith('/evidence/'):
                if os.environ.get('MANUAL_READER_PUBLIC_ORIGIN'):return self.send_error(404)
                folder=(ROOT/'evidence').resolve();target=(ROOT/path[1:]).resolve()
                if not target.is_relative_to(folder) or target.suffix not in ('.json','.txt','.html','.png'):return self.send_error(404)
                return self.file(target)
            file_match=re.fullmatch(r'/content-files/([A-Za-z0-9_-]+)/(original|asset/[^/]+)',path)
            if file_match:
                fid,kind=file_match.groups();row=next((r for r in workspace().read()['files'] if r['id']==fid),None)
                if not row:return self.send_error(404)
                folder=workspace().files/fid
                target=folder/row['stored_name'] if kind=='original' else folder/'assets'/kind.split('/')[-1]
                if not target.resolve().is_relative_to(folder.resolve()):return self.send_error(404)
                return self.file(target)
            if path.startswith('/page/') and mid()!=DEFAULT:
                name=Path(path).name
                if not re.fullmatch(r'page-\d{4}\.(png|text\.json)',name):return self.send_error(404)
                return self.file(LIBRARY/mid()/'assets'/name)
            if path.startswith('/asset/') and mid()!=DEFAULT:
                name=Path(path).name
                if not re.fullmatch(r'media-[a-f0-9]{18}\.(png|jpe?g|webp|gif|svg)',name):return self.send_error(404)
                return self.file(LIBRARY/mid()/'assets'/name)
            if path in ('/original.pdf','/original') and mid()!=DEFAULT:
                filename=material().get('original_file','')
                if not filename:return self.json({'error':'这份材料没有独立原件下载'},404)
                return self.file(LIBRARY/mid()/filename,'application/pdf' if filename.endswith('.pdf') else 'application/octet-stream')
            if path.startswith('/api/') and path!='/api/session':
                if not self.authenticated():return self.json({'error':'连接码不正确'},403)
                m=material()
                if path in ('/api/agent/reader_bridge.py','/api/agent/reader_worker.py','/api/agent/reader_execute.py','/api/agent/reader_term_names.py','/api/agent/reader_prompts.py','/api/agent/reader_media.py'):return self.file(ROOT/path.split('/')[-1],'text/x-python; charset=utf-8')
                if path=='/api/speech/status':return self.json(speech_proxy('/status'))
                if path=='/api/terms/audit':
                    state=core.read_state();report=normalize_state(state,lambda:'audit_only');return self.json(report)
                if path=='/api/runs':return self.json({'runs':Runs(mpath().parent).list()})
                run_match=re.fullmatch(r'/api/runs/(run-[a-f0-9]{20})(?:/(groups/[12]|reference|caller))?',path)
                if run_match:
                    row=Runs(mpath().parent).get(run_match[1]);part=run_match[2]
                    if part=='caller':return self.json(row.get('caller',{'state':'not_collected'}))
                    if part and part.startswith('groups/'):
                        group=row['groups'].get(part.split('/')[1])
                        if not group:raise ValueError('没有这个写作组')
                        return self.json(group_packet(row['packet'],group['question_ids']))
                    if part=='reference':
                        needed={s.get('file_id') for s in row['packet']['sources']};eids={s.get('explanation_id') for s in row['packet']['sources']}
                        return self.json({'material_id':mid(),'passages':[{'id':p['id'],'file_id':p.get('file_id'),'text':p.get('aligned_original') or p.get('en') or p.get('zh','')} for p in m['passages'] if p.get('file_id') in needed or p.get('explanation_id') in eids and p.get('explanation_id')]})
                    return self.json(Runs.public(row))
                if path=='/api/learning':
                    state=core.read_state();return self.json({**learning_payload(m,workspace().read(),state),'completion':completion_payload(m,workspace().read(),state)})
                if path=='/api/coverage':return self.json(coverage_payload(m,workspace().read(),core.read_state()))
                if path=='/api/content':return self.json({'learning':learning_payload(m,workspace().read(),core.read_state()),'material_id':mid(),'revision':m['workspace_revision'],'files':m['reading_files'],'explanations':m['explanations'],'presentation':m['presentation'],'rules':ASSOCIATION_RULES})
                if path=='/api/library':return self.json(library())
                if path=='/api/library/tree':return self.json(library_tree())
                if path=='/api/material':return self.json({**m,'material_revision':material_revision()})
                if path=='/api/progress':
                    progress=core.read_state()
                    query=parse_qs(urlsplit(self.path).query)
                    metadata={'material_revision':material_revision(),'settings_revision':SETTINGS.stat().st_mtime_ns if SETTINGS.exists() else 0}
                    if query.get('since')==[str(progress['revision'])] and all(float(query.get(key,[-1])[0])==float(value) for key,value in metadata.items()):
                        return self.json({'unchanged':True,'revision':progress['revision']})
                    return self.json(progress_payload(progress))
                if path=='/api/settings':return self.json({**settings(SETTINGS),'defaults':default_templates()})
                if path=='/api/work':
                    query=parse_qs(urlsplit(self.path).query)
                    records=core.read_state();stored=workspace().read()
                    if query.get('mode')==['lesson']:return self.json(lesson_task(m,stored,records,query.get('node_id',[None])[0]))
                    packet=work_packet(m,records,query.get('round',[None])[0],query.get('include_answered')==['true'])
                    context=teaching_context(m,stored,records);packet['instructions']+=context.pop('instructions');packet.update(context);packet['question_pool']={'pending':len(packet['questions']),'drafts':sum(bool(v.strip()) for values in records.get('drafts',{}).values() for v in values.values()),'scope':'project'}
                    return self.json(packet)
                if path in ('/api/samples','/api/statistics'):
                    query=parse_qs(urlsplit(self.path).query)
                    if path=='/api/statistics':
                        result=statistics_payload(query.get('scope',['current'])[0],query.get('include_records')==['true'])
                        return self.json(result)
                    records=sample_records(query.get('scope',['current'])[0])
                    if query.get('format')==['jsonl']:
                        payload=('\n'.join(json.dumps(row,ensure_ascii=False) for row in records)+'\n').encode('utf8');self.send_response(200);self.send_header('Content-Type','application/x-ndjson; charset=utf-8');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);return
                    return self.json({'records':records})
                if path=='/api/index':return self.json({'id':mid(),'title':m['title'],'outline':m['outline'],'passages':[{'id':p['id'],'section_id':p['section_id'],'preview':(p.get('zh') or p.get('en',''))[:100]} for p in m['passages']]})
                if path.startswith('/api/passages/'):return self.json(locate(path.split('/')[3]))
                if path.startswith('/api/rounds/'):return self.json(round_context(core.read_state(),path.split('/')[3]))
                if path=='/api/translations':
                    fid=parse_qs(urlsplit(self.path).query).get('file_id',['primary'])[0]
                    selected=[p for p in m['passages'] if p.get('file_id')==fid and p.get('content_kind')!='explanation' and p.get('translatable',True)]
                    return self.json({'material_id':mid(),'file_id':fid,'coverage':m.get('coverage',{}),'units':[{'source_id':p['id'],'original':p['en'],'translation':p['zh'],'kind':p.get('kind'),'sentences':p['sentences']} for p in selected]})
                if path=='/api/guide':return self.json(agent_guide())
            return super().do_GET()
        except (ValueError,KeyError,OSError) as error:return self.json({'error':str(error)},400)
    def do_POST(self):
        self.context();path=unquote(urlsplit(self.path).path)
        custom=path in ('/api/speech/transcribe','/api/content/import','/api/questions/sync','/api/teaching/result','/api/learning/outline','/api/learning/select','/api/content/normalize','/api/draft-batches','/api/question-batches','/api/projects','/api/content/files','/api/content/explanations','/api/content/presentation','/api/terms','/api/terms/normalize','/api/folders','/api/library/clear-trash','/api/read','/api/settings','/api/library/import','/api/lectures','/api/translations','/api/focus','/api/history/back','/api/history/forward') or re.fullmatch(r'/api/rounds/[^/]+/(status|metrics)',path) or re.fullmatch(r'/api/questions/[^/]+/answer',path) or re.fullmatch(r'/api/library/(folder|material)/[A-Za-z0-9_-]+/(rename|move|trash|restore)',path)
        if re.fullmatch(r'/api/quizzes/[^/]+/options',path) or re.fullmatch(r'/api/questions/[^/]+',path) or path=='/api/runs' or re.fullmatch(r'/api/runs/run-[a-f0-9]{20}/(?:events|groups/[12]/result)',path):custom=True
        if not custom and path!='/api/rounds':return super().do_POST()
        if not self.safe_origin() or not self.authenticated():return self.json({'error':'连接码或提交来源不正确'},403)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length>64_000_000:raise ValueError('上传内容超过 64 MB')
            data=json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(data,dict):raise ValueError('提交内容必须是对象')
            if path=='/api/speech/transcribe':return self.json(speech_proxy('/transcribe',data))
            if path=='/api/runs':
                started=time.perf_counter();records=core.read_state();packet=compact_packet(material(),records,data.get('question_ids'),data.get('include_answered') is True);context=teaching_context(material(),workspace().read(),records)
                row=Runs(mpath().parent).create(mid(),packet);manifest={**Runs.public(row),'groups':[{'id':key,'question_ids':group['question_ids'],'input_url':'/api/runs/'+row['id']+'/groups/'+key,'result_url':'/api/runs/'+row['id']+'/groups/'+key+'/result'} for key,group in row['groups'].items()],'reference_url':'/api/runs/'+row['id']+'/reference','translation_queue':context['translation_queue'],'current_task':context['current_task'],'prepare_seconds':round(time.perf_counter()-started,4),'instructions':['这次任务已经准备好，不必先再读 guide/content/learning/progress/work，也不打印整篇材料','一次把各组输入保存到本地文件，立即新建最多两位 Terra high 写作者，fork_turns=none；不要沿用旧写作者的长历史','各组自己按需查资料；完成后直接写 result JSON 文件，主对话不抄写正文，脚本逐组及时保存','只有用户勾选完成才进入下一节；这次写回不改用户位置、笔记或阅读进度']}
                return self.json(manifest)
            run_event=re.fullmatch(r'/api/runs/(run-[a-f0-9]{20})/events',path)
            if run_event:return self.json(Runs.public(Runs(mpath().parent).update(run_event[1],data)))
            run_result=re.fullmatch(r'/api/runs/(run-[a-f0-9]{20})/groups/([12])/result',path)
            if run_result:
                store=Runs(mpath().parent);run=store.get(run_result[1]);group=run['groups'].get(run_result[2])
                if not group:raise ValueError('没有这个写作组')
                answers=data.get('answers',[])
                if not isinstance(answers,list) or any(not isinstance(a,dict) or a.get('question_id') not in group['question_ids'] for a in answers):raise ValueError('回答必须属于当前写作组')
                store.update(run['id'],{'group_id':group['id'],'type':'writeback_started'});saved=[];issues=[];seen_terms=set()
                for answer in answers:
                    try:
                        records=core.read_state();round_=next(r for r in records['rounds'] if any(q['id']==answer['question_id'] for q in r['questions']));ids={q['id'] for q in round_['questions']};payload={'answers':[answer],'terms':[],'quizzes':[]}
                        for field in ('terms','quizzes'):
                            for item in data.get(field,[]):
                                if answer['question_id'] not in item.get('question_ids',[]):continue
                                value={**item,'question_ids':[qid for qid in item['question_ids'] if qid in ids]}
                                if field=='terms':
                                    key=item.get('term_id') or item.get('name')
                                    seen_terms.add(key)
                                payload[field].append(value)
                        core.mutate(lambda state:save_result(state,round_['id'],payload));saved.append(answer['question_id'])
                    except (ValueError,KeyError,StopIteration,TypeError) as error:issues.append({'question_id':answer.get('question_id'),'message':str(error)})
                if set(group['question_ids'])<=set(saved):store.update(run['id'],{'group_id':group['id'],'type':'group_saved'})
                return self.json({'saved':saved,'issues':issues,'missing_question_ids':list(set(group['question_ids'])-set(saved)),'run':Runs.public(store.get(run['id']))})
            if (path.startswith(('/api/content/','/api/learning/')) or path in ('/api/question-batches','/api/draft-batches','/api/translations','/api/focus')) and data.get('material_id') is not None and data['material_id']!=mid():raise ValueError('请求体的 material_id 与当前材料不一致，请核对 X-Reader-Material')
            if path=='/api/teaching/result':
                with core.LOCK:
                    records=core.read_state();node_id=data.get('learning_node_id');plan=learning_payload(material(),workspace().read(),records)
                    if node_id not in {n['id'] for n in plan['nodes']} or node_id!=plan['selected_id']:return self.json({'error':'本次只能写回当前学习章节'},403)
                    request_id=core.text(data.get('request_id'),'任务提交编号',80);prior=records.get('teaching_results',{}).get(request_id)
                    if prior:
                        if prior['learning_node_id']!=node_id:raise ValueError('这个提交编号已经用于其他章节')
                        return self.json(prior)
                    explanation=data.get('explanation')
                    if not isinstance(explanation,dict):raise ValueError('请同时提交本节讲解、术语和练习')
                    for batch in data.get('translations',[]):
                        if not isinstance(batch,dict):raise ValueError('章节译文需要按文件提交对象')
                        extra=workspace().translations({**batch,'complete':False})
                        if extra is None:
                            updated=apply_translations(copy.deepcopy(raw_material()),batch.get('translations'),False);target=mpath();versions=target.parent/'versions';versions.mkdir(exist_ok=True);(versions/(str(target.stat().st_mtime_ns)+'.json')).write_bytes(target.read_bytes());temporary=target.with_suffix('.next.json');temporary.write_text(json.dumps(updated,ensure_ascii=False,indent=2),'utf8');temporary.replace(target)
                    remaining=translation_queue(material(),learning_payload(material(),workspace().read(),records)['context'])['current_source_ids']
                    if remaining:return self.json({'error':'本章关联的原文还有译文缺项，请只补齐这些原文编号后重交已有讲解，不要重新生成讲解','code':'chapter_translation_incomplete','missing_source_ids':remaining,'explanation_preserved_on_client':True},409)
                    payload={**explanation,'learning_node_id':node_id};used={q['passage_id'] for r in records['rounds'] for q in r['questions']}|set(records.get('read_passages',{}))
                    result=workspace().explain(payload,material(),used)
                    def finish_lesson(state):
                        assets=append_assets(state,data,node_id,result['id'],result['passage_ids'],core.stamp)
                        wait_for_user(state,node_id,core.stamp,request_id)
                        receipt={'request_id':request_id,'learning_node_id':node_id,'explanation':{k:result[k] for k in ('id','version','passage_ids','learning_node_ids')},**assets,'next_action':'handle_current_section'}
                        state.setdefault('teaching_results',{})[request_id]=receipt;return receipt
                    return self.json(core.mutate(finish_lesson))
            if path=='/api/content/normalize':
                with core.LOCK:return self.json(workspace().normalize_files(data))
            if path=='/api/learning/outline':
                with core.LOCK:
                    store=workspace();value=store.read();value['learning_outline']=validate_outline(data,material());store.write(value,data.get('expected_revision'))
                return self.json(learning_payload(material(),workspace().read(),core.read_state()))
            if path=='/api/learning/select':
                plan=learning_payload(material(),workspace().read(),core.read_state());node=next((n for n in plan['nodes'] if n['id']==data.get('node_id')),None)
                if not node:raise ValueError('没有找到这一个学习章节')
                if getattr(self,'_remote_machine',False) and plan['selected_id'] and node['id']!=plan['selected_id'] and not completion_payload(material(),workspace().read(),core.read_state())['can_advance']:return self.json({'error':'当前章节尚未由用户勾选完成，Agent 不能进入下一节'},403)
                def choose_learning(state):
                    old=dict(state['focus']);old['learning_node_id']=state.get('learning_node_id');state.setdefault('history',[]).append({'focus':old,'view':data.get('previous_view',{})});state['forward_history']=[]
                    state['learning_node_id']=node['id'];target=next((pid for pid in node['passage_ids'] if not locate(pid).get('heading')),None)
                    if not target and node.get('source_links'):target=node['source_links'][0]['passage_id']
                    state['focus']={'passage_id':target,'sentence_id':None,'revision':state['focus']['revision']+1,'view':{}}
                    if old.get('learning_node_id')!=node['id']:state['teaching_state']={'state':'ready','learning_node_id':node['id'],'updated_at':core.stamp()}
                    return {'node_id':node['id'],'status':node['status']}
                return self.json(core.mutate(choose_learning))
            if path=='/api/projects':
                identity='project-'+uuid.uuid4().hex[:14];title=core.text(data.get('title'),'项目名称',300)
                document={'id':identity,'title':title,'source':'','kind':'project','pages':{},'passages':[],'outline':[],'inventory':[],'schema_version':2,'translation_status':'not_required','coverage':{'source_coverage':1,'outline_coverage':1,'translation_coverage':1},'original_file':''}
                target=mpath(identity);target.parent.mkdir(parents=True);target.write_text(json.dumps(document,ensure_ascii=False),'utf8')
                return self.json({'id':identity,'title':title,'url':f'{public_base()}/?material={identity}'})
            if path in ('/api/content/import','/api/content/files','/api/content/explanations','/api/content/presentation'):
                with core.LOCK:
                    store=workspace()
                    if path.endswith(('/files','/import')):
                        if data.get('repository_url'):data.update(kind='repository',url=data['repository_url'])
                        elif data.get('url'):data.setdefault('kind','url')
                        elif data.get('files'):data.setdefault('kind','directory')
                        elif str(data.get('filename','')).lower().endswith('.zip'):data.setdefault('kind','zip')
                        row=store.add_file(data);result={k:v for k,v in row.items() if k!='document'};result['original_url']=(os.environ.get('MANUAL_READER_PUBLIC_ORIGIN') or public_base())+'/content-files/'+row['id']+'/original?material='+mid();result['api_original_url']=public_base()+'/content-files/'+row['id']+'/original?material='+mid()
                        if path.endswith('/import'):result={'file':result,'files':[result],'status':row.get('import_status','ready'),'pending':row.get('pending',[]),'coverage':row.get('coverage',{}),'model_calls':0}
                    elif path.endswith('/explanations'):
                        if getattr(self,'_remote_machine',False):return self.json({'error':'请用 /teaching/result 同时写回当前章节的讲解、术语和练习，写回后等待用户'},400)
                        document=material();plan=learning_payload(document,store.read(),core.read_state())
                        nodes=[n for n in plan['nodes'] if n['id']==data.get('learning_node_id') or data.get('id') in n.get('explanation_ids',[])]
                        source_ids={l['passage_id'] for n in nodes for l in n['effective_source_links']}|{l.get('passage_id') for l in data.get('source_links',[])}
                        remaining=translation_queue(document,{'passage_ids':source_ids})['current_source_ids']
                        if remaining:return self.json({'error':'本章关联的原文还有译文缺项，请先按原编号补齐译文，再提交已有讲解','code':'chapter_translation_incomplete','missing_source_ids':remaining},409)
                        records=stored_state(mid());used={r['passage_id'] for r in records.get('rounds',[])}|set(records.get('read_passages',{}))
                        result=store.explain(data,material(),used)
                    else:
                        if data.get('focus'):locate(data['focus'].get('passage_id'),data['focus'].get('sentence_id'))
                        result=store.present(data,material())
                        document=material();focus=core.read_state()['focus'];existing=next((p for p in document['passages'] if p['id']==focus.get('passage_id')),None)
                        target=data.get('focus')
                        if target:locate(target.get('passage_id'),target.get('sentence_id'))
                        elif result.get('explanation_id') and result['columns']==[['explanation']]:
                            e=next(e for e in document['explanations'] if e['id']==result['explanation_id']);target={'passage_id':e['passage_ids'][0]}
                        elif result.get('file_id') and (not existing or existing.get('file_id')!=result['file_id']):
                            f=next(f for f in document['reading_files'] if f['id']==result['file_id']);target={'passage_id':next((pid for pid in f['passage_ids'] if not locate(pid).get('heading')),f['passage_ids'][0])}
                        if target:
                            def select_content(state):
                                state.setdefault('history',[]).append({'focus':dict(state['focus']),'view':data.get('previous_view',{})});state['forward_history']=[]
                                state['focus']={'passage_id':target['passage_id'],'sentence_id':target.get('sentence_id'),'revision':state['focus']['revision']+1,'view':{}}
                            core.mutate(select_content)
                return self.json(result)
            if path=='/api/settings':
                with core.LOCK:result=save_settings(SETTINGS,data)
                return self.json(result)
            if path=='/api/terms/normalize':return self.json(core.mutate(lambda state:normalize_state(state,core.stamp)))
            if path=='/api/terms':
                updates=data.get('updates')
                if not isinstance(updates,list) or not updates:raise ValueError('请提交需要更新的术语列表')
                def update_terms(state):
                    known={t['id']:t for t in state['terms']};prepared=[]
                    for update in updates:
                        if not isinstance(update,dict) or update.get('term_id') not in known:raise ValueError('请使用已登记的术语编号')
                        fields={key:core.text(update[key], '术语解释',12000) for key in ('professional_definition','intuitive_explanation') if key in update}
                        if 'name' in update:
                            item=known[update['term_id']]
                            if update.get('expected_name')!=item['name']:raise ValueError('术语名称已经改变，请重新核对后提交')
                            name=analyze_name(update['name'],update.get('case_exceptions',item.get('case_exceptions',[])),bool(update.get('english_case_exception',item.get('english_case_exception'))))
                            if name['issues']:raise ValueError('术语名称仍有未解决的中英文格式问题')
                            fields['name']=name['name']
                        if not fields:raise ValueError('请提交术语名称、专业定义或直观解释')
                        prepared.append((known[update['term_id']],fields,update.get('intuitive_source')))
                    for item,fields,source in prepared:
                        if 'name' in fields:
                            old=item['name'];item.setdefault('name_history',[]).append({'name':old,'at':core.stamp(),'action':'bounded_name_repair'});item.setdefault('source_names',[]).append(old);item['name']=fields.pop('name')
                            proposed=next(u for u in updates if u['term_id']==item['id'])
                            for key in ('case_exceptions','english_case_exception','official_spelling','case_source'):
                                if key in proposed:item[key]=copy.deepcopy(proposed[key])
                        revise_term(item,{**fields,'action':'revise','expected_version':next(u.get('expected_version',item.get('version',1)) for u in updates if u['term_id']==item['id'])},core.stamp)
                        if source:item['intuitive_source']=source
                    return {'updated':len(prepared)}
                return self.json(core.mutate(update_terms))
            if path=='/api/folders':
                with core.LOCK:result=reader_library.change(LIBRARY/'library-index.json',library_sources(),'folder',None,'create',data)
                return self.json(result)
            if path=='/api/library/clear-trash':
                with core.LOCK:result=reader_library.clear_trash(LIBRARY/'library-index.json',library_sources(),data.get('confirmed'))
                return self.json(result)
            library_match=re.fullmatch(r'/api/library/(folder|material)/([A-Za-z0-9_-]+)/(rename|move|trash|restore)',path)
            if library_match:
                kind,identity,action=library_match.groups()
                with core.LOCK:
                    result=reader_library.change(LIBRARY/'library-index.json',library_sources(),kind,identity,action,data)
                    if kind=='material' and action=='rename':
                        document=copy.deepcopy(material(identity));document['title']=result['name'];target=mpath(identity);versions=target.parent/'versions';versions.mkdir(exist_ok=True);(versions/(target.stem+'-'+str(target.stat().st_mtime_ns)+'.json')).write_bytes(target.read_bytes());temporary=target.with_suffix('.next.json');temporary.write_text(json.dumps(document,ensure_ascii=False,indent=2),'utf8');temporary.replace(target);CACHE.pop(identity,None)
                return self.json(result)
            if path=='/api/read':
                if getattr(self,'_remote_machine',False) or data.get('read_origin')!='manual_ui' or self.headers.get('Sec-Fetch-Site')!='same-origin' or self.headers.get('Sec-Fetch-Mode') not in ('cors','same-origin'):
                    return self.json({'error':'请在阅读页面手动勾选已读；Agent 和脚本不能修改阅读进度'},403)
                ids=data.get('passage_ids',[data.get('passage_id')]);known={p['id'] for p in material()['passages']}
                if not isinstance(ids,list) or not ids or any(not isinstance(pid,str) or pid not in known for pid in ids):raise ValueError('请提交当前材料中存在的正文段落编号')
                if not isinstance(data.get('read'),bool):raise ValueError('请指定是否标记已读')
                ids=list(dict.fromkeys(ids))
                def mark_page(state):
                    records=state.setdefault('read_passages',{});at=core.stamp()
                    for pid in ids:
                        if data['read']:records[pid]=at
                        else:records.pop(pid,None)
                    channel='machine_api' if getattr(self,'_remote_machine',False) else 'browser' if self.headers.get('Sec-Fetch-Mode') in ('cors','same-origin') else 'local_api'
                    state.setdefault('read_events',[]).append({'at':at,'passage_ids':ids,'read':data['read'],'channel':channel,'learning_node_id':state.get('learning_node_id'),'client_action':data.get('read_origin')})
                    completion=record_completion(material(),workspace().read(),state,{**data,'passage_ids':ids},core.stamp)
                    return {'passage_ids':ids,'passage_id':ids[0] if len(ids)==1 else None,'read':data['read'],'count':len(ids),'completion':completion}
                return self.json(core.mutate(mark_page))
            answer_match=re.fullmatch(r'/api/questions/([^/]+)/answer',path)
            if answer_match:return self.json(core.mutate(lambda state:change_answer(state,answer_match[1],data,core.stamp)))
            quiz_options=re.fullmatch(r'/api/quizzes/([^/]+)/options',path)
            if quiz_options:
                from reader_contract import revise_quiz_options
                return self.json(core.mutate(lambda state:revise_quiz_options(state,quiz_options[1],data,core.stamp)))
            if path=='/api/lectures':
                identity=data.get('material_id') or 'lecture-'+uuid.uuid4().hex[:14]
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,90}',identity) or identity==DEFAULT:raise ValueError('讲解材料编号无效，不能覆盖当前原件')
                path_to_material=mpath(identity);previous=material(identity) if path_to_material.exists() else None
                if previous and previous.get('reader_mode')!='authored':raise ValueError('只能更新讲解材料，不能覆盖已导入的原件')
                with core.LOCK:
                    updated=lecture_material(data,identity,previous)
                    if previous:
                        existing=stored_state(identity);used={r['passage_id'] for r in existing.get('rounds',[])}|set(existing.get('read_passages',{}))
                        if used-{p['id'] for p in updated['passages']}:raise ValueError('这些段落已有阅读或提问记录，请保留对应章节和段落')
                    path_to_material.parent.mkdir(parents=True,exist_ok=True)
                    if previous:
                        versions=path_to_material.parent/'versions';versions.mkdir(exist_ok=True);(versions/(str(path_to_material.stat().st_mtime_ns)+'.json')).write_bytes(path_to_material.read_bytes())
                    temporary=path_to_material.with_suffix('.next.json');temporary.write_text(json.dumps(updated,ensure_ascii=False,indent=2),'utf8');temporary.replace(path_to_material)
                return self.json({'material_id':identity,'title':updated['title'],'outline':updated['outline'],'coverage':updated['coverage'],
                                  'url':f'{public_base()}/?material={identity}'})
            if path=='/api/questions/sync':
                def synchronize(state):
                    result=sync_questions(state,data,material(),core.create_round,core.stamp)
                    owners={q['id']:r['id'] for r in state['rounds'] for q in r['questions']}
                    result['questions']=[{**decorate_question(material(),q),'round_id':owners[q['id']]} for q in result['questions']]
                    return result
                return self.json(core.mutate(synchronize))
            if path=='/api/questions/associate':
                node_id=data.get('learning_node_id');nodes={n['id'] for n in learning_payload(material(),workspace().read(),core.read_state())['nodes']}
                if node_id not in nodes:raise ValueError('没有这个学习小节')
                evidence=core.text(data.get('evidence'),'归属依据',2000);ids=data.get('question_ids')
                if not isinstance(ids,list) or not ids or len(ids)>500:raise ValueError('请提交需要登记归属的问题编号列表')
                def associate(state):
                    known={q['id']:q for row in state['rounds'] for q in row['questions']};updated=[];skipped=[]
                    if any(qid not in known for qid in ids):raise ValueError('问题不属于当前项目')
                    for qid in ids:
                        q=known[qid]
                        if q.get('deleted_at') or q.get('learning_node_id') not in (None,'',node_id):skipped.append(qid);continue
                        if q.get('learning_node_id')==node_id:continue
                        q.setdefault('association_history',[]).append({'learning_node_id':q.get('learning_node_id'),'evidence':evidence,'at':core.stamp()});q['learning_node_id']=node_id;updated.append(qid)
                    for row in state['terms']+state['quizzes']:
                        if set(row.get('question_ids',[])).intersection(ids):row['learning_node_ids']=list(dict.fromkeys(row.get('learning_node_ids',[])+[node_id]))
                    return {'updated_question_ids':updated,'skipped_question_ids':skipped,'content_and_progress_preserved':True}
                return self.json(core.mutate(associate))
            question_match=re.fullmatch(r'/api/questions/([^/]+)',path)
            if question_match:return self.json(core.mutate(lambda state:change_question(state,question_match[1],data,core.stamp)))
            if path=='/api/draft-batches':return self.json(core.mutate(lambda state:save_drafts(state,data,locate)))
            if path=='/api/question-batches':
                return self.json(core.mutate(lambda state:create_batch(state,data,material(),core.create_round)))
            if path=='/api/rounds':
                rows=data.get('questions')
                if isinstance(rows,list):
                    for row in rows:
                        if isinstance(row,dict):row['original_text']=row.get('original_text',row.get('text',''));row['text']=normalize_question(row.get('text',''))
                def save(state):
                    r=core.create_round(state,data)
                    valid_nodes={n['id'] for n in learning_payload(material(),workspace().read(),state)['nodes']}
                    for q,row in zip(r['questions'],data['questions']):
                        q.setdefault('original_text',row['original_text'])
                        parent=next((old for record in state['rounds'] for old in record['questions'] if old['id']==q.get('parent_question_id')),None)
                        q.setdefault('learning_node_id',row.get('learning_node_id') or (parent or {}).get('learning_node_id') or state.get('learning_node_id') or learning_payload(material(),workspace().read(),state)['selected_id'])
                        if q.get('learning_node_id') and q['learning_node_id'] not in valid_nodes:raise ValueError('提问的小节编号不属于当前项目')
                        p=locate(q['passage_id'],q.get('sentence_id'));part=next((v for v in p['sentences'] if v['id']==q.get('sentence_id')),p)
                        q.setdefault('target',{'kind':p.get('content_kind','source'),'file_id':p.get('file_id'),'explanation_id':p.get('explanation_id'),'version':p.get('content_version'),'passage_id':p['id'],'sentence_id':q.get('sentence_id'),'original':part.get('en') or p.get('alt',''),'translation':part.get('zh','')})
                    return r
                return self.json(core.mutate(save))
            if path=='/api/library/import':
                identity='doc-'+uuid.uuid4().hex[:14];folder=LIBRARY/identity;folder.mkdir(parents=True)
                incoming=dict(data)
                if incoming.get('source_path'):
                    if getattr(self,'_remote_machine',False) or os.environ.get('MANUAL_READER_PUBLIC_ORIGIN'):raise ValueError('远程导入请上传内容或网址，不能读取服务器路径')
                    source=Path(incoming.pop('source_path')).resolve()
                    if source.is_dir():incoming.update(kind='directory',files=[{'filename':str(p.relative_to(source)).replace(chr(92),'/'),'content_base64':base64.b64encode(p.read_bytes()).decode()} for p in source.rglob('*') if p.is_file()])
                    else:incoming.update(filename=source.name,content_base64=base64.b64encode(source.read_bytes()).decode())
                if incoming.get('repository_url'):incoming.update(kind='repository',url=incoming['repository_url'])
                elif incoming.get('url'):incoming.setdefault('kind','url')
                elif incoming.get('files'):incoming.setdefault('kind','directory')
                elif str(incoming.get('filename','')).lower().endswith('.zip'):incoming.setdefault('kind','zip')
                document={'id':identity,'title':incoming.get('title') or '新材料','source':'','kind':'project','pages':{},'passages':[],'outline':[],'inventory':[],'schema_version':2,'translation_status':'pending','coverage':{},'original_file':''}
                target=folder/'material.json';target.write_text(json.dumps(document,ensure_ascii=False),'utf8')
                row=Workspace(folder).import_source(incoming)
                return self.json({'id':identity,'title':incoming.get('title') or row['name'],'coverage':row['coverage'],'pending':row.get('pending',[]),'import_status':row.get('import_status','ready'),'model_calls':0})
            if path=='/api/translations':
                with core.LOCK:
                    if data.get('only_missing'):
                        from reader_coverage import translation_ready
                        current={p['id']:p for p in material()['passages']}
                        if any(row.get('source_id') not in current for row in data.get('translations',[])):raise ValueError('译文引用了当前项目不存在的原文编号')
                        data['translations']=[row for row in data.get('translations',[]) if not translation_ready(current[row['source_id']])]
                    extra=workspace().translations(data)
                    if extra is not None:return self.json(extra)
                    m=raw_material()
                    if m.get('schema_version')!=2:raise ValueError('现有读本请更新对应映射，不能替换整份译文而改动已有引用')
                    updated=apply_translations(copy.deepcopy(m),data.get('translations'),data.get('complete',True));versions=mpath().parent/'versions';versions.mkdir(exist_ok=True);(versions/(str(mpath().stat().st_mtime_ns)+'.json')).write_bytes(mpath().read_bytes());temporary=mpath().with_suffix('.next.json');temporary.write_text(json.dumps(updated,ensure_ascii=False,indent=2),'utf8');temporary.replace(mpath())
                return self.json(updated['coverage'])
            if path=='/api/focus':
                p=locate(data.get('passage_id'),data.get('sentence_id'))
                def save(state):
                    if (state['focus']['passage_id'],state['focus'].get('sentence_id'))!=(p['id'],data.get('sentence_id')):
                        state.setdefault('history',[]).append({'focus':dict(state['focus']),'view':data.get('previous_view',{})});state['history']=state['history'][-100:];state['forward_history']=[]
                    state['focus']={'passage_id':p['id'],'sentence_id':data.get('sentence_id'),'revision':state['focus']['revision']+1,'view':data.get('view',{})};return state['focus']
                with core.LOCK:
                    result=core.mutate(save)
                    response={'focus':result,'progress':progress_payload()} if data.get('include_progress') is True else result
                return self.json(response)
            if path in ('/api/history/back','/api/history/forward'):
                def save(state):
                    source='history' if path.endswith('/back') else 'forward_history';target='forward_history' if source=='history' else 'history'
                    if state.get(source):
                        old=state[source].pop();state.setdefault(target,[]).append({'focus':dict(state['focus']),'view':data.get('view',{})});state['focus']={**old['focus'],'view':old.get('view',{}),'revision':state['focus']['revision']+1};state['learning_node_id']=old['focus'].get('learning_node_id',state.get('learning_node_id'))
                    return state['focus']
                return self.json(core.mutate(save))
            match=re.fullmatch(r'/api/rounds/([^/]+)/(status|metrics)',path)
            if match:
                def save(state):
                    r=core.find_round(state,match[1])
                    if match[2]=='status':r['processing']={'state':data.get('state','running'),'model':data.get('model'),'effort':data.get('effort'),'updated_at':core.stamp()}
                    else:r.setdefault('metrics',[]).append(data)
                    return r
                return self.json(core.mutate(save))
            return self.json({'error':'没有这个接口'},404)
        except (ValueError,KeyError,TypeError,OSError) as error:return self.json({'error':str(error)},400)

class ReaderHTTPServer(ThreadingHTTPServer):
    allow_reuse_address=os.name!='nt'
    def server_bind(self):
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()

class ProductionHandler(RemoteMixin,Handler):
    def bind_material(self,identity):CONTEXT.material_id=identity
    def default_material_id(self):return DEFAULT

if __name__=='__main__':
    core.read_state();ReaderHTTPServer(('127.0.0.1',core.PORT),ProductionHandler).serve_forever()
