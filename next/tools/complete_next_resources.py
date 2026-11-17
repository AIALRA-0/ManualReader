"""Run the NEXT recovery branch on already generated chapters."""
import argparse
import asyncio
from collections import Counter
from pathlib import Path
from time import perf_counter

from manualreader.stages.finish.provider import Provider
from manualreader.stages.step03.cache import digest
from manualreader.artifacts import read_json as read, write_json as write
from manualreader.steps.step_12_resource_recovery import repair

async def main(root,out):
    provider=Provider(out,sol=40,vision=40)
    jobs=[p.parent for p in sorted((root/'jobs').glob('*/resource-explanations.json'))]
    start=perf_counter()
    result=await asyncio.gather(*(repair(root,j,provider) for j in jobs),return_exceptions=True)
    rows=[r if not isinstance(r,Exception) else {'sample_id':j.name,'error':str(r)}
          for j,r in zip(jobs,result) if r is not None]
    for row in rows:
        if not row.get('explanations_updated'):
            continue
        job=root/'jobs'/row['sample_id']
        resource_explanations=read(job/'resource-explanations.json')
        attachments=job/'attachments.final.json'
        if attachments.is_file():
            write(attachments,{**read(attachments),'resource_explanations':resource_explanations})
        receipt=job/'run.json'
        if receipt.is_file():
            counts=Counter(item['status'] for item in resource_explanations['coverage_by_resource'])
            write(receipt,{**read(receipt),'p2':dict(counts)})
        marker=job/'preview-revision.json'
        write(marker,{**(read(marker) if marker.is_file() else {}),
                      'resource_explanation_revision':digest(resource_explanations),
                      'resource_explanation_updates':sorted(row['explanations_updated'])})
    report={'samples':rows,'wall_seconds':round(perf_counter()-start,3),
            'requests':[{'sample_id':r['sample_id'],'step':r['step_slug'],'variant':r['variant'],
                         'profile':r['profile'],'status':r['status'],'seconds':r['ended_at']-r['started_at'],
                         'usage':r.get('response',{}).get('usage',{}),'error':r['error']} for r in provider.log]}
    write(out/'step-12-recovery.json',report)
    print([(r['sample_id'],len(r.get('explanations_updated',[])),len(r.get('remaining',[])),r.get('error'))
           for r in rows],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('root',type=Path)
    p.add_argument('output',type=Path)
    args=p.parse_args()
    asyncio.run(main(args.root,args.output))
