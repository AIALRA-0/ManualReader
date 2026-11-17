"""Restore demonstrated source links and add only grounded missing explanations."""
from collections import Counter
from hashlib import sha256

from manualreader.stages.finish.formatting import format_artifact
from manualreader.stages.finish.provider import ModelFailure
from manualreader.artifacts import read_json as read, write_json as write


async def one(root,job,provider):
    sample=job.name
    data=read(job/'part-blocks.json')
    if data['mode']!='NORMAL':
        return None
    body=sha256((job/'body.final.md').read_bytes()).hexdigest()
    selected=read(root/'inputs'/sample/'source-index.json')['sources']
    mapped={e['source_id'] for e in data['source_edges'] if e['relation_type']=='direct_semantic'}
    gaps=[s for s in selected if s['source_id'] not in mapped]
    if not gaps:
        return {'sample_id':sample,'items':[],'counts':{}}
    sources={s['source_id']:s for s in gaps}
    blocks={b['block_id']:b['markdown'] for b in data['blocks'] if b.get('surface')=='body'}
    previous=job/'source-gap-recovery.json'
    old=read(previous) if previous.is_file() else {}
    reliable_reference={r['source_id']:r for r in old.get('items',[])
                        if old.get('body_sha256')==body and r['disposition']!='unresolved'}
    ordered={row['source_id']:i for i,row in enumerate(selected)}
    pending=[]
    for source in gaps:
        sid=source['source_id']
        if sid in reliable_reference:
            continue
        if source['kind'] in ('image','page_image') and source['text'].strip() in ('','原页图片','原页'):
            reliable_reference[sid]={'source_id':sid,'block_id':'','disposition':'reference_only',
                                  'original_excerpt':'','body_excerpt':'','addition':'',
                                  'reason':'此编号为保留的原页图像载体，具备定位；实质信息由相应文字或独立图表记录。'}
        elif not source['text'].strip() and not source.get('asset_refs'):
            reliable_reference[sid]={'source_id':sid,'block_id':'','disposition':'reference_only',
                                  'original_excerpt':'','body_excerpt':'','addition':'',
                                  'reason':'已登记的节点没有文字或媒体，保留原位置。'}
        else:
            i=ordered[sid]
            neighbors=[{'source_id':r['source_id'],'text':r['text'][:700]} for r in selected[max(0,i-1):i+2]
                       if r['source_id']!=sid and r['file_id']==source['file_id']]
            pending.append({'source_id':sid,'kind':source['kind'],'text':source['text'],'role':source.get('role'),
                            'position':source.get('position'),'neighboring_original':neighbors})
    new=dict(reliable_reference)
    issues=[]
    if pending:
        try:
            response=await provider.ask(sample,'SOURCE_GAP',{'source_items':pending,'explanation_blocks':blocks})
        except ModelFailure as exc:
            response={}
            issues.append(str(exc))
        for item in response.get('items',[]):
            sid=item.get('source_id')
            disposition=item.get('disposition')
            block=item.get('block_id')
            if sid not in sources or sid in new:
                continue
            if disposition in ('already_explained','add'):
                original=item.get('original_excerpt','').strip()
                if not original or original not in sources[sid]['text'] or block not in blocks:
                    continue
                if disposition=='already_explained':
                    quote=item.get('body_excerpt','').strip()
                    if not quote or quote not in blocks[block]:
                        continue
                else:
                    extra=item.get('addition','').strip()
                    if not extra:
                        continue
                    wrapped,_=await format_artifact({'overview':extra},('source_gap',))
                    item={**item,'addition':wrapped['overview']}
            if disposition in ('already_explained','add','reference_only','unresolved'):
                new[sid]=item
    for sid in sources:
        if sid not in new:
            new[sid]={'source_id':sid,'block_id':'','disposition':'unresolved','original_excerpt':'',
                      'body_excerpt':'','addition':'','reason':'未拿到可验证的正文锚点或原文证据。'}
    if sha256((job/'body.final.md').read_bytes()).hexdigest()!=body:
        raise ValueError('本篇正文已发生变化，需要从新版本检查该来源。')
    out={'sample_id':sample,'body_sha256':body,'items':list(new.values()),'issues':issues,
         'counts':dict(Counter(r['disposition'] for r in new.values()))}
    write(job/'source-gap-recovery.json',out)
    return out
