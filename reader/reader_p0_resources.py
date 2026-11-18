"""Scan frozen Reader Markdown and keep every occurrence at its original position."""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
from time import perf_counter
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

C3_SAMPLES = (
    '01-yang', '02-laozhang', '03-ibm', '04-olympus', '05-zen6',
    '06-paper', '07-exam2', '08-property', '09-five-companies', '10-genshin',
)
KINDS = ('formula_display', 'formula_inline', 'image', 'mermaid', 'table')
STATUSES = ('ok', 'missing_file', 'parse_error', 'unverified')
HERE = Path(__file__).resolve().parent
ENTRY = re.compile(r'samples/([a-z0-9-]+)/chapter\.md\Z')
EOL = re.compile(r'\r\n|\n|\r')
OFFSET_RULE = {
    'encoding': 'UTF-8',
    'unit': 'unicode_code_point',
    'offset_base': 0,
    'end_offset': 'exclusive',
    'line_base': 1,
    'end_line': 'inclusive',
    'line_breaks': 'CRLF, LF or CR; their original code points are counted',
}
ZERO_USAGE = {'model_calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'api_cost_usd': 0}
EXPLANATION_CONTRACT = {
    'version': 'resource-explanation-v1',
    'item_fields': ['resource_id', 'overall_explanation', 'components', 'question_context', 'status'],
    'component_fields': ['raw_content', 'explanation', 'location_id'],
    'statuses': ['complete', 'missing', 'needs_review'],
}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def digest(value):
    return sha256(value).hexdigest()


def save(path, data):
    path.write_bytes((json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf8'))


def vendor_path():
    for path in (HERE/'static/markdown-vendor.js', HERE/'vendor/markdown-vendor.js'):
        if path.is_file():
            return path
    raise FileNotFoundError('Reader 的 markdown-vendor.js 不在脚本旁的 static 或 vendor 目录')


def scan_markdown(text, vendor=None, *, details=False):
    args = ['node', str(HERE/'reader_p0_resources.js'), str(vendor or vendor_path())]
    if details:
        args.append('--details')
    result = subprocess.run(args,
                            input=text.encode('utf8'), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf8', errors='replace').strip())
    return json.loads(result.stdout)


def load_optional(archive, path):
    try:
        return json.loads(archive.read(path))
    except KeyError:
        return {}


class Media:
    def __init__(self, archive, sample):
        self.archive = archive
        self.root = f'samples/{sample}'
        record = load_optional(archive, self.root+'/run.json')
        card = load_optional(archive, self.root+'/task-card.json')
        self.selected = {s for f in card.get('material_plan', []) for s in f.get('source_ids', [])}
        self.verified_assets = record.get('media', {}).get('assets', {})
        self.files = {}

    def resolve(self, path):
        if not path:
            return 'parse_error', None, [], None
        try:
            url = urlsplit(path)
        except ValueError:
            return 'unverified', None, [], None
        name = unquote(url.path)
        if url.scheme or url.netloc or not name or name.startswith('/') or any(c in name for c in '\\\0'):
            return 'unverified', None, [], None
        combined = posixpath.normpath(posixpath.join(self.root, name))
        if not combined.startswith(self.root+'/'):
            return 'unverified', None, [], None
        if combined not in self.files:
            try:
                info = self.archive.getinfo(combined)
                if info.is_dir():
                    raise KeyError(combined)
                self.files[combined] = self.archive.read(info)
            except KeyError:
                self.files[combined] = None
        data = self.files[combined]
        if data is None:
            return 'missing_file', None, [], None
        verified = self.verified_assets.get(PurePosixPath(combined).name, {}) if combined.startswith(self.root+'/media/') else {}
        ids = list(dict.fromkeys(s for s in verified.get('source_ids', []) if s in self.selected))
        return 'ok', combined, ids, digest(data)


def build_inventory(sample, raw, spans, media=None, *, extra_kinds=()):
    text = raw.decode('utf8')
    chapter_hash = digest(raw)
    lines = [0] + [m.end() for m in EOL.finditer(text)]
    result = []
    for item in spans:
        start, end = item['start'], item['end']
        kind, status = item['kind'], item['status']
        require(0 <= start < end <= len(text), f'{sample}: invalid range {start}:{end}')
        first, last = bisect_right(lines, start), bisect_right(lines, end-1)
        # Excerpt starts and ends on real lines and retains the entire resource.
        context = text[lines[max(0, first-3)]:lines[min(len(lines)-1, last+2)]
                       if last+2 < len(lines) else len(text)]
        path, actual, ids, media_hash = item.get('media_path'), None, [], None
        if kind == 'image' and status == 'ok':
            status, actual, ids, media_hash = media.resolve(path) if media else ('unverified', None, [], None)
        family = ('latex' if kind.startswith('formula_') else kind)
        key = media_hash or path or EOL.sub('\n', item.get('key', text[start:end])).strip()
        if status == 'parse_error':
            family = f'{family}-incomplete'
        content_hash = digest((family + '\0' + key).encode())
        resource_id = 'res-' + digest(f'{sample}\0{chapter_hash}\0{kind}\0{start}\0{end}'.encode())[:24]
        row = {'resource_id': resource_id, 'kind': kind, 'raw_content': text[start:end],
               'location': {'start_offset': start, 'end_offset': end, 'start_line': first, 'end_line': last},
               'heading': item['heading'], 'context_excerpt': context, 'media_path': path,
               'source_ids': ids, 'content_hash': content_hash, 'status': status}
        if actual:
            row['media_archive_path'] = actual
        result.append(row)
    inventory = {'sample_id': sample, 'chapter_sha256': chapter_hash, 'position_convention': OFFSET_RULE, 'resources': result}
    verify_inventory(raw, inventory, extra_kinds=extra_kinds)
    return inventory


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def verify_inventory(raw, inventory, *, extra_kinds=()):
    text = raw.decode('utf8')
    starts = [0] + [m.end() for m in EOL.finditer(text)]
    resources = inventory['resources']
    ids = set()
    active = []
    prev = (-1, 0, '')
    require(inventory['chapter_sha256'] == digest(raw), '正文 SHA-256 不匹配')
    for item in resources:
        rid, kind, loc = item['resource_id'], item['kind'], item['location']
        first, last = loc['start_offset'], loc['end_offset']
        order = (first, -last, kind)
        require(kind in (*KINDS, *extra_kinds) and item['status'] in STATUSES, f'{rid}: unknown kind/status')
        require(rid not in ids and order >= prev, f'{rid}: duplicate or unordered occurrence')
        require(0 <= first < last <= len(text) and text[first:last] == item['raw_content'], f'{rid}: original slice differs')
        require((bisect_right(starts, first), bisect_right(starts, last-1)) ==
                (loc['start_line'], loc['end_line']), f'{rid}: line numbers differ')
        active = [(a,b,k) for a,b,k in active if b > first]
        require(all(k == 'table' and a <= first and last <= b and kind in ('image', 'formula_inline')
                    for a,b,k in active), f'{rid}: resource overlaps another block')
        active.append((first, last, kind))
        ids.add(rid)
        prev = order


def duplicate_groups(rows):
    grouped = defaultdict(list)
    for sample, row in rows:
        grouped[row['content_hash']].append({'sample_id': sample, 'resource_id': row['resource_id'],
                                             'kind': row['kind'], 'location': row['location']})
    return [{'content_hash': h, 'occurrences': members} for h, members in grouped.items() if len(members) > 1]


def verify_c3_counts(archive, key, raw, counts, statuses):
    browser = json.loads(archive.read(f'samples/{key}/browser.json'))
    text = raw.decode('utf8')
    expected = {name: browser[k] for name, k in (('mermaid','rendered_mermaid'), ('image','images_loaded'), ('table','tables'))}
    # The frozen samples use explicit balanced delimiters, with no code examples containing them.
    marks = Counter(m.group(1) for m in re.finditer(r'(?<!\\)(?:\\\\)*\\([\[\]()])', text))
    expected.update(formula_display=marks['[']+text.count('$$')//2, formula_inline=marks['('])
    require(marks['[']==marks[']'] and marks['(']==marks[')'] and text.count('$$')%2==0,
            f'{key}: C3 delimiter tally is unbalanced')
    require(counts == expected and not statuses['parse_error'], f'{key}: C3 inventory {counts} differs from {expected}')
    return expected


def scan_sample(archive_path, output, sample, verify_frozen):
    started, timer = utc_now(), perf_counter()
    member = f'samples/{sample}/chapter.md'
    dest = output/'samples'/sample
    dest.mkdir(parents=True)
    with ZipFile(archive_path) as archive:
        raw = archive.read(member)
        media = Media(archive, sample)
        inventory = build_inventory(sample, raw, scan_markdown(raw.decode('utf8')), media)
        resources = inventory['resources']
        by_type = Counter(x['kind'] for x in resources)
        counts = {k: by_type[k] for k in KINDS}
        by_status = Counter(x['status'] for x in resources)
        expected = verify_c3_counts(archive, sample, raw, counts, by_status) if verify_frozen else None
        for path, payload in media.files.items():
            if payload is not None:
                copy = output/Path(path)
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_bytes(payload)
        (dest/'chapter.md').write_bytes(raw)
        save(dest/'resources.json', inventory)
    with ZipFile(archive_path) as archive:
        again = archive.read(member)
    require(raw == again == (dest/'chapter.md').read_bytes(), f'{sample}: 正文在扫描期间变化')
    duplicates = duplicate_groups((sample, r) for r in resources)
    state = 'needs_review' if any(by_status[k] for k in STATUSES if k != 'ok') else 'complete'
    metrics = {'sample_id': sample, 'started_at': started, 'ended_at': utc_now(),
               'wall_seconds': round(perf_counter()-timer, 6), 'resource_counts': counts,
               'resource_count': len(resources), 'duplicate_extra_occurrence_count':
                   sum(len(g['occurrences'])-1 for g in duplicates), 'duplicate_group_count': len(duplicates),
               'missing_file_count': by_status['missing_file'], 'parse_error_count': by_status['parse_error'],
               'unverified_count': by_status['unverified'], **ZERO_USAGE, 'state': state,
               'chapter_sha256_before': digest(raw), 'chapter_sha256_after': digest(again),
               'c3_reference_counts': expected}
    save(dest/'run-metrics.json', metrics)
    return inventory, metrics


def scan_archive(archive, output, workers, verify_frozen=False):
    timer, started = perf_counter(), utc_now()
    archive, output = Path(archive).resolve(), Path(output).resolve()
    before = digest(archive.read_bytes())
    with ZipFile(archive) as source:
        samples = sorted(m[1] for name in source.namelist() if (m := ENTRY.fullmatch(name)))
    require(samples and len(set(samples)) == len(samples), '正文条目为空，或样本 ID 重复')
    if verify_frozen:
        require(tuple(samples) == C3_SAMPLES, '测试包中的正文与冻结的十份 C3 样本不一致')
    output.mkdir(parents=True, exist_ok=False)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(lambda key: scan_sample(archive, output, key, verify_frozen), samples))
    after = digest(archive.read_bytes())
    require(after == before, '输入 ZIP 在扫描期间变化')
    counts = dict.fromkeys(KINDS, 0)
    for _, m in results:
        for k, n in m['resource_counts'].items():
            counts[k] += n
    all_groups = duplicate_groups((inventory['sample_id'], r)
                                  for inventory, _ in results for r in inventory['resources'])
    summary = {'started_at': started, 'ended_at': utc_now(), 'wall_seconds': round(perf_counter()-timer, 6),
               'workers': min(workers, len(samples)), 'sample_count': len(samples), 'resource_counts': counts,
               'resource_count': sum(counts.values()), 'duplicate_group_count': len(all_groups),
               'duplicate_extra_occurrence_count': sum(len(g['occurrences'])-1 for g in all_groups),
               'duplicate_groups': all_groups, 'archive_sha256_before': before, 'archive_sha256_after': after,
               **{k: sum(m[k] for _,m in results) for k in ('missing_file_count','parse_error_count','unverified_count')},
               **ZERO_USAGE, 'samples': [m for _,m in results]}
    summary['state'] = 'complete' if all(m['state']=='complete' for _,m in results) else 'needs_review'
    save(output/'summary.json', summary)
    save(output/'resource-explanations.json', {'version': 'resource-explanation-v1', 'items': []})
    save(output/'resource-explanation-contract.json', EXPLANATION_CONTRACT)
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('zip', type=Path)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--workers', default=4, type=int)
    p.add_argument('--verify-c3', action='store_true')
    args = p.parse_args()
    if args.workers < 1:
        p.error('--workers must be positive')
    result = scan_archive(args.zip, args.out, args.workers, args.verify_c3)
    print(json.dumps({k: v for k, v in result.items() if k not in ('duplicate_groups', 'samples')}, ensure_ascii=False))
