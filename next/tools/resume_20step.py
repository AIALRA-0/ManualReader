"""Resume missing isolated saves and FINALE tasks that have not dispatched a model."""

from argparse import ArgumentParser
import asyncio
from pathlib import Path
import sys
from time import sleep,time

from manualreader.store import Project
from manualreader.stages.step05.inference import read,write
from run_whiteboard import Whiteboard


def main():
    cli=ArgumentParser(description=__doc__)
    cli.add_argument('root',type=Path)
    cli.add_argument('legacy',type=Path)
    cli.add_argument('preview_url')
    opts=cli.parse_args()
    sys.path.insert(0,str(opts.legacy))
    root=opts.root
    final=root/'whiteboard-result.json'
    deadline=time()+7200
    while not final.is_file():
        if time()>=deadline:
            raise TimeoutError('冷跑没有留下完整的结束记录')
        sleep(1)
    manifest=read(root/'whiteboard-inputs.json')
    results=read(final)
    controller=Whiteboard(root,manifest,40,opts.preview_url)
    saved={}
    errors={}
    for sample,record in manifest['samples'].items():
        if record['mode']!='NORMAL':
            continue
        job=root/'jobs'/sample
        if (job/'isolated-save.json').is_file() or not (job/'run.json').is_file():
            continue
        try:
            receipt=controller.isolated_save(sample)
            card=read(root/'inputs'/sample/'task-card.json')
            controller.save_fresh_for_finale(Project(controller.data,record['project_id']),sample,card,None,
                                             job/'body.final.md',receipt)
            controller.event(sample,20,'commit_receipted',{'commit_id':receipt['commit_id'],
                             'progress_ids':receipt['progress']['updated_file_ids']},upstream=(17,))
            results['finished'][sample]=time()
            results['failures'].pop(sample,None)
            saved[sample]=receipt['progress']['updated_file_ids']
            print('正常章节写回已恢复',sample,flush=True)
        except (ValueError,KeyError,OSError,RuntimeError) as exc:
            errors[sample]=str(exc)
    write(final,results)
    planned={rec['project_id'] for sample,rec in manifest['samples'].items() if (root/'jobs'/sample/'isolated-save.json').is_file()}
    only={sample for sample,rec in manifest['samples'].items()
          if rec['mode']=='FINALE' and rec['project_id'] in planned and sample not in results['finished']
          and not (root/'planners'/sample).exists()}
    write(root/'resume-20step.json',{'saved':saved,'errors':errors,'previously_unstarted_finales':sorted(only)})
    if only:
        asyncio.run(controller.run(only))


if __name__=='__main__':
    main()
