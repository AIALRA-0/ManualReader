"""Downloadable client for STEP-03 inspection and parallel STEP-02 preparation."""

from argparse import ArgumentParser
import json
from pathlib import Path
from threading import Thread

from .client import API


class ParallelPlanAPI(API):
    def __init__(self,base,token,project,file_ids):
        super().__init__(base,token,project)
        self.file_ids=set(file_ids)
        self.round_id=None
        self.warm_results=[]

    def call(self,step,action,**fields):
        value=super().call(step,action,**fields)
        if (step,action)==('step01','prepare'):
            self.round_id=value['round_id']
            request=super().call
            selected=[f['file_id'] for f in value['manifest']['files']
                      if f['status']=='active' and f['file_id'] in self.file_ids]
            def warm():
                for fid in selected:
                    try:
                        request('step03','warm',round_id=value['round_id'],file_id=fid)
                    except RuntimeError:
                        # Warming is optional. The actual file is read when the task needs it.
                        continue
                    self.warm_results.append(fid)
            if selected:
                Thread(target=warm,name='step03-prewarm',daemon=True).start()
        return value


def parallel_plan(base,token,project,out,mode,file_ids=(),**options):
    from .planning import plan
    api=ParallelPlanAPI(base,token,project,file_ids)
    result=plan(api,out,mode,**options)
    if result['status']=='planned':
        card=json.loads((Path(out)/'task-card.json').read_bytes())
        try:
            binding=api.call('step03','bind',round_id=api.round_id,task_card=card)
        except (OSError,ValueError,RuntimeError) as error:
            binding={'status':'unavailable','reason':str(error)}
        result={**result,'step03':binding}
    return result


def main():
    parser=ArgumentParser()
    parser.add_argument('--base',required=True)
    parser.add_argument('--token-file',required=True,type=Path)
    parser.add_argument('--project',required=True)
    parser.add_argument('--round')
    parser.add_argument('--file')
    parser.add_argument('--page',type=int,action='append')
    parser.add_argument('--preview',type=Path)
    parser.add_argument('--plan',choices=('NORMAL','INTRO','FINALE','ANSWER'))
    parser.add_argument('--out',type=Path)
    parser.add_argument('--cache',type=Path)
    parser.add_argument('--warm-file',action='append',default=[])
    parser.add_argument('--question')
    parser.add_argument('--confirm-finale',action='store_true')
    opts=parser.parse_args()
    token=opts.token_file.read_text('utf8')
    if opts.plan:
        if opts.out is None or bool(opts.question)!=(opts.plan=='ANSWER'):
            parser.error('并行规划需要输出目录，ANSWER 需要真实问题编号')
        result=parallel_plan(opts.base,token,opts.project,opts.out,opts.plan,
                             file_ids=opts.warm_file,question_id=opts.question,
                             explicit_finale=opts.confirm_finale,cache=opts.cache)
        if result.get('step03') is not None:
            (opts.out/'step03-binding.json').write_text(
                json.dumps(result['step03'],ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result['status']=='planned' else 2
    if not opts.round or not opts.file:
        parser.error('读取原件需要轮次和文件编号')
    instance=API(opts.base,token,opts.project)
    if opts.preview:
        result=instance.call('step03','preview',round_id=opts.round,file_id=opts.file,pages=opts.page)
        opts.preview.write_text(result.pop('html'),encoding='utf8')
    else:
        result=instance.call('step03','inspect',round_id=opts.round,file_id=opts.file,pages=opts.page)
    print(json.dumps(result,ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
