"""STEP-16 formats generated text while preserving original bytes and stable IDs."""
import asyncio
from collections import defaultdict
from hashlib import sha256
import re
from pathlib import Path

from ..step07.fusion import layer, remap_span
from ..step07.resources import scan_batch
from .structure import OFFSET_UNIT, identity, read


HAN = r'\u3400-\u9fff\uf900-\ufaff'
SEPARATORS = '；;'
STRUCTURAL = {'blockquote_open', 'html_block', 'code_block', 'fence'}
NATURAL_KEYS = {
    'application', 'assumptions_and_limits', 'background', 'cause', 'causes',
    'chapter_intro', 'chapter_outro', 'composition', 'composition_or_steps', 'conclusion',
    'conditions', 'definition', 'description', 'evidence', 'explanation', 'focus', 'intuition',
    'meaning', 'synthesis', 'summary', 'next_transition', 'options', 'overview', 'prompt', 'question', 'relations', 'title', 'translated',
}
LITERALS = re.compile(
    r'\x60+(?!\x60)[^\n]*?(?<!\x60)\x60+(?!\x60)'
    r'|\[[^\n\]]+\]\([^\n)]*\)|<[^>\n]+>'
    r'|(?:[A-Za-z][A-Za-z\d+.-]*://|(?:www\.)|[A-Za-z]:[\\/]|(?:^|(?<=\s))[.]{0,2}[\\/])'
    r'[^\s<>，。；、！？：]+'
    r'|(?:「[^」\n]*」|『[^』\n]*』|“[^”\n]*”|‘[^’\n]*’|"[^"\n]*")'
)
SPACING = re.compile(
    rf'(?<=[{HAN}])(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])(?=[{HAN}])'
)
LINE_MARKER = re.compile(r'^[ \t]{0,3}(#{1,6})(?=[^\s#])', re.M)
CHINESE_SEPARATOR = re.compile(r'[。；](?=[ \t]*(?:\r?\n|$))|。')


def sha(text):
    return sha256(text.encode()).hexdigest()


def locked(start, end, spans):
    if start == end:
        return any(a < start < b for a, b in spans)
    return any(start < b and end > a for a, b in spans)


def protection(text, scan, resources=()):
    spans = [(row['start'], row['end']) for row in scan['resources']]
    spans.extend((row['start'], row['end']) for row in resources)
    spans.extend((row['start'], row['end']) for row in scan['blocks'] if row['type'] in STRUCTURAL)
    spans.extend(m.span() for m in LITERALS.finditer(text))
    return spans


def trim_span(text, start, end):
    while start < end and text[start].isspace():
        start += 1
    while start < end and text[end - 1].isspace():
        end -= 1
    return [start, end]


def find_candidates(key, text, scan, spans):
    for row in scan['blocks']:
        if row['type'] != 'paragraph_open':
            continue
        start, end = row['start'], row['end']
        raw = text[start:end]
        if '\n' in raw.strip() or '\r' in raw.strip():
            continue
        markers = [(i, c) for i, c in enumerate(raw)
                   if c in ':：' + SEPARATORS and not locked(start + i, start + i + 1, spans)]
        for point, mark in markers:
            if mark not in ':：' or not 1 <= point <= 110:
                continue
            separators = [i for i, c in markers if c in SEPARATORS and point < i < len(raw.rstrip()) - 1]
            if not separators:
                continue
            optional = [trim_span(raw, a + 1, b)
                        for a, b in zip([point, *separators], [*separators, len(raw)], strict=True)]
            if any(a == b for a, b in optional):
                continue
            candidate_id = identity('format', key, sha(raw), start, point)
            yield {
                'candidate_id': candidate_id, 'original': raw, 'colon_offset': point,
                'separator_offsets': separators, 'possible_item_spans': [{'start': a, 'end': b} for a, b in optional],
                'before': text[max(0, start - 160):start].strip(),
                'after': text[end:end + 160].strip(), '_start': start,
            }
            break


def operation(text, start, end, replacement, kind):
    return {'start': start, 'end': end, 'old': text[start:end], 'new': replacement, 'kind': kind}


