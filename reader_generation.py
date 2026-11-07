"""统一同次写作的结构与练习位置；旧记录通过读取适配，不改正文和阅读状态。"""
import copy,re

VERSION='reader-framework-v3'
PLAN_CONTRACT={'target':'当前要解决的事','direct_answer':'先交代的答案','units':[{'id':'u1','say':'实际要讲的内容，不写任务说明','needs':[],'source_refs':[{'source_id':'输入中的真实来源编号','quote':'原样表达'}]}],'order':['u1'],'stop_when':'已经讲清到哪里'}
PLACEMENT_CONTRACT={'lesson':{'kind':'lesson','after_part_id':'讲解parts中的id'},'answer':{'kind':'answer','question_id':'原问题编号','after_part_id':'该回答parts中的id'}}

def _text(value):return value.strip()if isinstance(value,str)else''
def blank_source(source):
    return source.get('kind')in ('paragraph','text','line','whitespace')and not any(_text(source.get(k))for k in ('en','zh','original','translation','excerpt','text'))and not(source.get('src')or source.get('media_items'))

def part_id(part,index):
    identity=part.get('id')or'ap'+str(index+1)
    if not isinstance(identity,str)or not re.fullmatch(r'[A-Za-z0-9_-]{1,70}',identity):raise ValueError('内容部分的 id 需要使用字母、数字、下划线或连字符')
    return identity

def display_parts(question):
    """旧分段补齐展示字段；不改回答正文、问题版本或已读哈希。"""
    rows=question.get('answer_parts')
    if not isinstance(rows,list):return question
    parts=[]
    for i,row in enumerate(rows):
        if not isinstance(row,dict):return question
        part=copy.deepcopy(row);part.setdefault('id','ap'+str(i+1));part.setdefault('unit_ids',[])
        part.setdefault('markdown','\n\n'.join(str(part.get(k)or'')for k in ('direct_answer','explanation')if part.get(k)))
        if not part['markdown']:return question
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
        if not _text(unit.get('say'))or not isinstance(unit.get('needs'),list)or not set(unit['needs'])<=seen:raise ValueError('每个内容单元需要具体说明，前置单元要先出现')
        refs=unit.get('source_refs',[])
        if not isinstance(refs,list):raise ValueError('source_refs 需要是列表')
        for ref in refs:
            if isinstance(ref,dict)and ref.get('source_id')in known and blank_source(known[ref['source_id']])and not _text(ref.get('quote')):continue
            if not isinstance(ref,dict)or not _text(ref.get('quote')):raise ValueError('来源引用需要保留原句')
            if known:
                source=known.get(ref.get('source_id'));quote=re.sub(r'\s+','',ref['quote'])
                if source is None or not any(quote in re.sub(r'\s+','',str(source.get(k)or''))for k in ('en','zh','original','translation','excerpt','text')):raise ValueError('plan 引用没有对应到输入原句：'+str(ref.get('source_id')))
        seen.add(identity)
    return copy.deepcopy(plan)

