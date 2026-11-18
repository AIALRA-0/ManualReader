"""Read the project inventory around existing Reader writers; no progress is written here."""
import json
import os
from pathlib import Path
from threading import Event, RLock, Thread


class MaterialRound:
    def __init__(self, bridge, out, mode):
        self.bridge, self.out, self.mode = bridge, Path(out), mode
        self.stop, self.lock = Event(), RLock()
        self.selected = False

    def _call(self, action, **fields):
        return self.bridge.request('/api/step01', {'action': action, 'round_id': self.rid, **fields})

    def _log(self, name, payload):
        path = self.out / (name + '.json')
        temp = path.with_suffix('.next.json')
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temp, path)

    def __enter__(self):
        self.start = self.bridge.request('/api/step01', {'action': 'prepare', 'mode': self.mode})
        self.rid = self.start['round_id']
        self._log('step01-start', self.start)
        try:
            interval = max(1, min(300, float(os.environ.get('MANUAL_READER_STEP01_POLL_SECONDS', '8'))))
        except ValueError:
            interval = 8
        self.thread = Thread(target=self._watch, args=(interval,), daemon=True, name='reader-step01-watch')
        self.thread.start()
        return self

    def _watch(self, interval):
        while not self.stop.wait(interval):
            try:
                result = self._call('observe')
                if result['changes'] or result['affected_file_ids']:
                    with self.lock:
                        if not self.stop.is_set():
                            with (self.out / 'step01-events.jsonl').open('a', encoding='utf-8') as stream:
                                stream.write(json.dumps(result, ensure_ascii=False) + '\n')
            except Exception as error:
                with self.lock:
                    if not self.stop.is_set():
                        self._log('step01-observer-error', {'error': str(error), 'final_rescan_required': True})

    def select(self, file_ids):
        result = self._call('select', file_ids=sorted(set(file_ids)))
        self._log('step01-selection', result)
        if result['status'] != 'selected':
            raise ValueError('选用的原件与本轮开始时不一致，请核对文件后重新准备。'
                             + '、'.join(result.get('affected_file_ids', [])))
        self.selected = True

    def reconcile(self):
        with self.lock:
            result = self._call('reconcile')
            self._log('step01-reconcile', result)
        if result['status'] != 'ready':
            raise ValueError('本轮选用的材料未登记或已经变动，原稿已保留。'
                             + '、'.join(result.get('affected_file_ids', [])))
        return result

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=.2)


def answer_file_ids(payloads, manifest):
    stored_ids = {row['file_id'] for row in manifest['files']}
    ids = set()
    for packet in payloads:
        rows = packet.get('reference_blocks', [])
        sources = packet.get('sources', {})
        rows = [*rows, *(sources.values() if isinstance(sources, dict) else sources)]
        for row in rows:
            if not isinstance(row, dict) or row.get('explanation_id') or row.get('content_kind') == 'explanation':
                continue
            fid = row.get('file_id')
            if fid:
                ids.add(fid)
            elif 'primary' in stored_ids and row.get('content_kind', 'source') == 'source':
                ids.add('primary')
    return ids
