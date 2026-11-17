"""Resolve only draft images whose bytes can be verified locally."""

from base64 import b64decode
from hashlib import sha256
import re


SHA_IMAGE = r'assets/[a-f0-9]{64}\.png'
PAGE_IMAGE = re.compile(r'/content-files/([A-Za-z0-9_-]+)/asset/page-(\d{1,6})\.png')


def check_references(folder, body, documents, engine, project, round_id):
    findings = []
    seen = set()
    by_file = {doc['file_id']:doc for doc in documents}
    for name in re.findall(r'!\[[^\]\n]*\]\(<?([^\s)>]+)',body.decode('utf8')):
        if name in seen:
            continue
        seen.add(name)
        item = {'path':name,'available':False}
        findings.append(item)
        if re.fullmatch(SHA_IMAGE,name):
            path = folder/name
            item['available'] = path.is_file() and sha256(path.read_bytes()).hexdigest()==path.stem
            if item['available']:
                item['media_path'] = name
            continue
        match = PAGE_IMAGE.fullmatch(name)
        if not match:
            continue
        file_id, number = match[1],int(match[2])
        doc = by_file.get(file_id,{})
        if doc.get('type')!='pdf_page' or not any(r['n']==number for r in doc['original']):
            continue
        try:
            result = engine.inspect(project,round_id,file_id,[number])
            if result['content_sha256'] != doc['sha256']:
                continue
            asset = next((a for a in result['pages'][0]['assets']
                          if a['kind']=='page_visual' and 'inspect_visual' in a['capabilities']),None)
            if not asset:
                continue
            payload = engine.consume(project,round_id,file_id,asset['asset_id'],'inspect_visual',number)
            data = b64decode(payload['content']['image_url'].split(',',1)[1],validate=True)
            digest=sha256(data).hexdigest()
            if digest != asset['carrier']['sha256']:
                continue
            media_path = f'assets/{digest}.png'
            (folder/'assets').mkdir(exist_ok=True)
            (folder/media_path).write_bytes(data)
            item.update(available=True,media_path=media_path,sha256=digest,asset_id=asset['asset_id'],
                        file_id=file_id,locator=asset['locator'])
        except (ValueError,OSError,RuntimeError) as error:
            item['error'] = str(error)
    return findings
