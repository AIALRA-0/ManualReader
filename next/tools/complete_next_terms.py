"""Bind equivalent glossary names and fill the remaining planned terms."""
import argparse
import asyncio
from collections import Counter
from pathlib import Path
import sys
from time import perf_counter

from complete_next_teaching import key, valid_terms
from manualreader.stages.finish.formatting import format_artifact
from manualreader.stages.finish.provider import Provider
from manualreader.stages.step05.inference import read,write
from manualreader.steps.step_13_terminology import bind,generate,glossary


def match(all_terms,name):
    from reader_term_quality import planned_term_keys
    keys=planned_term_keys(name)
    for row in all_terms.values():
        candidates=[row['name'],*row.get('aliases',[])]
        stored=set().union(*(planned_term_keys(n) for n in candidates))
        if keys & stored:
            return row['name']


async def main(root,out,legacy):
    sys.path.insert(0,str(legacy))
    provider=Provider(out,sol=40,vision=40)
    archive={}
    for receipt_path in (out/'requests').glob('*/*/receipt.json'):
        receipt=read(receipt_path)
        result_path=receipt_path.with_name('result.json')
        if receipt.get('status')=='complete' and result_path.is_file():
            old=read(result_path).get('result',{})
            if old.get('terms'):
                archive.setdefault(receipt['sample_id'],[]).append(old)
    began=perf_counter()
    async def one(job):
        sample=job.name
        proof=read(job/'term-planning-coverage.json')
        status=proof['planning_goals']
        candidates=read(job/'term-candidates.json')
        known=glossary(root,sample).get('existing_terms',[])
        before=valid_terms(candidates,known)
        waiting=[]
        for row in status:
            actual=[name for name in row.get('missing_terms',[]) if not match(before,name)]
            if row['status']=='needs_mapping' or actual:
                waiting.append({'goal_id':row['goal_id'],'topic':row['topic'],'requested_terms':actual})
            elif row['status']=='missing_terms':
                row.update(status='linked',missing_terms=[])
        added={}
        result={}
        if waiting:
            structure=read(job/'part-blocks.json')
            card=read(root/'inputs'/sample/'task-card.json')
            for old in archive.get(sample,[]):
                result_from_archive={**old,'known_terms':known}
                archive_rows,_=await format_artifact(bind(result_from_archive,structure),('attachments','terms'))
                added.update(valid_terms(archive_rows,known))
            available={**added,**before}
            waiting=[g for g in waiting if not g['requested_terms']
                     or any(not match(available,name) for name in g['requested_terms'])]
            async def ask(stage,payload):
                add='\n仅处理 planned_term_goals 明确列出的缺项。先读题目和真实正文，区分能解释的概念与作者明确留待后续的数值或证据。如果目标已注明待深入，解释能够从正文确认的基本概念，其正式结论或具体界用 reason 记录范围。requested_terms 中过度具体且证据不足的词不要沿用，说明哪一个真实术语更合适。现有正式词条直接复用。每条新解释应独立成文。'
                return await provider.ask(sample,stage,payload,prompt=provider.prompts[stage]+add.encode())
            if waiting:
                result=await generate(root,sample,card,ask,structure=structure,remaining=waiting,
                         existing=[{'name':row['name'],'aliases':row.get('aliases',[])} for row in available.values()])
                fresh,_=await format_artifact(bind(result,structure),('attachments','terms'))
                added.update(valid_terms(fresh,known))
            if added:
                candidates={key(row['name']):row for row in candidates if row.get('name')}
                write(job/'term-candidates.json',list(({**candidates,**added,**before}).values()))
        terms={**added,**before}
        updated={r['goal_id']:r for r in result.get('planned_term_reviews',[])}
        for row in status:
            next_row=updated.get(row['goal_id'],{})
            names=list(dict.fromkeys([*row.get('term_names',[]),*next_row.get('term_names',[])]))
            if '待深入' in row['topic'] and next_row.get('term_names') and all(
                    match(terms,name) for name in next_row['term_names']):
                row['deferred_term_names']=[n for n in row.get('term_names',[]) if not match(terms,n)]
                names=next_row['term_names']
            absent=[name for name in names if not match(terms,name)]
            if names or next_row:
                row.update(term_names=names,missing_terms=absent,mapped_terms=list(dict.fromkeys(
                    m for name in names if (m:=match(terms,name)))),
                    status='missing_terms' if absent else 'linked' if names else 'no_term_identified')
        proof.update(after=len(terms),planning_goals=status,supplement_issues=result.get('issues',[]))
        write(job/'term-planning-coverage.json',proof)
        return {'sample_id':sample,'available':len(terms),'new_terms':len(set(added)-set(before)),
                'requested_goals':len(waiting),'counts':dict(Counter(r['status'] for r in status)),
                'issues':result.get('issues',[])}
    jobs=[p.parent for p in sorted((root/'jobs').glob('*/term-planning-coverage.json'))]
    results=await asyncio.gather(*(one(job) for job in jobs),return_exceptions=True)
    rows=[r if not isinstance(r,Exception) else {'sample_id':j.name,'error':str(r)} for j,r in zip(jobs,results)]
    write(out/'step-13-terminology-supplement.json',{'samples':rows,'requests':provider.log,
                                                  'wall_seconds':round(perf_counter()-began,2)})
    print('terminology',len(rows),sum(r.get('new_terms',0) for r in rows),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for argument in ('root','output','legacy'):
        parser.add_argument(argument,type=Path)
    args=parser.parse_args()
    asyncio.run(main(args.root,args.output,args.legacy))
