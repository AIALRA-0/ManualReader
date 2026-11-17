"""Single-pass content scope accounting; diagnostics are NOT a quality gate."""
from hashlib import sha256
import re


def _target_ids(record, anchors, patch_by_index):
    result = set()
    for aid in record.get('body_anchor_ids', []):
        if isinstance(aid,str) and aid in anchors:
            result.add(aid)
    for i in record.get('addition_indices', []):
        if type(i) is int and i in patch_by_index:
            result.add('overlay:'+patch_by_index[i])
    return sorted(result)


def _split_raw_question(question):
    """Coarse punctuation-based anchors, never a semantic answer key."""
    result=[]
    for match in re.finditer(r'[^？?！!；;]+(?:[？?！!；;]+|$)',question):
        segment=match.group()
        quote=segment.strip()
        if quote:
            start=match.start()+len(segment)-len(segment.lstrip())
            result.append({'id':f'qraw-{start}-{sha256(quote.encode()).hexdigest()[:10]}',
                           'start':start,'end':start+len(quote),'text':quote})
    return result or ([{'id':'qraw-0-'+sha256(question.encode()).hexdigest()[:10],
                         'start':0,'end':len(question),'text':question}] if question.strip() else [])


def resolve_raw_fragments(raw_units, question_rows):
    """Navigation-only reuse of already answered spans; does not add semantic subquestions."""
    raw_check=[]
    # A raw punctuation segment may be only a list item, setup sentence, or
    # difficulty feedback. Reuse a *real answered subquestion's* anchor as its
    # navigational context; never add a new semantic question or model call.
    linked_questions=[p for p in question_rows if p['covered']]
    for part in raw_units:
        overlapping=[p for p in linked_questions if p['start']<part['end'] and p['end']>part['start']]
        if overlapping:
            targets=sorted({aid for p in overlapping for aid in p['target_ids']})
            raw_check.append({**part,'covered':True,'target_ids':targets,
                              'relation_type':'direct_question_overlap',
                              'navigation_only':False,
                              'attached_to_question_span_id':overlapping[0]['question_span_id']})
            continue
        preceding=[p for p in linked_questions if p['end']<=part['start']]
        following=[p for p in linked_questions if p['start']>=part['end']]
        if preceding:
            parent=max(preceding,key=lambda x:(x['end'],x['start']))
            relation='absorbed_to_previous_answered_question'
        elif following:
            parent=min(following,key=lambda x:(x['start'],x['end']))
            relation='absorbed_to_next_answered_question'
        else:
            parent=None
            relation='unlinked_without_answered_subquestion'
        raw_check.append({**part,'covered':bool(parent),
                          'target_ids':parent['target_ids'] if parent else [],
                          'relation_type':relation,
                          'navigation_only':bool(parent),
                          'substantive_coverage':False,
                          'attached_to_question_span_id':parent['question_span_id'] if parent else None})
    return raw_check


def analyze(card, mode, anchors, reply, patch_by_index):
    """Compute actual target links; unmatched entries remain visible, no reruns or blocks."""
    allowed={x['anchor_id'] for x in anchors}
    goals=(card.get('goals') or {})
    idx={}
    for record in reply.get('goal_links',[]):
        if not isinstance(record,dict):
            continue
        role,i=record.get('role'),record.get('goal_index')
        if role in ('reader','teacher') and type(i) is int and 0<=i<len(goals.get(role,[])):
            idx.setdefault((role,i),set()).update(_target_ids(record,allowed,patch_by_index))
    goal_rows=[]
    for role in ('reader','teacher'):
        for i,topic in enumerate(goals.get(role,[])):
            targets=sorted(idx.get((role,i),set()))
            goal_rows.append({'role':role,'goal_index':i,'topic':topic,
                              'target_ids':targets,'covered':bool(targets)})
    question=(card.get('question') or {}).get('text','') if mode=='ANSWER' else ''
    raw_units=_split_raw_question(question)
    question_rows=[]
    seen=set()
    for item in reply.get('question_links',[]):
        if mode!='ANSWER' or not isinstance(item,dict):
            continue
        quote=item.get('question_quote')
        if not isinstance(quote,str) or not quote.strip() or quote not in question:
            continue
        start=question.find(quote)
        # Ambiguous repeated substring must not silently become an invented span.
        if question.find(quote,start+1)>=0 or (start,quote) in seen:
            continue
        seen.add((start,quote))
        targets=_target_ids(item,allowed,patch_by_index)
        question_rows.append({'question_span_id':f'qspan-{start}-{sha256(quote.encode()).hexdigest()[:10]}',
                              'start':start,'end':start+len(quote),'question_quote':quote,
                              'target_ids':targets,'covered':bool(targets)})
    raw_check=resolve_raw_fragments(raw_units,question_rows)
    def measure(rows):
        done=sum(bool(x['covered']) for x in rows)
        return {'total':len(rows),'covered':done,
                'coverage_pct':round(100*done/len(rows),2) if rows else 0.0}
    goals_m=measure(goal_rows)
    question_m=measure(question_rows)
    units_m=measure(raw_check)
    if mode=='ANSWER' and not question_rows:
        question_m['coverage_pct']=0.0
    return {'mode':mode,'goal_items':goal_rows,'goal_coverage':goals_m,
            'question_items':question_rows,'question_item_coverage':question_m,
            'question_raw_units':raw_check,'question_raw_unit_coverage':units_m,
            'all_goals_linked':goals_m['covered']==goals_m['total'],
            'all_question_items_linked':bool(question_rows) and question_m['covered']==question_m['total']
                                        and units_m['covered']==units_m['total'],
            'measurement':'draft_or_overlay_reference; not a self-review score',
            'raw_fragments_are_navigational_not_independent_questions':True,
            'extra_model_calls':0}
