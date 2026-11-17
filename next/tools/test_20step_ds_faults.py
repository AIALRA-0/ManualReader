"""Inject three DS transport failures without sending billable requests."""

import argparse
import asyncio
from pathlib import Path
from time import perf_counter, time
from unittest.mock import patch

from manualreader.stages.finish import provider as p
from manualreader.artifacts import write_json as write


async def one(root, code):
    client=p.Provider(root/f'http-{code}',sol=4,vision=4,ds=3)
    codes=[]
    bodies=[]
    reply={'batch_id':f'fault-{code}','units':[]}

    def injected(folder, stage, prompt, payload):
        first=not codes
        codes.append(code if first else 200)
        bodies.append(payload)
        return {'result':None if first else reply,'response':{'model':p.DS['model']},
                'attempts':[{'started_at':time(),'status':codes[-1],'seconds':0,
                             'retry_after':'0' if code==429 else None}],
                'error':f'HTTP {code}' if first else None}

    async def unrelated_sol():
        async with client.lanes['sol'].slot():
            return time()

    before=time()
    with patch.object(p,'invoke_ds',injected):
        task=asyncio.create_task(client.scope('fault-injection')('B1',{'batch_id':f'fault-{code}'}))
        independently=await unrelated_sol()
        try:
            recovered=await task==reply
            blocked=False
        except p.ModelFailure:
            recovered=False
            blocked=True
    record=client.log[0]
    expected=[403] if code==403 else [code,200]
    assert codes==expected and blocked==(code==403) and recovered==(code!=403)
    assert independently<=record['ended_at'] and (len(bodies)==1 or bodies[0]==bodies[1])
    return {'forced_status':code,'observed_http':codes,'recovered':recovered,'blocked':blocked,
            'retry_count':record['retries'],'independent_slot_opened_while_ds_active':before<=independently<=record['ended_at'],
            'same_payload_on_retry':len(bodies)==2 and bodies[0]==bodies[1],
            'lane_changes':client.lanes['ds'].changes,'actual_models_called':0}


async def main(base):
    start=perf_counter()
    rows=[]
    for code in (429,503,403):
        rows.append(await one(base/'fault-test-isolated',code))
    write(base/'translation-fault-20step.json',{'wall_seconds':perf_counter()-start,
           'mocked_transport':True,'external_provider_calls':0,'rows':rows})


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('base',type=Path)
    asyncio.run(main(cli.parse_args().base))
