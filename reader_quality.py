"""按公开的内容结构整理显示格式；检查不发起模型调用，不改变原文或用户提问。"""
import copy,re

VERSION='reader-format-v6'
QA_PART_CONTRACT={'id':'稳定的本部分编号，例如 ap1','unit_ids':['本部分实际讲清的 plan.units 编号'],'question_span':'逐字摘取对应子问；不同对象、判断或推理阶段分 parts','markdown':'先直接回答，再把必要原因与步骤讲成自然相连的短段'}
LESSON_PART_CONTRACT={'id':'稳定的本部分编号，例如 lp1','unit_ids':['本部分实际讲清的 plan.units 编号'],'source_id':'本节原文的真实编号；纯独立讲解省略','source_span':'逐字登记对应原句，只用于定位；纯独立讲解省略','source_ids':['可同时登记实际解释到的多个原文编号'],'heading':'本部分话题的小标题，必须提供；只在这里写一次，正文不重复标题','markdown':'接着上一部分带读者弄懂当前内容的连续正文'}
ROLES={'目录':'安排学习顺序，登记每个原文单元在哪一节讲解，明确尚未安排或尚未讲解的内容',
       '原文':'按原件展示文本、页码、图片和坐标，读者可以直接核对',
       '译文':'连续读懂同页和相邻段后翻译，保留原意与结构，逐项返回对应编号；不把额外讲解放进译文',
       '讲解':'围绕本节原句说明它在说什么，再补齐理解它必需的概念、动作和推论',
       '问题':'保留用户原话，分别回答本轮每个疑问，再围绕各自答案说明原因',
       '术语':'覆盖讲解与回答实际使用的概念，同一概念复用编号，分别给出独立定义和直观解释',
       '练习':'覆盖已经讲清的判断、计算与操作，题目关联对应内容，保留作答和错题记录',
       '笔记与进度':'笔记始终关联原问题或片段；只有用户手动勾选才增加已读进度，父章节汇总子章节时去重'}
