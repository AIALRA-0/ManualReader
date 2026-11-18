"""按已有 Part 和正文标题定位阅读题，不改正文、题目或作答。"""
import re
from copy import deepcopy

from reader_contract import current_quizzes
from reader_generation import practice_placements


def _body(row):
    return row.get('display_markdown') or row.get('zh') or row.get('en') or ''


def _substantive(row):
    return not any(row.get(key) for key in ('framework_scaffold','progress_exempt','display_omit',
                                           'presentation_omit','chapter_ending_role')) and bool(_body(row).strip())


def lesson_parts(material, explanation):
    """优先使用已保存的 Part 边界；旧首稿按真实标题分组，连续正文保留到该 Part 末尾。"""
    eid = explanation['id']
    passages = {p['id']:p for p in material.get('passages',[])}
    ordered = [passages[pid] for pid in explanation.get('passage_ids',[]) if pid in passages]
    route = explanation.get('narrative',{}).get('flow') or []
    starts = {eid+'--'+row['part_id']:row for row in route if row.get('part_id')}
    starts = {pid:row for pid,row in starts.items() if pid in passages}
    if not starts:
        headings = []
        for row in ordered:
            if not _substantive(row):
                continue
            match = re.match(r'^\s{0,3}(#{1,6})[ \t]+([^\n]+)',_body(row))
            if match:
                headings.append((row['id'],len(match[1]),match[2].strip().rstrip('#').strip()))
            elif row.get('reading_heading') or row.get('display_reading_heading'):
                headings.append((row['id'],2,row.get('display_reading_heading') or row['reading_heading']))
        if (len(headings)>1 and headings[0][1]<min(h[1] for h in headings[1:]) and
                sum(h[1]==headings[0][1] for h in headings)==1):
            headings.pop(0)
        level = min((h[1] for h in headings), default=0)
        starts = {pid:{'part_id':pid.removeprefix(eid+'--'),'label':title}
                  for pid,depth,title in headings if depth==level}
    result=[]
    current=None
    for row in ordered:
        if row['id'] in starts:
            item=starts[row['id']]
            current={'part_id':item['part_id'],'title':item.get('label') or explanation.get('title',''),
                     'passage_ids':[],'blocks':[]}
            result.append(current)
        if not _substantive(row):
            continue
        # Reader 保存的整章标题不单独成为需要出题的 Part。
        if current is None and starts:
            if re.match(r'^\s*#\s+[^\n]+\s*$',_body(row)):
                continue
        if current is None:
            current={'part_id':row['id'].removeprefix(eid+'--'),'title':explanation.get('title',''),
                     'passage_ids':[],'blocks':[]}
            result.append(current)
        current['passage_ids'].append(row['id'])
        current['blocks'].append({'block_id':row['id'].removeprefix(eid+'--'),
                                  'markdown':_body(row),'source_ids':[]})
    return [{**part,'end_passage_id':part['passage_ids'][-1]} for part in result if part['blocks']]


def coverage(material, state):
    """只计算题是否位于 Part 末尾；题意由生成模型依据对应正文负责。"""
    placements=practice_placements(state,material)['lesson']
    quizzes={q['id']:q for q in current_quizzes(state.get('quizzes',[]))}
    chapters=[]
    for explanation in material.get('explanations',[]):
        if explanation.get('deleted_at'):
            continue
        parts=lesson_parts(material,explanation)
        for part in parts:
            part['quiz_ids']=[qid for qid in placements.get(part['end_passage_id'],[]) if qid in quizzes]
            part['existing_quiz_ids']=list(dict.fromkeys(qid for pid in part['passage_ids']
                                  for qid in placements.get(pid,[]) if qid in quizzes))
        chapters.append({'explanation_id':explanation['id'],'title':explanation.get('title',''),
                         'mode':explanation.get('generation_mode') or 'NORMAL','parts':parts,
                         'covered':sum(bool(p['quiz_ids']) for p in parts),'total':len(parts)})
    owners={}
    for chapter in chapters:
        for part in chapter['parts']:
            for qid in part['existing_quiz_ids']:
                owners.setdefault(qid,set()).add((chapter['explanation_id'],part['part_id']))
    for chapter in chapters:
        for part in chapter['parts']:
            part['relocatable_quiz_ids']=[qid for qid in part['existing_quiz_ids'] if len(owners[qid])==1]
    total=sum(c['total'] for c in chapters)
    covered=sum(c['covered'] for c in chapters)
    return {'chapters':chapters,'total':total,'covered':covered,
            'percentage':f'{covered/total*100:.2f}' if total else '100.00'}


def relocate_existing(material, state):
    """旧题能唯一归属到一个 Part 时，只把原编号的展示位置移到该 Part 末尾。"""
    report=coverage(material,state)
    quizzes={q['id']:q for q in current_quizzes(state.get('quizzes',[]))}
    changes=[]
    for chapter in report['chapters']:
        for part in chapter['parts']:
            if part['quiz_ids']:
                continue
            for qid in part['relocatable_quiz_ids']:
                quiz=quizzes[qid]
                old=deepcopy(quiz.get('placement'))
                eid=chapter['explanation_id']
                quiz['placement']={'kind':'lesson','explanation_id':eid,
                    'after_part_id':part['end_passage_id'].removeprefix(eid+'--'),
                    'after_passage_id':part['end_passage_id']}
                changes.append({'quiz_id':qid,'explanation_id':eid,'part_id':part['part_id'],
                                'before':old,'after':deepcopy(quiz['placement'])})
    return changes


def install_missing(material, state, explanation_id, additions, stamp):
    """只追加当前仍缺的题，重放时不重复写入，不改变已有题和学习记录。"""
    from reader_teaching import append_assets
    explanation=next(e for e in material['explanations'] if e['id']==explanation_id)
    chapter=next(c for c in coverage(material,state)['chapters'] if c['explanation_id']==explanation_id)
    by_id={p['part_id']:p for p in chapter['parts']}
    rows=[]
    for question in additions:
        part=by_id.get(question.get('part_id'))
        if part and not part['quiz_ids']:
            rows.append({**question,'placement':{'kind':'lesson',
                         'after_part_id':part['end_passage_id'].removeprefix(explanation_id+'--')}})
            part['quiz_ids']=['being_added']
    if not rows:
        return {'saved':{'terms':[],'quizzes':[]},'issues':[],'complete':True}
    node_ids=explanation.get('learning_node_ids') or [explanation_id]
    return append_assets(state,{'terms':[],'quizzes':rows},node_ids[0],explanation_id,
                         explanation['passage_ids'],stamp,exclusive_owner=True)
