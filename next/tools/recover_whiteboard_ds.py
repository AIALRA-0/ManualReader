"""Retry this run's failed DS calls after the provider recovers."""

import argparse
import asyncio
from pathlib import Path
from time import time

from manualreader.stages.finish.provider import Lane, frozen_prompts, invoke_ds
from manualreader.stages.step05.contracts import digest
from manualreader.stages.step05.inference import read,write


async def recover(root):
    deadline=time()+4200
    names=set(read(root/'whiteboard-inputs.json')['samples'])
    while True:
        ds_inputs=[path for stage in ('B1','B2') for path in (root/'requests'/stage).glob('*/input.json')]
        ready=all((root/'jobs'/name/'translation-initial.json').is_file() and
                  (root/'jobs'/name/'generated/visual.json').is_file() and
                  (root/'jobs'/name/'generated/source-labels.json').is_file() for name in names)
        if (root/'whiteboard-result.json').is_file() or (
                ready and ds_inputs and all((path.parent/'receipt.json').is_file() for path in ds_inputs)):
            break
        if time()>deadline:
            raise TimeoutError('译文和图中文字的首轮请求尚未全部返回')
        await asyncio.sleep(2)
    prompts=frozen_prompts()
    lane=Lane('ds',12)
    failed=[]
    for stage in ('B1','B2'):
        for receipt in sorted((root/'requests'/stage).glob('*/receipt.json')):
            previous=read(receipt)
            if previous['status']=='complete' or not previous['attempts'] or len(previous['attempts'])>2:
                continue
            if previous['attempts'][-1].get('status') not in (200,429,500,502,503,504,None):
                continue
            payload=read(receipt.parent/'input.json')
            if (payload['id']!=receipt.parent.name or payload['stage']!=stage
                    or payload['prompt_sha256']!=digest(prompts[stage]) or payload['image_sha256']):
                raise ValueError(f'需补试的请求与本轮记录不一致 {payload["id"]}')
            failed.append((receipt,previous,payload))
    async def retry(receipt,previous,payload):
        stage=payload['stage']
        nth=len(previous['attempts'])+1
        folder=receipt.parent/f'attempt-{nth}'
        folder.mkdir()
        async with lane.slot():
            started=time()
            value=await asyncio.to_thread(invoke_ds,folder,stage,prompts[stage],payload['input'])
        await lane.observed(value['attempts'],value['result'] is not None)
        previous_attempts=[dict(x) for x in previous['attempts']]
        if 'response' not in previous_attempts[-1]:
            previous_attempts[-1]['response']=previous['response']
        attempts=previous_attempts+[{**row,'response':value['response']} for row in value['attempts']]
        ok=value['result'] is not None
        record={**previous,'attempts':attempts,'retries':len(attempts)-1,'ended_at':time(),
                'response':value['response'],'status':'complete' if ok else 'partial','error':value['error']}
        write(receipt.parent/'receipt-before-technical-recovery.json',previous)
        if ok:
            write(receipt.parent/'result.json',value)
        write(receipt,record)
        return {'stage':stage,'id':previous['id'],'outcome':record['status'],'started_at':started,
                'ended_at':record['ended_at'],'error':value['error']}
    print('DS technical failures eligible for retry',len(failed),flush=True)
    rows=await asyncio.gather(*(retry(*item) for item in failed))
    write(root/'ds-technical-retry.json',{'attempted':len(failed),'outcomes':rows,
          'recovered':sum(x['outcome']=='complete' for x in rows),'output_kind':'current_run_provider_recovery'})
    print('DS retry results',sum(x['outcome']=='complete' for x in rows),'/',len(rows),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    asyncio.run(recover(p.parse_args().root.resolve()))
