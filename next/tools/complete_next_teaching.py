"""Complete only missing Next terminology, practice and chapter transitions."""
import argparse
import asyncio
from collections import Counter
from pathlib import Path
import sqlite3
import json
import sys
from time import perf_counter

from manualreader.stages.finish.content import _part_contents, _section_state
from manualreader.stages.finish.formatting import format_artifact
from manualreader.stages.finish.provider import Provider
from manualreader.artifacts import read_json as read, write_json as write
from manualreader.steps import step_13_terminology as terms, step_14_practice as quizzes
from manualreader.steps.step_15_chapter_guidance import guidance


def key(name):
    return ''.join(str(name).split()).casefold()


def valid_terms(rows, registered):
    from reader_term_quality import validate_asset_term
    kept={}
    for row in rows:
        name=row.get('name')
        if not name or row.get('status')=='needs_review':
            continue
        if not row.get('block_ids'):
            continue
        native={**row,'term_id':row.get('reuse_id'),
                'professional_definition':row.get('definition') or row.get('professional_definition'),
                'intuitive_explanation':row.get('intuition') or row.get('intuitive_explanation')}
        try:
            validate_asset_term(native,registered)
        except (ValueError,KeyError,TypeError):
            continue
        kept[key(name)]=row
    return kept


def recover_heading(root, job, card, context, mode):
    if mode!='INTRO' or (context.get('adjacent_chapters') or {}).get('next'):
        return False
    pid=(card.get('project') or {}).get('id')
    if not pid or not pid.replace('-','').isalnum():
        return False
    registered=root/'original-inputs'/'projects'/pid/'project.json'
    if not registered.is_file():
        return False
    directory=read(registered).get('directory',[])
    if isinstance(directory,dict):
        directory=directory.get('nodes',[])
    first=next((row for row in directory if row.get('node_id',row.get('id')) and row.get('title')
                and row['title'].strip() not in ('导读','章节导读','阶段性收束')),None)
    if not first:
        return False
    context={**context,'adjacent_chapters':{**context.get('adjacent_chapters',{}),'next':first}}
    write(root/'inputs'/job.name/'reader-context.json',context)
    return True


def attempted_prompts(root,sample):
    db=root/'reader-isolated'/'library'/('next-final-'+sample)/'state.sqlite3'
    if not db.is_file():
        return set()
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as conn:
        value=conn.execute('SELECT value FROM reader_state WHERE id=1').fetchone()
    return {key(row.get('prompt')) for row in json.loads(value[0]).get('quizzes',[])
            if row.get('selected') is not None or row.get('attempts')} if value else set()


async def finish_terms(root,job,structure,card,ask):
    ctx=terms.glossary(root,job.name)
    registered=ctx.get('existing_terms',[])
    existing=read(job/'term-candidates.json')
    previous=valid_terms(existing,registered)
    result=await terms.generate(root,job.name,card,ask,structure=structure,
                                existing=[{'name':r['name'],'aliases':r.get('aliases',[])} for r in previous.values()])
    fresh=terms.bind(result,structure)
    fresh,_=await format_artifact(fresh,('attachments','terms'))
    new=valid_terms(fresh,registered)
    older={key(r.get('name')):r for r in existing if r.get('name')}
    collected={**older,**new,**previous}
    for row in fresh:
        name=key(row['name'])
        if name not in collected:
            collected[name]=row
    if new:
        write(job/'term-candidates.json',list(collected.values()))
    final_valid={**new,**previous}
    aliases={key(name) for row in final_valid.values() for name in [row['name'],*row.get('aliases',[])]}
    by_goal={r['goal_id']:r for r in result.get('planned_term_reviews',[])}
    goals=[]
    for goal in terms.planned_terms(card.get('goals',{})):
        row=by_goal.get(goal['goal_id'],{})
        names=row.get('term_names',[])
        unresolved=[name for name in names if key(name) not in aliases]
        goals.append({**goal,'term_names':names,'missing_terms':unresolved,'reason':row.get('reason'),
                      'status':'needs_mapping' if not row else 'missing_terms' if unresolved else
                      'linked' if names else 'no_term_identified'})
    proof={'before':len(previous),'after':len(final_valid),'planning_goals':goals,
           'issues':result.get('issues',[]),'provider_unresolved':result.get('unresolved_terms',[])}
    write(job/'term-planning-coverage.json',proof)
    return proof


