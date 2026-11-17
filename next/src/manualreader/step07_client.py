"""List, inspect and verify downloads from a local STEP-07 service."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from urllib.request import Request
from zipfile import ZipFile

from .client import API


def download(api, sample, output):
    receipt=api.call('step07','status',sample_id=sample)
    request=Request(f'{api.base}/step07/{sample}/download',headers={'Authorization':'Reader '+api.key})
    with api.opener.open(request,timeout=90) as response:
        data=response.read()
    with ZipFile(BytesIO(data)) as archive:
        if archive.testzip() or sha256(archive.read('body.fused.md')).hexdigest()!=receipt['fused_sha256']:
            raise ValueError('下载的融合稿与服务回执不一致')
    output.mkdir(parents=True,exist_ok=True)
    (output/(sample+'.zip')).write_bytes(data)
    return {'sample_id':sample,'sha256':sha256(data).hexdigest()}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--base',required=True)
    p.add_argument('--token-file',required=True,type=Path)
    p.add_argument('--action',choices=('list','status','download'),default='list')
    p.add_argument('--sample',action='append')
    p.add_argument('--output',type=Path)
    args=p.parse_args()
    api=API(args.base,args.token_file.read_text('utf8'),'isolated')
    samples=api.call('step07','list')['samples']
    if args.action=='list':
        result=samples
    elif args.action=='status':
        result=[api.call('step07','status',sample_id=name) for name in args.sample or samples]
    else:
        if args.output is None:
            p.error('下载时请指定 --output')
        with ThreadPoolExecutor() as worker:
            result=list(worker.map(lambda name:download(api,name,args.output),args.sample or samples))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
