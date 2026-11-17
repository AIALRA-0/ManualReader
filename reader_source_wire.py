"""Standard-library helpers for transporting already verified source images."""
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

MAX_IMAGE_BYTES=30*1024*1024


def digest(data):
    return sha256(data).hexdigest()


def put(path,value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    data=value if isinstance(value,bytes) else (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.quality-',delete=False) as out:
        tmp=Path(out.name)
        out.write(data)
    try:
        try:
            os.replace(tmp,path)
        except PermissionError:
            if not path.is_file() or path.read_bytes()!=data:
                raise
    finally:
        tmp.unlink(missing_ok=True)


def file_hash(path):
    with Path(path).open('rb') as f:
        return __import__('hashlib').file_digest(f,'sha256').hexdigest()


def visual_expectations(payload):
    expected={}
    def walk(obj):
        if isinstance(obj,dict):
            for record in obj.get('source_evidence',[]):
                idx=record.get('attachment_number'); sha=record.get('sha256')
                if not isinstance(idx,int) or idx<1 or not isinstance(sha,str):
                    raise ValueError('视觉来源缺少图片序号或校验值')
                if idx in expected and expected[idx]!=sha:
                    raise ValueError('同一序号指向了不同原图')
                expected[idx]=sha
            for key,value in obj.items():
                if key!='source_evidence':
                    walk(value)
        elif isinstance(obj,(list,tuple)):
            for value in obj:
                walk(value)
    walk(payload)
    return expected


def require_visual_payload(payload, images):
    expected=visual_expectations(payload)
    for i,sha in expected.items():
        if i>len(images) or file_hash(images[i-1])!=sha:
            raise ValueError('视觉来源的原图没有进入本次模型请求')
    return expected
