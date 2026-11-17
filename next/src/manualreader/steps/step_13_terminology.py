"""Prepare terminology independently of the chapter guidance."""

import re

from manualreader.stages.finish.content import _objects, _request, _strings
from manualreader.artifacts import read_json as read


EXPLICIT_NAME = re.compile(
    r'(?:(?<![A-Za-z0-9])(?P<abbreviation>[A-Z][A-Z0-9-]{1,14})[ \t]+)?'
    r'(?P<chinese>[\u3400-\u9fff]+)[ \t]*[（(][ \t]*'
    r"(?P<english>[A-Za-z][A-Za-z0-9 \t.'’&,/–-]*?)[ \t]*[）)]"
)
MARKDOWN_LITERAL = re.compile(r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`')


def explicit_terms(blocks):
    names = {}
    for block in blocks:
        for match in EXPLICIT_NAME.finditer(MARKDOWN_LITERAL.sub(' ',block['markdown'])):
            if len(match['english'].strip())<2:
                continue
            entry={**match.groupdict(),'block_ids':[],'evidence':match[0]}
            identity=(entry['chinese'],entry['english'].casefold())
            saved=names.setdefault(identity,entry)
            if entry['abbreviation'] and not saved['abbreviation']:
                saved.update(abbreviation=entry['abbreviation'],evidence=entry['evidence'])
            if block['block_id'] not in saved['block_ids']:
                saved['block_ids'].append(block['block_id'])
    return list(names.values())


def missing_explicit(names, terms):
    def matches(expected, term):
        name=EXPLICIT_NAME.fullmatch(term.get('name','').strip())
        if not name or name['english'].casefold()!=expected['english'].casefold():
            return False
        return expected['chinese'].endswith(name['chinese'])
    return [expected for expected in names if not any(matches(expected,t) for t in terms
                                                     if t.get('status')!='needs_review')]


def glossary(root, sample):
    file = root / 'inputs' / sample / 'reader-context.json'
    return read(file) if file.is_file() else {}


def planned_terms(goals):
    return [{'goal_id':f'{role}-{index}', 'topic':topic}
            for role in ('reader','teacher') for index,topic in enumerate(goals.get(role) or [])
            if isinstance(topic,str) and topic.strip()]


async def generate(root, sample, card, ask, structure=None, existing=None, remaining=None, mandatory=None):
    context = glossary(root, sample)
    if 'existing_terms' not in context:
        return {'terms': [], 'issues': [{'reason': 'glossary_unavailable'}]}
    frozen = root / 'upstream07' / sample
    body = (frozen / 'body.fused.md').read_text('utf-8')
    sources = read(root / 'inputs' / sample / 'source-index.json')
    known = context['existing_terms']
    blocks=[b for b in (structure or {}).get('blocks',[]) if b.get('surface')=='body']
    payload = {
        'mode': read(frozen / 'run.json')['mode'], 'body': body,
        'chapter': card.get('chapter', {}), 'goals': card.get('goals', {}),
        'glossary_snapshot_sha256': context.get('glossary_snapshot_sha256'),
        'glossary_snapshot_status': 'available_with_entries' if known else 'available_verified_empty',
        'glossary_entry_count': len(known),
        'existing_terms': [{**t, 'term_id': t['id']} for t in known],
        'source_ids': [row['source_id'] for row in sources['sources']],
        'part_ids': list(dict.fromkeys(b['part_id'] for b in blocks)),
        'block_ids': [b['block_id'] for b in blocks],
        'block_contexts': [{'block_id':b['block_id'],'part_id':b['part_id'],'text':b['markdown'][:360]} for b in blocks],
        'planned_term_goals':planned_terms(card.get('goals',{})) if remaining is None else remaining,
        'existing_chapter_candidates':existing or [],
        'required_bilingual_names':explicit_terms(blocks) if mandatory is None else mandatory,
    }

    raw, error = await _request(ask, 'F2', payload)
    return {**raw, 'known_terms': known, 'planned_term_goals':payload['planned_term_goals'],
            'issues': [{'reason': error}] if error else []}


def matching_term_blocks(term, blocks):
    literal = [term.get('name',''), *_strings(term.get('aliases'))]
    for value in list(literal):
        if match:=re.fullmatch(r'(.+?)\s*[（(]([^()（）]+)[）)]',value.strip()):
            literal.extend(x.strip() for x in match.groups() if len(x.strip())>1)
        if match:=EXPLICIT_NAME.fullmatch(value.strip()):
            literal.extend(x for x in (match['chinese'],match['english'],match['abbreviation']) if x)
    patterns=[re.compile(r'(?<![a-zA-Z0-9])'+re.escape(name)+r'(?![a-zA-Z0-9])',re.I)
              if name.isascii() else re.compile(re.escape(name),re.I)
              for name in dict.fromkeys(x.strip() for x in literal) if len(name)>1]
    return [b for b in blocks if any(p.search(b['markdown']) for p in patterns)]


def bind(result, structure):
    """Use the final Block IDs without asking the model to write the same terms twice."""
    known = {t['id']: t for t in result.get('known_terms', [])}
    blocks = [b for b in structure['blocks'] if b.get('surface') == 'body']
    goals={f"{g['role']}-{g['goal_index']}":g for g in structure.get('coverage',{}).get('goal_items',[])}
    reviews = _objects(result.get('planned_term_reviews'))
    source_ids = {e['source_id'] for e in structure['source_edges']}
    rows = []
    for row in _objects(result.get('terms')):
        reuse = known.get(row.get('term_id')) if row.get('action') == 'reuse_existing' else None
        name = (reuse or row).get('name', '').strip()
        if not name:
            continue
        matching = matching_term_blocks(reuse or row, blocks)
        related=[v for v in reviews if ({name,row.get('name')} & set(_strings(v.get('term_names'))))]
        wanted={x for v in related for x in goals.get(v.get('goal_id'),{}).get('block_ids',[])}
        if not matching:
            submitted=set(_strings(row.get('block_ids')))
            mapped=submitted & wanted
            if reuse and related and not mapped:
                mapped=submitted
            matching=[b for b in blocks if b['block_id'] in mapped]
        defined = row.get('professional_definition', '')
        intuition = row.get('intuitive_explanation', '')
        out = {**row, 'name': name, 'block_ids': [b['block_id'] for b in matching],
               'part_ids': list(dict.fromkeys(b['part_id'] for b in matching)),
               'source_ids': [i for i in row.get('source_ids', []) if i in source_ids],
               'reuse_id': reuse['id'] if reuse else None,
               'definition': (reuse or {}).get('professional_definition', defined),
               'intuition': (reuse or {}).get('intuitive_explanation', intuition)}
        out['status'] = 'reused' if reuse else (
            'candidate' if row.get('action') == 'new' and matching else 'needs_review')
        rows.append(out)
    return rows


async def repair_context_references(rows, ask):
    try:
        from reader_term_quality import INDEPENDENCE, inspect
    except ImportError:
        return rows
    selected=[]
    for row in rows:
        if row.get('status')!='candidate':
            continue
        fields={field:row.get(field,'') for field in ('professional_definition','intuitive_explanation')}
        if any(INDEPENDENCE.search(value) for value in fields.values()):
            selected.append({**{k:row[k] for k in ('name','aliases','source_ids','block_ids','part_ids')},**fields})
    if not selected:
        return rows
    reply,_=await _request(ask,'F2',{'mode':'repair_standalone_definitions','terms_to_repair':selected})
    original={t['name']:t for t in selected}
    accepted={}
    for term in _objects(reply.get('terms')):
        before=original.get(term.get('name'))
        if before is None:
            continue
        fields={field:term[field] if INDEPENDENCE.search(before[field]) else before[field]
                for field in ('professional_definition','intuitive_explanation')}
        if inspect(fields)['passed']:
            accepted[term['name']]=fields
    revised=[]
    for row in rows:
        if fields:=accepted.get(row['name']):
            row={**row,**fields,'definition':fields['professional_definition'],
                 'intuition':fields['intuitive_explanation']}
        revised.append(row)
    return revised


STANDALONE_REPAIR = (
    '\n本次使用 repair_standalone_definitions，只修复 terms_to_repair 列出的正式词条。'
    '保留原有定义、条件与直观解释中的事实。把「本章」「本图」等依赖前文的指代改为完整名称、已知电路或条件。'
    '已有合格段落原样复制，两个段落各自独立可读。返回原词名和原编号，禁止扩写新结论。'
    '这次不重新识别术语，planned_term_reviews 和 unresolved_terms 返回空数组。'
).encode()
