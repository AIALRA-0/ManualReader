"""Start each chapter's independent tasks when their own inputs are ready."""

import asyncio
from collections import Counter, defaultdict
from time import time

from ..step03.cache import atomic
from manualreader.artifacts import read_json as read, write_json as write
from .structure import build_structure, reading_groups


EMPTY_EXPLANATION={'groups':[],'coverage_by_resource':[],'verified_labels':[],'issues':[]}
EMPTY_GUIDE={'chapter_intro':'','chapter_outro':'','reading_flow':[],'part_recaps':[],'uncertainties':[]}
EMPTY_TEXT={'groups':[],'links':[],'unresolved_source_ids':[],'counts':{},'issues':[]}


def result_at(job,name,default=None):
    p=job/name
    return read(p) if p.is_file() else default


async def wait_translation(root,job):
    path=job/'translation-initial.json'
    expires=time()+3600
    while not path.is_file():
        queue=root/'translation-queue.json'
        outside=time()>=expires
        settled=queue.is_file() and read(queue).get('state')=='done'
        if outside or settled:
            return {**EMPTY_TEXT,'issues':[{'reason':'这篇没有收到译文产物，正文和其他附件仍可阅读。'}]}
        await asyncio.sleep(0.4)
    return read(path)


async def replayable(timings,sample,node,path,task):
    if path.is_file():
        return read(path)
    result=await timings.run(sample,node,task())
    if result is not None:
        write(path,result)
    return result


def update_structure_files(job,structure):
    write(job/'part-blocks.json',structure)
    write(job/'anchor-lineage.json',structure['anchor_lineage'])
    write(job/'block-source-links.json',structure['source_edges'])
    write(job/'resource-block-links.json',[
        {k:r.get(k) for k in ('resource_id','block_id','render_anchor','inactive_in_body','carrier')}
        for r in structure['resources']])


def explanations(structure,*results):
    from .content import p2_inventory
    eligibility=p2_inventory(structure)
    lookup={}
    for result in results:
        rows=result.get('coverage_by_resource',[])
        if isinstance(rows,dict):
            rows=[{'resource_id':rid,**row} for rid,row in rows.items()]
        for row in rows:
            lookup[row['resource_id']]=row
    coverage=[]
    for resource in structure['resources']:
        identifier=resource['resource_id']
        scope=eligibility[identifier]
        coverage.append({'resource_id':identifier,'block_id':resource.get('block_id'),
                         'inactive_in_body':resource.get('inactive_in_body'),**scope,
                         'status':'pending' if scope['eligible'] else 'excluded',
                         **(lookup.get(identifier,{}) if scope['eligible'] else {})})
    return {'schema_version':'resource-explanation-v2',
            'body_sha256':structure['body_sha256'],
            'scope_counts':{'total':len(coverage),'eligible':sum(row['eligible'] for row in coverage),
                            'excluded':sum(not row['eligible'] for row in coverage)},
            'groups':[row for value in results for row in value.get('groups',[])],
            'coverage_by_resource':coverage,
            'verified_labels':[row for value in results for row in value.get('verified_labels',[])],
            'issues':[row for value in results for row in value.get('issues',[])]}


def relations(structure,translated):
    st=translated['links']
    se=structure['source_edges']
    source_translation=defaultdict(list)
    translation_source=defaultdict(list)
    source_blocks=defaultdict(list)
    block_sources=defaultdict(list)
    for edge in st:
        if not edge.get('translation_id'):
            continue
        s,t=edge['source_id'],edge['translation_id']
        if t not in source_translation[s]:
            source_translation[s].append(t)
        if s not in translation_source[t]:
            translation_source[t].append(s)
    for edge in se:
        if not edge.get('block_id'):
            continue
        s,b=edge['source_id'],edge['block_id']
        if b not in source_blocks[s]:
            source_blocks[s].append(b)
        if s not in block_sources[b]:
            block_sources[b].append(s)
    return {'schema_version':'reader-two-relations-v1','source_to_translation':dict(source_translation),
            'translation_to_source':dict(translation_source),'source_to_explanation':dict(source_blocks),
            'explanation_to_source':dict(block_sources),'source_translation_links':st,
            'source_explanation_links':se,'source_navigation':structure['source_navigation'],
            'coverage':structure['coverage'],'offset_unit':structure['offset_unit']}


def store_attachments(job,attachments):
    write(job/'chapter-guidance.json',attachments['guidance'])
    write(job/'term-candidates.json',attachments['terms'])
    write(job/'quiz-candidates.json',attachments['questions'])
    write(job/'resource-explanations.json',attachments['resource_explanations'])
    write(job/'translation-visuals.json',attachments['visual_translations'])


