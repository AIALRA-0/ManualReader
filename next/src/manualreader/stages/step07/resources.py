"""Scan Reader Markdown with its own parser and verify local image bytes."""

from collections import defaultdict
from functools import lru_cache
from importlib.resources import files
import json
import re
import subprocess

from ..step05.contracts import digest


MEDIA_PATH = re.compile(r'assets/([a-f0-9]{64})\.png\Z')


def scan_batch(texts):
    stage = files(__package__)
    vendor = files('manualreader.stages.step04').joinpath('markdown-vendor.js')
    result = subprocess.run(['node',str(stage.joinpath('resource-scanner.cjs')),str(vendor),'--batch'],
                            input=json.dumps(texts,ensure_ascii=False).encode(),capture_output=True,
                            timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf8',errors='replace'))
    return json.loads(result.stdout)


@lru_cache(maxsize=None)
def verify_image(path):
    return path.is_file() and digest(path.read_bytes()) == path.stem


class Media:
    def __init__(self, folder, run, source):
        self.folder = folder
        self.asset = source['assets']
        self.path_aliases = {row['path']:row['media_path'] for row in run.get('resource_references',[])
                             if row.get('available') and row.get('media_path')}
        self.source_ids = defaultdict(set)
        for item in source['sources']:
            for identifier in item['asset_refs']:
                if sha := self.asset.get(identifier,{}).get('sha256'):
                    self.source_ids[sha].add(item['source_id'])

    def resolve(self, path):
        alias = self.path_aliases.get(path,path or '')
        match = MEDIA_PATH.fullmatch(alias)
        if not match:
            return {'status':'unverified','media_path':path}
        sha = match[1]
        if not verify_image(self.folder/f'{sha}.png'):
            return {'status':'missing_or_invalid','media_path':path}
        return {'status':'ok','media_path':path,'verified_media_path':alias,'media_sha256':sha,
                'source_ids':sorted(self.source_ids[sha])}

    def patch_assets_readable(self, patch, inventory):
        if any(row['kind']=='image' and (row['status']!='ok' or not self.resolve(
                row.get('media_path')).get('verified_media_path')) for row in inventory):
            return False
        for ref in patch['asset_refs']:
            item = self.asset.get(ref)
            if not item or (item.get('sha256') and not verify_image(self.folder/(item['sha256']+'.png'))):
                return False
        return True
