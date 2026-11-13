"""统一同次写作的结构与练习位置；旧记录通过读取适配，不改正文和阅读状态。"""
import copy,re,unicodedata

VERSION='reader-framework-v3'
PLAN_CONTRACT={'target':'当前要解决的事','direct_answer':'供核对的结论；问答先给，章节到正文推导时再给','units':[{'id':'u1','say':'实际要讲的内容，不写任务说明','needs':[],'source_refs':[{'source_id':'输入中的真实来源编号','quote':'原样表达'}]}],'order':['u1'],'stop_when':'已经讲清到哪里','coverage':'仅讲解任务：按输入source_groups逐组填写 {组编号:{unit_id:真实单元编号,part_id:真实讲解编号,quote:本组实际讲解正文短摘句}}；问答不需要'}
PLACEMENT_CONTRACT={'lesson':{'kind':'lesson','after_part_id':'讲解parts中的id'},'answer':{'kind':'answer','question_id':'原问题编号','after_part_id':'该回答parts中的id'}}

def requires_update(task,result):
    """只约束按新合同领取的任务；已存内容和旧任务继续按原合同读取。"""
    from reader_prompts import PAST_WRITING_VERSIONS, WRITING_VERSION
    if task.get('framework_version') == VERSION and result.get('framework_version') != VERSION:
        return True
    issued, submitted = task.get('writing_version'), result.get('writing_version')
    if issued == WRITING_VERSION:
        return submitted != WRITING_VERSION
    if issued in PAST_WRITING_VERSIONS:
        return submitted not in (*PAST_WRITING_VERSIONS[PAST_WRITING_VERSIONS.index(issued):], WRITING_VERSION)
    return False

def uses_current_contract(version):
    from reader_prompts import PAST_WRITING_VERSIONS, WRITING_VERSION
    return version in (*PAST_WRITING_VERSIONS, WRITING_VERSION)

def exclusive_practice(version):
    from reader_prompts import WRITING_VERSION
    return version == WRITING_VERSION

def practice_owner(quiz):
    position = quiz.get('placement') or {}
    return position.get('question_id'), position.get('after_part_id')

def practice_prompt_key(quiz):
    prompt = unicodedata.normalize('NFKC', str(quiz.get('prompt') or '')).casefold()
    # 保留数字、单位和运算符，它们会改变题意。
    return re.sub(r'\s+', '', prompt).rstrip('。？！?!；;')

def practice_issues(quizzes, owners, *, lesson=False):
    """把每道题对应到一个实际正文部分，并一次指出所有归属缺口。"""
    if not isinstance(quizzes, list):
        return ['quizzes 需要使用列表']
    issues, seen, stems = [], {}, {}
    for index, quiz in enumerate(quizzes):
        if not isinstance(quiz, dict) or not isinstance(quiz.get('placement'), dict):
            issues.append(f'/quizzes/{index} 缺少实际正文位置')
            continue
        owner = practice_owner(quiz)
        position = quiz['placement']
        expected = [] if lesson else [owner[0]]
        if (position.get('kind') != ('lesson' if lesson else 'answer')
                or not isinstance(owner[1], str) or owner[0] is not None and not isinstance(owner[0], str)
                or owner not in owners):
            issues.append(f'/quizzes/{index}/placement 需要指向本次提交的正文部分 {owner}')
            continue
        if 'question_ids' in quiz and quiz['question_ids'] not in ([], expected):
            issues.append(f'/quizzes/{index}/question_ids 必须和 placement 指向同一个问题 {owner[0]}')
        if position.get('after_unit_id'):
            issues.append(f'/quizzes/{index}/placement 只用 after_part_id 指定练习归属')
        if owner in seen:
            issues.append(f'{owner} 有多道题，请保留一道检验本部分内容的练习')
        seen[owner] = index
        stem = practice_prompt_key(quiz)
        if stem and stem in stems:
            issues.append(f'/quizzes/{index}/prompt 与 /quizzes/{stems[stem]}/prompt 重复，请为 {owner} 写独立变式')
        if stem:
            stems[stem] = index
    for qid, part in sorted(owners - seen.keys(), key=lambda owner: (owner[0] or '', owner[1])):
        issues.append(f'{qid or "本章"}/{part} 缺少专属练习')
    return issues

