"""Read and atomically store versioned Reader JSON artifacts."""

import json

from .stages.step03.cache import atomic


def read_json(path):
    return json.loads(path.read_bytes())


def write_json(path, value):
    data = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    atomic(path, data + b'\n')
