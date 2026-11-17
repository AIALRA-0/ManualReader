from argparse import ArgumentParser
from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
from time import sleep

from .client import API


def run(api, sample, action='submit', output=None, resume=False):
    view=api.call('step56',action,sample_id=sample,resume=resume)
    if action=='download':
        raw=b64decode(view['zip'],validate=True)
        if sha256(raw).hexdigest()!=view['sha256']:
            raise ValueError('下载文件的哈希与回包不一致')
        if output is None:
            raise ValueError('下载时需要指定输出目录')
        output.mkdir(parents=True,exist_ok=True)
        (output/f'{sample}.zip').write_bytes(raw)
        return {'sample_id':sample,'sha256':view['sha256']}
    return view


def main():
    parser=ArgumentParser(prog='manualreader-step56')
    parser.add_argument('--base',required=True)
    parser.add_argument('--token-file',required=True,type=Path)
    parser.add_argument('--sample',action='append',default=[])
    parser.add_argument('--all',action='store_true')
    parser.add_argument('--action',choices=('submit','status','cancel','download'),default='submit')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--wait',action='store_true')
    opts=parser.parse_args()
    key=opts.token_file.read_text('utf8')
    items=API(opts.base,key,'isolated').call('step56','list')['samples'] if opts.all else opts.sample
    if not items:
        parser.error('请提供一个任务编号，或使用 --all')

    def one(sample):
        api=API(opts.base,key,'isolated')
        return run(api,sample,opts.action,opts.output,opts.resume)

    with ThreadPoolExecutor(max_workers=min(len(items),40)) as pool:
        result=list(pool.map(one,items))
        if opts.wait and opts.action=='submit':
            while any(r['status'] in ('queued','running') for r in result):
                sleep(2)
                result=list(pool.map(lambda sample:run(API(opts.base,key,'isolated'),sample,'status'),items))
    print(json.dumps(result,ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