def answer_fragment(draft, ids, analysis=None):
    """保留指定回答、它们使用的单元及前置和相关练习、图、术语。"""
    ids = set(ids)
    answers = [a for a in draft.get('answers', []) if isinstance(a,dict) and a.get('question_id') in ids]
    fragment = {key: draft[key] for key in ('writing_version','framework_version','evidence_gaps') if key in draft}
    fragment['answers'] = answers
    parts = [p for answer in answers for p in answer.get('parts') or answer.get('answer_parts', [])]
    rows=draft['plan']['units']
    units={u['id']:u for u in rows}
    if len(units)!=len(rows):
        raise ValueError('plan 的内容编号重复')
    used = {uid for part in parts for uid in part.get('unit_ids', [])}
    pending = list(used)
    while pending:
        for need in units.get(pending.pop(), {}).get('needs', []):
            if need in units and need not in used:
                used.add(need)
                pending.append(need)
    fragment['plan'] = {**draft['plan'], 'units': [u for u in units.values() if u['id'] in used],
                        'order': [uid for uid in draft['plan']['order'] if uid in used]}
    fragment['quizzes'] = [q for q in draft.get('quizzes', []) if isinstance(q,dict)
                           and isinstance(q.get('placement'),dict) and practice_owner(q)[0] in ids]
    for field in ('terms', 'generated_assets'):
        fragment[field] = []
        for row in draft.get(field, []):
            if not isinstance(row,dict) or not isinstance(row.get('question_ids'),list):
                continue
            selected_ids=[qid for qid in row['question_ids'] if qid in ids]
            if selected_ids:
                fragment[field].append({**row,'question_ids':selected_ids})
    if analysis is not None:
        from reader_analysis import required_checks
        selected = {**analysis,'items':[row for row in analysis['items'] if row['task_id'] in ids]}
        checks = required_checks(selected)
        visual_ids = {visual['id'] for item in selected['items'] for visual in item.get('visuals', [])}
        fragment['reader_checks'] = [row for row in draft.get('reader_checks', []) if row['addresses'] in checks]
        fragment['visual_checks'] = [row for row in draft.get('visual_checks', []) if row['visual_id'] in visual_ids]
        from reader_media import COORDINATE_IMAGE
        referenced = {identity for part in parts for _, identity in COORDINATE_IMAGE.findall(part.get('markdown',''))}
        fragment['coordinate_figures'] = [row for row in draft.get('coordinate_figures', []) if row['id'] in referenced or row['id'] in visual_ids]
        return fragment, selected
    return fragment

def _text(value):return value.strip()if isinstance(value,str)else''
def blank_source(source):
    return source.get('kind')in ('paragraph','text','line','whitespace')and not any(_text(source.get(k))for k in ('en','zh','original','translation','excerpt','text'))and not(source.get('src')or source.get('media_items'))

def source_texts(source):
    """文字引用核对原句；非文字原件也可用实际名称或文件名定位。"""
    values=[str(source.get(k)or'')for k in ('en','zh','original','translation','excerpt','text')]
    if source.get('kind')in('image','visual','page_image','scan','table','figure'):
        values += [str(source.get(k)or'')for k in ('alt','name','caption')]
        values += [str(m.get('alt')or m.get('name')or'')for m in source.get('media_items',[])if isinstance(m,dict)]
        if source.get('src'):values.append(str(source['src']).split('?')[0].rsplit('/',1)[-1])
    return values


def answer_evidence_sources(packet, legacy_passages=()):
    """核对写作者收到的来源快照，保留同一编号的原句和所在段落。"""
    if 'reference_blocks' not in packet:
        return [*legacy_passages, *packet.get('sources', [])]
    rows = {row['id']:dict(row) for row in packet['reference_blocks']}
    for row in packet.get('sources', []):
        rows.setdefault(row['id'], {}).update(row)
    return list(rows.values())

def source_groups(sources):
    """把连续的短字幕登记为小组；代码、图表和完整长段保留原来的边界。"""
    groups=[];current=[];size=0;previous_file=None
    def flush():
        nonlocal current,size
        if current:groups.append({'id':'g'+str(len(groups)+1),'source_ids':[s['id']for s in current]});current=[];size=0
    for source in sources:
        if blank_source(source):continue
        text=source.get('original')or source.get('en')or source.get('zh')or'';kind=source.get('kind');file=source.get('file_id')
        separate=kind in('image','visual','page_image','scan','code','table','equation','formula')or len(text)>500
        if current and (file!=previous_file or separate or size+len(text)>500):flush()
        current.append(source);size+=len(text);previous_file=file
        if separate:flush()
    flush();return groups