async def finish_practice(root,job,structure,card,ask):
    from reader_practice_policy import validate_practice
    saved=read(job/'quiz-candidates.json')
    state=_section_state(root,job.name,structure,card)
    parts=_part_contents(structure,state)
    target=len(parts)

    def check(rows):
        if {r.get('part_id') for r in rows}!={p['part_id'] for p in parts} or not rows:
            return False
        try:
            validate_practice(rows,current_writing=True)
            return all(quizzes.native_question(row) for row in rows)
        except (ValueError,KeyError,TypeError):
            return False

    prior=Counter(r['type'] for r in saved)
    if check(saved):
        return {'before':dict(prior),'after':dict(prior),'target':target,'meets_online_policy':True,'model_needed':False}
    preserve=list(saved)
    generated=await quizzes.practice(root,job.name,structure,card,ask,pinned=preserve)
    rows=generated['questions']
    new_parts={r['part_id'] for r in rows}
    for row in saved:
        if row['part_id'] not in new_parts:
            rows.append(row)
    normalized=[]
    for row in rows:
        if row in saved:
            normalized.append(row)
        else:
            formatted,_=await format_artifact(row,('attachments','questions'))
            normalized.append(quizzes.native_question(formatted))
    if normalized and normalized!=saved:
        write(job/'quiz-candidates.json',normalized)
    return {'before':dict(prior),'after':dict(Counter(r['type'] for r in normalized)),
            'target':target,'meets_online_policy':check(normalized),'model_needed':True,
            'issues':generated['issues'],'unfilled_parts':generated['unresolved_part_ids']}


async def finish_guidance(root,job,structure,card,ask):
    old=read(job/'chapter-guidance.json')
    ctx=terms.glossary(root,job.name)
    found=recover_heading(root,job,card,ctx,structure['mode'])
    mode=structure['mode']
    needs_new=mode!='ANSWER' and (not old.get('chapter_synthesis') or not old.get('reading_flow'))
    identify=found or bool((ctx.get('adjacent_chapters') or {}).get('next'))
    if mode!='ANSWER' and (needs_new or identify and not old.get('next_transition')):
        result=await guidance(root,job.name,structure,card,ask)
        proposed=result['guidance']
        cleaned,_=await format_artifact(proposed,('attachments','guidance'))
        if cleaned.get('chapter_synthesis') and not old.get('chapter_synthesis'):
            old=cleaned
        elif cleaned.get('next_transition') and not old.get('next_transition'):
            old={**old,'next_transition':cleaned['next_transition'],'ending_kind':cleaned['ending_kind']}
        write(job/'chapter-guidance.json',old)
    else:
        result={'issues':[]}
    return {'real_next_chapter':identify,'next_transition':bool(old.get('next_transition')),
            'chapter_synthesis':bool(old.get('chapter_synthesis')),'issues':result['issues']}


async def main(root,out,legacy):
    sys.path.insert(0,str(legacy))
    provider=Provider(out,sol=40,vision=40)
    jobs=[p.parent for p in sorted((root/'jobs').glob('*/part-blocks.json'))]
    began=perf_counter()

    async def per(job):
        sample=job.name
        card=read(root/'inputs'/sample/'task-card.json')
        structure=read(job/'part-blocks.json')
        ask=provider.scope(sample)
        outcome=await asyncio.gather(finish_terms(root,job,structure,card,ask),
                                     finish_practice(root,job,structure,card,ask),
                                     finish_guidance(root,job,structure,card,ask),return_exceptions=True)
        receipt={'sample_id':sample,**{name: {'error':str(r)} if isinstance(r,Exception) else r
                 for name,r in zip(('step_13_terminology','step_14_practice','step_15_chapter_guidance'),outcome)}}
        write(out/'samples'/f'{sample}.json',receipt)
        print(sample,[(stage,bool(receipt[stage].get('error'))) for stage in receipt if stage.startswith('step')],flush=True)
        return receipt
    rows=await asyncio.gather(*(per(job) for job in jobs))
    write(out/'step-13-15-recovery.json',{'samples':rows,'wall_seconds':round(perf_counter()-began,3),
          'requests':[{'sample_id':r['sample_id'],'step':r['step_slug'],'variant':r['variant'],'profile':r['profile'],
                      'status':r['status'],'seconds':r['ended_at']-r['started_at'],
                      'usage':r['response'].get('usage',{}),'error':r['error']} for r in provider.log]})

if __name__=='__main__':
    parse=argparse.ArgumentParser()
    parse.add_argument('root',type=Path); parse.add_argument('output',type=Path); parse.add_argument('legacy',type=Path)
    args=parse.parse_args()
    asyncio.run(main(args.root,args.output,args.legacy))
