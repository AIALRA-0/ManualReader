"""Answer representative, explicitly submitted questions in the isolated Reader only."""
import argparse
import asyncio
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from reader_bridge import Bridge
from reader_execute import codex_executable,run_group
from reader_postprocess_core import atomic,read
from reader_postprocess_transport import offload,timestamp


def prepare(out,base,material,qids):
    out.mkdir(parents=True,exist_ok=False)
    bridge=Bridge(base,material)
    state=bridge.request('/api/progress')
    known={q['id']:q for r in state['rounds'] for q in r['questions']}
    if any('资源：res-' not in (known.get(qid,{}).get('target',{}).get('text') or '') for qid in qids):
        raise ValueError('当前问题没有真实资源上下文')
    bridge.prepare(out,observe=False,question_ids=qids)
    return bridge,read(out/'run.json'),read(out/'reference.json')


async def answer(out,base,material,pairs):
    started=timestamp(); errors=[]
    try:
        bridge,run,reference=await offload(prepare,out,base,material,list(pairs.values()))
        def run_one(group):
            import json
            payload=json.loads((out/('prompt-'+group['id']+'.txt')).read_text('utf8').split('\n',1)[1])
            return run_group(bridge,out,group,payload,reference,codex_executable())
        results=await asyncio.gather(*(offload(run_one,g) for g in run['groups']),return_exceptions=True)
        errors=[str(x) for x in results if isinstance(x,Exception)]
        stored=bridge.request('/api/progress')
        by_id={q['id']:q for r in stored['rounds'] for q in r['questions']}
        rows=[{'kind':k,'question_id':qid,'answer':by_id.get(qid,{}).get('answer'),
               'question_target':by_id.get(qid,{}).get('target',{}).get('text')} for k,qid in pairs.items()]
        execution=bridge.request('/api/runs/'+bridge.run_id)
        result={'material_id':material,'started_at':started,'ended_at':timestamp(),'answers':rows,
                'issues':errors,'usage':execution.get('usage'),'state':execution.get('state')}
    except Exception as e:
        result={'material_id':material,'started_at':started,'ended_at':timestamp(),'error':str(e)}
    atomic(out/'qa-receipt.json',result)
    return result


async def main(args):
    projects={j['sample_id']:j for j in read(args.run/'reader-projects.json') if j.get('reader_url')}
    available=[(w['sample_id'],k,v) for w in read(args.run/'browser-receipts.json')
         for k,v in w.get('resource_kinds',{}).items() if v.get('native_question_saved')]
    chosen={}
    for s,kind,item in available:
        chosen.setdefault(kind,(s,item['question_ids'][0]))
    by_project=defaultdict(dict)
    for k,(s,qid) in chosen.items():
        by_project[s][k]=qid
    if any(urlsplit(projects[s]['reader_url']).hostname!='127.0.0.1' for s in by_project):
        raise ValueError('这项问答验证只能跑在本机隔离 Reader')
    results=await asyncio.gather(*(answer(args.run/'qa'/s,projects[s]['reader_url'].split('/?')[0],
        projects[s]['material_id'],qids) for s,qids in by_project.items()))
    atomic(args.run/'qa-receipts.json',results)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True)
    asyncio.run(main(p.parse_args()))
