"""按材料目录的当前文件集合计算讲解关联；新文件在登记前按零计算。"""
from decimal import Decimal, ROUND_HALF_UP
from hashlib import file_digest
from pathlib import Path
from threading import RLock

from reader_step01_scan import Scanner

SCANNER = Scanner()
LOCK = RLock()
CACHE = {}


def original_hash(path):
    info = path.stat()
    signature = (str(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    with LOCK:
        if signature not in CACHE:
            with path.open('rb') as source:
                CACHE[signature] = file_digest(source, 'sha256').hexdigest()
            if len(CACHE) > 512:
                CACHE.pop(next(iter(CACHE)))
        return CACHE[signature]


def directory_association(project, workspace, coverage):
    config = project.ensure()
    scan = SCANNER.scan(config)
    by_path = {name: fid for fid, name in config['sources'].items()}
    registered = {row['id']: row for row in workspace.read()['files']}
    metrics = {row['file_id']: row['explanation'] for row in coverage.get('files', [])}
    rows = []
    for name, actual in sorted(scan['rows'].items()):
        if actual['status'] != 'active':
            continue
        identity = by_path.get(name)
        entry = registered.get(identity)
        current = False
        if entry:
            stored = workspace.files / identity / entry['stored_name']
            current = stored.is_file() and original_hash(stored) == actual['content_sha256']
        elif identity == 'primary' and project.original.get('original_file'):
            stored = (workspace.folder / project.original['original_file']).resolve()
            current = stored.is_file() and original_hash(stored) == actual['content_sha256']
        metric = metrics.get(identity, {})
        percent = Decimal(str(metric.get('percent') or 0)) if current else Decimal(0)
        rows.append({'path': name, 'file_id': identity, 'content_sha256': actual['content_sha256'],
                     'percent': float(percent), 'registered_current_version': current})
    percent = sum((Decimal(str(row['percent'])) for row in rows), Decimal(0)) / len(rows) if rows else Decimal(0)
    return {'scope': 'material_directories', 'roots': config.get('material_roots', ['source', 'sources']),
            'percent': float(percent.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)),
            'files': rows, 'total': len(rows), 'unregistered': sum(not row['registered_current_version'] for row in rows),
            'errors': scan['errors'], 'calculation': '当前有效材料文件的讲解关联百分比等权平均'}