def list_patches(text, candidate, decision, spans):
    """Use only the request's original words and delimiters at the proposed item boundaries."""
    action = decision.get('action')
    if action not in ('list', 'separate_paragraphs'):
        return []
    original = candidate['original']
    starts = []
    ends = []
    rows = decision.get('item_spans')
    if not isinstance(rows, list) or not 2 <= len(rows) <= len(candidate['possible_item_spans']):
        return []
    for row in rows:
        if not isinstance(row, dict) or type(row.get('start')) is not int or type(row.get('end')) is not int:
            return []
        a, b = row['start'], row['end']
        if not candidate['colon_offset'] < a < b <= len(original) or (ends and a < ends[-1]):
            return []
        a, b = trim_span(original, a, b)
        if a == b:
            return []
        starts.append(a)
        ends.append(b)
    first = candidate['possible_item_spans'][0]['start']
    if starts[0] != first or original[ends[-1]:].strip() not in ('', '。', '；', ';'):
        return []

    offset = candidate['_start']
    nl = '\r\n' if '\r\n' in text else '\n'
    list_item = '- ' if action == 'list' else ''
    boundaries = [(candidate['colon_offset'] + 1, starts[0], nl * 2 + list_item)]
    for last, following in zip(ends[:-1], starts[1:], strict=True):
        gap = original[last:following]
        separators = gap.strip()
        if separators not in ('', '；', ';'):
            return []
        join = original[last - 1] in SEPARATORS or bool(separators)
        if not join:
            return []
        space = nl if action == 'list' else nl * 2
        boundaries.append((last, following, separators + space + list_item))
    if any(locked(offset + a, offset + b, spans) for a, b, _ in boundaries):
        return []
    return [operation(text, offset + a, offset + b, new, 'parallel_list') for a, b, new in boundaries]


def type_patches(text, scan, resources=()):
    spans = protection(text, scan, resources)
    patches = []
    for m in CHINESE_SEPARATOR.finditer(text):
        a, b = m.span()
        if locked(a, b, spans):
            continue
        start = text.rfind('\n', 0, a) + 1
        end = text.find('\n', b)
        if end < 0:
            end = len(text)
        before = text[start:a]
        after = text[b:end].strip()
        if not re.search(f'[{HAN}]', before):
            continue
        if m[0] == '；':
            # A real list can retain semicolons between sibling items.
            if after or (re.match(r'^\s*(?:[-+*]|\d+[.)])\s+', before)
                         and re.match(r'^\n[ \t]*(?:[-+*]|\d+[.)])\s+', text[end:])):
                continue
            new = ''
        elif after[:1] in ('；', '，', '：', '、', '？', '！', '。'):
            new = ''
        else:
            new = '；' if after else ''
        patches.append(operation(text, a, b, new, 'chinese_punctuation'))
    for m in SPACING.finditer(text):
        a = m.start()
        if not locked(a, a, spans):
            patches.append(operation(text, a, a, ' ', 'cjk_spacing'))
    for m in LINE_MARKER.finditer(text):
        if not locked(m.start(), m.end(), spans):
            patches.append(operation(text, m.end(), m.end(), ' ', 'heading_spacing'))
    return patches


def text_leaves(value, path=(), natural=False):
    if isinstance(value, str):
        if natural and value.strip():
            yield path, value
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from text_leaves(child, path + (i,), natural)
    elif isinstance(value, dict):
        for key, child in value.items():
            translated = key == 'text' and (value.get('relation') == 'translated'
                                            or value.get('translation_id') and value.get('status') == 'complete'
                                            and value.get('relation') is None)
            yield from text_leaves(child, path + (key,), key in NATURAL_KEYS or translated)


def replace_leaves(value, changes, path=()):
    if path in changes:
        return changes[path]
    if isinstance(value, list):
        return [replace_leaves(child, changes, path + (i,)) for i, child in enumerate(value)]
    if isinstance(value, dict):
        return {key: replace_leaves(child, changes, path + (key,)) for key, child in value.items()}
    return value


def remapped(row, phases, final_path=None):
    start, end = row['start'], row['end']
    for phase in phases:
        start, end = remap_span(start, end, phase['operations'])
    return {**row, 'start': start, 'end': end, 'path': final_path or row.get('path')}


async def classify(ask, candidates):
    if not ask or not candidates:
        return {}
    batches = [candidates[i:i + 28] for i in range(0, len(candidates), 28)]

    async def one(rows):
        return await ask('C1', {'candidates': [{k: v for k, v in row.items() if k != '_start'} for row in rows]},
                         images=None)

    results = await asyncio.gather(*(one(rows) for rows in batches), return_exceptions=True)
    decisions = {}
    for rows, result in zip(batches, results, strict=True):
        if not isinstance(result, dict) or not isinstance(result.get('decisions'), list):
            continue
        expected = {row['candidate_id'] for row in rows}
        for row in result['decisions']:
            if isinstance(row, dict) and row.get('candidate_id') in expected:
                decisions.setdefault(row['candidate_id'], []).append(row)
    return {key: rows[0] for key, rows in decisions.items() if len(rows) == 1}


async def format_artifact(value, path):
    """Format one finished attachment independently of the other branches."""
    leaves=list(text_leaves(value))
    if not leaves:
        return value,[]
    scans=await asyncio.to_thread(scan_batch,[text for _,text in leaves])
    changes={}
    patches=[]
    for (key,text),scan in zip(leaves,scans,strict=True):
        trimmed,edits=layer(text,type_patches(text,scan))
        if edits:
            changes[key]=trimmed
            patches.append({'path':[*path,*key],'from_sha256':sha(text),'to_sha256':sha(trimmed),
                            'formatted_text':trimmed,'phases':[{'name':'typography','operations':edits}]})
    return replace_leaves(value,changes),patches


