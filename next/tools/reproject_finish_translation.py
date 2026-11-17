"""Reproject actual first responses and one wire retry without invoking a model."""

import argparse
import asyncio
from collections import Counter, defaultdict
from pathlib import Path

from manualreader.stages.finish.content import (
    TranslationCatalog,reproject_math_glyphs,translate,_hash,
)
from manualreader.stages.finish.formatting import layer,scan_batch,type_patches
from manualreader.stages.finish.pipeline import relations
from manualreader.stages.finish.provider import ModelFailure,serialize
from manualreader.stages.finish.runner import sources
from manualreader.stages.step05.inference import read,write


def archived(root):
    table={}
    for stage in ('B1','B2'):
        for folder in (root/'requests'/stage).iterdir():
            payload=read(folder/'input.json')['input']
            entry=folder/'result.json'
            table[(stage,serialize(payload))]=read(entry)['result'] if entry.is_file() else None
    async def answer(stage,payload):
        value=table.get((stage,serialize(payload)))
        if value is None:
            raise ModelFailure('相同的原始请求未返回完整译文')
        return value
    return answer


def fresh_typography(rows,key):
    if not rows:
        return
    raw=[r[key] for r in rows]
    for row,text,scan in zip(rows,raw,scan_batch(raw),strict=True):
        row[key]=layer(text,type_patches(text,scan))[0]


def by_recovered_label(root):
    evidence={}
    receipts=read(root/'ds-technical-retry.json')['outcomes']
    for rec in receipts:
        if rec['stage']!='B2' or rec['outcome']!='complete':
            continue
        folder=root/'requests/B2'/rec['id']
        expected={r['label_id']:r for r in read(folder/'input.json')['input']['labels']}
        for row in read(folder/'result.json')['result']['translations']:
            ref=expected.get(row.get('label_id'))
            if ref and all(ref[k]==row.get(k) for k in ('resource_id','original')) and (
                    row.get('status') in ('complete','uncertain') and isinstance(row.get('translated'),str)
                    and row['translated'].strip()):
                evidence[row['label_id']]={**row,'asset_sha256':ref['asset_sha256'],'request_id':rec['id']}
    return evidence


def restore_visual(folder,recovered,consumer_ids):
    path=folder/'translation-visuals.json'
    visuals=read(path)
    old=visuals['translations']
    accepted={(r['resource_id'],r['label_id']):r for r in old}
    labels=visuals.get('source_labels',[])+read(folder/'generated/visual.json').get('verified_labels',[])
    restored=[]
    for row in labels:
        key=(row['resource_id'],row['label_id'])
        sha=row.get('asset_sha256')
        original=row.get('original')
        if key in accepted or not sha or not isinstance(original,str):
            continue
        match=recovered.get('label-'+_hash(sha,original.strip())[:22])
        if not match or consumer_ids and match['request_id'] not in consumer_ids or (
                match['asset_sha256'],match['original'])!=(sha,original):
            continue
        new={**row,'translated':match['translated'],'status':match['status']}
        accepted[key]=new
        restored.append(new)
    if restored:
        fresh_typography(restored,'translated')
    completed={r['label_id'] for r in accepted.values() if r['status']=='complete'}
    issues=[r for r in visuals.get('issues',[]) if any(label not in recovered for label in r.get('label_ids',[]))]
    visual={**visuals,'translations':list(accepted.values()),'issues':issues,
            'unresolved_label_ids':[lid for lid in visuals['unresolved_label_ids'] if lid not in completed]}
    write(path,visual)
    return len(restored),dict(Counter(row['status'] for row in visual['translations']))


async def update(root):
    raw=sources(root)
    by_source={}
    for f in (root/'requests/B1').glob('*/input.json'):
        pkt=read(f)['input']
        for u in pkt['units']:
            key=(pkt['file_id'],u['source_id'],pkt['source_version'],u['original_sha256'])
            if key in by_source and by_source[key]!=pkt:
                raise ValueError(f'同一原文在白板运行中出现两个请求 {u["source_id"]}')
            by_source[key]=pkt
    catalog=TranslationCatalog(by_source,{},())
    replay=archived(root)
    recovered=by_recovered_label(root)
    consumers=defaultdict(set)
    logs=root/'metrics/whiteboard-consumers.json'
    if logs.is_file():
        for row in read(logs):
            if row['stage']=='B2':
                consumers[row['sample_id']].add(row['id'])
    target=root/'deterministic-b1-reprojection.json'
    summaries=read(target).get('samples',{}) if target.is_file() else {}
    raw={sample:src for sample,src in raw.items() if sample not in summaries and (root/'jobs'/sample/'run.json').is_file()}
    translated=await asyncio.gather(*(translate(root,sample,src,replay,catalog) for sample,src in raw.items()))
    for (sample,src),tx_new in zip(raw.items(),translated,strict=True):
        folder=root/'jobs'/sample
        run=read(folder/'run.json')
        initial=read(folder/'translation-initial.json')
        existing={row['translation_id']:row for g in read(folder/'translation-groups.json') for row in g['segments']}
        tx=reproject_math_glyphs(src,tx_new)
        restored_ids=[]
        new=[]
        for group in tx['groups']:
            for i,row in enumerate(group['segments']):
                if row['translation_id'] in existing:
                    group['segments'][i]=existing[row['translation_id']]
                else:
                    restored_ids.append(row['source_id'])
                    new.append(row)
        fresh_typography(new,'text')
        labels,visual_counts=restore_visual(folder,recovered,consumers[sample])
        correction={'before':initial['counts'],'after':tx['counts'],
                    'classification_corrections':tx.get('classification_corrections',[]),
                    'source_ids_restored_after_wire_retry':restored_ids,'image_label_references_restored':labels}
        write(folder/'translation-corrections.json',correction)
        write(folder/'source-translation-links.json',tx['links'])
        write(folder/'translation-groups.json',tx['groups'])
        write(folder/'alignment.json',relations(read(folder/'part-blocks.json'),tx))
        write(folder/'run.json',{**run,'translation':tx['counts'],'visual_translations':visual_counts,
                               'issues':{**run['issues'],'translation':tx.get('issues',[]),
                                         'visual':read(folder/'translation-visuals.json')['issues']}})
        summaries[sample]={'preserved_math':len(correction['classification_corrections']),
                           'source_translations_restored':len(restored_ids),'image_labels_restored':labels}
    write(root/'deterministic-b1-reprojection.json',{
        'samples':summaries,'additional_model_calls':0,
        'totals':{key:sum(row[key] for row in summaries.values()) for key in next(iter(summaries.values()))}})
    print('B1/B2 reprojection is complete for',len(summaries),'chapters')


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('root',type=Path)
    asyncio.run(update(p.parse_args().root.resolve()))
