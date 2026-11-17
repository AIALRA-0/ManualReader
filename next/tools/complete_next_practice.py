"""Replay usable exercise replies, then supplement missing parts once."""
import argparse
import asyncio
from pathlib import Path
import sys
from time import perf_counter

from complete_next_teaching import finish_practice
from manualreader.stages.finish.provider import Provider
from manualreader.stages.step05.inference import read,write


async def main(root,prior,out,legacy):
    sys.path.insert(0,str(legacy))
    provider=Provider(out,sol=40,vision=40)
    archive={}
    for path in (prior/'requests').glob('*/*/receipt.json'):
        receipt=read(path)
        if receipt.get('step_slug')=='step_14_practice' and (path.parent/'result.json').is_file():
            archive[receipt['sample_id']]=read(path.parent/'result.json')['result']
    async def one(job):
        sample=job.name
        card=read(root/'inputs'/sample/'task-card.json')
        structure=read(job/'part-blocks.json')
        async def replay(stage,payload):
            return archive.get(sample,{})
        checked=await finish_practice(root,job,structure,card,replay)
        if checked['meets_online_policy']:
            return {'sample_id':sample,'replay':checked,'final':checked,'new_request':False}
        async def ask(stage,payload):
            wanted=payload['practice_mix']['requested_total']
            addon='\n本请求是定点补题。优先用题意明确、能判分的选择题。NORMAL 请按提供的 Part 逐项各给一道，任何遗漏都保留 missing 记录。单题模式只出一道客观题。'
            return await provider.ask(sample,stage,{**payload,'completion_required_count':wanted,
                       'supply_each_requested_part':structure['mode']=='NORMAL'},prompt=provider.prompts[stage]+addon.encode())
        recovered=await finish_practice(root,job,structure,card,ask)
        return {'sample_id':sample,'replay':checked,'final':recovered,'new_request':True}
    began=perf_counter()
    jobs=[p.parent for p in sorted((root/'jobs').glob('*/quiz-candidates.json'))]
    results=await asyncio.gather(*(one(j) for j in jobs),return_exceptions=True)
    rows=[r if not isinstance(r,Exception) else {'sample_id':j.name,'error':str(r)}
         for j,r in zip(jobs,results)]
    write(out/'step-14-practice-supplement.json',{'samples':rows,
      'wall_seconds':round(perf_counter()-began,2),'requests':provider.log})
    print('practice',sum(r.get('final',{}).get('meets_online_policy',False) for r in rows),'/',len(rows),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('root','prior','output','legacy'):
        parser.add_argument(name,type=Path)
    args=parser.parse_args()
    asyncio.run(main(args.root,args.prior,args.output,args.legacy))
