"""Discover and fingerprint the original files under a Reader project's material roots."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from itertools import islice
from pathlib import Path
from time import perf_counter, sleep
import json
import os
import stat
import threading

from .paths import IMAGES, OFFICE, PAGED, TEXT, relative

SUPPORTED = IMAGES | OFFICE | PAGED | TEXT | {
    '.pdf', '.zip', '.srt', '.vtt', '.ass', '.ssa', '.scc', '.url', '.mhtml',
}
CHUNK_BYTES = 1024 * 1024
DEFAULT_IO = max(1, min(os.cpu_count() or 2, 10))
SYSTEM_DIRS = {'.git', '.reader', '.reader-cache', '.reader-preview', '__pycache__'}
ROOT_OUTPUTS = {'explanations', '讲解草稿', 'file-previews', 'reading-workspace', 'reading-files', 'runs'}


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(',', ':')).encode()).hexdigest()


def link(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))


def inside(root, name):
    path = root
    for part in Path(name).parts:
        path = path / part
        if link(path.lstat()):
            raise OSError('linked_path')
    return path


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def unavailable(name, reason, info=None):
    return {'relative_path': name, 'status': 'unavailable', 'reason': reason,
            'size_bytes': info.st_size if info and stat.S_ISREG(info.st_mode) else None,
            'content_sha256': None, 'modified_ns': info.st_mtime_ns if info else None,
            'physical_key': None}


def fingerprint(root, name):
    for attempt in range(3):
        info = None
        try:
            path = inside(root, name)
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                return unavailable(name, 'not_regular_file', info)
            with path.open('rb') as stream:
                opened = os.fstat(stream.fileno())
                # On Windows the path and handle APIs can report different ctime values
                # for the same file. Compare each view with itself over the read.
                if signature(opened)[:-1] != signature(info)[:-1]:
                    raise OSError('changed_while_opening')
                encoded = sha256()
                total = 0
                while data := stream.read(CHUNK_BYTES):
                    encoded.update(data)
                    total += len(data)
                ended = os.fstat(stream.fileno())
            if not (signature(opened) == signature(ended)
                    and signature(info) == signature(inside(root, name).lstat())
                    and total == info.st_size):
                raise OSError('changed_while_hashing')
            return {'relative_path': name, 'status': 'active' if total else 'unavailable',
                    'reason': None if total else 'empty_file', 'size_bytes': total,
                    'content_sha256': encoded.hexdigest(), 'modified_ns': info.st_mtime_ns,
                    'physical_key': [info.st_dev, info.st_ino] if info.st_ino else None}
        except (FileNotFoundError, PermissionError) as error:
            return unavailable(name, type(error).__name__, info)
        except OSError as error:
            if attempt == 2 or str(error) == 'linked_path':
                return unavailable(name, str(error) if error.errno is None else type(error).__name__, info)
            sleep(.025 * (attempt + 1))


class Scanner:
    def __init__(self, workers=DEFAULT_IO):
        self.workers = max(1, workers)
        self.pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix='reader-material')
        self.slots = threading.BoundedSemaphore(self.workers * 2)
        self.lock = threading.Lock()
        self.active = self.peak = 0

    def close(self):
        self.pool.shutdown()

    def scan(self, config):
        began = perf_counter()
        configured_root = Path(config['root'])
        if link(configured_root.lstat()):
            raise ValueError('材料目录不能通过符号链接或目录联接指向其他位置')
        root = configured_root.resolve(strict=True)
        registered = config.get('sources', {})
        registered_paths = {relative(path) for path in registered.values()}
        exclusions = {relative(item) for item in config.get('non_material_paths', [])} | ROOT_OUTPUTS
        # Only folders marked by the importer's archive rule are considered derived copies.
        exclusions.update(relative(item) for item in config.get('derived_paths', []))
        # An independently registered original takes precedence over an extracted-copy exclusion.
        archives = {p for p in exclusions if not any(x == p or x.startswith(p + '/') for x in registered_paths)}
        roots = {relative(p, empty=True) for p in config.get('material_roots', ['source', 'sources'])}
        roots = {p for p in roots if not any(p != other and (not other or p.startswith(other + '/')) for other in roots)}
        findings, unsupported, errors, excluded, names = {}, set(), {}, set(), set()

        def ignored(name):
            return (any(name == entry or name.startswith(entry + '/') for entry in archives)
                    or any(part in SYSTEM_DIRS or part.startswith(('.reader-write-', '.quality-'))
                           for part in Path(name).parts)
                    or Path(name).suffix.lower() in ('.tmp', '.part', '.crdownload', '.swp')
                    or name.endswith('.next.json'))

        def add(name):
            if ignored(name):
                excluded.add(name)
            elif Path(name).suffix.lower() not in SUPPORTED and name not in registered_paths:
                unsupported.add(name)
            else:
                names.add(name)

        def visit(folder, prefix):
            try:
                with os.scandir(folder) as children:
                    for item in children:
                        name = (prefix + '/' if prefix else '') + item.name
                        if ignored(name):
                            excluded.add(name)
                            continue
                        try:
                            info = item.stat(follow_symlinks=False)
                            if link(info):
                                findings[name] = unavailable(name, 'linked_path', info)
                            elif stat.S_ISDIR(info.st_mode):
                                # Recheck all parents; an atomic replacement can occur after scandir.
                                visit(inside(root, name), name)
                            else:
                                add(name)
                        except OSError as error:
                            errors[name] = type(error).__name__
            except OSError as error:
                errors[prefix] = type(error).__name__

        for name in sorted(roots):
            try:
                visit(inside(root, name) if name else root, name)
            except FileNotFoundError:
                if name not in ('source', 'sources') or 'material_roots' in config:
                    errors[name] = 'material_root_missing'
            except OSError as error:
                errors[name] = str(error) if error.errno is None else type(error).__name__
        spelling = {os.path.normcase(name): name for name in names | findings.keys()}
        registered = {fid: spelling.get(os.path.normcase(relative(name)), relative(name))
                      for fid, name in registered.items()}
        for name in set(registered.values()):
            if name in names or name in findings:
                continue
            try:
                inside(root, name)
            except FileNotFoundError:
                continue
            except OSError as error:
                findings[name] = unavailable(name, str(error) if error.errno is None else type(error).__name__)
            else:
                add(name)

        timings = {'peak_file_reads': 0, 'queue_seconds': 0.0, 'file_read_seconds': 0.0}
        local_active = 0
        def job(name, queued):
            nonlocal local_active
            start = perf_counter()
            with self.lock:
                local_active += 1
                self.active += 1
                self.peak = max(self.peak, self.active)
                timings['peak_file_reads'] = max(timings['peak_file_reads'], local_active)
                timings['queue_seconds'] += start - queued
            try:
                return fingerprint(root, name)
            finally:
                with self.lock:
                    local_active -= 1
                    self.active -= 1
                    timings['file_read_seconds'] += perf_counter() - start
                self.slots.release()

        ordered = iter(sorted(names - findings.keys()))
        while batch := list(islice(ordered, self.workers)):
            pending = []
            for name in batch:
                queued = perf_counter()
                self.slots.acquire()
                try:
                    pending.append(self.pool.submit(job, name, queued))
                except BaseException:
                    self.slots.release()
                    raise
            for future in pending:
                row = future.result()
                findings[row['relative_path']] = row
        timings['wall_seconds'] = perf_counter() - began
        timings['bytes_hashed'] = sum(row['size_bytes'] or 0 for row in findings.values() if row['content_sha256'])
        return {'rows': findings, 'registered': registered.copy(), 'unsupported': sorted(unsupported),
                'excluded': sorted(excluded), 'errors': errors, 'metrics': timings}