def apply_group_coverage(plan,parts,sources):
    """每组必须指出实际讲解及其原句；只据显式登记恢复原编号。"""
    from reader_connected import prose_punctuation
    groups=source_groups(sources);expected={g['id']:g for g in groups};coverage=plan.get('coverage');by_part={p['id']:p for p in parts};units={u['id']for u in plan['units']};quotes=set()
    if not groups:return {'groups':0,'covered_groups':0,'source_ids':0}
    if not isinstance(coverage,dict)or set(coverage)!=set(expected):raise ValueError('本章每个原文组都需要正文证据，缺少：'+','.join(sorted(set(expected)-set(coverage or{}))))
    for identity,group in expected.items():
        row=coverage[identity];part=by_part.get(row.get('part_id'));uid=row.get('unit_id');quote=prose_punctuation(_text(row.get('quote')))
        compact=lambda text:re.sub(r'\s+|[`*]','',prose_punctuation(str(text)))
        if not part or uid not in units or uid not in part.get('unit_ids',[]):raise ValueError('原文组没有对应到实际讲解单元：'+identity)
        if len(compact(quote))<8 or compact(quote)not in compact(part['markdown']):raise ValueError('原文组的讲解证据没有逐字出现在正文：'+identity)
        if compact(quote)in quotes:raise ValueError('不同原文组需要摘录各自实际讲解的句子，不重复同一句凑覆盖：'+identity)
        quotes.add(compact(quote));part['source_ids']=list(dict.fromkeys(part.get('source_ids',[])+group['source_ids']))
    return {'groups':len(groups),'covered_groups':len(coverage),'source_ids':len({sid for g in groups for sid in g['source_ids']})}

def part_id(part,index):
    identity=part.get('id')or'ap'+str(index+1)
    if not isinstance(identity,str)or not re.fullmatch(r'[A-Za-z0-9_-]{1,70}',identity):raise ValueError('内容部分的 id 需要使用字母、数字、下划线或连字符')
    return identity

def display_parts(question,quizzes=(),quiz_index=None):
    """旧分段补齐展示字段；不改回答正文、问题版本或已读哈希。"""
    rows=question.get('answer_parts')
    if not isinstance(rows,list):return question
    parts=[]
    for i,row in enumerate(rows):
        if not isinstance(row,dict):return question
        part=copy.deepcopy(row);part.setdefault('id','ap'+str(i+1));part.setdefault('unit_ids',[])
        part.setdefault('markdown','\n\n'.join(str(part.get(k)or'')for k in ('direct_answer','explanation')if part.get(k)))
        if not part['markdown']:return question
        if quizzes:
            part['display_markdown']=__import__('reader_practice_text').clean(part['markdown'],quizzes,quiz_index)[0]
        parts.append(part)
    return {**question,'answer_parts':parts}

def validate_plan(plan,sources=()):
    """只检查显式结构和逐字引用，不把通过脚本当成已经讲透。"""
    if not isinstance(plan,dict)or not _text(plan.get('target'))or not _text(plan.get('direct_answer')):raise ValueError('请在同次写作中返回 plan，写清本次要解决的事和直接答案')
    units=plan.get('units');order=plan.get('order')
    if not isinstance(units,list)or not units or not isinstance(order,list):raise ValueError('plan 需要非空 units 和讲述顺序 order')
    ids=[u.get('id')for u in units if isinstance(u,dict)]
    if len(ids)!=len(units)or len(set(ids))!=len(ids)or set(ids)!=set(order)or len(order)!=len(ids):raise ValueError('plan 的内容编号重复或讲述顺序有缺项')
    by_id={u['id']:u for u in units};known={s['id']:s for s in sources};seen=set()
    for identity in order:
        unit=by_id[identity]
        if not _text(unit.get('say'))or not isinstance(unit.get('needs'),list)or not set(unit['needs'])<=seen|{'普通中文','四则运算'}:raise ValueError('每个内容单元需要具体说明，前置单元要先出现')
        refs=unit.get('source_refs',[])
        if not isinstance(refs,list):raise ValueError('source_refs 需要是列表')
        for ref in refs:
            if isinstance(ref,dict)and ref.get('source_id')in known and blank_source(known[ref['source_id']])and not _text(ref.get('quote')):continue
            if not isinstance(ref,dict)or not _text(ref.get('quote')):raise ValueError('来源引用需要保留原句')
            if known:
                source=known.get(ref.get('source_id'));quote=re.sub(r'\s+','',ref['quote'])
                if source is None or not any(quote in re.sub(r'\s+','',text) for text in source_texts(source)):
                    raise ValueError('plan 引用没有对应到输入原句：'+str(ref.get('source_id')))
        seen.add(identity)
    return copy.deepcopy(plan)

