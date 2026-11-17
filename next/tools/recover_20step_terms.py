"""Resume only F2 requests that treated a verified empty dictionary as unavailable."""

import argparse
import asyncio
from pathlib import Path
from time import perf_counter, time

from manualreader.stages.finish.formatting import format_artifact
from manualreader.stages.finish.provider import Provider
from manualreader.steps.step_13_terminology import bind, generate, glossary
from manualreader.artifacts import read_json as read, write_json as write


async def main(root):
    provider = Provider(root, sol=40, vision=12, ds=12)
    started = perf_counter()
    prior = {}
    for path in (root/'requests/F2').glob('*/receipt.json'):
        receipt = read(path)
        stored = read(path.parent/'result.json').get('result') or {}
        sample = receipt['sample_id']
        context = glossary(root, sample)
        if ('existing_terms' in context and context['existing_terms']==[] and context.get('glossary_snapshot_sha256')
            and not stored.get('terms') and 'glossary_unavailable' in stored.get('unresolved_terms', [])):
            prior[sample] = receipt['id']

    async def recover(sample, old_id):
        job = root/'jobs'/sample
        marker = job/'terminology-recovery.json'
        if marker.is_file():
            return read(marker)
        live = job/'run.json'
        if not live.is_file():
            return {'sample':sample, 'state':'no_final_artifact', 'old_request':old_id}
        result = await generate(root, sample, read(root/'inputs'/sample/'task-card.json'), provider.scope(sample))
        rows = bind(result, read(job/'part-blocks.json'))
        rows, edits = await format_artifact(rows, ('attachments','terms'))
        if rows:
            content = read(job/'attachments.final.json')
            content['terms'] = rows
            patches = [p for p in read(job/'format-patches.json') if p['path'][:2]!=['attachments','terms']]
            current = read(live)
            current['terms'] = len(rows)
            current['issues']['F2'] = result['issues']
            write(job/'term-candidates.json', rows)
            write(job/'attachments.final.json',content)
            write(job/'format-patches.json',patches+edits)
            write(live,current)
            write(job/'preview-revision.json',{'kind':'f2-verified-empty-glossary','updated_at':time()})
        rec = {'sample':sample,'old_request':old_id,'candidates':len(rows),
               'state':'recovered' if rows else 'still_missing','unresolved':result.get('unresolved_terms',[]),
               'issues':result['issues']}
        write(marker,rec)
        print(sample,rec['state'],len(rows),flush=True)
        return rec

    results = await asyncio.gather(*(recover(sample, old) for sample,old in prior.items()), return_exceptions=True)
    rows = [out if isinstance(out,dict) else {'sample':sample,'error':str(out)}
            for sample,out in zip(prior,results)]
    write(root/'terminology-recovery-20step.json',{'wall_seconds':perf_counter()-started,'runs':rows})


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    asyncio.run(main(parser.parse_args().root))
