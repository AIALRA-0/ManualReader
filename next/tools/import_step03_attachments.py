"""Import prior authorized snapshot attachments matched by exact immutable source hash, URL and DOM ID."""

from argparse import ArgumentParser
import json
from pathlib import Path
import re

from bs4 import BeautifulSoup
from manualreader.stages.step03.cache import Cache, atomic, digest
from manualreader.stages.step03.media import pixels


def archive_snapshot(cache, project, fid, original, history, raw_assets):
    sha=digest(original)
    dom=BeautifulSoup(original,'html.parser')
    refs={i.get('src') or i.get('data-src') for i in dom.find_all('img')}
    exact={}
    for asset in history:
        if asset.get('file_id')!=fid or asset.get('original_sha256')!=sha or asset.get('status')!='verified_visual':
            continue
        evidence=asset.get('evidence') or {}
        src=evidence.get('registered_src')
        ids=(evidence.get('source_locator') or {}).get('dom_units') or []
        info=asset.get('carrier') or {}
        h=info.get('sha256','')
        if src not in refs or not isinstance(src,str) or not re.fullmatch(r'[0-9a-f]{64}',h):
            continue
        p=(raw_assets/(h+'.png')).resolve()
        if not p.is_relative_to(raw_assets.resolve()) or not p.is_file():
            continue
        data=p.read_bytes()
        if digest(data)!=h:
            continue
        try:
            pixels(data)
        except (ValueError,OSError):
            continue
        for dom_id in ids:
            if isinstance(dom_id,str) and re.fullmatch(r'dom\d+',dom_id):
                key=src,dom_id
                if key in exact and exact[key]!=h:
                    exact[key]=None
                else:
                    exact[key]=cache.blob(data)
    images=[{'ref':u,'dom_id':dom_id,'sha256':h} for (u,dom_id),h in sorted(exact.items()) if h]
    if images:
        dest=cache.root/'web-attachments'/f'{digest([project,fid,sha])}.json'
        atomic(dest,json.dumps({'html_sha256':sha,'file_id':fid,'images':images},ensure_ascii=False).encode())
    return len(images)


def main():
    args=ArgumentParser()
    for name in ('data','history','cache'):
        args.add_argument('--'+name,type=Path,required=True)
    opts=args.parse_args()
    cache=Cache(opts.cache)
    projects=json.loads((opts.data/'import-report.json').read_bytes())
    totals={}
    for name,p in projects.items():
        archive=opts.history/name
        receipt=archive/'source-ready.json'
        if not receipt.is_file():
            continue
        metadata=json.loads(receipt.read_bytes())
        for f in p['files'].values():
            rel=Path(f['relative_path'])
            if rel.suffix.lower() not in ('.htm','.html'):
                continue
            root=(opts.data/'projects'/p['project_id']/'originals').resolve()
            raw=(root/rel).resolve(strict=True)
            if not raw.is_relative_to(root):
                raise ValueError('快照不在登记的原件目录')
            original=raw.read_bytes()
            if digest(original)!=f['sha256']:
                raise ValueError('快照和原件登记哈希不同')
            totals[name]=archive_snapshot(cache,p['project_id'],f['file_id'],original,
                              metadata['assets'].values(),archive/'assets')
    print(json.dumps(totals,ensure_ascii=False))


if __name__=='__main__':
    main()
