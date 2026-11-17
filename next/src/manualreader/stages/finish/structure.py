"""STEP-08 projects existing Reader anchors onto blocks without changing the draft."""
from collections import defaultdict
from hashlib import sha256
import json
import re
from pathlib import Path

from ..step07.resources import scan_batch


OFFSET_UNIT = 'Unicode code points; zero-based; end exclusive'
ATX = re.compile(r'^[ \t]{0,3}(#{1,6})(?:[ \t]|$)')
LIST = re.compile(r'^\s*(?:[-*•▪◦]|\d+[.)、])\s+')
LABEL = {'image': 'figure', 'visual': 'figure', 'page_image': 'figure', 'formula': 'equation'}


def identity(prefix, *parts):
    body = json.dumps(parts, ensure_ascii=False, separators=(',', ':'))
    return prefix + '-' + sha256(body.encode()).hexdigest()[:20]


def read(path):
    return json.loads(path.read_bytes())


def overlaps(start, end, rows):
    return [row for row in rows if start < row['end'] and end > row['start']]


def heading_level(text, start):
    first, _, following = text[start:].partition('\n')
    match = ATX.match(first)
    if match:
        return len(match[1])
    return 1 if following.split('\n', 1)[0].strip().startswith('=') else 2


def ast_regions(text, scan, resources):
    # Resource boundaries can span AST tokens, especially when MarkdownIt sees display math as prose.
    ranges = [(row['start'], row['end'], row['type']) for row in scan['blocks']]
    ranges.extend((row['start'], row['end'], row['kind']) for row in resources)
    regions = []
    for start, end, kind in sorted(ranges, key=lambda x: (x[0], -x[1])):
        if not 0 <= start < end <= len(text):
            continue
        if regions and start < regions[-1][1]:
            regions[-1][1] = max(regions[-1][1], end)
        else:
            regions.append([start, end, kind])

    cursor = 0
    for start, end, kind in regions:
        if text[cursor:start].strip():
            yield cursor, start, 'markdown'
        yield start, end, kind
        cursor = end
    if text[cursor:].strip():
        yield cursor, len(text), 'markdown'


def project_carrier(task_id, carrier, surface, text, scan, resources, anchors, title):
    regions = list(ast_regions(text, scan, resources))
    anchors = [(a, q) for a, q in anchors.items() if q['surface'] == surface]
    headings = {h['start']: (heading_level(text, h['start']), h['name'])
                for h in scan['headings']}
    boundaries = [(a, *headings[a]) for a, _, kind in regions
                  if kind == 'heading_open' and a in headings]
    if len(boundaries) > 1 and boundaries[0][0] == 0 and boundaries[0][1] < min(x[1] for x in boundaries[1:]):
        # A single document title is not a separate teaching Part.
        boundaries = boundaries[1:]
    level = min((level for _, level, _ in boundaries), default=0)
    sections = {start: name for start, candidate, name in boundaries if candidate == level}

    parts, blocks = [], []
    block_ord = defaultdict(int)
    part_ord = defaultdict(int)
    part = None
    for start, end, kind in regions:
        if not text[start:end].strip():
            continue
        owners = tuple(sorted(a for a, q in anchors if start < q['end'] and end > q['start']))
        seed = owners or (parts[-1]['part_id'] if parts else carrier,)
        if part is None or (start in sections and surface == 'body'):
            part_title = sections.get(start, title) or title
            part_ord[seed] += 1
            part_id = identity('part', task_id, carrier, seed, part_ord[seed])
            part = {'part_id': part_id, 'title': part_title, 'surface': surface, 'block_ids': []}
            parts.append(part)
        block_ord[seed] += 1
        block_id = identity('block', task_id, carrier, seed, block_ord[seed])
        part['block_ids'].append(block_id)
        blocks.append({
            'block_id': block_id, 'part_id': part['part_id'], 'render_anchor': block_id,
            'kind': kind, 'surface': surface, 'path': 'body.fused.md' if surface == 'body' else 'deferred.json',
            'carrier': carrier, 'start': start, 'end': end, 'markdown': text[start:end],
            'anchor_ids': list(owners),
        })
    return parts, blocks


