"""Reuse the Reader's question types, answers and placement contract."""

from hashlib import sha256
from math import ceil
import re

from manualreader.stages.finish.content import _request, _section_state, _part_contents, _objects
from manualreader.stages.finish.contracts import QUESTION
from .step_13_terminology import glossary


def native_question(row):
    from reader_contract import normalize_quiz, option_explanations
    value = dict(row)
    if isinstance(value.get('unit_aliases'), list):
        value['unit_aliases'] = {x['unit']:x['multiplier'] for x in value['unit_aliases']}
    if value.get('type')=='numeric' and value.get('calculation'):
        from objective_grading import arithmetic, calculation
        expected=arithmetic(value['expected'])
        tolerance=arithmetic(value.get('tolerance') or '0')
        original=value['calculation']
        try:
            calculation(original,tolerance)
        except ValueError:
            cleaned=original.replace('\\(',' ').replace('\\)',' ').replace('\\times','*').replace('\\cdot','*')
            cleaned=cleaned.replace('×','*').replace('÷','/').replace('−','-')
            cleaned=re.sub(r'(?<=\d)%(?=\s*[/+-])','/100',cleaned)
            cleaned=re.sub(r'(?<=\d|\))(?=\()', '*',cleaned)
            candidates=[x.strip() for x in re.findall(r'[0-9().+*/-]+(?:[ \t]+[0-9().+*/-]+)*',cleaned)]
            for candidate in sorted(set(candidates),key=len,reverse=True):
                if not re.search(r'\d\s*[+*/-]\s*[-(\d]',candidate):
                    continue
                try:
                    computed=calculation(candidate,tolerance)
                except ValueError:
                    continue
                if abs(computed-expected)<=tolerance:
                    value['calculation']=candidate
                    value['explanation']+='\n\n'+original
                    break
    value = normalize_quiz(value)
    if value.get('type') in ('choice', 'true_false') and not option_explanations(value):
        raise ValueError('这道题缺少与每个原选项对应的解析')
    return value


async def practice(root, sample, structure, card, ask, *, pinned=()):
    state = _section_state(root, sample, structure, card)
    parts = _part_contents(structure, state)
    if not parts:
        return {'questions': [], 'issues': []}
    snapshot = glossary(root, sample)
    known = snapshot.get('existing_quizzes', [])
    found = {}
    issues = []
    part_map = {part['part_id']: part for part in parts}

    def accept(row, part, existing=False):
        part_id = part['part_id']
        placement = {'kind': 'lesson', 'after_part_id': part_id}
        value = native_question({**row, 'placement':placement})
        identity = row.get('id') if existing else 'quiz-' + sha256(
            (part_id+'\0'+value['prompt']).encode()).hexdigest()[:22]
        valid_blocks = {b['block_id'] for b in part['blocks']}
        valid_sources = {sid for b in part['blocks'] for sid in b['source_ids']}
        return {**value, 'id':identity, 'part_id':part_id,
                'block_ids':[bid for bid in row.get('block_ids', []) if bid in valid_blocks],
                'source_ids':[sid for sid in row.get('source_ids', []) if sid in valid_sources]}

    for row in [*pinned,*known]:
        owner = row.get('part_id') or row.get('placement', {}).get('after_part_id')
        if (owner in part_map and owner not in found and not row.get('deleted_at')
                and (row in pinned or row.get('body_fused_sha')==state['body_fused_sha'])):
            try:
                found[owner] = accept(row, part_map[owner], True)
                if state['mode'] != 'NORMAL':
                    break
            except (ValueError, KeyError, TypeError) as exc:
                issues.append({'part_id':owner,'reason':str(exc)})
    missing = [p for p in parts if p['part_id'] not in found]
    if missing and (state['mode'] == 'NORMAL' or not found):
        planned_total=len(parts) if state['mode']=='NORMAL' else 1
        choices=sum(row['type']=='choice' for row in found.values())
        objective=sum(row['type']!='recall' for row in found.values())
        payload = {'mode':state['mode'], 'chapter_title':state['title'], 'parts':missing,
                   'known_terms':snapshot.get('existing_terms', []),
                   'existing_quiz_ids':[{k:q.get(k) for k in ('id','placement','selected')} for q in known],
                   'native_question_contract':QUESTION,
                   'available_code_runners':snapshot.get('available_code_runners', []),
                   'practice_mix':{'requested_total':planned_total-len(found),
                       'minimum_choice':max(0,planned_total//2+1-choices) if planned_total>1 else 0,
                       'minimum_objective':max(0,ceil(planned_total*.75)-objective),
                       'objective_types':['choice','true_false','numeric','ordering','code']}}
        if state['mode'] != 'NORMAL':
            payload['requested_exercise_count'] = 1
        raw, error = await _request(ask, 'F3', payload)
        if error:
            issues.append({'reason':error})
        for row in _objects(raw.get('questions')):
            owner = row.get('placement', {}).get('after_part_id')
            if owner not in part_map or owner in found:
                continue
            if state['mode'] != 'NORMAL' and found:
                break
            if row.get('type') == 'code' and row.get('runner_id') not in payload['available_code_runners']:
                issues.append({'part_id':owner,'reason':'代码运行器未通过本轮环境验证'})
                continue
            try:
                found[owner] = accept(row, part_map[owner])
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                issues.append({'part_id':owner,'reason':str(exc)})
    questions=[found[p['part_id']] for p in parts if p['part_id'] in found]
    if questions:
        from reader_practice_policy import validate_practice
        try:
            validate_practice(questions,current_writing=True)
        except ValueError as exc:
            issues.append({'reason':str(exc)})
    return {'questions':questions,
            'unresolved_part_ids':[p['part_id'] for p in parts if p['part_id'] not in found], 'issues':issues}
