"""Retry unfinished resource explanations against verified NEXT originals."""
import asyncio
from hashlib import sha256
import re

from manualreader.stages.finish import content
from manualreader.stages.finish.formatting import format_artifact
from manualreader.stages.finish.preview import file_config
from manualreader.stages.step03.cache import atomic
from manualreader.artifacts import read_json as read, write_json as write

FULL={'explained','covered_by_group'}


def recover_crops(root, source, resources, files):
    import fitz
    linked = {}
    for row in resources:
        if content._kind(row) not in content._VISUAL_KINDS:
            continue
        asset = next((a for a in source.get('assets',{}).values()
                      if a.get('sha256')==row.get('media_sha256')
                      and a.get('locator',{}).get('type')=='pdf_bbox'),None)
        if not asset:
            continue
        original=files.get(asset['file_id'],{}).get('path')
        if not original:
            continue
        loc=asset['locator']
        try:
            with fitz.open(original) as doc:
                page=doc[int(loc['number'])-1]
                rect=fitz.Rect(loc['bbox'])
                expanded=fitz.Rect(rect.x0-12,rect.y0-10,rect.x1+12,rect.y1+28)
                unrotated=page.rect * page.derotation_matrix
                clip=(expanded & unrotated) * page.rotation_matrix
                if clip.is_empty:
                    continue
                png=page.get_pixmap(matrix=fitz.Matrix(2.4,2.4),clip=clip,alpha=False).tobytes('png')
        except (ValueError,OSError,KeyError,IndexError,RuntimeError):
            continue
        digest=sha256(png).hexdigest()
        atomic(root/'media'/(digest+'.png'),png)
        linked[row['resource_id']]=[{'resource_id':row['resource_id'],
             'raw_content':f"已校验原件第 {loc['number']} 页的扩大裁图，用于查看旧裁图边缘",
             'media_sha256':digest}]
    return linked


def definition_excerpts(sources, unknown):
    markers=re.compile(r':=|\\coloneqq|定义|记作|denot|defined|denotes|let |where ',re.I)
    words={re.sub(r'[^a-zA-Z0-9]','',word).casefold() for word in unknown}
    words={word for word in words if 1<len(word)<=12}
    if not words:
        return []
    matched=[]
    for source in sources:
        text=source.get('text','')
        if not markers.search(text):
            continue
        matches=sum(bool(re.search(r'(?<![a-zA-Z])'+re.escape(word)+r'(?![a-zA-Z])',
                    text.replace('_','').replace('{','').replace('}',''),re.I)) for word in words)
        if matches:
            matched.append((matches,source['source_id'],text[:840]))
    return [{'source_id':sid,'text':text} for _,sid,text in sorted(matched,reverse=True)[:12]]


def registered_sources(project_dir, file_ids):
    path=project_dir/'original-registration.json'
    if not path.is_file():
        return []
    registered=read(path).get('sources',{})
    return [{'source_id':sid,'text':row.get('raw_text') or row.get('en') or row.get('alt') or '',
             'file_id':file_id}
            for file_id,rows in registered.items() if file_id in file_ids
            for sid,row in rows.items()]


def neighboring_definitions(sources, linked_ids, window=2):
    positions={i for i,row in enumerate(sources) if row['source_id'] in linked_ids}
    neighbors={j for i in positions for j in range(max(0,i-window),min(len(sources),i+window+1))
               if sources[j]['file_id']==sources[i]['file_id']}
    return [{'source_id':sources[i]['source_id'],'text':sources[i]['text'][:900]}
            for i in sorted(neighbors - positions) if sources[i]['text'].strip()][:12]


def missing_delivery(p2):
    by_group={g['group_id']:set(g['resource_ids']) for g in p2['groups']}
    return {r['resource_id'] for r in p2['coverage_by_resource'] if r['eligible']
            and r['resource_id'] not in by_group.get(r.get('group_id'),set())}


async def retry_delivery(job, refresh, *, pause=asyncio.sleep):
    delay=2
    cycle=1
    while missing_delivery(read(job/'resource-explanations.json')):
        await pause(delay)
        await refresh(cycle)
        cycle+=1
        delay=min(delay*2,120)


