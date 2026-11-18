"""给旧练习登记唯一归属，原题、历史作答和笔记继续沿用原编号。"""
from collections import defaultdict
from copy import deepcopy

from reader_contract import current_quizzes, same_exercise
from reader_generation import part_id, practice_placements, practice_prompt_key
from reader_questions import answer_body, has_current_answer, infer_learning_node
from reader_term_quality import term_matcher


def teaching_parts(state, material):
    """从现有章节和回答取真实正文部分；章首、流程图和回顾无需各出一题。"""
    owners = {}
    passages = {row['id']: row for row in material['passages']}
    nodes=material.get('learning',{}).get('nodes',[])
    for explanation in material.get('explanations', []):
        eid = explanation['id']
        lesson_nodes=explanation.get('learning_node_ids') or [node['id'] for node in nodes if eid in node.get('explanation_ids',[])]
        route = explanation.get('narrative', {}).get('flow', [])
        ids = [eid + '--' + row['part_id'] for row in route]
        if not ids:
            ids = explanation.get('passage_ids', [])
        for pid in ids:
            row = passages.get(pid, {})
            if row.get('framework_scaffold') or row.get('progress_exempt') or row.get('display_omit'):
                continue
            if row.get('chapter_ending_role') or row.get('presentation_omit'):
                continue
            body = row.get('display_markdown') or row.get('zh') or row.get('en') or ''
            if not body.strip() or pid.rsplit('--', 1)[-1] in ('route', 'opening', 'opening-question', 'closing'):
                continue
            owner = ('lesson', eid, pid.removeprefix(eid + '--'))
            owners[owner] = {'markdown':body, 'heading':row.get('display_reading_heading') or row.get('reading_heading') or '',
                             'passage_id':pid, 'learning_node_ids':lesson_nodes}
    if material.get('reader_mode')=='authored':
        sections={section:node for node in nodes for section in node.get('legacy_section_ids',[])}
        for pid,row in passages.items():
            node=sections.get(row.get('section_id'))
            if not node or row.get('file_id')!='primary' or row.get('explanation_id') or row.get('heading'):
                continue
            if row.get('display_omit') or row.get('progress_exempt') or row.get('framework_scaffold'):
                continue
            body=row.get('display_markdown') or row.get('zh') or row.get('en') or ''
            if body.strip():
                owners[('lesson',node['id'],pid)]={'markdown':body,'heading':node['title'],
                                                  'passage_id':pid,'learning_node_ids':[node['id']]}
    for row in state['rounds']:
        for question in row['questions']:
            if not has_current_answer(question):
                continue
            qid = question['id']
            node_id=infer_learning_node(material,question) if nodes else question.get('learning_node_id')
            parts = question.get('answer_parts') or [{'id':'__answer__','markdown':answer_body(question['answer'], {})}]
            for index, part in enumerate(parts):
                body = part.get('markdown') or '\n\n'.join(part.get(key) or '' for key in ('direct_answer','explanation'))
                if not body.strip():
                    continue
                owners[('answer', qid, part_id(part,index))] = {
                    'markdown':body,'heading':part.get('question_span') or question['text'],
                    'passage_id':question['passage_id'],'round_id':row['id'],
                    'learning_node_ids':[node_id] if node_id else []}
    return owners


