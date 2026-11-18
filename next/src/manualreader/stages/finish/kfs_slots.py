"""Share KFS in-flight requests across Reader processes on the same machine."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, closing
from email.utils import parsedate_to_datetime
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
from tempfile import gettempdir
from time import time
from uuid import uuid4


DEFAULT_DS_CONCURRENCY = 20
_LEASE_SECONDS = 300
_STATE_WORKERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix='reader-kfs-state')
OVERLOAD_STATUSES = {502, 503, 504, 524}


def retry_after_seconds(value, now):
    try:
        return max(0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - now)
        except (TypeError, ValueError, OverflowError):
            return 0


class KfsUnavailable(RuntimeError):
    pass


class KfsSlots:
    name = 'ds'

    def __init__(self, endpoint, credential, maximum=DEFAULT_DS_CONCURRENCY, *, directory=None):
        root = Path(directory or os.environ.get('MANUAL_READER_DS_STATE_DIR')
                    or Path(gettempdir()) / 'manualreader-kfs')
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = sha256(f'{endpoint}\n{credential}'.encode()).hexdigest()
        self.path = root / ('v2-' + key + '.sqlite3')
        self.maximum = maximum
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS leases (id TEXT PRIMARY KEY, expires REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS lane (id INTEGER PRIMARY KEY, width INTEGER, '
                       'pause REAL, blocked REAL, successes INTEGER, errors INTEGER, error_at REAL, probe_at REAL)')
            db.execute('INSERT OR IGNORE INTO lane VALUES (1, ?, 0, 0, 0, 0, 0, 0)',
                       (DEFAULT_DS_CONCURRENCY,))
        self.admission = asyncio.Lock()
        self.peak = 0
        self.changes = []

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    async def state(self, method, *args):
        return await asyncio.get_running_loop().run_in_executor(_STATE_WORKERS, method, *args)

    def acquire(self, lease):
        now = time()
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            width, pause, blocked, probe_at = db.execute(
                'SELECT width,pause,blocked,probe_at FROM lane WHERE id=1').fetchone()
            if blocked > now:
                raise KfsUnavailable('KFS 拒绝了本轮翻译请求，已保存此前返回的结果')
            if pause > now:
                return None
            if probe_at and now >= probe_at and width < self.maximum:
                width = min(self.maximum, width + max(1, width//4))
                db.execute('UPDATE lane SET width=?,probe_at=? WHERE id=1',
                           (width, now+12 if width < self.maximum else 0))
            db.execute('DELETE FROM leases WHERE expires<=?', (now,))
            busy = db.execute('SELECT COUNT(*) FROM leases').fetchone()[0]
            if busy >= min(width, self.maximum):
                return None
            db.execute('INSERT INTO leases VALUES (?, ?)', (lease, now+_LEASE_SECONDS))
            return busy+1

    def renew(self, lease):
        with closing(self.connect()) as db, db:
            db.execute('UPDATE leases SET expires=? WHERE id=?', (time()+_LEASE_SECONDS, lease))

    def release(self, lease):
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM leases WHERE id=?', (lease,))

    async def heartbeat(self, lease):
        while True:
            await asyncio.sleep(_LEASE_SECONDS/3)
            await self.state(self.renew, lease)

    @asynccontextmanager
    async def slot(self):
        lease = uuid4().hex
        request = None
        heartbeat = None
        try:
            async with self.admission:
                while True:
                    request = asyncio.create_task(self.state(self.acquire, lease))
                    busy = await asyncio.shield(request)
                    if busy is not None:
                        break
                    await asyncio.sleep(.1)
            self.peak = max(self.peak, busy)
            heartbeat = asyncio.create_task(self.heartbeat(lease))
            yield
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            if request is not None:
                [result] = await asyncio.gather(request, return_exceptions=True)
                if isinstance(result, int):
                    await self.state(self.release, lease)

    def adjust(self, attempts, good):
        now = time()
        codes = {row.get('status') for row in attempts}
        if not good and not codes.intersection({401, 403, 429} | OVERLOAD_STATUSES):
            return None
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            width, pause, blocked, successes, errors, error_at, probe_at = db.execute(
                'SELECT width,pause,blocked,successes,errors,error_at,probe_at FROM lane WHERE id=1').fetchone()
            before = width
            cause = 'successful responses'
            if codes.intersection({401, 403}):
                blocked = max(blocked, now+30)
            elif 429 in codes:
                cause = 'HTTP 429'
                wait = max(1, *(retry_after_seconds(r.get('retry_after'), now) for r in attempts))
                if pause <= now:
                    width = max(1, width - max(1, width//3))
                pause = max(pause, now+wait)
                probe_at = pause + 12
                successes = 0
            elif codes.intersection(OVERLOAD_STATUSES):
                cause = 'transient HTTP errors'
                errors = errors+1 if now-error_at <= 8 else 1
                error_at = now
                successes = 0
                if errors >= 2 and pause <= now:
                    width = min(width, max(6, width // 2))
                    pause = now + min(8, 1+errors)
                    probe_at = pause + 12
            elif now >= max(pause, blocked):
                errors = 0
                successes += 1
                if successes >= max(4, width) and width < self.maximum:
                    width += 1
                    successes = 0
            db.execute('UPDATE lane SET width=?,pause=?,blocked=?,successes=?,errors=?,error_at=?,probe_at=? WHERE id=1',
                       (width, pause, blocked, successes, errors, error_at, probe_at))
        if width != before:
            return {'at':now,'provider':'ds','before':before,'after':width,
                    'cause':cause}
        return None

    async def observed(self, attempts, good):
        change = await self.state(self.adjust, attempts, good)
        if change:
            self.changes.append(change)
