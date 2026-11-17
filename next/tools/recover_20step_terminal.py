"""Continue only a saved-scope boundary or a Windows artifact write failure."""

import argparse
import asyncio
from pathlib import Path
import sys
from time import time

from manualreader.stages.finish.pipeline import finish_batch
from manualreader.stages.finish.runner import Timings, persist
from manualreader.artifacts import read_json as read, write_json as write
from run_whiteboard import Whiteboard


class NoNewModel:
    log=[]
    consumer_log=[]
    lanes={}
    started=time()

    def scope(self, sample):
        async def missing(*args,**kwargs):
            raise AssertionError(f'{sample}：这个装配回放不允许调用新模型')
        return missing


async def main(root, url):
    manifest=read(root/'whiteboard-inputs.json')
    terminal_path=root/'whiteboard-result.json'
    initial=read(terminal_path)
    todo={}
    for sample,error in initial['failures'].items():
        if ('source-translation-links.json' in error and 'WinError 5' in error
            and (root/'upstream07'/sample/'run.json').is_file()):
            todo[sample]='assemble'
        elif error=='FINALE 选取的原件范围超出能核对的已保存讲解' and not (root/'writer-runs'/sample).exists():
            todo[sample]='scope'
    if not todo:
        return
    before={sample:initial['failures'][sample] for sample in todo}
    async def assets():
        picks={name:read(root/'inputs'/name/'source-index.json') for name,op in todo.items() if op=='assemble'}
        if not picks:
            return {}
        provider=NoNewModel()
        timing=Timings()
        result=await finish_batch(root,provider,timing,picks,write_result=False)
        for name in picks.keys()-result['failures'].keys():
            write(root/'jobs'/name/'preview-revision.json',{'kind':'replayed-disk-write','updated_at':time()})
        persist(root,'whiteboard-windows-artifact-recovery',provider,timing)
        return {name:result['failures'].get(name) for name in picks}

    async def upstream():
        picks={n for n,op in todo.items() if op=='scope'}
        if picks:
            controller=Whiteboard(root,manifest,40,url)
            await controller.run(picks)

    recovered,_=await asyncio.gather(assets(),upstream())
    current=read(terminal_path)
    for name,error in recovered.items():
        if error:
            current['failures'][name]=error
        else:
            current['finished'][name]=time()
            current['failures'].pop(name,None)
    current['ended_at']=time()
    write(terminal_path,current)
    write(root/'finish-result.json',{'samples':sorted(current['finished']),'ready':len(current['finished']),
          'failures':current['failures'],'ended_at':current['ended_at']})
    write(root/'terminal-recovery-20step.json',{'before':before,'after':current['failures'],'operations':todo})
    print('terminal recovered',len(current['finished']),current['failures'],flush=True)


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('root',type=Path)
    cli.add_argument('legacy',type=Path)
    cli.add_argument('preview_url')
    ns=cli.parse_args()
    sys.path.insert(0,str(ns.legacy))
    asyncio.run(main(ns.root,ns.preview_url))
