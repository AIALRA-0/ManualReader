"""Prepare chapter navigation and endings without replacing chapter prose."""

from manualreader.stages.finish.content import _objects, _part_contents, _request, _section_state
from .step_13_terminology import glossary

RECAP_FIELDS = ('context', 'problem', 'claim', 'reason', 'evidence', 'implication')
LEGACY_FIELDS = ('background', 'question', 'conclusion', 'causes', 'evidence', 'application')


async def guidance(root, sample, structure, card, ask):
    state = _section_state(root, sample, structure, card)
    parts = _part_contents(structure, state)
    if not parts or state['mode'] == 'ANSWER':
        return {'guidance': {}, 'issues':[]}
    context = glossary(root, sample)
    neighbors = context.get('adjacent_chapters', {})
    before = neighbors.get('previous')
    after = neighbors.get('next')
    if state['mode'] == 'INTRO':
        before = None
    payload = {'mode':state['mode'], 'chapter_title':state['title'], 'parts':parts,
               'goals':card.get('goals',{}), 'previous_saved_chapter':before,
               'confirmed_next_chapter':after, 'confirmed_open_question':neighbors.get('open_question'),
               'progress':card.get('progress'), 'saved_chapters':neighbors.get('saved',[])}
    raw, error = await _request(ask, 'F1', payload)
    chosen = {r.get('part_id'):r for r in _objects(raw.get('reading_flow'))}
    recap = {r.get('part_id'):r for r in _objects(raw.get('part_recaps'))}
    route = [{'part_id':p['part_id'],'title':p['title'],'focus':chosen.get(p['part_id'],{}).get('focus','')}
             for p in parts]
    recaps = [recap[p['part_id']] for p in parts if p['part_id'] in recap]
    next_kind = 'next' if after else 'open' if neighbors.get('open_question') else 'complete'
    return {'guidance':{'schema_version':'chapter-guidance-v2','reading_flow':route,'part_recaps':recaps,
             'chapter_intro':raw.get('chapter_intro',''), 'chapter_synthesis':raw.get('chapter_synthesis',''),
             'previous_transition':raw.get('previous_transition','') if before else '',
             'next_transition':raw.get('next_transition','') if next_kind!='complete' else '',
             'ending_kind':next_kind,'uncertainties':raw.get('uncertainties',[])},
            'unresolved_part_ids':[p['part_id'] for p in parts if not all(
                recap.get(p['part_id'],{}).get(field) for field in RECAP_FIELDS)],
            'issues':[{'reason':error}] if error else []}


def recap_groups(guide, parts):
    recaps = {r['part_id']:r for r in guide.get('part_recaps',[])}
    for part in parts.values():
        row = recaps.get(part['part_id'])
        if not row:
            continue
        recap = {field:row.get(field) or row.get(alias) for field,alias in zip(RECAP_FIELDS,LEGACY_FIELDS)}
        available={field:text for field,text in recap.items() if isinstance(text,str) and text.strip()}
        if available:
            yield part['title'],available
