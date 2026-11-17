"""Content-addressed documents and carriers written atomically."""

from concurrent.futures import Future
from hashlib import sha256
import json
from pathlib import Path
from threading import Lock
from time import perf_counter, sleep
from uuid import uuid4


def digest(value):
    data = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return sha256(data).hexdigest()


def atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        temporary.write_bytes(raw)
        for attempt in range(6):
            try:
                temporary.replace(path)
                break
            except PermissionError as error:
                if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt==5:
                    raise
                sleep(.015*(2**attempt))
    finally:
        temporary.unlink(missing_ok=True)


class Cache:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = Lock()
        self.flights = {}

    def blob(self, data):
        identity = digest(data)
        path = self.root/'blobs'/identity
        if not path.is_file() or digest(path.read_bytes()) != identity:
            atomic(path, data)
        return identity

    def carrier(self, identity):
        if not isinstance(identity, str) or len(identity) != 64 or any(
                c not in '0123456789abcdef' for c in identity):
            raise ValueError('载体哈希无效')
        data = (self.root/'blobs'/identity).read_bytes()
        if digest(data) != identity:
            raise ValueError('载体字节与登记哈希不同')
        return data

    def calculate(self, namespace, inputs, build, *, refresh=False):
        key = namespace, digest(inputs)
        path = self.root/namespace/f'{key[1]}.json'
        wait = perf_counter()
        with self.lock:
            future = self.flights.get(key)
            if future is None:
                future = Future()
                self.flights[key] = future
                owner = True
            else:
                owner = False
        if not owner:
            return future.result(), 'shared', (perf_counter()-wait)*1000
        try:
            try:
                if refresh:
                    raise ValueError('重新从已经授权的原件读取')
                envelope = json.loads(path.read_bytes())
                data = envelope['data']
                if envelope['key'] != key[1] or envelope['sha256'] != digest(data):
                    raise ValueError('缓存内容损坏')
                hit = 'disk'
            except (OSError, ValueError, TypeError, KeyError):
                data = build()
                if data.get('cacheable', True):
                    raw = json.dumps({'key':key[1], 'sha256':digest(data), 'data':data},
                                     ensure_ascii=False, separators=(',', ':')).encode()
                    atomic(path, raw)
                hit = 'miss'
            future.set_result(data)
            return data, hit, 0.0
        except Exception as error:
            future.set_exception(error)
            raise
        finally:
            with self.lock:
                self.flights.pop(key, None)
