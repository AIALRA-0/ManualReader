"""Replay the sealed Reader output, with no model transport."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter

from verify_full_finish import verify_one


def bytes_file(file):
    return sha256(file.read_bytes()).hexdigest()


def one(root,name,rebuild=None,format_archive=None):
    job=root/'jobs'/name
    checks=verify_one(root,job,allow_content_gaps=True,format_archive=format_archive)
    delivery=json.loads((job/'run.json').read_bytes())
    manifest=root/'reader-isolated'/'library'/('next-final-'+name)/'postprocess-preview/manifest.json'
    shown=json.loads(manifest.read_bytes())
    if shown['body_sha256']!=delivery['body_final_sha256'] or shown['delivery_receipt_sha256']!=bytes_file(job/'run.json'):
        raise ValueError('Reader 实际读取的成稿与已保存稿的哈希不同')
    receipt={'sample':name,'body_sha256':delivery['body_final_sha256'],'checks':checks,
             'reader_projection_sha256':bytes_file(manifest)}
    if rebuild:
        from manualreader.stages.finish.preview import project_native
        backend, destination=rebuild
        result=project_native(root,name,destination,backend,root/'original-inputs/projects')
        other=destination/'library'/('next-final-'+name)/'postprocess-preview/manifest.json'
        reconstructed=json.loads(other.read_bytes())
        equivalent=['body_sha256','block_ids','first_block_id','delivery_receipt_sha256',
                    'read_only_source_versions','relation_counts','resources']
        drift=[k for k in equivalent if reconstructed[k]!=shown[k]]
        if drift:
            raise ValueError('独立重装后有差异：'+', '.join(drift))
        receipt['fresh_projection']={'equivalent_fields':equivalent,'view':{
            k:result[k] for k in ('blocks','source_units','resources','quizzes','terms','recap_parts')}}
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('out',type=Path)
    parser.add_argument('--legacy',type=Path)
    parser.add_argument('--rebuild',type=Path)
    parser.add_argument('--format-archive',type=Path,help='历史格式回执所需的原文哈希档案')
    args=parser.parse_args()
    started=perf_counter()
    rebuild=None
    if args.rebuild:
        from manualreader.stages.finish.preview import setup
        if not args.legacy or args.rebuild.exists():
            parser.error('重放需要原生 Reader 源码和空目录')
        destination=setup(args.rebuild,args.legacy,0)
        sys.path.insert(0,str(args.legacy))
        import reader_server as backend
        backend.core.read_state()
        rebuild=(backend,destination)
    request_dir=args.root/'requests'
    before={str(f.relative_to(request_dir)):bytes_file(f) for f in request_dir.glob('*/*/receipt.json')}
    ids=json.loads((args.root/'whiteboard-inputs.json').read_bytes())['samples']
    results={}
    archive=json.loads(args.format_archive.read_bytes()) if args.format_archive else None
    with ThreadPoolExecutor(max_workers=12) as pool:
        tasks={name:pool.submit(one,args.root,name,rebuild,archive) for name in ids}
        for name,future in tasks.items():
            try:
                results[name]=future.result()
            except Exception as exc:
                results[name]={'failure':str(exc)}
    after={str(f.relative_to(request_dir)):bytes_file(f) for f in request_dir.glob('*/*/receipt.json')}
    output={'model_dispatches':len(after.keys()-before.keys()),'model_receipts_identical':before==after,
            'elapsed_seconds':perf_counter()-started,'samples':results}
    args.out.write_text(json.dumps(output,ensure_ascii=False,default=dict,indent=2)+'\n',encoding='utf-8')
    return int(not output['model_receipts_identical'] or any('failure' in row for row in results.values()))


if __name__=='__main__':
    raise SystemExit(main())
