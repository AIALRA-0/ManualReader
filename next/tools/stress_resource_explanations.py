"""Generate real resource explanations in a fresh request directory without rewriting chapters."""

from argparse import ArgumentParser
import asyncio
from collections import Counter
from pathlib import Path
from time import perf_counter

from manualreader.stages.finish.content import applicable_resources, explain_math, explain_visual
from manualreader.stages.finish.pipeline import explanations
from manualreader.stages.finish.provider import Provider
from manualreader.artifacts import read_json as read, write_json as write


async def run(root, output, concurrency, *, replay=False):
    if (output/'requests').exists()!=replay:
        raise ValueError('首轮需要空请求目录，回放需要实际压测回执')
    evidence=output/'replay' if replay else output

    def canonical(value):
        return {**value,'groups':sorted(value['groups'],key=lambda group:group['group_id'])}
    provider=Provider(output,sol=concurrency,vision=concurrency,cache_only=replay)

    async def chapter(path):
        name=path.parent.name
        structure=read(path)
        expected={r['resource_id']:r['kind'] for r in applicable_resources(structure)}
        if not expected:
            return None
        source=read(root/'inputs'/name/'source-index.json')
        ask=provider.scope(name)
        started=perf_counter()
        math,visual=await asyncio.gather(explain_math(root,name,structure,ask,source=source),
                                         explain_visual(root,name,structure,source,ask))
        result=explanations(structure,math,visual)
        relative=Path('chapters')/name/'resource-explanations.json'
        if replay and canonical(result)!=canonical(read(output/relative)):
            raise ValueError('回放与首次生成的详解不一致')
        write(evidence/relative,result)
        eligible=[row for row in result['coverage_by_resource'] if row['eligible']]
        indexed={group['group_id']:set(group['resource_ids']) for group in result['groups']}
        delivered={r['resource_id'] for r in eligible if r['resource_id'] in indexed.get(r.get('group_id'),set())}
        full={r['resource_id'] for r in eligible if r['status'] in ('explained','covered_by_group')}
        return {'sample_id':name,'expected_kinds':dict(Counter(expected.values())),
                'expected':len(expected),'delivered':len(delivered),'model_explained':len(full),
                'missing':sorted(expected.keys()-delivered),'model_partial':sorted(delivered-full),
                'seconds':perf_counter()-started,'issues':result['issues']}

    paths=sorted((root/'jobs').glob('*/part-blocks.json'))
    started=perf_counter()
    results=await asyncio.gather(*(chapter(path) for path in paths),return_exceptions=True)
    rows=[{'sample_id':p.parent.name,'internal_failure':str(r)} if isinstance(r,Exception) else r
          for p,r in zip(paths,results) if r is not None]
    report={'wall_seconds':perf_counter()-started,'samples':rows,'inspected_chapters':len(paths),
            'totals':{key:sum(row.get(key,0) for row in rows) for key in ('expected','delivered','model_explained')},
            'cache_hits':sum(row['reused'] for row in provider.consumer_log),
            'requests':[{'stage':r['stage'],'status':r['status'],'attempts':len(r['attempts']),
                         'usage':r['response'].get('usage',{}),'wall_seconds':r['ended_at']-r['ready_at']}
                        for r in provider.log]}
    write(evidence/'resource-stress.json',report)
    print(report['totals'], 'requests:',len(report['requests']), 'seconds:',round(report['wall_seconds'],2),flush=True)
    return int((replay and provider.log) or any(row.get('internal_failure') for row in rows) or
               report['totals']['expected']!=report['totals']['delivered'])


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--models',type=int,default=24)
    parser.add_argument('--replay',action='store_true')
    args=parser.parse_args()
    raise SystemExit(asyncio.run(run(args.root,args.output,args.models,replay=args.replay)))
