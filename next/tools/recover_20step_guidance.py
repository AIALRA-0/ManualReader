"""Recover structured F1 results from this run's original replies without issuing requests."""

import argparse
import asyncio
from pathlib import Path
from time import perf_counter,time

from jsonschema import ValidationError, validate

from manualreader.stages.finish.contracts import SCHEMAS
from manualreader.stages.finish.formatting import format_artifact
from manualreader.stages.finish.provider import SOL, decode
from manualreader.artifacts import read_json as read, write_json as write
from manualreader.steps.step_15_chapter_guidance import guidance


async def recover(root,folder,receipt):
    sample=receipt['sample_id']
    job=root/'jobs'/sample
    run=job/'run.json'
    if not run.exists() or (job/'guidance-decoding-recovered.json').exists():
        return
    response=receipt['response']
    if response.get('status')!='completed' or response.get('model')!=SOL['model']:
        return
    stored=read(folder/'input.json')['input']
    candidate=(folder/'attempt-1'/'raw-output.txt').read_bytes()
    raw=decode(candidate,'F1')
    if raw is None:
        raise ValueError('原附件响应无法解码')
    validate(instance=raw,schema=SCHEMAS['F1'])
    structure=read(job/'generated/structure.json')
    card=read(root/'inputs'/sample/'task-card.json')

    async def supplied(stage,payload,images=None):
        if stage!='F1' or payload!=stored:
            raise ValueError('原附件请求的输入与本篇不一致')
        return raw

    result=await guidance(root,sample,structure,card,supplied)
    if result['issues']:
        raise ValueError(result['issues'])
    rendered,edits=await format_artifact(result['guidance'],('attachments','guidance'))
    formatted=read(job/'attachments.final.json')
    formatted['guidance']=rendered
    patches=read(job/'format-patches.json')
    patches=[p for p in patches if p['path'][:2]!=['attachments','guidance']]+edits
    current=read(run)
    current['issues']['F1']=[]
    current['reading_route_items']=len(rendered['reading_flow'])
    current['recaps']=len(rendered['part_recaps'])
    current['recovered_guidance_request']=receipt['id']
    write(job/'chapter-guidance.json',rendered)
    write(job/'attachments.final.json',formatted)
    write(job/'format-patches.json',patches)
    write(run,current)
    proof={'sample_id':sample,'model_request_id':receipt['id'],'model_calls':0,
           'remaining_parts':result['unresolved_part_ids'],'recovered_at':time()}
    write(job/'guidance-decoding-recovered.json',proof)
    write(job/'preview-revision.json',{'kind':'decoded-original-f1','updated_at':time()})
    return proof


async def main(args):
    root=args.root
    out={}
    began=perf_counter()
    final_since=None
    while perf_counter()-began<args.timeout:
        for path in (root/'requests'/'F1').glob('*/receipt.json'):
            receipt=read(path)
            if receipt['status']=='complete' or receipt['id'] in out:
                continue
            try:
                result=await recover(root,path.parent,receipt)
                if result:
                    out[receipt['id']]=result
                    print(result['sample_id'],'guidance recovered',flush=True)
            except (ValidationError,ValueError,OSError,KeyError,TypeError) as exc:
                out[receipt['id']]={'sample_id':receipt['sample_id'],'error':str(exc)}
        write(root/'guidance-recovery.json',{'requests':out,'model_calls':0})
        if (root/'whiteboard-result.json').is_file():
            final_since=final_since or perf_counter()
            if perf_counter()-final_since>=20:
                break
        await asyncio.sleep(.55)


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('root',type=Path)
    cli.add_argument('--timeout',type=float,default=5400)
    asyncio.run(main(cli.parse_args()))
