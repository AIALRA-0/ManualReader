"""Run the remaining Reader stages over the frozen forty STEP-07 outputs."""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import time

from manualreader.artifacts import read_json as read, write_json as write
from .provider import ALIASES, Provider, SCHEMAS
from ...steps.events import REGISTRY


STEP_BY_SLUG = {step['slug']:step['step_id'] for step in REGISTRY}
TIMING_STEPS = {
    '08':'step_08_structure', '16':'step_16_final_formatting',
    'reading-groups':'step_09_source_reading_layout',
    'wait-translation':'step_10_source_translation',
    'step_10_translation_job':'step_10_source_translation',
    'A2-source-labels':'step_11_visual_label_translation',
    'B2-labels':'step_11_visual_label_translation',
    'source-image-labels':'step_11_visual_label_translation',
    'step_11_visual_job':'step_11_visual_label_translation',
    'step_12_resource_recovery':'step_12_resource_explanation',
    'step_13_standalone_term_repair':'step_13_terminology',
    'step_18_source_gap_recovery':'step_18_alignment_coverage',
}


def timing_step(node):
    slug=ALIASES.get(node,{}).get('step') or TIMING_STEPS.get(node,node)
    return {'step_id':STEP_BY_SLUG.get(slug),'step_slug':slug}


def sources(root):
    samples={p.name:read(root/'inputs'/p.name/'source-index.json')
             for p in sorted((root/'upstream07').iterdir()) if p.is_dir()}
    if not samples:
        raise ValueError('没有已验收的 STEP-07 输入')
    return samples


class Timings:
    def __init__(self):
        self.rows=[]

    async def run(self,sample,node,awaitable):
        start=time()
        status='complete'
        error=None
        try:
            return await awaitable
        except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
            status='partial'
            error=f'{type(exc).__name__}: {exc}'
            return None
        finally:
            end=time()
            self.rows.append({'sample_id':sample,'node':node,**timing_step(node),'ready_at':start,'queue_at':start,
                              'start_at':start,'end_at':end,'duration_seconds':end-start,
                              'status':status,'error':error})


async def translation_batch(root,providers,timing):
    from .content import build_translation_catalog,translate,translation_processing
    catalog_inputs=sources(root)
    catalog=build_translation_catalog(catalog_inputs.values())
    batch_at=time()
    write(root/'translation-queue.json',{'state':'running','at':batch_at,'samples':list(catalog_inputs),
                                        'unique_foreign_sources':len(catalog.by_source)})
    async def sample(name,source):
        job=root/'jobs'/name
        previous=read(job/'translation-initial.json') if (job/'translation-initial.json').is_file() else None
        if previous and not translation_processing(source,previous.get('links',[]))['failed_source_ids']:
            write(job/'translation-ready.json',{'batch_at':batch_at})
            return
        translated=await timing.run(name,'B1',translate(root,name,source,providers.scope(name),catalog,existing=previous))
        if translated is None:
            translated={'groups':[],'links':[],'unresolved_source_ids':[],
                        'counts':{},'issues':['B1 作业失败，详见阶段日志']}
        write(job/'translation-initial.json',translated)
        write(job/'translation-ready.json',{'batch_at':batch_at})
        print('B1',name,translated.get('counts'),flush=True)

    await asyncio.gather(*(sample(name,source) for name,source in catalog_inputs.items()))
    write(root/'translation-queue.json',{'state':'done','at':time(),'samples':list(catalog_inputs),
                                        'unique_foreign_sources':len(catalog.by_source)})


def persist(root,phase,providers,timing):
    write(root/'metrics'/f'{phase}-requests.json',providers.log)
    write(root/'metrics'/f'{phase}-consumers.json',providers.consumer_log)
    write(root/'metrics'/f'{phase}-nodes.json',timing.rows)
    write(root/'metrics'/f'{phase}-limits.json',
          {k:{'peak_in_flight':x.peak,'changes':x.changes} for k,x in providers.lanes.items()})
    write(root/'metrics'/f'{phase}-wall.json',{'phase':phase,'started_at':providers.started,'ended_at':time()})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('root',type=Path)
    parser.add_argument('phase',choices=('translate','finish','all'))
    parser.add_argument('--sol',default=40,type=int)
    parser.add_argument('--ds',default=12,type=int)
    parser.add_argument('--vision',default=12,type=int)
    args=parser.parse_args()
    async def go():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(
            max_workers=args.sol+args.ds+8,thread_name_prefix='reader-finish'))
        root=args.root.resolve()
        providers=Provider(root,sol=args.sol,ds=args.ds,vision=args.vision)
        timings=Timings()
        write(root/'model-schemas.json',SCHEMAS)
        try:
            if args.phase=='translate':
                await translation_batch(root,providers,timings)
            else:
                from .pipeline import finish_batch
                if args.phase=='all':
                    await asyncio.gather(translation_batch(root,providers,timings),
                                         finish_batch(root,providers,timings))
                else:
                    await finish_batch(root,providers,timings)
        finally:
            persist(root,args.phase,providers,timings)
    asyncio.run(go())


if __name__=='__main__':
    main()