async def finish_batch(root,providers,timing,sample_sources=None,*,write_result=True,
                       early_visual=None,translation_task=None,on_update=None,refresh_translation=False):
    from . import content
    from .runner import sources
    from .formatting import finalize,format_artifact
    from ...steps import step_13_terminology as terminology
    from ...steps import step_14_practice as practice
    from ...steps import step_15_chapter_guidance as guidance
    from ...steps.step_12_resource_recovery import repair as repair_resources, retry_delivery
    from ...steps.step_18_source_gap_recovery import one as repair_sources
    if sample_sources is None:
        sample_sources=sources(root)
    visual_catalog=(content.build_visual_catalog(sample_sources.values())
                    if hasattr(content,'build_visual_catalog') else None)
    async def finish(name,source):
        root_sample=root/'inputs'/name
        job=root/'jobs'/name
        final_file=job/'run.json'
        if final_file.is_file():
            previous=read(final_file)
            if previous.get('format_status')!='partial' and not refresh_translation:
                return previous
        card=read(root_sample/'task-card.json')
        known_terms=result_at(root_sample,'reader-context.json',{}).get('existing_terms',[])
        baseline=read(root/'upstream07'/name/'run.json')
        ask=providers.scope(name)
        began=time()
        async def publish(number,event,value,status='partial_ready'):
            if on_update:
                await on_update(name,number,event,value,status)

        side=job/'generated'
        side.mkdir(parents=True,exist_ok=True)
        original=(root/'upstream07'/name/'body.fused.md').read_bytes()
        read_task=asyncio.create_task(replayable(timing,name,'reading-groups',
            job/'reading-groups.json',lambda: asyncio.to_thread(reading_groups,source)))
        tx_task=translation_task or asyncio.create_task(wait_translation(root,job))
        structure=await replayable(timing,name,'08',side/'structure.json',
                                   lambda:asyncio.to_thread(build_structure,root,name))
        if structure is None:
            raise ValueError('无法将已有 STEP-07 锚点投影到正式结构')
        terms_task=asyncio.create_task(replayable(timing,name,'step_13_terminology',side/'terminology.json',
                                   lambda:terminology.generate(root,name,card,ask,structure=structure)))
        update_structure_files(job,structure)
        write(job/'status.json',{'sample_id':name,'state':'reader-inputs-ready','started_at':began})
        atomic(job/'body.final.md',original)
        await read_task
        await publish(8,'structure_ready',structure,'complete')
        write(job/'alignment.json',relations(structure,EMPTY_TEXT))
        await publish(17,'reader_preview_ready',{'blocks':len(structure['blocks'])})
        source_visual_task=early_visual
        label_batches={}
        patches_ready=[]
        p2_progress={}

        async def store_translation(result):
            result=content.reproject_math_glyphs(source,result or EMPTY_TEXT)
            formatted,patches=await format_artifact(result,('translations',))
            patches_ready.extend(patches)
            write(job/'translation-groups.json',formatted['groups'])
            write(job/'source-translation-links.json',formatted['links'])
            write(job/'alignment.json',relations(structure,formatted))
            await publish(18,'translation_links_ready',formatted['counts'])
            return formatted

        async def assemble_translation():
            return await store_translation(await timing.run(name,'wait-translation',tx_task))

        translated_task=asyncio.create_task(assemble_translation())

        async def p2_partial(branch,result):
            p2_progress[branch]=result
            combined=explanations(structure,*p2_progress.values())
            write(job/'resource-explanations.json',combined)
            await publish(12,'p2_batch_ready',result)
        async def on_labels(rows):
            verified=tuple(sorted((r.get('resource_id',''),r.get('label_id',''),r.get('original','')) for r in rows))
            if not verified or verified in label_batches:
                return
            # Schedule the translation immediately, so other source and P2 batches continue.
            label_batches[verified]=asyncio.create_task(timing.run(
                name,'B2-labels',content.translate_labels(root,name,rows,ask,known_terms)))

        if source_visual_task is None and visual_catalog is not None and hasattr(content,'original_labels'):
            source_visual_task=asyncio.create_task(replayable(timing,name,'A2-source-labels',side/'source-labels.json',
                  lambda:content.original_labels(root,name,source,ask,visual_catalog,on_labels=on_labels)))
        early=content.source_labels(source)
        if early:
            await on_labels(early)
        independent=(
            ('A1','math.json',lambda:content.explain_math(root,name,structure,ask,
                on_partial=lambda result:p2_partial('A1',result),source=source)),
            ('A2','visual.json',lambda:content.explain_visual(root,name,structure,source,ask,on_labels=on_labels,
                on_partial=lambda result:p2_partial('A2',result))),
            ('F1','guidance-v2.json',lambda:guidance.guidance(root,name,structure,card,ask)),
            ('F3','practice-v2.json',lambda:practice.practice(root,name,structure,card,ask)),
        )

        async def attach(node,file,factory):
            outcome=await replayable(timing,name,node,side/file,factory)
            if node in ('F1','F3') and outcome:
                key,out_path,step=('guidance','chapter-guidance.json',15) if node=='F1' else (
                    'questions','quiz-candidates.json',14)
                outcome[key],edits=await format_artifact(outcome[key],('attachments',key))
                patches_ready.extend(edits)
                write(job/out_path,outcome[key])
                await publish(step,'attachment_ready',outcome)
            return outcome

        async def final_terms():
            outcome=await terms_task or {}
            rows=terminology.bind(outcome,structure)
            async def fix_term_references(stage,payload):
                return await providers.ask(name,stage,payload,
                        prompt=providers.prompts[stage]+terminology.STANDALONE_REPAIR)
            rows=await replayable(timing,name,'step_13_standalone_term_repair',side/'terminology-standalone.json',
                       lambda:terminology.repair_context_references(rows,fix_term_references))
            rows,edits=await format_artifact(rows,('attachments','terms'))
            patches_ready.extend(edits)
            write(job/'term-candidates.json',rows)
            await publish(13,'terms_bound',{'terms':rows,'issues':outcome.get('issues',[])})
            return rows,outcome.get('issues',[])

        math,visual,guide,quiz,term_result=await asyncio.gather(
            *(attach(*parts) for parts in independent),final_terms())
        src_visual=await source_visual_task if source_visual_task else None
        math=math or EMPTY_EXPLANATION
        visual=visual or EMPTY_EXPLANATION
        for batch in (visual,src_visual or {}):
            if batch.get('verified_labels'):
                # Resume a saved result even when its original callback is no longer running.
                seen={x for key in label_batches for x in key}
                pending=[r for r in batch['verified_labels']
                         if (r.get('resource_id',''),r.get('label_id',''),r.get('original','')) not in seen]
                if pending:
                    await on_labels(pending)
        translated_visual=await asyncio.gather(*label_batches.values()) if label_batches else []
        label_index={}
        unresolved=set()
        visual_issues=[]
        for val in (x for x in translated_visual if x is not None):
            for row in val.get('translations',[]):
                label_index[(row['resource_id'],row['label_id'])]=row
            unresolved.update(val.get('unresolved_label_ids',[]))
            visual_issues.extend(val.get('issues',[]))
        unresolved.difference_update(row['label_id'] for row in label_index.values() if row['status']=='complete')
        visual_result={'translations':list(label_index.values()),'unresolved_label_ids':sorted(unresolved),
                       'issues':visual_issues,
                       'source_labels':(src_visual or {}).get('verified_labels',[]),
                       'source_coverage':(src_visual or {}).get('coverage_by_asset',[])}
        visual_counts=Counter(row['status'] for row in visual_result['translations'])
        visual_status=('partial_ready' if unresolved or visual_issues or
                       any(key not in ('complete','identity') for key in visual_counts)
                       else 'complete' if label_index else 'skipped')
        await publish(11,'visual_translation_settled',{'counts':dict(visual_counts),
                      'unresolved':len(unresolved),'original_labels':len(visual_result['source_labels'])},visual_status)
        guide=guide or {}
        quiz=quiz or {}
        attachments={'guidance':guide.get('guidance',EMPTY_GUIDE),'terms':term_result[0],
                     'questions':quiz.get('questions',[]),'resource_explanations':explanations(structure,math,visual),
                     'visual_translations':visual_result}
        translated=await translated_task
        if translated.get('classification_corrections'):
            write(job/'translation-corrections.json',
                  {'classification_corrections':translated['classification_corrections']})
        store_attachments(job,attachments)
        write(job/'translation-groups.json',translated['groups'])
        write(job/'source-translation-links.json',translated['links'])
        if refresh_translation:
            final=await timing.run(name,'16',finalize(root,name,structure,attachments,translated))
            if final is not None:
                write(side/'formatted.json',final)
        else:
            final=await replayable(timing,name,'16',side/'formatted.json',
                                   lambda:finalize(root,name,structure,attachments,translated))
        if final is None:
            # Keep the structurally valid preview visible when formatting fails for this chapter.
            final={'body':original.decode(),'structure':structure,'attachments':attachments,
                   'translations':translated,'patches':[],'offset_map':[],'format_status':'partial'}
        s=final['structure']
        translated=final['translations']
        attachments=final['attachments']
        atomic(job/'body.final.md',final['body'].encode())
        update_structure_files(job,s)
        store_attachments(job,attachments)
        write(job/'translation-groups.json',translated['groups'])
        write(job/'source-translation-links.json',translated['links'])
        write(job/'alignment.json',relations(s,translated))
        write(job/'format-patches.json',patches_ready+final['patches'])
        write(job/'offset-map.json',final['offset_map'])
        write(job/'attachments.final.json',attachments)
        if 'deferred' in final:
            write(job/'deferred.final.json',final['deferred'])
        await publish(16,'final_coordinates_ready',final['offset_map'],
                      'partial_ready' if final.get('format_status')=='partial' else 'complete')
        async def recover_resources():
            recovered=[]
            async def refresh(cycle=None):
                result=await timing.run(name,'step_12_resource_recovery',
                                        repair_resources(root,job,providers,delivery_cycle=cycle))
                if result and result.get('explanations_updated'):
                    attachments['resource_explanations']=read(job/'resource-explanations.json')
                    write(job/'attachments.final.json',attachments)
                    await publish(12,'resource_explanation_recovered',result)
                if result:
                    recovered.append(result)
            await refresh()
            # Allow all previews and unrelated tasks to advance while missing IDs retry.
            await retry_delivery(job,refresh)
            return recovered[-1] if recovered else None

        async def recover_sources():
            result=await timing.run(name,'step_18_source_gap_recovery',repair_sources(root,job,providers))
            if result and result.get('items'):
                await publish(18,'source_gap_recovered',result['counts'])
            return result

        resource_recovery,source_recovery=await asyncio.gather(recover_resources(),recover_sources())
        cq=attachments['resource_explanations']
        totals=Counter(r['status'] for r in cq['coverage_by_resource'])
        await publish(12,'resource_delivery_settled',{'counts':dict(totals),'scope':cq['scope_counts']},
                      'complete' if cq['scope_counts']['eligible'] else 'skipped')
        await publish(18,'alignment_snapshot_ready',{'source_navigation':s['source_navigation'],
                      'translation':translated['counts']},'complete')
        record={'sample_id':name,'task_id':baseline['task_id'],'mode':baseline['mode'],
                'fixture_based':baseline['fixture_based'],'title':s['title'],'schema_version':'reader-final-v1',
                'body_fused_sha256':baseline['fused_sha256'],'body_final_sha256':s.get('body_final_sha256',s['body_sha256']),
                'source_snapshot':source['snapshot_sha256'],
                'state':'ready_to_commit' if final.get('format_status')!='partial' else 'partial_ready',
                'start_at':began,'end_at':time(),'elapsed_seconds':time()-began,
                'parts':len(s['parts']),'blocks':len(s['blocks']),
                'source_navigation':s['source_navigation'],'coverage':s['coverage'],
                'translation':translated['counts'],'p2':dict(totals),'p2_scope':cq['scope_counts'],
                'visual_translations':dict(visual_counts),
                'terms':len(attachments['terms']),'exercises':len(attachments['questions']),
                'reading_route_items':len(attachments['guidance'].get('reading_flow',[])),
                'recaps':len(attachments['guidance'].get('part_recaps',[])),
                'format_status':final.get('format_status','complete'),
                'recovery':{'step_12':resource_recovery,'step_18':source_recovery and source_recovery.get('counts')},
                'format_candidates':final.get('c1_candidates',0),'format_decisions':final.get('c1_decisions',0),
                'issues':{'translation':translated.get('issues',[]),'visual':visual_issues,
                          'F1':guide.get('issues',[]),'F2':term_result[1],'F3':quiz.get('issues',[]),
                          'P2':cq['issues'],'source_labels':(src_visual or {}).get('issues',[])}}
        write(final_file,record)
        delivery_ready=record['state']=='ready_to_commit'
        await publish(17,'reader_delivery_sealed' if delivery_ready else 'unformatted_preview_ready',record,
                      'complete' if delivery_ready else 'partial_ready')
        write(job/'status.json',{'sample_id':name,'state':'finished' if delivery_ready else 'partial_ready','started_at':began,'finished_at':time()})
        print('FINAL',name,dict(totals),translated['counts'].get('unresolved',0),flush=True)
        return record

    tasks=await asyncio.gather(*(finish(name,source) for name,source in sample_sources.items()),return_exceptions=True)
    problems={name:f'{type(row).__name__}: {row}' for name,row in zip(sample_sources,tasks) if isinstance(row,Exception)}
    outcome={'samples':list(sample_sources),'failures':problems,
             'ready':len(sample_sources)-len(problems),'ended_at':time()}
    if write_result:
        write(root/'finish-result.json',outcome)
    for sample,problem in problems.items():
        print('ERROR',sample,problem,flush=True)
    return outcome