PROTECTED=re.compile(r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\$\$[\s\S]*?\$\$|(?<![\\$])\$(?!\$)[^$\n]+(?<!\\)\$|https?://[^\s<>]+|[“「『][\s\S]*?[”」』]')

def contains_quote(quote,text):
    return bool(str(quote).strip()) and re.sub(r'\s+','',str(quote)) in re.sub(r'\s+','',str(text or ''))

def clean_markdown(value):
    """只整理生成正文的空白与重复分隔线，代码、公式、引用和网址保持原样。"""
    text=str(value or '');saved=[]
    def hide(m):saved.append(m[0]);return '\ue000'+str(len(saved)-1)+'\ue001'
    text=PROTECTED.sub(hide,text).replace('\r\n','\n')
    text=re.sub(r'(?m)^([ \t]*(?:\*\*)?(?:原文|原句|回答)(?:\*\*)?[：:](?:\*\*)?)[ \t]*(?=\S)',r'\1\n\n',text)
    text=re.sub(r'(?m)^([ \t]*(?:\*\*)?(?:原文|原句|回答)(?:\*\*)?[：:](?:\*\*)?)[ \t]*\n(?:[ \t]*\n)*[ \t]*(?=\S)',lambda m:m[1].strip()+'\n\n',text)
    text=re.sub(r'(?m)^([ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*)\n(?:[ \t]*\n)*(?:[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*\n?)+',r'\1\n',text)
    text=re.sub(r'\n{4,}','\n\n\n',text).strip()
    return re.sub(r'\ue000(\d+)\ue001',lambda m:saved[int(m[1])],text)

def format_audit(text):
    protected=PROTECTED.sub('',str(text or ''));issues=[]
    if re.search(r'(?m)^(?:原文|回答)[：:]\S',protected):issues.append('label_not_on_own_line')
    if re.search(r'(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*\n\s*(?:-{3,}|\*{3,}|_{3,})\s*$',protected):issues.append('repeated_separator')
    if protected.count('**')%2:issues.append('unpaired_bold_marker')
    if any(len(line)>220 and not re.match(r'^\s*(?:[|#>]|[-*]\s|\d+[.)])',line)
           for line in protected.splitlines()):issues.append('dense_paragraph')
    return issues

def generated_prose(value):
    """省略新写段落末尾的中文句号；引用、公式和代码仍保持原样。"""
    text=clean_markdown(value);saved=[]
    def hide(m):saved.append(m[0]);return '\ue000'+str(len(saved)-1)+'\ue001'
    text=PROTECTED.sub(hide,text)
    text=re.sub(r'。(?=[ \t]*(?:\n[ \t]*\n|$))','',text)
    from reader_connected import prose_line_breaks
    return prose_line_breaks(re.sub(r'\ue000(\d+)\ue001',lambda m:saved[int(m[1])],text))


def lesson_display(text,quote='',alternates=()):
    """来源仍存于映射；阅读正文只去掉旧版开头重复的原句，不删讲解。"""
    text=str(text or '')
    label=r'^\s*(?:#{1,6}\s*)?(?:\*\*)?原(?:文|句)(?:\*\*)?[：:](?:\*\*)?\s*'
    quotes=[re.sub(label,'',str(q)).strip() for q in (quote,*alternates) if q]
    while True:
        marked=re.match(label,text)
        if not marked:return text
        rest=text[marked.end():]
        for value in quotes:
            pattern=r'\s*'+r'\s+'.join(re.escape(part) for part in value.split())
            match=re.match(pattern,rest)
            if match:
                text=re.sub(r'^\s*(?:\*\*)?回答(?:\*\*)?[：:]\s*','',rest[match.end():]).lstrip()
                break
        else:return text


def standalone_heading(text):
    """只认整块都是标题的形式，不把正文中出现的加粗词当成标题。"""
    text=str(text or '').strip()
    return bool(re.fullmatch(r'#{1,6}\s+[^\n]+|\*\*[^*\n]+\*\*|__[^_\n]+__',text))


def explanation_headings(passages,explanations):
    """旧稿已有独立标题时保留原片段，只省略紧随正文的额外显示标题。"""
    by_id={p['id']:p for p in passages}
    for explanation in explanations:
        previous_title=False;last_title='';fallback_used=False
        for identity in explanation.get('passage_ids',[]):
            p=by_id.get(identity)
            if not p or p.get('display_omit'):continue
            text=p.get('display_markdown',p.get('zh')or p.get('en')or'')
            own_title=bool(p.get('heading'))or standalone_heading(text)
            starts_heading=bool(re.match(r'^\s*#{1,6}\s+',text))or standalone_heading(re.split(r'\n\s*\n',text.strip(),maxsplit=1)[0])
            heading=p.get('reading_heading','')
            if not heading and 'reading_heading'not in p and not fallback_used and not own_title and not starts_heading and str(text).strip():heading=explanation.get('title','')
            p['display_reading_heading']=''if previous_title or own_title or starts_heading or heading==last_title else heading
            if own_title or starts_heading:
                first=re.split(r'\n\s*\n',text.strip(),maxsplit=1)[0];current=re.sub(r'^[#* _]+|[#* _]+$','',first).strip()
                if current and current==last_title:
                    rest=text.strip()[len(first):].lstrip()
                    if rest:p['display_markdown']=rest
                    else:p['display_omit']=True
                last_title=current or last_title;fallback_used=True
            elif heading:last_title=heading;fallback_used=True
            previous_title=own_title

def _title(text):
    # 用户问句作为普通文字标题，不让其中的 Markdown 改变正文结构。
    return re.sub(r'([\\`*_<>{}\[\]#!|])',r'\\\1',re.sub(r'\s+',' ',text).strip())

def answer_parts(answer,question,preserve_prose=False):
    """将各子问及其答案一次装配成正文，保留模型原始结构用于检查。"""
    parts=answer.get('parts',answer.get('answer_parts'));issues=[]
    if parts is None:return clean_markdown(answer.get('text','')),[],format_audit(answer.get('text',''))
    if not isinstance(parts,list) or not 1<=len(parts)<=30:raise ValueError('回答 parts 需要包含 1 至 30 个部分')
    rendered=[];clean=[]
    format_prose=clean_markdown if preserve_prose else generated_prose
    for index,part in enumerate(parts):
        if not isinstance(part,dict):raise ValueError('回答每一部分都需要是对象')
        if isinstance(part.get('markdown'),str) and part['markdown'].strip():
            span=str(part.get('question_span') or '').strip()
            if not contains_quote(span,question):
                issues.append({'code':'question_span_not_exact','part':index,'submitted_span':span});span=question
            markdown=format_prose(part['markdown'])
            from reader_generation import part_id
            clean.append({'id':part_id(part,index),'unit_ids':part.get('unit_ids',[]),'question_span':span,'markdown':markdown})
            rendered.append('**'+_title(span)+'**\n\n'+markdown)
            continue
        span=str(part.get('question_span') or '').strip();direct=str(part.get('direct_answer') or '').strip();explanation=str(part.get('explanation') or '').strip()
        if not direct:raise ValueError('回答缺少 direct_answer，请保留原始结果并补齐这一部分')
        if not contains_quote(span,question):
            # 模型改写过问题时，显示真正的用户原话，不把改写冒充原句；答案仍及时保存。
            issues.append({'code':'question_span_not_exact','part':index,'submitted_span':span});span=question
        direct=format_prose(direct);explanation=format_prose(explanation)
        from reader_generation import part_id
        clean.append({'id':part_id(part,index),'unit_ids':part.get('unit_ids',[]),'question_span':span,'direct_answer':direct,'explanation':explanation,'markdown':direct+('\n\n'+explanation if explanation else '')})
        rendered.append('**'+_title(span)+'**\n\n'+direct+('\n\n'+explanation if explanation else ''))
    text='\n\n'.join(rendered)
    return text,clean,issues+format_audit(text)

def lesson_parts(data,material,previous_blocks=()):
    """原句与讲解共用稳定来源编号；将轻量结构转成现有讲解块。"""
    if data.get('parts') is None:return data
    from reader_prompts import WRITING_VERSION
    preserve_prose=data.get('writing_version')==WRITING_VERSION
    data=__import__('reader_sequence').prepare_explanation(data,preserve_prose)
    parts=data['parts'];sources={p['id']:p for p in material['passages'] if p.get('content_kind')!='explanation'}
    if not isinstance(parts,list) or not parts:raise ValueError('讲解 parts 需要是非空列表')
    blocks=[];issues=[]
    for i,part in enumerate(parts):
        source_ids=list(dict.fromkeys(part.get('source_ids',[])+([part['source_id']]if part.get('source_id')else[])));source=sources.get(source_ids[0])if source_ids else None;span=str(part.get('source_span') or '').strip();claim=str(part.get('claim') or '').strip();body=str(part.get('explanation') or '').strip()
        if any(identity not in sources for identity in source_ids):raise ValueError('讲解引用的原文编号不属于当前项目')
        markdown=str(part.get('markdown') or '').strip()
        if not claim and not markdown:raise ValueError('讲解需要提供正文 markdown')
        valid_text=[source.get(k,'') for k in ('en','zh')]+[s.get('text','') for s in source.get('translation_segments',[])]if source else[]
        if source and not any(contains_quote(span,text) for text in valid_text):
            issues.append({'code':'source_span_not_exact','part':i});span=source.get('zh') or source.get('en') or source.get('alt','')
        # 引文只作为来源保存，三栏点击定位；讲解正文不重复抄写原句。
        format_prose=clean_markdown if preserve_prose else generated_prose
        text=format_prose(markdown) if markdown else format_prose(claim)+('\n\n'+format_prose(body) if body else '')
        blocks.append({'id':part.get('id') or 'part-'+str(i+1),'unit_ids':part.get('unit_ids',[]),'markdown':clean_markdown(text),'source_quote':span,'source_ranges':[{'file_id':sources[identity].get('file_id','primary'),'passage_id':identity}for identity in source_ids],'explained_source_ids':source_ids,**({'reading_heading':str(part['heading']or'').strip().rstrip('？?')}if'heading'in part else{})})
    for part,block in zip(parts,blocks):
        if part.get('premise')and part.get('question'):block.update(heading_premise=part['premise'],heading_question=part['question'])
    blocks=__import__('reader_sequence').frame_blocks(data,blocks,previous_blocks)
    return {**data,'blocks':blocks,'format_audit':issues}

def project_audit(material,state):
    from reader_responsibility import audit_components
    answers=[]
    for r in state.get('rounds',[]):
        for q in r.get('questions',[]):
            if q.get('answer') and not q.get('deleted_at'):
                flags=format_audit(q['answer']);answers.append({'question_id':q['id'],'issues':flags,'structured':bool(q.get('answer_parts'))})
    explanations=[]
    for p in material['passages']:
        if p.get('content_kind')!='explanation':continue
        text=p.get('display_markdown',p.get('zh') or p.get('en') or '');quote=p.get('source_quote') or ''
        explanations.append({'passage_id':p['id'],'issues':format_audit(text),'source_quote_registered':bool(quote),'source_quote_in_body':bool(quote and quote in text)})
    return {'version':VERSION,'answers':answers,'explanations':explanations,'directory':material.get('learning',{}).get('coverage_plan',{}),'associations':audit_components(material,state),'import_conflicts':material.get('import_conflicts',[]),'scope':'脚本检查格式和编号，不把通过检查当作已经证明读者理解；日常写作不增加审稿模型'}


def generated_scope_figures(material):
    """单列已生成章节中的矢量图标签，避免与全项目尚未阅读的位图混为一谈。"""
    from reader_coverage import translation_ready
    nodes=material.get('learning',{}).get('nodes',[])
    ids={l['passage_id'] for n in nodes if n.get('status')=='ready' for l in n.get('effective_source_links',[])}
    sources={p['id']:p for p in material['passages']}
    figures=[]
    for group in material.get('reading_view',{}).get('groups',[]):
        members=[sources[pid] for pid in group['passage_ids'] if pid in sources]
        if group.get('kind')!='figure' or not ids.intersection(group['passage_ids']):continue
        if any(p.get('kind') in ('image','visual','page_image','scan') for p in members):continue
        labels=[p for p in members if (p.get('en') or p.get('zh') or '').strip()]
        figures.append({'id':group['id'],'label_ids':[p['id'] for p in labels],'labels_translated':all(translation_ready(p) for p in labels)})
    return {'total':len(figures),'done':sum(g['labels_translated'] for g in figures),'figures':figures}

def public_rules():
    from reader_generation import VERSION as FRAMEWORK_VERSION,PLAN_CONTRACT,PLACEMENT_CONTRACT
    from reader_prompts import STYLE,writing_section,WRITER_INSTRUCTIONS,NAMING_RULE,WRITING_VERSION
    from reader_task_prompts import READING_ANALYSIS, TRANSLATION, PROMPT_VERSION
    from reader_analysis import VERSION as ANALYSIS_VERSION
    return {'version':WRITING_VERSION,'task_prompt_version':PROMPT_VERSION,'format_version':VERSION,'framework_version':FRAMEWORK_VERSION,'plan':PLAN_CONTRACT,'practice_placement':PLACEMENT_CONTRACT,'roles':ROLES,
      'analysis':{'version':ANALYSIS_VERSION,'system':READING_ANALYSIS,'modes':['lesson','answer']},
      'qa':{'result_part':QA_PART_CONTRACT,'style':STYLE,'method':'\n\n'.join(writing_section(number) for number in ('6','6.1','6.2')),'writer_system':WRITER_INSTRUCTIONS,'naming':NAMING_RULE},
      'lesson':{'result_part':LESSON_PART_CONTRACT,'framing':__import__('reader_sequence').FRAMING_CONTRACT,'method':'\n\n'.join(writing_section(number) for number in ('5','5.1','5.2','5.3','5.4','5.5','5.6'))},'translation':TRANSLATION,
      'format_checks':['问题片段必须来自真实用户提问','讲解引用只保存在来源映射中，阅读正文不重复原句','原文和回答标签各占一段','重复分隔线只显示一条','每个话题只显示一个标题；显式清空的 reading_heading 不被旧排版文件恢复','代码、公式、引用和用户原话保持原样','译文按原编号保存，完整句组可以对应多个解析碎片'],
      'testing_scope':'章节和问答先共用一次分析，再在同一份成稿中输出正文、术语和练习。脚本核对证据、编号和格式。'}

LESSON_PART_CONTRACT=dict(LESSON_PART_CONTRACT)
LESSON_PART_CONTRACT.update(heading='单独的短标题，最多24字',premise='单独一段，至少50字。说清上一段的对象、动作和结果怎样接到本段',question='单独一段，至少50字。接着刚才的结果，说清本段要看什么或做什么、怎样核对；按实际语气用陈述句或问句')

LESSON_PART_CONTRACT['source_spans']=[{'first_source_id':'本任务sources中实际讲到的首个编号','last_source_id':'实际讲到的末个编号；按本任务输入顺序展开，不能跨过未讲内容'}]