def _reading_kind(row, assets):
    kind = LABEL.get(row['kind'], row['kind'])
    if kind == 'paragraph':
        table_ids = [identifier for identifier in row.get('asset_refs', [])
                     if assets.get(identifier, {}).get('kind') == 'table']
        if table_ids:
            return 'table', table_ids[0]
        if LIST.match(row.get('text') or ''):
            return 'list', None
    return kind, None


def _paragraph_continues(previous, current, pages):
    # The old Reader only joins ordinary PDF paragraphs when their boxes and sentence boundary agree.
    if len(pages) != 1 or previous.get('role') != current.get('role'):
        return False
    old, new = previous.get('text') or '', current.get('text') or ''
    if len(old) <= 25 or len(new) <= 15 or re.search(r'[。！？.!?:：;；]$', old.rstrip()):
        return False
    boxes = []
    for row in (previous, current):
        box = next((ref.get('bbox') for ref in row.get('refs', []) if ref.get('page_id') == pages[0]), None)
        if not box or len(box) != 4:
            return False
        boxes.append(box)
    a, b = boxes
    return abs(a[0] - b[0]) < 14 and -.5 <= b[1] - a[3] <= max(5, (a[3] - a[1]) * .65)


def reading_groups(source):
    """Apply the old Reader's ordered grouping rules to Next source and page identities."""
    sources, assets = (source, {}) if isinstance(source, list) else (source['sources'], source.get('assets', {}))
    groups = []
    previous = None
    for row in sources:
        source_id = row['source_id']
        file_id = row['file_id']
        position = row.get('position') or {}
        pages = list(dict.fromkeys(ref['page_id'] for ref in row.get('refs', []) if ref.get('page_id')))
        kind, carrier = _reading_kind(row, assets)
        section_id = row.get('section_id')
        if (groups and previous and kind == groups[-1]['kind']
            and file_id == groups[-1]['file_id'] and pages == groups[-1]['page_ids']
            and section_id == groups[-1]['section_id']
            and (previous.get('position') or {}).get('type') == position.get('type')
            and (kind == 'list'
                 or kind == 'paragraph' and _paragraph_continues(previous, row, pages)
                 or kind == 'table' and carrier == groups[-1].get('asset_id'))):
            group = groups[-1]
            group['source_ids'].append(source_id)
            group['position']['end'] = position.get('end')
            group['positions'].append(position)
        else:
            group_id = identity('reading', file_id, source_id)
            groups.append({
                'group_id': group_id, 'file_id': file_id, 'source_ids': [source_id], 'kind': kind,
                'position': dict(position), 'positions': [position], 'page_ids': pages,
                'section_id': section_id, 'asset_id': carrier,
            })
        previous = row
    return groups


def project_source_links(mapping, lineage):
    """Preserve each contributing source anchor and its original relation type."""
    edges, unresolved = [], []
    rows = mapping['links'] + mapping.get('navigation_only_links', [])
    seen = set()
    for row in rows:
        anchor_id = row['body_anchor_id']
        blocks = lineage.get(anchor_id, [])
        if not blocks:
            unresolved.append(dict(row))
        for block_id in blocks:
            edge_id = (row['source_id'], anchor_id, block_id, row['relation_type'])
            if edge_id in seen:
                continue
            seen.add(edge_id)
            edges.append({**row, 'block_id': block_id})
    return edges, unresolved


def project_coverage(coverage, lineage):
    result = dict(coverage)
    for field, summary in (('goal_items', 'goal_coverage'), ('question_items', 'question_item_coverage'),
                           ('question_raw_units', 'question_raw_unit_coverage')):
        items = []
        for item in result.get(field, []):
            block_ids = list(dict.fromkeys(b for a in item['target_ids'] for b in lineage.get(a, [])))
            items.append({**item, 'block_ids': block_ids, 'covered': bool(block_ids)})
        result[field] = items
        covered = sum(item['covered'] for item in items)
        result[summary] = {'total': len(items), 'covered': covered,
                           'coverage_pct': round(100 * covered / len(items), 2) if items else 0.0}
    return result