def normalize_output(value,questions=(),sources=(),lesson=False,required_source_ids=(),reading_context=None,practice_policy=None,registered_terms=None,check_practice_mix=True):
    """把演示和通用客户端装成同一份输出；原始模型文件由调用者另存。"""
    if not isinstance(value,dict):raise ValueError('写作结果需要是 JSON 对象')
    if lesson and not (value.get('framing')or value.get('explanation',{}).get('framing')):raise ValueError('本次新讲解需要章首承接、阅读流程、逐段前提与问题及章末回顾；请保留已有正文，只补缺失结构')
    data=__import__('reader_sequence').prepare_output(value)if lesson else copy.deepcopy(value);data['framework_version']=VERSION
    plan=validate_plan(data.get('plan'),sources);data['plan']=plan
    unit_sources={u['id']:[r['source_id']for r in u.get('source_refs',[])]for u in plan['units']}
    blanks={s['id']for s in sources if blank_source(s)}
    if blanks:
        # 原始空行仍留在材料中；它没有可讲解的文字，不作为正文定位锚点。
        for unit in plan['units']:unit['source_refs']=[r for r in unit.get('source_refs',[])if r.get('source_id')not in blanks]
        data['ignored_empty_source_ids']=sorted(blanks)
    media={s['id']:s.get('src')or(s.get('media')or{}).get('src')for s in sources}
    ordered=[s['id'] for s in sources]
    positions={sid:i for i,sid in enumerate(ordered)}
    def resolve_images(part):
        # 逐字核对过的原句通过 unit_ids 归属于本部分，不要求模型再抄一份编号。
        inherited=[sid for uid in part.get('unit_ids',[])for sid in unit_sources.get(uid,[])if sid not in blanks]
        for span in part.get('source_spans',[]):
            first=positions.get(span.get('first_source_id'));last=positions.get(span.get('last_source_id'))
            if first is None or last is None or first>last:raise ValueError('连续来源范围必须使用输入中顺序正确的真实首尾编号')
            inherited.extend(ordered[first:last+1])
        part['source_ids']=list(dict.fromkeys(part.get('source_ids',[])+inherited))
        if sources and not set(part['source_ids'])<=positions.keys():raise ValueError('正文来源包含输入以外的编号；外部资料应使用正文引用链接')
        def resolve(match):
            src=media.get(match[1])
            if not src:raise ValueError('引用的原图编号没有可读取图片：'+match[1])
            return ']('+src+')'
        if isinstance(part.get('source_ids'),list):part['source_ids']=[sid for sid in part['source_ids']if sid not in blanks]
        if part.get('source_id')in blanks:part.pop('source_id',None);part.pop('source_span',None)
        part['markdown']=re.sub(r'\]\(source:([^\s)]+)\)',resolve,part['markdown'])
    if lesson:
        explanation=data.get('explanation')
        if not isinstance(explanation,dict):explanation={'parts':data.get('lesson_parts')}
        parts=explanation.get('parts')or explanation.get('blocks')
        if not isinstance(parts,list)or not parts:raise ValueError('没有收到讲解正文；已有输入和原始输出保留，不能把这一节记为完成')
        for i,part in enumerate(parts):
            if not isinstance(part,dict)or not _text(part.get('markdown')):raise ValueError('讲解第 '+str(i+1)+' 部分没有正文')
            part['id']=part_id(part,i)
            if not _text(part.get('heading')or part.get('reading_heading')):raise ValueError('讲解部分缺少小标题：'+part['id'])
            resolve_images(part)
        if len({p['id']for p in parts})!=len(parts):raise ValueError('讲解部分编号重复')
        if uses_current_contract(data.get('writing_version')):
            data['source_group_audit']=apply_group_coverage(plan,parts,sources)
        headings=[re.sub(r'[^\w\u3400-\u9fff]','',_text(p.get('heading')or p.get('reading_heading'))).casefold()for p in parts]
        if len(set(headings))!=len(headings):raise ValueError('讲解小标题重复，请把每一部分实际回答的疑问写清楚')
        narrative=data.get('narrative',explanation.get('narrative'))
        if narrative:explanation['narrative']=__import__('reader_sequence').validate_narrative(narrative,parts)
        if data.get('objectives'):explanation['objectives']=copy.deepcopy(data['objectives'])
        explanation['parts']=parts;explanation.pop('blocks',None);data['explanation']=explanation
        __import__('reader_sequence').validate_context(explanation,reading_context)
        if not __import__('reader_lesson_checks').requirements(sources) and explanation.get('evidence',{}).get('facts'):
            explanation['evidence']['facts']=[]
            data.setdefault('script_processing',{})['unused_parameter_evidence_removed']=True
        from reader_prompts import WRITING_VERSION
        __import__('reader_lesson_checks').validate(explanation,sources,require_transitions=uses_current_contract(data.get('writing_version')),
                                                  require_continuation=data.get('writing_version')==WRITING_VERSION)
        covered={s for p in parts for s in p.get('source_ids',[])+([p['source_id']]if p.get('source_id')else[])}
        structural={s['id']for s in sources if s.get('kind')=='heading'or s.get('heading')or blank_source(s)}
        if set(required_source_ids)-covered-structural:raise ValueError('本节讲解还有原文未安排：'+','.join(sorted(set(required_source_ids)-covered-structural)))
        owners={p['id']:p for p in parts};answers={}
    else:
        expected={q['id']:q for q in questions};answers={}
        rows=data.get('answers')
        if not isinstance(rows,list):raise ValueError('没有收到 answers 列表')
        for answer in rows:
            if not isinstance(answer,dict)or answer.get('question_id')not in expected or answer['question_id']in answers:raise ValueError('回答编号未知或重复')
            q=expected[answer['question_id']]
            if answer.get('question_version')!=q.get('question_version',q.get('version',1)):raise ValueError('回答的问题版本与任务不一致')
            parts=answer.get('parts')or answer.get('answer_parts')
            if not isinstance(parts,list)or not parts:raise ValueError('回答缺少按子问组织的 parts：'+answer['question_id'])
            for i,part in enumerate(parts):
                if not isinstance(part,dict)or not _text(part.get('markdown')):raise ValueError('子问没有回答正文：'+answer['question_id'])
                part['id']=part_id(part,i);span=re.sub(r'\s+','',_text(part.get('question_span')))
                if not span or span not in re.sub(r'\s+','',q['text']):raise ValueError('子问没有逐字对应用户问题：'+answer['question_id'])
                resolve_images(part)
            if len({p['id']for p in parts})!=len(parts):raise ValueError('同一回答的部分编号重复')
            answer['parts']=parts;answer['framework_version']=VERSION;answer['plan']=plan;answer.pop('answer_parts',None);answers[answer['question_id']]=answer
        if set(answers)!=set(expected):raise ValueError('有问题尚未回答：'+','.join(sorted(set(expected)-set(answers))))
        owners={}
    all_parts=parts if lesson else [p for a in answers.values()for p in a['parts']]
    used={uid for part in all_parts for uid in part.get('unit_ids',[])}
    known={u['id']for u in plan['units']}
    covered=set(used)
    if not lesson:
        # units 可以按任意顺序提交，依赖先后由 order 确定。
        dependencies={u['id']:u['needs'] for u in plan['units']}
        for uid in reversed(plan['order']):
            if uid in covered:
                covered.update(need for need in dependencies[uid] if need in known)
    if covered!=known:raise ValueError('plan 中安排的内容与实际正文没有逐项对应')
    if exclusive_practice(data.get('writing_version')):
        exercise_owners = ({(None, part) for part in owners} if lesson else
                           {(qid, p['id']) for qid, answer in answers.items() for p in answer['parts']})
        issues = practice_issues(data.get('quizzes'), exercise_owners, lesson=lesson)
        if issues:
            raise ValueError('\n'.join(issues))
        for index,asset in enumerate(data.get('generated_assets',[])):
            qids=asset.get('question_ids',[]) if isinstance(asset,dict) else None
            qid=qids[0] if isinstance(qids,list) and len(qids)==1 else None
            owner=(qid,asset.get('after_part_id')) if isinstance(asset,dict) else ()
            if (lesson and qids!=[]) or (not lesson and qid is None) or owner not in exercise_owners:
                raise ValueError(f'/generated_assets/{index} 需要指定一处真实正文及其所属回答或章节')
    if not isinstance(data.get('terms'),list)or not isinstance(data.get('quizzes'),list)or not data['quizzes']:raise ValueError('同次写作需要返回术语列表和练习，不能把空练习记为完成')
    from reader_prompts import ASSET_CHECKS_VERSION, PAST_WRITING_VERSIONS, WRITING_VERSION
    asset_versions = PAST_WRITING_VERSIONS[PAST_WRITING_VERSIONS.index(ASSET_CHECKS_VERSION):]
    current=data.get('writing_version') in (*asset_versions, WRITING_VERSION)
    for index,term in enumerate(data['terms']):
        if not isinstance(term,dict):raise ValueError('术语需要使用对象')
        if not term.get('name')and term.get('chinese')and term.get('english'):
            term['name']=(term.get('abbreviation','').strip()+' 'if term.get('abbreviation')else'')+term['chinese'].strip()+'（'+term['english'].strip()+'）'
        if term.get('action')=='revise' and term.get('term_id'):
            fields=('professional_definition','intuitive_explanation')
            if any(_text(term.get(key)) for key in fields):
                # 只修一段时沿用另一段已保存的解释，空字段不参与更新。
                for key in fields:
                    if term.get(key) is None or isinstance(term[key],str) and not term[key].strip():
                        term.pop(key,None)
        if current and not lesson:
            qids=term.get('question_ids')
            if not isinstance(qids,list) or not qids or any(not isinstance(qid,str) or qid not in answers for qid in qids):
                raise ValueError(f'/terms/{index}/question_ids 必须至少关联本组的一个真实问题；没有关联的术语无法显示在术语表中')
    if current and registered_terms is not None:
        __import__('reader_term_quality').validate_asset_terms(data['terms'],registered_terms)
    elif uses_current_contract(data.get('writing_version')):
        __import__('reader_term_quality').validate_new_terms(data['terms'])
    if current:
        __import__('reader_media').validate_generated_diagrams(all_parts,vertical=data.get('writing_version')==WRITING_VERSION)
    from reader_contract import normalize_quiz,option_explanations
    if check_practice_mix:
        __import__('reader_practice_policy').validate_practice(
            data['quizzes'],practice_policy,current_writing=uses_current_contract(data.get('writing_version')))
    quiz_problems=[]
    for quiz_index,quiz in enumerate(data['quizzes']):
        try:
            if quiz.get('type')=='numeric'and isinstance(quiz.get('unit_aliases'),list):
                quiz['unit_aliases']={row['unit']:row['multiplier']for row in quiz['unit_aliases']}
            checked=normalize_quiz(quiz)
            if not option_explanations(checked):
                raise ValueError('练习缺少逐项解析')
            data['quizzes'][quiz_index]=checked
        except (ValueError,KeyError,TypeError,AttributeError) as error:
            quiz_problems.append(f'/quizzes/{quiz_index} {error}')
    if quiz_problems:
        raise ValueError('\n'.join(quiz_problems))
    for quiz in data['quizzes']:
        pos=quiz.get('placement')
        if not isinstance(pos,dict):raise ValueError('练习需要声明在正文中的插入位置')
        if lesson:
            if pos.get('after_unit_id'):
                matches=[p['id']for p in parts if pos['after_unit_id']in p.get('unit_ids',[])]
                if matches:pos['after_part_id']=matches[-1]
            if pos.get('kind')!='lesson'or pos.get('after_part_id')not in owners:raise ValueError('练习位置不属于本节讲解')
        else:
            answer=answers.get(pos.get('question_id'))
            if pos.get('kind')!='answer'or not answer or pos.get('after_part_id')not in {p['id']for p in answer['parts']}:raise ValueError('练习位置不属于本轮回答')
            qids=quiz.get('question_ids',[])
            if not isinstance(qids,list)or any(qid not in answers for qid in qids):raise ValueError('练习关联到本组以外的问题')
            quiz['question_ids']=[pos['question_id']] if exclusive_practice(data.get('writing_version')) else list(dict.fromkeys(qids+[pos['question_id']]))
    if lesson:
        if data.get('objectives'):__import__('reader_sequence').validate_objectives(data['objectives'],parts,data['quizzes'])
        practiced={q['placement']['after_part_id']for q in data['quizzes']}
        missing=set(owners)-practiced
        if missing:raise ValueError('每个讲解片段至少需要一道就地练习；以下片段缺题：'+','.join(sorted(missing)))
    duplicates=[]
    for part in (parts if lesson else [p for a in answers.values()for p in a['parts']]):
        clean,removed=__import__('reader_practice_text').clean(part['markdown'],data['quizzes'])
        if removed:
            if not clean:raise ValueError('本部分只有练习，没有讲解正文：'+part['id'])
            part['markdown']=clean;duplicates.extend({'part_id':part['id'],'quiz':q}for q in removed)
    if duplicates:data['script_processing']={'duplicate_practice_text_removed':duplicates,'quiz_records_unchanged':True}
    return data

