"""STEP-01: reconcile material identities and commit coverage after an authoritative save."""
from collections import Counter
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from itertools import chain
from time import time_ns
from uuid import uuid4
from weakref import WeakValueDictionary
import json
import re
import threading

from reader_project_files import relative
from reader_step01_scan import Scanner, digest, stamp, unavailable

VERSION = 'STEP-01'
MODES = ('NORMAL', 'INTRO', 'FINALE', 'ANSWER')
CALCULATION = '(files.coverage_pct + lessons.completion_pct) / 2'
LOCKS = WeakValueDictionary()
LOCK_INDEX = threading.Lock()
SCANNER = Scanner()


class Conflict(ValueError):
    def __init__(self, code, affected=()):
        super().__init__(code)
        self.code = code
        self.affected = sorted(set(affected))


def pct(number):
    return float(Decimal(number).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def valid_legacy(item):
    return (isinstance(item, dict) and type(item.get('covered_bytes')) is int
            and type(item.get('file_size')) is int
            and 0 <= item['covered_bytes'] <= item['file_size'])


def manual_counts(material, stored, state):
    from reader_completion import completion_payload
    from reader_learning import learning_payload

    plan = learning_payload(material, stored, state)
    nodes = [node['id'] for node in plan['nodes']]
    records = state.get('manual_completions', {})
    return sum(completion_payload(material, stored, state, plan={**plan, 'selected_id': nid})['completed']
               for nid in nodes if records.get(nid, {}).get('completed') is True
               and records[nid].get('origin', 'manual_ui') == 'manual_ui'), len(nodes)


def progress(files, completed, total, errors=False):
    present = [row for row in files.values() if row['status'] != 'deleted']
    active = [row for row in present if row['status'] == 'active']
    fractions = [Decimal(row['covered_bytes']) / row['size_bytes'] for row in active]
    file_pct = pct(sum(fractions) * 100 / len(active)) if active else 0.0
    manual_pct = pct(Decimal(completed) * 100 / total) if total else 0.0
    file_full = (bool(active) and len(active) == len(present) and not errors
                 and all(f == 1 and not row['needs_revalidation'] and not row['identity_pending']
                         for f, row in zip(fractions, active)))
    lesson_full = total > 0 and completed == total
    complete = file_full and lesson_full
    return {
        'files': {'coverage_pct': file_pct, 'valid_file_count': len(active),
                  'unavailable_file_count': len(present) - len(active),
                  'status': 'partial_inventory' if errors or len(active) < len(present) else
                            'ready' if any(row['covered_bytes'] for row in active) else 'initialized',
                  'verified_complete': file_full},
        'lessons': {'completed': completed, 'total': total, 'completion_pct': manual_pct,
                    'verified_complete': lesson_full},
        'overall': {'coverage_pct': pct((Decimal(str(file_pct)) + Decimal(str(manual_pct))) / 2),
                    'status': 'ready', 'calculation': CALCULATION},
        'trigger': {'no_uncovered_material': file_full, 'offer_finale': complete,
                    'allowed_actions': ['continue', 'review', 'add_material', 'finale'] if complete else [],
                    'next_interaction_message': ('现有材料都已讲解，你也已标记读完全部讲解。你想继续深入、复习、加入材料，还是做最后一章收尾？'
                                                 if complete else None)}
    }


def schema_defaults():
    return {'manifest_version': 0, 'last_scan_started': 0, 'files': {}, 'inventory': {},
            'events': [], 'rounds': {}, 'commits': {}}


class SQLiteLedger:
    """Use a separate table in the Reader DB; leave ordinary user records alone during scanning."""
    def __init__(self, connector, initial):
        self.connector, self.initial = connector, initial

    def __call__(self, action):
        with self.connector() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('CREATE TABLE IF NOT EXISTS reader_step01 (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
            row = conn.execute('SELECT value FROM reader_step01 WHERE id=1').fetchone()
            persisted = conn.execute('SELECT value FROM reader_state WHERE id=1').fetchone()
            ledger = json.loads(row[0]) if row else schema_defaults()
            state = json.loads(persisted[0]) if persisted else self.initial()
            state_before = deepcopy(state.get('file_coverage', {}))
            result = action(ledger, state)
            value = json.dumps(ledger, ensure_ascii=False, separators=(',', ':'))
            if row is None or row[0] != value:
                conn.execute('INSERT INTO reader_step01 VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value', (value,))
            if state.get('file_coverage', {}) != state_before:
                state['revision'] += 1
                conn.execute('INSERT INTO reader_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',
                             (json.dumps(state, ensure_ascii=False),))
        return result


def change_event(ledger, phase, rid, kind, **fields):
    row = {'sequence': len(ledger['events']) + 1, 'at': stamp(), 'phase': phase, 'round_id': rid,
           'type': kind, 'manifest_version': ledger['manifest_version'], **fields}
    ledger['events'].append(row)
    return row


def register(ledger, state, scan, phase, rid):
    prior = ledger['files']
    rows = scan['rows'].copy()
    for row in prior.values():
        path = row['relative_path']
        if row['status'] != 'deleted' and any(not prefix or path == prefix or path.startswith(prefix + '/')
                                              for prefix in scan['errors']):
            rows.setdefault(path, unavailable(path, 'parent_unavailable'))
    registered = {fid: relative(path) for fid, path in scan['registered'].items()}
    desired = {}
    for fid, name in registered.items():
        desired.setdefault(name, []).append(fid)
        if name not in rows and fid not in prior:
            rows[name] = unavailable(name, 'registered_path_missing')

    by_path = {row['relative_path']: fid for fid, row in sorted(prior.items(),
                key=lambda item: item[1]['status'] != 'deleted')}
    old_keys = Counter(tuple(row['physical_key']) for row in prior.values()
                       if row['status'] != 'deleted' and row['physical_key'])
    new_keys = Counter(tuple(row['physical_key']) for row in rows.values() if row['physical_key'])
    missing = {tuple(row['physical_key']): fid for fid, row in prior.items()
               if row['status'] != 'deleted' and row['relative_path'] not in rows and row['physical_key']
               and old_keys[tuple(row['physical_key'])] == 1}
    events, files, used = [], deepcopy(prior), set()
    history = state.get('file_coverage', {})
    for name, current in sorted(rows.items(), key=lambda item: (item[0] not in desired, item[0])):
        key = tuple(current['physical_key'] or ())
        options = desired.get(name, [])
        pinned_elsewhere = any(registered.get(fid) in rows and registered.get(fid) != name for fid in (by_path.get(name), missing.get(key)) if fid)
        uncertain = (len(options) > 1 or
                     bool(key and (old_keys[key] > 1 or new_keys[key] > 1)
                          and any(row['status'] != 'deleted' and tuple(row['physical_key'] or ()) == key
                                  and row['relative_path'] not in rows for row in prior.values())))
        fid = options[0] if len(options) == 1 else None
        path_id = by_path.get(name)
        if fid and path_id and path_id != fid and prior[path_id]['status'] != 'deleted':
            uncertain = True
        if not fid:
            if path_id not in used and not pinned_elsewhere:
                fid = path_id
            if not fid and key and new_keys[key] == 1 and key in missing and not pinned_elsewhere:
                fid = missing[key] if missing[key] not in used else None
        # A missing registered pathname does not claim the old ID ahead of a verified move.
        if fid in used:
            fid, uncertain = None, True
        if not fid:
            fid = 'material-' + uuid4().hex
        used.add(fid)
        old = prior.get(fid)
        size = current['size_bytes']
        previous_hash = old and (old['content_sha256'] or old.get('last_known_sha256'))
        content_changed = bool(old and current['content_sha256'] and previous_hash
                               and current['content_sha256'] != previous_hash)
        status_changed = bool(old and old['status'] != current['status'])
        compatible = history.get(fid)
        valid_history = valid_legacy(compatible)
        old_size = ((old.get('last_known_size') or old['size_bytes']) if old else
                    compatible['file_size'] if valid_history else None)
        carried = old.get('covered_bytes', 0) if old else compatible['covered_bytes'] if valid_history else 0
        if not old and valid_history and compatible.get('content_sha256') not in (None, current['content_sha256']):
            content_changed = True
        if type(size) is int and size > 0 and old_size:
            carried = size if size < old_size and carried >= old_size else min(carried, size)
        elif type(size) is int and size > 0:
            carried = min(carried, size)
        stale = bool((old and old['needs_revalidation']) or content_changed or
                     (not old and (valid_history and old_size != size or compatible is not None and not valid_history)))
        actual = pct(Decimal(carried * 100) / size) if current['status'] == 'active' else 0.0
        revision = (old['file_revision'] + int(content_changed or status_changed)) if old else 1
        row = {**current, 'file_id': fid, 'file_revision': revision, 'covered_bytes': carried,
               'progress_pct': actual, 'needs_revalidation': stale or current['status'] != 'active',
               'identity_pending': uncertain, 'registered_file_ids': options or (old or {}).get('registered_file_ids', []),
               'discovered_at': old['discovered_at'] if old else stamp(),
               'last_updated_round': (rid if not old or content_changed or status_changed
                                      or old['relative_path'] != name else old['last_updated_round']),
               'last_known_sha256': (current['content_sha256'] if current['status'] == 'active' else previous_hash),
               'last_known_size': size if current['status'] == 'active' else old_size,
               'verified_ranges': [] if content_changed else (old or {}).get('verified_ranges', [])}
        files[fid] = row
        kinds = []
        if not old:
            kinds.append('discovered')
        else:
            if old['relative_path'] != name:
                kinds.append('moved')
            if content_changed:
                kinds.append('content_changed')
            if status_changed:
                kinds.append('restored' if current['status'] == 'active' else 'unavailable')
        if current['status'] == 'unavailable' and (not old or old['reason'] != current['reason']) and 'unavailable' not in kinds:
            kinds.append('unavailable')
        if uncertain and (not old or not old['identity_pending']):
            kinds.append('identity_conflict')
        for kind in kinds:
            events.append({'type': kind, 'file_id': fid, 'before': old, 'after': row})
    for fid, old in prior.items():
        if fid not in used and old['status'] != 'deleted':
            row = {**old, 'status': 'deleted', 'reason': 'not_in_authorized_material_tree',
                   'last_updated_round': rid, 'file_revision': old['file_revision'] + 1}
            files[fid] = row
            events.append({'type': 'deleted', 'file_id': fid, 'before': old, 'after': row})
    inventory = {k: scan[k] for k in ('unsupported', 'excluded', 'errors')}
    # Modification times and excluded outputs don't invalidate a write. Content and valid membership do.
    ordering_fields = ('status', 'relative_path', 'file_revision', 'content_sha256', 'needs_revalidation',
                       'identity_pending', 'covered_bytes')
    def identities(items):
        return {fid: tuple(row[k] for k in ordering_fields) for fid, row in items.items()}
    if identities(prior) != identities(files) or ledger['inventory'].get('errors', {}) != inventory['errors']:
        ledger['manifest_version'] += 1
    ledger.update(files=files, inventory=inventory)
    for event in events:
        change_event(ledger, phase, rid, event.pop('type'), **event)


def scope(files):
    return {fid: (row['file_revision'], row['content_sha256']) for fid, row in files.items()
            if row['status'] == 'active' and not row['identity_pending']}


def stamp_manifest(project, ledger, counts):
    files = ledger['files']
    manifest = {'step': VERSION, 'project_id': project, 'manifest_version': ledger['manifest_version'],
                'files': [{**row, 'manifest_version': ledger['manifest_version']} for row in files.values()],
                'inventory': ledger['inventory'], 'progress': progress(files, *counts, ledger['inventory'].get('errors'))}
    manifest['snapshot_hash'] = digest({**manifest, 'files': sorted(manifest['files'], key=lambda row: row['file_id'])})
    return manifest


def intervals(ranges, size):
    if not isinstance(ranges, list) or not ranges:
        raise ValueError('覆盖提案必须有可核对的字节范围')
    for row in ranges:
        if (not isinstance(row, (list, tuple)) or len(row) != 2
                or any(type(n) is not int for n in row) or not 0 <= row[0] < row[1] <= size):
            raise ValueError('覆盖提案含有越界或空的字节范围')
    combined = []
    for start, end in sorted(ranges):
        if combined and start <= combined[-1][1]:
            combined[-1][1] = max(end, combined[-1][1])
        else:
            combined.append([start, end])
    return combined


class Step01:
    def __init__(self, project, config, transaction, manual, *, scanner=SCANNER, receipt_lookup=None):
        self.project, self.config, self.transaction = project, config, transaction
        self.manual, self.scanner, self.receipt_lookup = manual, scanner, receipt_lookup
        with LOCK_INDEX:
            self.lock = LOCKS.get(project)
            if self.lock is None:
                self.lock = LOCKS[project] = threading.RLock()

    def _scan(self, phase, rid, action):
        with self.lock:
            for _ in range(3):
                started = time_ns()
                scan = self.scanner.scan(self.config())
                def swap(ledger, state):
                    if started < ledger['last_scan_started']:
                        return None
                    ledger['last_scan_started'] = started
                    register(ledger, state, scan, phase, rid)
                    return action(ledger, state, scan['metrics'])
                result = self.transaction(swap)
                if result is not None:
                    return result
        raise Conflict('scan_superseded')

    def prepare(self, mode, round_id=None):
        if mode not in MODES:
            raise ValueError('写作模式必须是 NORMAL、INTRO、FINALE 或 ANSWER')
        rid = round_id or 'step01-' + uuid4().hex
        if not isinstance(rid, str) or not re.fullmatch(r'step01-[a-f0-9]{32}', rid):
            raise ValueError('轮次编号无效')
        def put(ledger, state, metrics):
            existing = ledger['rounds'].get(rid)
            if existing:
                if existing['mode'] != mode:
                    raise Conflict('mode_mismatch')
                return existing['m1']
            manifest = stamp_manifest(self.project, ledger, self.manual(state))
            m1 = {'round_id': rid, 'mode': mode, 'phase': 'M1', 'manifest': manifest, 'scan_metrics': metrics}
            ledger['rounds'][rid] = {'mode': mode, 'm1': m1, 'selected': None, 'pending': None,
                                     'last_event': len(ledger['events'])}
            change_event(ledger, 'M1', rid, 'started', trigger=manifest['progress']['trigger'])
            return m1
        return self._scan('M1', rid, put)

    def _round(self, ledger, rid):
        if not isinstance(rid, str) or rid not in ledger['rounds']:
            raise ValueError('找不到这一轮的文件清单')
        return ledger['rounds'][rid]

    def select(self, rid, file_ids):
        if not isinstance(file_ids, list) or any(not isinstance(fid, str) or not fid for fid in file_ids):
            raise ValueError('请选择本轮实际会使用的文件编号')
        def bind(ledger, state, metrics):
            row = self._round(ledger, rid)
            opening = scope({item['file_id']: item for item in row['m1']['manifest']['files']})
            current = scope(ledger['files'])
            invalid = [fid for fid in file_ids if fid not in opening or opening[fid] != current.get(fid)]
            if invalid:
                return {'status': 'selected_material_changed', 'affected_file_ids': sorted(set(invalid))}
            selected = {fid: list(opening[fid]) for fid in sorted(set(file_ids))}
            if row['selected'] is not None and selected != row['selected']:
                return {'status': 'selection_already_bound', 'affected_file_ids': sorted(set(selected) ^ set(row['selected']))}
            row['selected'] = selected
            return {'status': 'selected', 'selected_files': selected}
        return self._scan('selection', rid, bind)

    def observe(self, rid):
        return self._checkpoint(rid, False)

    def reconcile(self, rid):
        return self._checkpoint(rid, True)

    def _checkpoint(self, rid, final):
        phase = 'M2' if final else 'watch'
        def verify(ledger, state, metrics):
            row = self._round(ledger, rid)
            current = scope(ledger['files'])
            selected = row['selected'] or {}
            invalid = sorted(fid for fid, sig in selected.items() if tuple(sig) != current.get(fid))
            manifest = stamp_manifest(self.project, ledger, self.manual(state))
            changes = [e for e in ledger['events'][row['last_event']:]
                       if e['type'] in ('discovered', 'deleted', 'content_changed', 'moved', 'unavailable',
                                        'restored', 'identity_conflict')]
            row['last_event'] = len(ledger['events'])
            result = {'round_id': rid, 'phase': phase, 'manifest': manifest, 'changes': changes,
                      'affected_file_ids': invalid, 'scan_metrics': metrics,
                      'status': 'selected_material_changed' if invalid else 'selection_required'
                                if final and row['selected'] is None else 'ready'}
            if final:
                row['m2'] = result
                row['pending'] = None if result['status'] != 'ready' else {
                    'round_id': rid, 'status': 'awaiting_save', 'expected_manifest_version': ledger['manifest_version'],
                    'snapshot_hash': manifest['snapshot_hash'], 'selected_files': selected}
                result['pending_progress_update'] = row['pending']
                change_event(ledger, phase, rid, 'reconciled', status=result['status'], affected_file_ids=invalid)
            return result
        return self._scan(phase, rid, verify)

    def commit_progress_after_success(self, rid, commit_id, expected_manifest_version, proposals):
        """The save adapter must read a durable receipt. This method is not a public API."""
        if self.receipt_lookup is None:
            raise ValueError('正式保存还没有为文件进度提供可信回执')
        if not isinstance(commit_id, str) or not commit_id or len(commit_id) > 200 or type(expected_manifest_version) is not int:
            raise ValueError('保存编号或预计清单版本无效')
        if not isinstance(proposals, list) or any(not isinstance(row, dict) for row in proposals):
            raise ValueError('覆盖提案需要按文件提供')
        key = digest([rid, commit_id, expected_manifest_version])
        proposal_hash = digest(proposals)
        receipt = self.receipt_lookup(commit_id)
        def begin(ledger, state):
            row = self._round(ledger, rid)
            if key in ledger['commits']:
                old = ledger['commits'][key]
                if old['proposal_hash'] != proposal_hash:
                    raise Conflict('replay_proposals_differ')
                if old.get('result'):
                    return {**old['result'], 'replayed': True}
            if (not receipt or receipt.get('status') != 'saved' or receipt.get('project_id') != self.project
                    or receipt.get('round_id') != rid or receipt.get('commit_id') != commit_id
                    or not isinstance(receipt.get('coverage_by_file'), dict)):
                change_event(ledger, 'commit', rid, 'save_not_confirmed', commit_id=commit_id)
                return {'status': 'save_not_confirmed'}
            ledger['commits'].setdefault(key, {'proposal_hash': proposal_hash, 'commit_id': commit_id,
                                              'expected_manifest_version': expected_manifest_version, 'round_id': rid})
            if row['pending']:
                row['pending'] = {**row['pending'], 'status': 'saved_awaiting_progress', 'commit_id': commit_id}
            change_event(ledger, 'commit', rid, 'save_confirmed', commit_id=commit_id)
            return None

        early = self.transaction(begin)
        if early is not None and (early['status'] != 'applied' or 'post_commit_scan_metrics' in early):
            return early

        def apply(ledger, state, metrics):
            row = self._round(ledger, rid)
            current = scope(ledger['files'])
            if ledger['commits'][key].get('result'):
                return {**ledger['commits'][key]['result'], 'replayed': True}
            selected = row['selected'] or {}
            invalid = [fid for fid, sig in selected.items() if tuple(sig) != current.get(fid)]
            pending = row['pending']
            if invalid or not pending or pending['expected_manifest_version'] != expected_manifest_version or ledger['manifest_version'] != expected_manifest_version:
                code = 'selected_material_changed' if invalid else 'manifest_version_conflict'
                change_event(ledger, 'commit', rid, 'rejected', status=code, affected_file_ids=invalid,
                             expected_manifest_version=expected_manifest_version, commit_id=commit_id)
                return {'status': code, 'affected_file_ids': sorted(invalid),
                        'manifest_version': ledger['manifest_version']}
            evidence = receipt['coverage_by_file']
            updates = {}
            for proposal in proposals:
                fid = proposal.get('file_id')
                if not isinstance(fid, str) or fid not in selected or fid in updates:
                    raise ValueError('覆盖提案重复，或文件未在本轮选用')
                target = ledger['files'][fid]
                proof = evidence.get(fid, {})
                if (not isinstance(proof, dict)
                        or proof.get('file_revision') != target['file_revision']
                        or proof.get('content_sha256') != target['content_sha256']
                        or proposal.get('file_revision') != target['file_revision']
                        or proposal.get('content_sha256') != target['content_sha256']):
                    raise ValueError('正式保存的覆盖证据不属于当前文件版本')
                ids = proposal.get('source_ids')
                source_ranges = proof.get('source_ranges')
                if (not isinstance(ids, list) or not ids or any(not isinstance(s, str) or not s for s in ids)
                        or not isinstance(source_ranges, dict) or not set(ids).issubset(source_ranges)):
                    raise ValueError('覆盖提案引用了没有随讲解保存的来源编号')
                size = target['size_bytes']
                proven = intervals(list(chain.from_iterable(
                    intervals(source_ranges[sid], size) for sid in ids)), size)
                ranges = intervals(proposal.get('byte_ranges'), size)
                if any(not any(a <= start < end <= b for a, b in proven) for start, end in ranges):
                    raise ValueError('覆盖提案超出了已正式保存的讲解范围')
                merged = intervals(list(chain(target['verified_ranges'], ranges)), size)
                verified = sum(end - start for start, end in merged)
                n = max(target['covered_bytes'], verified)
                updates[fid] = {**target, 'covered_bytes': n, 'progress_pct': pct(Decimal(n * 100) / size),
                                'verified_ranges': merged, 'last_updated_round': rid,
                                'needs_revalidation': target['needs_revalidation'] and verified != size}
            if any(updates[fid] != ledger['files'][fid] for fid in updates):
                for fid, target in updates.items():
                    ledger['files'][fid] = target
                    history = state.setdefault('file_coverage', {})
                    history[fid] = {**history.get(fid, {}), 'file_size': target['size_bytes'],
                                    'covered_bytes': target['covered_bytes'], 'content_sha256': target['content_sha256']}
                ledger['manifest_version'] += 1
            row['pending'] = {**pending, 'status': 'applied', 'commit_id': commit_id}
            manifest = stamp_manifest(self.project, ledger, self.manual(state))
            result = {'status': 'applied', 'commit_id': commit_id, 'round_id': rid, 'replayed': False,
                      'updated_file_ids': sorted(updates), 'manifest': manifest, 'scan_metrics': metrics}
            ledger['commits'][key]['result'] = result
            change_event(ledger, 'commit', rid, 'progress_applied', commit_id=commit_id,
                         file_ids=sorted(updates), trigger=manifest['progress']['trigger'])
            return result
        try:
            result = early if early is not None else self._scan('commit', rid, apply)
        except (Conflict, ValueError) as error:
            def reject(ledger, state):
                change_event(ledger, 'commit', rid, 'rejected', status='invalid_proposal',
                             detail=str(error), commit_id=commit_id)
            self.transaction(reject)
            raise
        if result['status'] != 'applied' or 'post_commit_scan_metrics' in result:
            return result
        def recheck(ledger, state, metrics):
            latest = stamp_manifest(self.project, ledger, self.manual(state))
            new_version = latest['manifest_version'] != result['manifest']['manifest_version']
            public = {**result, 'manifest': latest, 'post_commit_scan_metrics': metrics,
                      'changed_during_commit': new_version}
            ledger['commits'][key]['result'] = {**public, 'replayed': False}
            if new_version:
                change_event(ledger, 'post_commit', rid, 'inventory_changed_after_save',
                             commit_id=commit_id, trigger=latest['progress']['trigger'])
            return public
        return self._scan('post_commit', rid, recheck)