def build_structure(root, sample):
    """Read frozen 07 carriers and return stable Part/Block, resource and source projections."""
    root = Path(root)
    origin = root / 'upstream07' / sample
    card = read(root / 'inputs' / sample / 'task-card.json')
    run = read(origin / 'run.json')
    remap = read(origin / 'anchor-remap.json')
    ledger = read(origin / 'resources.fused.json')
    deferred = read(origin / 'deferred.json')
    body_bytes = (origin / 'body.fused.md').read_bytes()
    body_sha = sha256(body_bytes).hexdigest()
    if not all(doc['fused_sha256'] == body_sha for doc in (run, remap, ledger)):
        raise ValueError(f'{sample}: frozen 07 body hash mismatch')
    body = body_bytes.decode()
    title = (card.get('chapter') or {}).get('title') or '本轮讲解'
    carriers = {'body': body}
    carriers.update({alias: row['markdown'] for alias, row in deferred.items()})
    scans = dict(zip(carriers, scan_batch(list(carriers.values())), strict=True))

    # Deferred carriers can share the same local coordinates; each has a separate namespace.
    lineage = {}
    parts, blocks, resources = [], [], []
    for carrier, text in carriers.items():
        surface = 'body' if carrier == 'body' else 'deferred'
        carrier_anchors = {key: row for key, row in remap['anchors'].items()
                           if row['surface'] == surface and (surface == 'body' or key == carrier)}
        carrier_resources = [row for row in ledger['items'] if row['surface'] == surface
                             and (surface == 'body' or row['origin'] == carrier)]
        part_rows, block_rows = project_carrier(run['task_id'], carrier, surface, text, scans[carrier],
                                                carrier_resources, carrier_anchors,
                                                title if surface == 'body' else '补充阅读')
        parts.extend(part_rows)
        blocks.extend(block_rows)
        for key, row in carrier_anchors.items():
            lineage[key] = [block['block_id'] for block in overlaps(row['start'], row['end'], block_rows)]
        for row in carrier_resources:
            start, end = row['start'], row['end']
            if text[start:end] != row['raw_content']:
                raise ValueError(f"{sample}: frozen resource mismatch {row['resource_id']}")
            owning = next((b for b in block_rows if b['start'] <= start < end <= b['end']), None)
            resources.append({**row, 'carrier': carrier, 'block_id': owning['block_id'] if owning else None,
                              'render_anchor': row['resource_id'], 'inactive_in_body': surface != 'body',
                              'location_status': 'located' if owning else 'unresolved'})

    source_edges, unresolved_edges = project_source_links(remap['mapping'], lineage)
    linked = {edge['source_id'] for edge in source_edges}
    direct = {edge['source_id'] for edge in source_edges if edge['relation_type'] == 'direct_semantic'}
    total = remap['mapping']['source_navigation']['total']
    source_navigation = {
        'total': total, 'linked': len(linked), 'direct_semantic_count': len(direct),
        'navigation_only_count': len(linked - direct), 'unresolved': total - len(linked),
        'coverage_pct': round(100 * len(linked) / total, 2) if total else 0.0,
    }
    return {
        'schema_version': 'reader-structure-v1', 'sample_id': sample, 'task_id': run['task_id'],
        'mode': run['mode'], 'title': title, 'fixture_based': run['fixture_based'],
        'body_sha256': body_sha, 'parts': parts, 'blocks': blocks, 'resources': resources,
        'source_edges': source_edges, 'anchor_lineage': lineage, 'unresolved_source_edges': unresolved_edges,
        'coverage': project_coverage(remap['goal_question_coverage'], lineage),
        'source_navigation': source_navigation, 'offset_unit': OFFSET_UNIT,
    }
