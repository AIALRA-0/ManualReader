"""Read native resource questions and run the existing answer writer on the isolated Reader."""

import argparse
import asyncio
from collections import defaultdict
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from manualreader.stages.step05.inference import read,write


MATCH=re.compile(r'(?:资源[：:]|resource:)\s*(res-[a-f0-9]+)')


async def run(root,legacy):
    sys.path.insert(0,str(legacy))
    from reader_bridge import Bridge
    from reader_postprocess_qa import answer

    ready=read(root/'reader-service-ready.json')
    base=ready['url']
    if urlsplit(base).hostname!='127.0.0.1' or not (root/'reader-isolated/library').is_dir():
        raise ValueError('资源问答实测只对本轮隔离 Reader 开放')
    projects=read(root/'reader-projections.json')['samples']
    cases={}
    found=[]
    for row in projects:
        sample,material=row.get('sample_id'),row.get('material_id')
        if not material or not material.startswith('next-final-'):
            continue
        path=root/'reader-isolated/library'/material
        if not path.is_dir():
            raise ValueError('浏览结果和本地隔离项目无法互相验证')
        job=root/'jobs'/sample
        resources=read(job/'part-blocks.json')['resources']
        resource_type={str(r['resource_id']):r['kind'] for r in resources}
        state=await asyncio.to_thread(Bridge(base,material).request,'/api/progress')
        found.extend({'sample_id':sample,'material_id':material,'question_id':q['id'],
                      'resource_id':m.group(1),'kind':resource_type[m.group(1)],
                      'has_parent':bool(q.get('parent_id') or q.get('parent_question_id'))}
                     for group in state['rounds'] for q in group['questions']
                     if (m:=MATCH.search((q.get('target') or {}).get('text') or ''))
                     and m.group(1) in resource_type)
        for q in found:
            key=q['kind']
            cases.setdefault(key,q)
    write(root/'qa-browser-resource-ids.json',found)
    by_project=defaultdict(dict)
    for kind,row in cases.items():
        by_project[(row['sample_id'],row['material_id'])][kind]=row['question_id']
    async def actual(pair,questions):
        sample,material=pair
        return await answer(root/'qa'/sample,base,material,questions)
    results=await asyncio.gather(*(actual(pair,questions) for pair,questions in by_project.items()))
    write(root/'qa-receipts.json',{'selected':cases,'native':results,'attempted':len(cases),
                                   'saved_answers':sum(bool(a.get('answer')) for r in results
                                                      for a in r.get('answers',[]))})
    print('resource types tried',sorted(cases),'saved answers',
          sum(bool(a.get('answer')) for r in results for a in r.get('answers',[])))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('root',type=Path)
    p.add_argument('legacy',type=Path)
    x=p.parse_args()
    asyncio.run(run(x.root.resolve(),x.legacy.resolve()))