async def repair(root, job, provider, *, delivery_cycle=None):
    sample=job.name
    p2=read(job/'resource-explanations.json')
    wanted=missing_delivery(p2) if delivery_cycle is not None else {
        r['resource_id'] for r in p2['coverage_by_resource'] if r['eligible'] and r['status'] not in FULL}
    if not wanted:
        return None
    side=root/'inputs'/sample
    structure=read(job/'part-blocks.json')
    originals=read(side/'source-index.json')
    card=read(side/'task-card.json')
    project=read(side/'step04-run.json')['project_id']
    file_rows=await asyncio.to_thread(file_config,originals,card,root/'original-inputs'/'projects',project)
    resources=[r for r in structure['resources'] if r['resource_id'] in wanted]
    related=await asyncio.to_thread(recover_crops,root,originals,resources,file_rows)
    scope={**structure,'resources':resources,'resource_context_catalog':structure['resources'],
           'related_visual_inputs':related}
    source_by_id={row['source_id']:row for row in originals['sources']}
    filing=registered_sources(root/'original-inputs'/'projects'/project,originals['source_files'])
    prior={r['resource_id']:g for g in p2['groups'] for r in p2['coverage_by_resource']
           if r.get('group_id')==g['group_id']}
    async def ask(stage,payload,images=None):
        ids={sid for g in payload.get('semantic_groups',[]) for sid in g.get('source_ids',[])}
        selected=[{'source_id':sid,'text':source_by_id[sid].get('text','')[:1050]}
                  for sid in sorted(ids) if sid in source_by_id]
        history={rid:{'overview':prior[rid].get('overview',''),
                      'conditions':prior[rid].get('assumptions_and_limits',[]),
                      'unknown':[e['raw_content'] for e in prior[rid].get('components',[])
                                 if e['certainty']=='uncertain'],
                      'previous_components':[
                          {k:v for k,v in e.items() if k in ('raw_content','meaning','location_hint','certainty')}
                          for e in prior[rid].get('components',[])]}
                 for rid in payload['resource_ids'] if rid in prior}
        candidates=[s for h in history.values() for s in h['unknown']]
        if not candidates:
            for group in payload.get('semantic_groups',[]):
                for row in group.get('resources',[]):
                    candidates.extend(re.findall(r'\\[a-zA-Z]+|[A-Za-z](?:_\{?[A-Za-z0-9]+\}?)?',
                                                 row.get('raw_content') or row.get('raw','')))
        definitions=definition_excerpts(filing,candidates)
        prompt=provider.prompts[stage]+(
            '\n本次只补尚未解释清楚的资源。依次解释实际存在的元素、怎样组合、已知条件。'
            '读取 related_images 中给出的所有真实图片，区分资源和扩展参考图。'
            '先从邻近正文、原件定义和现有说明核对各组成部分。对原文没有命名的符号，解释它在本式中能确认的计算作用，'
            '把命名或外延未知单列在条件里。如果连作用也无法确认，保留该元素及具体原因。'
            '每组务必返回 summary。仅在确实没有证据时保留不确定项，不虚构变量含义、图内文字、单位或数值。'
            '不要把正文改写成新的讲解章节。\n').encode()
        if delivery_cycle is not None:
            payload={**payload,'delivery_retry':delivery_cycle}
        return await provider.ask(sample,stage,{**payload,'source_excerpts':selected,'previous_missing':history,
                                                'candidate_definition_excerpts':definitions,
                                                'neighboring_source_excerpts':neighboring_definitions(filing,ids)},
                                  images,prompt)
    variants=await asyncio.gather(content.explain_math(root,sample,scope,ask),
                                  content.explain_visual(root,sample,scope,originals,ask))
    found=[]
    improved={}
    for result in variants:
        for group in result['groups']:
            keep=[rid for rid in group['resource_ids'] if rid in wanted]
            if not keep:
                continue
            group={**group,'resource_ids':keep,
                   'question_context':{**group['question_context'],'resource_ids':keep}}
            found.append(group)
            for rid in keep:
                improved[rid]={'group_id':group['group_id'],'status':group['status']}
    if found:
        formatted,format_patches=await format_artifact(found,('attachments','resource_explanations','groups'))
        retained=[]
        for old in p2['groups']:
            ids=[rid for rid in old['resource_ids'] if rid not in improved]
            if ids:
                retained.append({**old,'resource_ids':ids,
                                 'question_context':{**old.get('question_context',{}),'resource_ids':ids}})
        p2['groups']=retained+formatted
        p2['coverage_by_resource']=[{**r,**improved.get(r['resource_id'],{})} for r in p2['coverage_by_resource']]
        write(job/'resource-explanations.json',p2)
        journal=job/'format-patches.json'
        if format_patches and journal.is_file():
            for patch in format_patches:
                patch['path'][3]+=len(retained)
            write(journal,read(journal)+format_patches)
    return {'sample_id':sample,'requested':sorted(wanted),'explanations_updated':improved,
            'expanded_original_crops':related,
            'remaining':[r['resource_id'] for r in p2['coverage_by_resource'] if r['resource_id'] in wanted and r['status'] not in FULL],
            'request_issues':[i for result in variants for i in result['issues']]}