def reconcile(state, material):
    """只依据明确位置修复旧记录，同时列出仍缺练习的教学部分。"""
    owners=teaching_parts(state,material)
    answered={key[1] for key in owners if key[0]=='answer'}
    placements=practice_placements(state,material)
    by_passage={part['passage_id']:owner for owner,part in owners.items() if owner[0]=='lesson'}
    inferred={}
    for pid, ids in placements['lesson'].items():
        if pid in by_passage:
            inferred.update((qid,by_passage[pid]) for qid in ids)
    for qid, parts in placements['answers'].items():
        for pid, ids in parts.items():
            owner=('answer',qid,pid)
            if owner in owners:
                inferred.update((identity,owner) for identity in ids)
    history=state.get('practice_book',{}).get('attempts',[])
    attempt_ids={item.get('quiz_id') for item in history}
    choices=defaultdict(list)
    for quiz in current_quizzes(state.get('quizzes',[])):
        position=quiz.get('placement') or {}
        owner=(position.get('kind'),position.get('question_id') or position.get('explanation_id'),position.get('after_part_id'))
        if owner not in owners:
            if position.get('kind')=='answer':
                if position.get('question_id') in answered:
                    quiz['practice_archived']='answer_part_removed'
                continue
            owner=inferred.get(quiz['id'])
        if owner not in owners and not quiz.get('question_ids'):
            pid=position.get('after_passage_id') or quiz.get('passage_id')
            owner=by_passage.get(pid)
        if owner not in owners:
            continue
        correctly_assigned = quiz.get('question_ids',[])==[owner[1]] if owner[0]=='answer' else not quiz.get('question_ids')
        choices[owner].append((quiz,correctly_assigned))
    chosen={}
    for owner, rows in choices.items():
        kind,identity,part_id_=owner
        if kind=='answer':
            # 旧写回会在别的轮次复制同一道题。优先保留原问题轮次的那份。
            rows.sort(key=lambda item:(not item[1],not (item[0].get('selected') is not None or item[0]['id'] in attempt_ids)))
            if not rows[0][1] and identity not in rows[0][0].get('question_ids',[]):
                continue
        else:
            rows.sort(key=lambda item: (item[0].get('selected') is None and item[0]['id'] not in attempt_ids))
        primary=rows[0][0]
        if kind=='answer':
            placement={'kind':kind,'question_id':identity,'after_part_id':part_id_}
            qids=[identity]
        else:
            placement={'kind':kind,'explanation_id':identity,'after_part_id':part_id_,
                       'after_passage_id':owners[owner]['passage_id']}
            qids=primary.get('question_ids',[])
        if primary.get('placement')!=placement or primary.get('question_ids')!=qids:
            primary.setdefault('legacy_practice_placement',{'placement':deepcopy(primary.get('placement')),
                                                           'question_ids':deepcopy(primary.get('question_ids',[]))})
            primary.update(placement=placement,question_ids=qids)
        chosen[owner]=primary
        for quiz, _ in rows[1:]:
            if same_exercise(primary,quiz):
                quiz['archived_duplicate_of']=primary['id']
            else:
                quiz['practice_archived']='previous_exercise_at_same_part'
    # 不猜测相同题干属于哪个部分；仍有真正重复时只保留第一处并把其他位置列为待补。
    prompts={}
    for owner,quiz in list(chosen.items()):
        stem=practice_prompt_key(quiz)
        key=(owner[0],owner[1],stem)
        if stem and key in prompts and same_exercise(quiz,prompts[key]):
            quiz['archived_duplicate_of']=prompts[key]['id']
            chosen.pop(owner)
        elif stem:
            prompts[key]=quiz
    return owners,chosen


def associate_terms(state, *, excluded=()):
    """把正文中唯一匹配的术语挂回原问题；已有不同关联留给有来源证据的修复。"""
    terms=state.get('terms',[])
    mentions=term_matcher(terms)
    from reader_term_names import term_name
    # 读回结果隐藏没有中英文全称的旧条目，补关联也使用同一条规则。
    invalid={'missing_english_name','missing_chinese_name','chinese_inside_english'}
    hidden={term['id'] for term in terms if invalid.intersection(term_name(term)['issues'])}
    count=0
    for row in state['rounds']:
        for question in row['questions']:
            if not has_current_answer(question):
                continue
            body='\n\n'.join(part.get('markdown','') for part in question.get('answer_parts',[]))
            body=body or answer_body(question['answer'],{})
            for term in mentions(question['text']+'\n\n'+body):
                if term['id'] in hidden or (question['id'],term['id']) in excluded:
                    continue
                ids=question.setdefault('term_ids',[])
                linked=term.setdefault('question_ids',[])
                if term['id'] not in ids:
                    ids.append(term['id'])
                    count+=1
                if question['id'] not in linked:
                    linked.append(question['id'])
    return count
