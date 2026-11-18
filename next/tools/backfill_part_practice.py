"""对导出的真实章节只补末尾缺题，模型回执保存在指定的私有目录。"""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
from time import perf_counter

from reader_part_practice import coverage, relocate_existing
from manualreader.artifacts import write_json as write
from manualreader.steps.step_14_practice import complete_parts, practice
from manualreader.stages.finish.provider import Provider


def read(path):
    return json.loads(path.read_bytes())


async def run(inventory, output, regression=None):
    output.mkdir(parents=True,exist_ok=True)
    began=perf_counter()
    jobs=[]
    baseline=[]
    for folder in sorted(inventory.iterdir()):
        material=read(folder/'material.json')
        state=read(folder/'progress.json')
        report=coverage(material,state)
        baseline.append({'material_id':folder.name,**report})
        moves=relocate_existing(material,state)
        write(output/'placements'/f'{folder.name}.json',moves)
        report=coverage(material,state)
        by_id={q['id']:q for q in state.get('quizzes',[])}
        for chapter in report['chapters']:
            if chapter['covered']==chapter['total']:
                continue
            existing=[{**by_id[qid],'part_id':part['part_id']}
                      for part in chapter['parts'] for qid in part['quiz_ids']]
            jobs.append((folder.name,chapter,existing))
    provider=Provider(output,sol=max(40,len(jobs)+4))
    write(output/'coverage-before.json',baseline)

    async def one(pid,chapter,existing):
        sample=pid+'__'+chapter['explanation_id']
        start=perf_counter()
        result=await complete_parts(chapter['parts'],provider.scope(sample),mode=chapter['mode'],
                                    title=chapter['title'],existing=existing)
        old_ids={q['id'] for q in existing}
        additions=[q for q in result['questions'] if q['id'] not in old_ids]
        value={'material_id':pid,'explanation_id':chapter['explanation_id'],
               'title':chapter['title'],'mode':chapter['mode'],
               'part_snapshot_sha256':hashlib.sha256(json.dumps(chapter['parts'],sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
               'before':{'covered':chapter['covered'],'total':chapter['total']},
               'after':result['coverage'],'additions':additions,'issues':result['issues'],
               'unresolved_part_ids':result['unresolved_part_ids'],'seconds':perf_counter()-start}
        write(output/'chapters'/f'{sample}.json',value)
        print(json.dumps({'chapter':sample,'before':chapter['covered'],'after':result['coverage'],
                          'new_questions':len(additions)},ensure_ascii=False),flush=True)
        return value

    async def modes():
        if regression is None:
            return []
        selected={}
        for job in sorted((regression/'jobs').glob('*/part-blocks.json')):
            structure=read(job)
            selected.setdefault(structure['mode'],job.parent)
        async def check(mode,job):
            structure=read(job/'part-blocks.json')
            card=read(regression/'inputs'/job.name/'task-card.json')
            prior=read(job/'quiz-candidates.json')
            # 隔离用例故意留一个缺项，验证四种模式都走相同补齐路径。
            pinned=prior[1:]
            result=await practice(regression,job.name,structure,card,
                                  provider.scope('four-mode-'+job.name),pinned=pinned)
            write(output/'four-modes'/f'{mode}.json',result)
            return {'mode':mode,'coverage':result['coverage'],'unresolved_part_ids':result['unresolved_part_ids']}
        return await asyncio.gather(*(check(mode,job) for mode,job in selected.items()))

    outcomes,matrix=await asyncio.gather(asyncio.gather(*(one(*job) for job in jobs)),modes())
    write(output/'plan.json',outcomes)
    write(output/'summary.json',{'chapters':len(jobs),'additions':sum(len(r['additions']) for r in outcomes),
          'unresolved':sum(len(r['unresolved_part_ids']) for r in outcomes),'four_modes':matrix,
          'wall_seconds':perf_counter()-began,'actual_model_calls':len(provider.log),
          'requests':provider.log,'consumers':provider.consumer_log,
          'new_question_types':dict(Counter(q['type'] for row in outcomes for q in row['additions']))})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--four-mode-regression-root',type=Path)
    args=parser.parse_args()
    asyncio.run(run(args.inventory,args.output,args.four_mode_regression_root))
