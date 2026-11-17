"""Retry only failed DS wire responses once, preserving the first receipt and payload."""

import argparse
import asyncio
from pathlib import Path
from time import time

from manualreader.stages.finish.provider import frozen_prompts, invoke_ds
from manualreader.stages.step05.contracts import digest
from manualreader.stages.step05.inference import read,write


async def recover(root):
    frozen=frozen_prompts()
    candidates=[]
    for stage in ('B1','B2'):
        for path in sorted((root/'requests'/stage).glob('*/receipt.json')):
            rec=read(path)
            if rec['status']=='complete' or len(rec['attempts'])!=1:
                continue
            status=rec['attempts'][0].get('status')
            if status not in (200,429,503,None):
                continue
            data=read(path.parent/'input.json')
            if (data['id']!=path.parent.name or data['stage']!=stage
                    or data['prompt_sha256']!=digest(frozen[stage]) or data['image_sha256']):
                raise ValueError(f'原始 DS 请求记录与冻结指令不一致 {path}')
            candidates.append((path,rec,data))
    async def retry(path,rec,data):
        folder=path.parent/'attempt-2'
        folder.mkdir()
        started=time()
        attempt=await asyncio.to_thread(invoke_ds,folder,data['stage'],frozen[data['stage']],data['input'])
        response=attempt['response']
        first=[dict(x) for x in rec['attempts']]
        first[-1]['response']=rec['response']
        attempts=first+[{**a,'response':response} for a in attempt['attempts']]
        fixed=attempt['result'] is not None
        update={**rec,'attempts':attempts,'retries':1,'ended_at':time(),'response':response,
                'status':'complete' if fixed else 'partial','error':attempt['error']}
        write(path.parent/'receipt-before-technical-retry.json',rec)
        if fixed:
            write(path.parent/'result.json',attempt)
        write(path,update)
        return {'stage':data['stage'],'id':rec['id'],'outcome':update['status'],
                'started_at':started,'ended_at':time(),'error':update['error']}
    observed=await asyncio.gather(*(retry(*row) for row in candidates))
    write(root/'ds-technical-retry.json',{'attempted':len(candidates),'outcomes':observed,
                                           'recovered':sum(x['outcome']=='complete' for x in observed)})
    print(f"DS technical retry: {sum(x['outcome']=='complete' for x in observed)}/{len(candidates)}")


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('root',type=Path)
    asyncio.run(recover(p.parse_args().root.resolve()))
