"""Restore accepted B1 keep-original decisions using the original, versioned receipts."""

from argparse import ArgumentParser
import asyncio
from collections import Counter
from pathlib import Path
from time import time

from manualreader.stages.finish.content import (_key, _translation_reply, source_units,
    translation_processing, translation_output, TranslationCatalog, source_literal_retention)
from manualreader.stages.finish.formatting import format_artifact
from manualreader.stages.finish.pipeline import relations
from manualreader.stages.finish.provider import Provider
from manualreader.artifacts import read_json as read, write_json as write


async def main(root, out, retry_directory=None):
    decisions={}
    batches={}
    for request in (root/'requests/B1').glob('*/input.json'):
        result=request.with_name('result.json')
        if not result.is_file():
            continue
        batch=read(request)['input']
        accepted=_translation_reply(read(result).get('result',{}),batch)
        for unit in batch['units']:
            sid=unit['source_id']
            key=(batch['file_id'],sid,batch['source_version'],unit['original_sha256'])
            batches[key]=(request.parent.name,batch)
            if sid in accepted:
                choice=accepted[sid]
                if decisions.get(key,{}).get('relation')!='translated' or choice['relation']=='translated':
                    decisions[key]=choice
    pending=set()
    covered=set()
    jobs=[]
    for job in sorted((root/'jobs').iterdir()):
        if not (job/'run.json').is_file():
            continue
        source=read(root/'inputs'/job.name/'source-index.json')
        rows={r['source_id']:r for r in read(job/'source-translation-links.json')}
        for unit in source_units(source):
            match=rows.get(unit['source_id'],{})
            key=_key(unit)
            if match.get('status') in ('complete','uncertain') and match.get('source_version')==key[2]:
                covered.add(key)
            elif unit['relation']=='translated':
                pending.add(key)
        jobs.append((job,source))
    calls=0
    if retry_directory:
        provider=Provider(retry_directory)
        batches_to_retry={}
        for key in pending-covered-decisions.keys():
            if key in batches:
                name,batch=batches[key]
                group=batches_to_retry.setdefault(name,{**batch,'units':[]})
                group['units'].extend(u for u in batch['units'] if u['source_id']==key[1])

        async def retry(name,batch):
            try:
                response=await provider.ask(name,'B1',batch)
            except (ValueError,RuntimeError,OSError):
                return
            for u in batch['units']:
                accepted=_translation_reply(response,batch).get(u['source_id'])
                if accepted:
                    decisions[(batch['file_id'],u['source_id'],batch['source_version'],u['original_sha256'])]=accepted

        await asyncio.gather(*(retry(name,batch) for name,batch in batches_to_retry.items()))
        calls=len(provider.log)
    summary={}
    distinct={}
    for job,source in jobs:
        links=read(job/'source-translation-links.json')
        columns={r['source_id']:r for r in links}
        restored=[]
        additions=[]
        for unit in source_units(source):
            key=_key(unit)
            fid,sid,version,sha=key
            decision=decisions.get(key,{})
            literal=source_literal_retention(unit)
            if not decision and literal and key in batches:
                decision={'relation':'identity','status':'complete','origin':literal}
            if sid in columns or not decision:
                continue
            if decision['relation']=='translated':
                generated=translation_output(job.name,[unit],{key},TranslationCatalog({}, {}, ()),
                    {key:{**decision,'translation_group_id':''}},[])
                link=generated['links'][0]
                additions.extend(generated['groups'])
            else:
                link={'source_id':sid,'translation_id':sid,'relation':decision['relation'],
                      'source_version':version,'original_sha256':sha,'status':decision['status'],
                      'provenance':decision.get('origin','provider_preserved')}
            columns[sid]=link
            links.append(link)
            restored.append(link)
        processing=translation_processing(source,links)
        for unit in source_units(source):
            if unit['relation']=='source_asset' or not unit.get('text','').strip():
                continue
            row=columns.get(unit['source_id'],{})
            if row.get('status') in ('complete','uncertain'):
                state='translated' if row.get('relation')=='translated' else 'preserved'
            else:
                state='failed'
            prior=distinct.get(_key(unit),'failed')
            if prior!='translated' and (state!='failed' or prior=='failed'):
                distinct[_key(unit)]=state
        if restored:
            run=read(job/'run.json')
            run['translation']['unresolved']-=len(restored)
            run['translation']['provider_preserved']=run['translation'].get('provider_preserved',0)+len(restored)-len(additions)
            run['translation']['preserved_uncertain']=run['translation'].get('preserved_uncertain',0)+sum(
                r['status']=='uncertain' and r['relation']!='translated' for r in restored)
            for entry in restored:
                if entry['relation']=='translated':
                    run['translation'][entry['status']]+=1
            if run['translation']['unresolved']<0:
                raise ValueError(f'{job.name} 的补回数量超过原缺项')
            write(job/'source-translation-links.json',links)
            if additions:
                existing=read(job/'translation-groups.json')
                new, edits=await format_artifact(additions,('translations','groups'))
                for edit in edits:
                    edit['path'][2]+=len(existing)
                write(job/'translation-groups.json',existing+new)
                write(job/'format-patches.json',read(job/'format-patches.json')+edits)
                formatted=job/'generated/formatted.json'
                if formatted.is_file():
                    v=read(formatted)
                    v['translations']['groups']=existing+new
                    v['translations']['links']=links
                    write(formatted,v)
            write(job/'alignment.json',relations(read(job/'part-blocks.json'),{'links':links}))
            write(job/'run.json',run)
            write(job/'preview-revision.json',{'kind':'accepted-translation-disposition','updated_at':time()})
        summary[job.name]={**processing,'restored_count':len(restored)}
    counts=dict(Counter(distinct.values()))
    write(out,{'samples':summary,'unique_text_units':counts,
               'total':len(distinct),'restored_refs':sum(r['restored_count'] for r in summary.values()),
               'new_model_requests':calls})
    print(counts,flush=True)


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('out',type=Path)
    parser.add_argument('--retry-directory',type=Path)
    opts=parser.parse_args()
    asyncio.run(main(opts.root,opts.out,opts.retry_directory))
