"""Send a browser-submitted resource question through the native Reader writer."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
from time import time

from manualreader.stages.step05.inference import read,write


async def main(root,legacy):
    sys.path.insert(0,str(legacy))
    from reader_bridge import Bridge
    from reader_execute import codex_executable,run_group

    rec=read(root/'browser-qa-receipt.json')
    row=next(x for x in rec['samples'] if x.get('real_question',{}).get('question_id'))
    question=row['real_question']
    tag=question['target'].get('source_segment_id')
    if tag!='resource:'+question['resource_id']:
        raise ValueError('Reader 没有登记本回答的资源编号')
    base=row['url'].split('/?')[0]
    if not base.startswith('http://127.0.0.1:'):
        raise ValueError('仅使用这轮隔离 Reader')
    material=row['url'].split('material=',1)[1]
    b=Bridge(base,material)
    out=root/'native-qa'/row['sample_id']
    out.mkdir(parents=True)
    start=time()
    issues=[]
    try:
        await asyncio.to_thread(b.prepare,out,observe=False,question_ids=[question['question_id']])
        run=read(out/'run.json')
        ref=read(out/'reference.json')
        for group in run['groups']:
            raw=(out/('prompt-'+group['id']+'.txt')).read_text('utf8').split('\n',1)[1]
            await asyncio.to_thread(run_group,b,out,group,json.loads(raw),ref,codex_executable())
    except Exception as e:
        issues.append(str(e))
    state=b.request('/api/progress')
    answers=[q for rd in state['rounds'] for q in rd['questions'] if q['id']==question['question_id']]
    report={'sample_id':row['sample_id'],'question':question,'attempted':1,'started_at':start,
            'ended_at':time(),'answered':bool(answers and answers[0].get('answer')),
            'answer':answers[0] if answers else None,'issues':issues}
    write(root/'qa-whiteboard.json',report)
    print(row['sample_id'],'native resource answer saved:',report['answered'],flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    p.add_argument('legacy',type=Path)
    args=p.parse_args()
    asyncio.run(main(args.root,args.legacy))