async def finalize(root, sample, structure, attachments, translations, ask=None):
    """Apply one final formatting pass and emit replayable coordinate changes."""
    origin = Path(root) / 'upstream07' / sample
    body = (origin / 'body.fused.md').read_bytes().decode()
    deferred = read(origin / 'deferred.json')
    fields = {('body',): body}
    fields.update({('deferred', key): row['markdown'] for key, row in deferred.items()})
    fields.update({('attachments', *key): text for key, text in text_leaves(attachments)})
    fields.update({('translations', *key): text for key, text in text_leaves(translations)})

    keys = list(fields)
    scans = dict(zip(keys, await asyncio.to_thread(scan_batch, list(fields.values())), strict=True))
    by_carrier = defaultdict(list)
    for row in structure['resources']:
        by_carrier[row['carrier']].append(row)

    def provenance(key):
        return by_carrier['body'] if key == ('body',) else by_carrier[key[1]] if key[0] == 'deferred' else []

    protected = {key: protection(fields[key], scans[key], provenance(key)) for key in keys}
    candidates = {key: list(find_candidates(key, fields[key], scans[key], protected[key])) for key in keys}
    decisions = await classify(ask, [row for key in keys for row in candidates[key]])
    stages = defaultdict(list)
    listed = {}
    for key, text in fields.items():
        ops = [op for candidate in candidates[key]
               for op in list_patches(text, candidate, decisions.get(candidate['candidate_id'], {}), protected[key])]
        listed[key], manifest = layer(text, ops)
        if manifest:
            stages[key].append({'name': 'parallel_structure', 'operations': manifest})

    changed_keys = [k for k in keys if listed[k] != fields[k]]
    if changed_keys:
        later = await asyncio.to_thread(scan_batch, [listed[key] for key in changed_keys])
        scans.update(dict(zip(changed_keys, later, strict=True)))

    patches, values = [], {}
    for key, original in listed.items():
        resources = [remapped(row, stages[key]) for row in provenance(key)]
        values[key], manifest = layer(original, type_patches(original, scans[key], resources))
        if manifest:
            stages[key].append({'name': 'typography', 'operations': manifest})
        if stages[key]:
            patch={'path':list(key),'from_sha256':sha(fields[key]),'to_sha256':sha(values[key]),
                   'phases':stages[key]}
            if key[0] in ('attachments','translations'):
                patch['formatted_text']=values[key]
            patches.append(patch)

    def root_key(row):
        return ('body',) if row['surface'] == 'body' else ('deferred', row['carrier'])

    locations = {'body': {'path': 'body.final.md', 'from_sha256': sha(body), 'to_sha256': sha(values[('body',)]),
                          'phases': stages[('body',)]},
                 'deferred': {key: stages[('deferred', key)] for key in deferred}}
    final_structure = dict(structure)
    final_structure['body_final_sha256'] = sha(values[('body',)])
    original_anchors = read(origin / 'anchor-remap.json')['anchors']
    final_anchors = {}
    for source_id, row in original_anchors.items():
        key = ('body',) if row['surface'] == 'body' else ('deferred', source_id)
        final_anchors[source_id] = remapped(row, stages[key], 'body.final.md' if key[0] == 'body' else None)
    locations['anchors'] = final_anchors
    for collection, id_key in (('blocks', 'block_id'), ('resources', 'resource_id')):
        rows = []
        locations[collection] = {}
        for row in structure[collection]:
            key = root_key(row)
            final_row = remapped(row, stages[key], 'body.final.md' if key[0] == 'body' else None)
            exposed = values[key][final_row['start']:final_row['end']]
            if collection == 'blocks':
                final_row['markdown'] = exposed
            elif exposed != row['raw_content']:
                raise ValueError(f"{sample}: formatting changed resource bytes {row['resource_id']}")
            rows.append(final_row)
            locations[collection][row[id_key]] = {
                'carrier': row['carrier'], 'old_start': row['start'], 'old_end': row['end'],
                'start': final_row['start'], 'end': final_row['end'],
            }
        final_structure[collection] = rows

    updated_deferred = {key: {**row, 'markdown': values[('deferred', key)]} for key, row in deferred.items()}
    return {
        'body': values[('body',)], 'structure': final_structure, 'deferred': updated_deferred,
        'attachments': replace_leaves(attachments, {k[1:]: v for k, v in values.items() if k[0] == 'attachments'}),
        'translations': replace_leaves(translations, {k[1:]: v for k, v in values.items() if k[0] == 'translations'}),
        'patches': patches, 'offset_map': {'offset_unit': OFFSET_UNIT, **locations},
        'c1_candidates': sum(len(rows) for rows in candidates.values()), 'c1_decisions': len(decisions),
    }
