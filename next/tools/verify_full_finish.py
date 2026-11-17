"""Verify the isolated 40-chapter result against its accepted STEP-07 inputs."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from time import monotonic, sleep

from manualreader.stages.finish.formatting import sha, text_leaves, type_patches
from manualreader.stages.step07.fusion import invert, layer
from manualreader.stages.step07.resources import scan_batch


MODES = {'INTRO': 10, 'NORMAL': 10, 'FINALE': 10, 'ANSWER': 10}
TOTALS = {'source_references': 6701, 'goals': 982, 'question_items': 36, 'question_raw_units': 36}
STAGE07_COPY = {'body.fused.md', 'fusion-edits.json', 'anchor-remap.json',
                'resources.fused.json', 'deferred.json', 'run.json'}
REQUIRED = {
    'run.json', 'reading-groups.json', 'translation-groups.json', 'source-translation-links.json',
    'part-blocks.json', 'block-source-links.json', 'anchor-lineage.json', 'resource-block-links.json',
    'resource-explanations.json', 'chapter-guidance.json', 'term-candidates.json', 'quiz-candidates.json',
    'translation-visuals.json', 'body.final.md', 'attachments.final.json', 'format-patches.json',
    'offset-map.json', 'alignment.json',
}


def require(condition, note):
    if not condition:
        raise ValueError(note)


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def relative_path(text):
    posix = PurePosixPath(text)
    require(not posix.is_absolute() and '..' not in posix.parts and '\\' not in text and ':' not in text,
            f'invalid manifest path: {text}')
    return Path(*posix.parts)


def baseline_files(root, prior):
    manifest = read(root / 'upstream-baseline.json')
    counts = Counter()
    failures = []
    for name, expected in manifest.items():
        path = relative_path(name)
        origin = prior / path
        if not origin.is_file() or digest(origin) != expected:
            failures.append({'file': name, 'scope': 'original 0.7.1'})
        counts['original_07_files'] += 1
        pieces = path.parts
        local = None
        if pieces[0] in ('inputs', 'media') or pieces[:2] == ('workspace', 'src'):
            local = root / path
        elif pieces[0] == 'jobs' and path.name in STAGE07_COPY:
            local = root / 'upstream07' / Path(*pieces[1:])

        if local is not None:
            counts['immutable_copies'] += 1
            if not local.is_file() or digest(local) != expected:
                failures.append({'file': name, 'scope': 'copied input'})
    return dict(counts), failures


def registry(rows, key, label):
    indexed = {row[key]: row for row in rows}
    require(len(indexed) == len(rows) and all(isinstance(k, str) and k for k in indexed), f'{label}: invalid IDs')
    return indexed


def flatten(mapping):
    return {(a, b) for a, values in mapping.items() for b in values}


def check_relations(alignment, structure, sources, groups):
    require(alignment['schema_version'] == 'reader-two-relations-v1', 'invalid alignment schema')
    require(not ({'translation_to_explanation', 'explanation_to_translation',
                  'translation_explanation_links'} & alignment.keys()), 'persisted a third relation')
    actual = set()
    blocks = {b['block_id'] for b in structure['blocks']}
    for row in alignment['source_explanation_links']:
        a, b = row['source_id'], row['block_id']
        require(a in sources and b in blocks, f'invalid source–explanation endpoint {a}/{b}')
        rel = row['relation_type']
        require(rel in ('direct_semantic', 'navigation_only'), f'invalid relation: {rel}')
        require((rel == 'direct_semantic') == bool(row.get('substantive_coverage')), 'navigation-only semantics changed')
        actual.add((a, b))
    require(actual == flatten(alignment['source_to_explanation']), 'source–explanation forward edges differ')
    require(actual == {(b, a) for a, b in flatten(alignment['explanation_to_source'])},
            'source–explanation reverse edges differ')

    translated = registry([item for group in groups for item in group['segments']], 'translation_id', 'translations')
    true_edges = set()
    for row in alignment['source_translation_links']:
        s, t = row['source_id'], row.get('translation_id')
        require(s in sources, f'unknown source of translation {s}')
        if row['relation'] == 'unresolved':
            require(not t, f'fake translation endpoint for {s}')
        elif row['relation'] == 'translated':
            require(t in translated and translated[t]['source_id'] == s, f'missing real translation {s}/{t}')
        elif row['relation'] in ('identity', 'preserved_math'):
            require(s == t, f'identity endpoint drift {s}/{t}')
        else:
            raise ValueError(f"invalid translation relation {row['relation']}")
        if t:
            true_edges.add((s, t))
    require(true_edges == flatten(alignment['source_to_translation']), 'source–translation forward edges differ')
    require(true_edges == {(b, a) for a, b in flatten(alignment['translation_to_source'])},
            'source–translation reverse edges differ')
    return len(actual), len(true_edges)


def span(row, value, label):
    a, b = row['start'], row['end']
    require(type(a) is int and type(b) is int and 0 <= a < b <= len(value), f'{label} has an invalid span: {a}/{b}')
    return value[a:b]


def value_at(tree, path):
    for key in path:
        tree = tree[key]
    return tree


def check_format(job, values, source_carriers, resources, archive=None):
    patches = read(job / 'format-patches.json')
    offset_map = read(job / 'offset-map.json')
    raw = read(job / 'generated/formatted.json')
    formatted = {'body': values['body'], 'deferred': {k: v for k, v in values.items() if k != 'body'},
                 'attachments': read(job / 'attachments.final.json'), 'translations': raw['translations']}
    historical={sha(text):text for tree in (formatted['attachments'],raw['attachments'],raw['translations'])
                for _,text in text_leaves(tree)}
    verified_archive=archive or {}
    checked = 0
    verified_revisions = 0
    for patch in patches:
        path = patch['path']
        try:
            resulting=value_at(formatted,path)
        except (IndexError,KeyError):
            resulting=None
        if not isinstance(resulting,str) or sha(resulting)!=patch['to_sha256']:
            require(path[0] in ('attachments','translations'),f'正文格式回执与交稿不一致：{path}')
            resulting=patch.get('formatted_text') or historical.get(patch['to_sha256'])
            if resulting is None:
                target=verified_archive.get(patch['to_sha256'],{})
                source=verified_archive.get(patch['from_sha256'],{})
                resulting=target.get('text')
                if resulting is None and isinstance(before:=source.get('text'),str):
                    require(sha(before)==patch['from_sha256'],f'历史格式输入版本不匹配：{path}')
                    resulting=before
                    for step in patch['phases']:
                        resulting,_=layer(resulting,step['operations'])
            require(isinstance(resulting,str) and sha(resulting)==patch['to_sha256'],
                    f'找不到可核对的历史格式版本：{path}')
            verified_revisions+=1
        preceding = resulting
        for step in reversed(patch['phases']):
            preceding = invert(preceding, step['operations'])
        require(sha(preceding) == patch['from_sha256'], 'format operations are not reversible')
        if path[0] in ('body', 'deferred'):
            require(preceding == source_carriers[path[-1]], 'format reverse did not return accepted body')
        checked += 1

    materials = [values[carrier] for carrier in values]
    scans = scan_batch(materials)
    for (carrier, text), scan in zip(values.items(), scans, strict=True):
        rows = [row for row in resources if row['carrier'] == carrier]
        require(not type_patches(text, scan, rows), f'repeating typography changes {carrier}')
    other_text = [(kind, path, text)
                  for kind in ('attachments', 'translations')
                  for path, text in text_leaves(formatted[kind])]
    if other_text:
        scans = scan_batch([text for _, _, text in other_text])
        for (kind, path, text), scan in zip(other_text, scans, strict=True):
            require(not type_patches(text, scan), f'repeating typography changes {kind}/{path}')
    require(offset_map['offset_unit'].startswith('Unicode code points'), 'offset map has the wrong unit')
    return checked, len(other_text) + len(values), verified_revisions


def verify_one(root, job, *, allow_content_gaps=False, format_archive=None):
    contents = {f.name for f in job.iterdir() if f.is_file()}
    require(REQUIRED <= contents, f'missing files {sorted(REQUIRED - contents)}')
    final = read(job / 'run.json')
    origin = root / 'upstream07' / job.name
    before = read(origin / 'run.json')
    require(final['schema_version'] == 'reader-final-v1', 'final schema not recognized')
    require(final['sample_id'] == job.name and final['task_id'] == before['task_id'], 'final task identity changed')
    require(final['mode'] == before['mode'] and final['fixture_based'] == before['fixture_based'], 'final mode or fixture changed')
    source = read(root / 'inputs' / job.name / 'source-index.json')
    all_sources = {row['source_id'] for row in source['sources']}
    require(final['source_snapshot'] == source['snapshot_sha256'], 'source snapshot changed')
    groups = read(job / 'reading-groups.json')
    member_ids = [i for row in groups for i in row['source_ids']]
    require(set(member_ids) == all_sources and len(member_ids) == len(all_sources), 'reading groups dropped or repeated source IDs')

    body = (job / 'body.final.md').read_bytes().decode()
    frozen_body = (origin / 'body.fused.md').read_bytes().decode()
    require(final['body_fused_sha256'] == before['fused_sha256'] == sha(frozen_body), 'frozen body changed')
    require(final['body_final_sha256'] == sha(body), 'final body SHA mismatch')
    require(final['format_status'] == 'complete', 'formatting is partial')
    old_deferred = read(origin / 'deferred.json')
    new_deferred = read(job / 'deferred.final.json') if old_deferred else {}
    require(set(old_deferred) == set(new_deferred), 'deferred carriers changed')
    values = {'body': body, **{k: v['markdown'] for k, v in new_deferred.items()}}
    originals = {'body': frozen_body, **{k: v['markdown'] for k, v in old_deferred.items()}}
    old_structure = read(job / 'generated/structure.json')
    structure = read(job / 'part-blocks.json')
    require(structure['schema_version'] == 'reader-structure-v1', 'unexpected structure schema')
    blocks = registry(structure['blocks'], 'block_id', 'blocks')
    parts = registry(structure['parts'], 'part_id', 'parts')
    require(set(parts) == {p['part_id'] for p in old_structure['parts']}, 'Part IDs changed after formatting')
    require(set(blocks) == {b['block_id'] for b in old_structure['blocks']}, 'Block IDs changed after formatting')
    for part in parts.values():
        require(part['block_ids'] and all(blocks[b]['part_id'] == part['part_id'] for b in part['block_ids']),
                f"broken ownership in {part['part_id']}")
    for block in blocks.values():
        require(span(block, values[block['carrier']], block['block_id']) == block['markdown'], 'Block payload/offset mismatch')

    offset_map = read(job / 'offset-map.json')
    frozen_anchors = read(origin / 'anchor-remap.json')['anchors']
    lineage = read(job / 'anchor-lineage.json')
    require(set(lineage) == set(frozen_anchors) == set(offset_map['anchors']), 'upstream anchor identity lost')
    require(lineage == old_structure['anchor_lineage'] == structure['anchor_lineage'], 'lineage changed during formatting')
    for identifier, anchor in offset_map['anchors'].items():
        key = 'body' if anchor['surface'] == 'body' else identifier
        require(span(anchor, values[key], identifier).strip(), f'{identifier}: anchor has no reachable text')
        require(lineage[identifier] and all(b in blocks for b in lineage[identifier]), f'{identifier}: missing Block')
    resources = registry(structure['resources'], 'resource_id', 'resources')
    frozen_resources = registry(read(origin / 'resources.fused.json')['items'], 'resource_id', 'frozen resources')
    resource_links = registry(read(job / 'resource-block-links.json'), 'resource_id', 'resource links')
    require(set(resources) == set(frozen_resources) == set(resource_links), 'resource identities changed')
    for rid, row in resources.items():
        block = blocks[row['block_id']]
        require(block['carrier'] == row['carrier'] and block['start'] <= row['start'] < row['end'] <= block['end'],
                f'{rid}: resource not within its Block')
        require(row['block_id'] == resource_links[rid]['block_id'], f'{rid}: disagreeing Block link')
        require(row['surface'] == frozen_resources[rid]['surface'], f'{rid}: resource visibility changed')
        require(span(row, values[row['carrier']], rid) == row['raw_content'] == frozen_resources[rid]['raw_content'],
                f'{rid}: resource bytes affected')
    p2 = read(job / 'resource-explanations.json')
    require(p2['schema_version'] == 'resource-explanation-v2', 'unexpected P2 schema')
    require(set(resources) == {r['resource_id'] for r in p2['coverage_by_resource']}, 'P2 inventory dropped resources')

    alignment = read(job / 'alignment.json')
    require(alignment['source_explanation_links'] == read(job / 'block-source-links.json'), 'source links differ')
    require(alignment['source_translation_links'] == read(job / 'source-translation-links.json'), 'translation links differ')
    se, st = check_relations(alignment, structure, all_sources, read(job / 'translation-groups.json'))
    unique_sources = {row['source_id'] for row in alignment['source_explanation_links']}
    require(len(unique_sources) == before['source_navigation']['linked'], 'source navigation dropped targets')
    counts = Counter(source_references=len(unique_sources), se_edges=se, st_edges=st, parts=len(parts),
                     blocks=len(blocks), resources=len(resources), deferred_resources=sum(r['surface'] == 'deferred' for r in resources.values()))
    roles = (('goal_items', 'goals', 'goal_coverage'), ('question_items', 'question_items', 'question_item_coverage'),
             ('question_raw_units', 'question_raw_units', 'question_raw_unit_coverage'))
    for field, metric, summary in roles:
        values_ = structure['coverage'][field]
        require(all(bool(row['block_ids'])==bool(row['covered']) and all(b in blocks for b in row['block_ids']) for row in values_),f'invalid {field} destinations')
        counts[metric] = sum(row['covered'] for row in values_)
        if allow_content_gaps:
            counts[f'{metric}_unlinked']=len(values_)-counts[metric]
        else:
            require(counts[metric]==len(values_),f'missing {field} destinations')
        require(counts[metric] == before[metric]['covered'] == alignment['coverage'][summary]['covered'],
                f'{field} counts differ')
    (counts['format_carriers_verified'],counts['idempotent_texts'],counts['historical_format_revisions_verified']
     )=check_format(job,values,originals,list(resources.values()),format_archive)
    return {'mode': final['mode'], 'sample_id': job.name, 'counts': dict(counts)}


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('root', type=Path)
    cli.add_argument('prior', type=Path)
    cli.add_argument('--receipt', type=Path)
    cli.add_argument('--wait', type=int, default=0, help='Seconds to wait for the existing batch to finish.')
    cli.add_argument('--allow-partial', action='store_true', help='Return a receipt for completed chapters without requiring all 40.')
    args = cli.parse_args()
    began = monotonic()
    root = args.root
    samples = sorted(d.name for d in (root / 'upstream07').iterdir() if d.is_dir())
    while (monotonic() - began < args.wait
           and not (root / 'finish-result.json').is_file()
           and any(not (root / 'jobs' / sample / 'run.json').is_file() for sample in samples)):
        sleep(1)
    count, problems = baseline_files(root, args.prior)
    rows, errors, missing = [], {}, []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for sample in samples:
            job = root / 'jobs' / sample
            if not (job / 'run.json').is_file():
                missing.append(sample)
            else:
                futures[sample] = executor.submit(verify_one, root, job)
        for sample, future in futures.items():
            try:
                rows.append(future.result())
            except (ValueError, KeyError, FileNotFoundError) as exc:
                errors[sample] = f'{type(exc).__name__}: {exc}'
    totals = Counter()
    modes = Counter()
    for row in rows:
        totals.update(row['counts'])
        modes[row['mode']] += 1
    overall_errors = []
    if not missing and len(rows) == 40:
        if dict(modes) != MODES:
            overall_errors.append(f'mode counts differ: {modes}')
        overall_errors.extend(f'{key}: expected {value}, got {totals[key]}' for key, value in TOTALS.items() if totals[key] != value)
    status = 'passed' if len(rows) == 40 and not (problems or errors or overall_errors) else 'partial' if missing else 'failed'
    receipt = {
        'schema_version': 'reader-full-structural-receipt-v1',
        'status': status, 'expected_samples': len(samples), 'checked_samples': len(rows), 'modes': dict(modes),
        'totals': dict(totals), 'baseline_counts': count, 'baseline_problems': problems,
        'missing_samples': missing, 'failures': errors, 'overall_errors': overall_errors,
        'sample_receipts': rows, 'elapsed_seconds': round(monotonic() - began, 3),
    }
    target = args.receipt or root / 'structural-verification.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({**receipt, 'sample_receipts': len(rows)}, ensure_ascii=False))
    return 0 if status == 'passed' or (args.allow_partial and status == 'partial' and not (problems or errors)) else 1


if __name__ == '__main__':
    raise SystemExit(main())