def practice_placements(state,material):
    """把旧题关联转换成同一位置结构；只计算展示，不写回数据库。"""
    questions={q['id']:q for r in state.get('rounds',[])for q in r['questions']if not q.get('deleted_at')}
    passages={p['id']:p for p in material.get('passages',[])};explanations={e['id']:e for e in material.get('explanations',[])}
    nodes={n['id']:n for n in material.get('learning',{}).get('nodes',[])};result={'lesson':{},'answers':{}};unplaced=[]
    def add(qid,part,quiz):
        ids=result['answers'].setdefault(qid,{}).setdefault(part,[])
        if quiz not in ids:ids.append(quiz)
    from reader_contract import current_quizzes
    active = current_quizzes(state.get('quizzes', []))
    for quiz in active:
        pos=quiz.get('placement')or{};qid=pos.get('question_id');pid=pos.get('after_passage_id');placed=False
        if pos.get('kind')=='answer'and qid in questions:
            parts=questions[qid].get('answer_parts')or[];part=pos.get('after_part_id')or'__answer__'
            if part=='__answer__'or part in {p.get('id')or'ap'+str(i+1)for i,p in enumerate(parts)}:add(qid,part,quiz['id']);placed=True
        elif pos.get('kind')=='lesson'and pid in passages:result['lesson'].setdefault(pid,[]).append(quiz['id']);placed=True
        if placed:continue
        qids=[qid for qid in quiz.get('question_ids',[])if qid in questions]
        if qids:
            # 一道共享题默认只插一次，其余关联仍可在右侧汇总查到。
            qid=qids[0];parts=questions[qid].get('answer_parts')or[];part=parts[-1].get('id')or'ap'+str(len(parts))if parts else'__answer__';add(qid,part,quiz['id']);continue
        eids=list(quiz.get('explanation_ids',[]))
        for nid in quiz.get('learning_node_ids',[]):eids.extend(nodes.get(nid,{}).get('explanation_ids',[]))
        candidates=[quiz.get('passage_id')]+[pid for eid in eids for pid in explanations.get(eid,{}).get('passage_ids',[])]
        candidates=[pid for pid in candidates if pid in passages and passages[pid].get('content_kind')=='explanation']
        if candidates:result['lesson'].setdefault(candidates[-1],[]).append(quiz['id'])
        else:unplaced.append(quiz['id'])
    # 旧框架里的题号随修订链找到当前题目，正文与练习栏继续共用同一份作答。
    successors={}
    for q in state.get('quizzes',[]):
        if q.get('revises_quiz_id'):successors.setdefault(q['revises_quiz_id'],[]).append(q['id'])
    explicit={q['id'] for q in active if (q.get('placement') or {}).get('kind')=='lesson'
              and q['placement'].get('after_passage_id') in passages}
    __import__('reader_framework_legacy').practice_overlay(result,material,{q['id'] for q in active},successors,explicit)
    placed_ids={q for ids in result['lesson'].values()for q in ids}
    return {'version':VERSION,**result,'unplaced_quiz_ids':[q for q in unplaced if q not in placed_ids]}
