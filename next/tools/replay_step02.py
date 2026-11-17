"""Replay archived initial drafts through the current client without dispatching a model."""

from argparse import ArgumentParser
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from time import perf_counter
import json
import sys

from live_acceptance import client


def read(path):
    return json.loads(path.read_bytes())


def replay(path, output, cli, token, cache, memory):
    from manualreader.client import API
    from manualreader.planning import plan, save

    def no_model(*_):
        raise RuntimeError('回放找不到对应的首轮缓存。')

    archive = read(path/'run.json')
    previous = read(path/'task-card.json')
    before = path/'first-semantic-result.json'
    task = output/path.name
    api = API(cli,token,archive['project_id'])
    result = plan(api,task,archive['mode'],question_id=archive['question_id'],
                  explicit_finale=archive['explicit_finale'],cache=cache,
                  original_cache=memory,backend=no_model)
    if result['status'] != 'planned':
        raise RuntimeError(result.get('error',result['status']))
    if not result['cache_hit'] or result['model_dispatches']:
        raise RuntimeError('回放运行不应请求模型。')
    if before.read_bytes() != (task/before.name).read_bytes():
        raise ValueError('首轮模型结果与归档不同。')
    actual = read(task/'task-card.json')
    if not (task/'task-card.md').is_file():
        raise ValueError('缺少任务卡 Markdown。')
    if set(actual) != set(previous) or any(
            actual[k] != previous[k] for k in previous if k not in ('goals','material_plan')):
        raise ValueError('任务卡固定字段发生变化。')
    if len(previous['material_plan']) != len(actual['material_plan']):
        raise ValueError('原件列表发生变化。')
    ids, added, edits = 0, 0, []
    for old,new in zip(previous['material_plan'],actual['material_plan']):
        if {k:v for k,v in old.items() if k not in ('source_ranges','source_ids')} != {
                k:v for k,v in new.items() if k not in ('source_ranges','source_ids')}:
            raise ValueError('材料固定字段发生变化。')
        old_ids, new_ids = set(old['source_ids']), set(new['source_ids'])
        if not old_ids <= new_ids:
            raise ValueError('出卡后缺少此前已核对的来源 ID。')
        if old['source_ranges'] == new['source_ranges'] and old['source_ids'] != new['source_ids']:
            raise ValueError('未修改范围时，不应改动既有来源。')
        ids += len(old_ids)
        added += len(new_ids-old_ids)
        if old['source_ranges'] != new['source_ranges']:
            edits.append({'file_id':old['file_id'],'before':old['source_ranges'],'after':new['source_ranges']})
    diff = {'local_repairs':result['local_repairs'],'old_source_ids_kept':ids,'source_ids_added':added,
            'range_changes':edits,'goals_before':previous['goals'],'goals_after':actual['goals'],
            'first_draft_sha256':sha256(before.read_bytes()).hexdigest(),'fixed_fields_equal':True}
    save(task/'comparison.json',diff)
    return {'status':'planned','mode':result['mode'],'wall_seconds':result['wall_seconds'],
            'model_dispatches':result['model_dispatches'],'cache_hit':result['cache_hit'],**diff}


def main():
    p = ArgumentParser()
    for name in ('runs','cache','output','token-file'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--base',required=True)
    args = p.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        p.error('请指定新的回放目录，以便保留已有任务卡。')
    output.mkdir(parents=True,exist_ok=True)
    sdk = client(args.base,output)
    sys.path.insert(0,str(sdk))
    from manualreader import planning
    assert Path(planning.__file__).is_relative_to(sdk)
    print('Loaded STEP-02 client from',sdk,flush=True)
    token = args.token_file.read_text('utf8')
    jobs = sorted(path for path in args.runs.iterdir() if (path/'task-card.json').is_file())
    if not jobs:
        p.error('没有找到已归档的首轮任务卡。')
    memory = {}
    def run(path):
        try:
            result = replay(path,output/'cards',args.base,token,args.cache,memory)
        except (OSError,ValueError,RuntimeError,KeyError) as error:
            result = {'status':'failed','error':str(error)}
        print(path.name,result['status'],result.get('error',''),flush=True)
        return path.name,result
    began = perf_counter()
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        results = dict(pool.map(run,jobs))
    totals = Counter(item['mode'] for item in results.values() if item['status']=='planned')
    plan_successes = sum(totals.values())
    planning.save(output/'summary.json',{'jobs':len(jobs),'planned':plan_successes,'modes':dict(totals),
                                         'wall_seconds':perf_counter()-began,'results':results})
    return 0 if plan_successes == len(jobs) else 2


if __name__ == '__main__':
    raise SystemExit(main())
