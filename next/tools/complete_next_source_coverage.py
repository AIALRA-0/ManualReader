"""Run the NEXT recovery branch on already generated chapters."""
import argparse
import asyncio
from pathlib import Path

from manualreader.stages.finish.provider import Provider
from manualreader.artifacts import write_json as write
from manualreader.steps.step_18_source_gap_recovery import one

async def main(root,out):
    provider=Provider(out,sol=40,vision=40)
    jobs=[folder.parent for folder in sorted((root/'jobs').glob('*/part-blocks.json'))]
    results=await asyncio.gather(*(one(root,j,provider) for j in jobs),return_exceptions=True)
    rows=[r if not isinstance(r,Exception) else {'sample_id':job.name,'error':str(r)}
          for job,r in zip(jobs,results) if r is not None]
    print([(r['sample_id'],r.get('counts'),r.get('error')) for r in rows],flush=True)
    write(out/'step-18-source-coverage-recovery.json',{'samples':rows,'requests':[
        {'sample_id':r['sample_id'],'step':r['step_slug'],'variant':r['variant'],
         'status':r['status'],'profile':r['profile'],'usage':r['response'].get('usage',{}),'error':r['error']}
        for r in provider.log]})

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('root',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args()
    asyncio.run(main(args.root,args.output))