def normalize_output(value,questions=(),sources=(),lesson=False,required_source_ids=(),reading_context=None):
    """把演示和通用客户端装成同一份输出；原始模型文件由调用者另存。"""
    if not isinstance(value,dict):raise ValueError('写作结果需要是 JSON 对象')
    if lesson and not (value.get('framing')or value.get('explanation',{}).get('framing')):raise ValueError('本次新讲解需要章首承接、阅读流程、逐段前提与问题及章末回顾；请保留已有正文，只补缺失结构')
    data=__import__('reader_sequence').prepare_output(value)if lesson else copy.deepcopy(value);data['framework_version']=VERSION
    plan=validate_plan(data.get('plan'),sources);data['plan']=plan
    blanks={s['id']for s in sources if blank_source(s)}
    if blanks:
        # 原始空行仍留在材料中；它没有可讲解的文字，不作为正文定位锚点。
        for unit in plan['units']:unit['source_refs']=[r for r in unit.get('source_refs',[])if r.get('source_id')not in blanks]
        data['ignored_empty_source_ids']=sorted(blanks)
    media={s['id']:s.get('src')or(s.get('media')or{}).get('src')for s in sources}
    def resolve_images(part):
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
        headings=[re.sub(r'[^\w\u3400-\u9fff]','',_text(p.get('heading')or p.get('reading_heading'))).casefold()for p in parts]
        if len(set(headings))!=len(headings):raise ValueError('讲解小标题重复，请把每一部分实际回答的疑问写清楚')
        narrative=data.get('narrative',explanation.get('narrative'))
        if narrative:explanation['narrative']=__import__('reader_sequence').validate_narrative(narrative,parts)
        if data.get('objectives'):explanation['objectives']=copy.deepcopy(data['objectives'])
        explanation['parts']=parts;explanation.pop('blocks',None);data['explanation']=explanation
        __import__('reader_sequence').validate_context(explanation,reading_context)
        __import__('reader_lesson_checks').validate(explanation,sources,require_transitions=data.get('writing_version')==__import__('reader_prompts').WRITING_VERSION)
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
    used={uid for part in (parts if lesson else [p for a in answers.values()for p in a['parts']])for uid in part.get('unit_ids',[])}
    known={u['id']for u in plan['units']}
    if not used<=known or not known<=used:raise ValueError('plan 中安排的内容与实际正文没有逐项对应')
    if not isinstance(data.get('terms'),list)or not isinstance(data.get('quizzes'),list)or not data['quizzes']:raise ValueError('同次写作需要返回术语列表和练习，不能把空练习记为完成')
    for term in data['terms']:
        if not isinstance(term,dict):raise ValueError('术语需要使用对象')
        if not term.get('name')and term.get('chinese')and term.get('english'):
            term['name']=(term.get('abbreviation','').strip()+' 'if term.get('abbreviation')else'')+term['chinese'].strip()+'（'+term['english'].strip()+'）'
    from reader_contract import normalize_quiz
    data['quizzes']=[normalize_quiz(q)for q in data['quizzes']]
    for quiz in data['quizzes']:
        options=quiz.get('options');index=quiz.get('correct_index');pos=quiz.get('placement')
        if not _text(quiz.get('prompt'))or not _text(quiz.get('explanation'))or not isinstance(options,list)or not 2<=len(options)<=6 or not all(_text(o)for o in options)or len(set(options))!=len(options)or type(index)is not int or not 0<=index<len(options):raise ValueError('练习题目、选项或正确答案不完整')
        if not isinstance(quiz.get('option_explanations'),list)or len(quiz['option_explanations'])!=len(options)or not all(_text(x)for x in quiz['option_explanations']):raise ValueError('练习缺少逐项解析')
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
            quiz['question_ids']=list(dict.fromkeys(qids+[pos['question_id']]))
    if lesson:
        if data.get('objectives'):__import__('reader_sequence').validate_objectives(data['objectives'],parts,data['quizzes'])
        practiced={q['placement']['after_part_id']for q in data['quizzes']}
        missing=set(owners)-practiced
        if missing:raise ValueError('每个讲解片段至少需要一道就地练习；以下片段缺题：'+','.join(sorted(missing)))
    return data

def practice_placements(state,material):
    """把旧题关联转换成同一位置结构；只计算展示，不写回数据库。"""
    questions={q['id']:q for r in state.get('rounds',[])for q in r['questions']if not q.get('deleted_at')}
    passages={p['id']:p for p in material.get('passages',[])};explanations={e['id']:e for e in material.get('explanations',[])}
    nodes={n['id']:n for n in material.get('learning',{}).get('nodes',[])};result={'lesson':{},'answers':{}};unplaced=[]
    def add(qid,part,quiz):
        ids=result['answers'].setdefault(qid,{}).setdefault(part,[])
        if quiz not in ids:ids.append(quiz)
    replaced={q.get('revises_quiz_id')for q in state.get('quizzes',[])if q.get('revises_quiz_id')}
    for quiz in state.get('quizzes',[]):
        if quiz['id']in replaced or quiz.get('deleted_at'):continue
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
    __import__('reader_framework_legacy').practice_overlay(result,material,{q['id']for q in state.get('quizzes',[])if not q.get('deleted_at')and q['id']not in replaced},successors)
    placed_ids={q for ids in result['lesson'].values()for q in ids}
    return {'version':VERSION,**result,'unplaced_quiz_ids':[q for q in unplaced if q not in placed_ids]}
