"""Read actual STEP-08～12 artifacts and package the isolated acceptance evidence."""

from argparse import ArgumentParser
from collections import Counter, defaultdict
from difflib import unified_diff
from hashlib import sha256
from pathlib import Path
import json
import math
from zipfile import ZIP_DEFLATED, ZipFile

from manualreader.stages.finish.content import source_units


MODES=('NORMAL','INTRO','FINALE','ANSWER')
FILES=('run.json','part-blocks.json','reading-groups.json','body.final.md','anchor-lineage.json',
       'block-source-links.json','resource-block-links.json','resource-explanations.json','translation-groups.json',
       'translation-visuals.json','source-translation-links.json','chapter-guidance.json','term-candidates.json',
       'quiz-candidates.json','alignment.json','offset-map.json','format-patches.json')


def read(path,default=None):
    return json.loads(path.read_bytes()) if path.is_file() else default


def write(path,obj):
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def percentile(values,pct):
    if not values:
        return None
    data=sorted(values)
    return round(data[max(0,math.ceil(pct/100*len(data))-1)],4)


def distribution(values):
    numbers=list(values)
    return {key:percentile(numbers,p) for key,p in [('p50',50),('p90',90),('p95',95),('max',100)]}


def measure(root,runs):
    nodes=[row for p in sorted((root/'metrics').glob('*-nodes.json')) for row in read(p,[])]
    consumers=[row for p in sorted((root/'metrics').glob('*-consumers.json')) for row in read(p,[])]
    timeline={}
    chunks=defaultdict(list)
    for row in nodes:
        chunks[row['node']].append(row['duration_seconds'])
        key=row['sample_id']
        start,end=timeline.get(key,(row['start_at'],row['end_at']))
        timeline[key]=(min(start,row['start_at']),max(end,row['end_at']))
    providers=defaultdict(list)
    records=[]
    for file in sorted((root/'requests').glob('*/*/receipt.json')):
        row=read(file)
        row['directory']=file.parent.relative_to(root).as_posix()
        records.append(row)
        providers['vision' if row.get('uses_image') else row['provider']].append(row)
    by_stage=defaultdict(list)
    for row in records:
        by_stage[row['stage']].append(row)
    stage_runs={stage:{'logical_requests':len(rows),'http_attempts':sum(len(r['attempts']) for r in rows),
                      'complete':sum(r['status']=='complete' for r in rows),
                      'retries':sum(r['retries'] for r in rows)}
                for stage,rows in sorted(by_stage.items())}
    totals={}
    for lane,rows in providers.items():
        attempts=Counter(str(attempt.get('status','network')) for row in rows for attempt in row['attempts'])
        intervals=sorted((a['started_at']+delta,amount)
                         for row in rows for a in row['attempts'] if a.get('seconds') is not None
                         for delta,amount in ((0,1),(a['seconds'],-1)))
        level=peak=0
        for _,amount in intervals:
            level+=amount
            peak=max(peak,level)
        token=Counter()
        for row in rows:
            # Sol can issue an HTTP technical retry before any model output.
            responses=[a.get('response',{}) for a in row.get('attempts',[]) if a.get('response')]
            final=row.get('response') or {}
            for response in responses or [final]:
                usage=response.get('usage') or {}
                token['input']+=usage.get('input_tokens',usage.get('prompt_tokens',0))
                token['output']+=usage.get('output_tokens',usage.get('completion_tokens',0))
                token['cache']+=(usage.get('input_tokens_details') or usage.get('prompt_tokens_details') or {}).get(
                    'cached_tokens',usage.get('prompt_cache_hit_tokens',0))
                token['reasoning']+=(usage.get('output_tokens_details') or {}).get(
                    'reasoning_tokens',(usage.get('completion_tokens_details') or {}).get('reasoning_tokens',0))
        wire_durations=[a['seconds'] for r in rows for a in r['attempts'] if a.get('seconds') is not None]
        totals[lane]={'logical_requests':len(rows),'statuses':dict(Counter(r['status'] for r in rows)),
                      'http':dict(attempts),'retries':sum(r['retries'] for r in rows),
                      'tokens':dict(token),'peak_http':peak,'cost_usd':None,
                      'queue_seconds_accumulated':round(sum(r.get('queue_seconds') or 0 for r in rows),3),
                      'wire_attempt_seconds':distribution(wire_durations),
                      'logical_active_seconds':distribution(sum(a.get('seconds') or 0 for a in r['attempts'])
                                                           for r in rows)}
    run_end=max((r['end_at'] for r in runs),default=None)
    batch_start=min((r[0] for r in timeline.values()),default=None)
    retry=read(root/'ds-technical-retry.json',{}).get('outcomes',[])
    last_wire=max((row['ended_at'] for row in retry),default=run_end)
    elapsed={r['sample_id']:round(r['end_at']-timeline.get(r['sample_id'],(r['start_at'],0))[0],3) for r in runs}
    bottleneck=sorted(({'sample_id':r['sample_id'],'node':r['node'],'duration_seconds':r['duration_seconds']}
                       for r in nodes),key=lambda row:row['duration_seconds'],reverse=True)[:10]
    return {'batch_start_at':batch_start,'batch_end_at':run_end,
            'actual_wall_seconds':round(run_end-batch_start,3) if run_end and batch_start else None,
            'wall_including_technical_retry_seconds':(
                round(max(run_end,last_wire)-batch_start,3) if run_end and batch_start and last_wire else None),
            'per_sample':elapsed,'per_sample_seconds':distribution(elapsed.values()),
            'top_observed_delays':bottleneck,'nodes_by_stage_seconds':{k:distribution(v) for k,v in chunks.items()},
            'consumer_reuse':dict(Counter(r['stage'] for r in consumers if r['reused'])),
            'providers':totals,'model_stages':stage_runs,'attempt_records':records,'node_records':nodes,
            'note':'墙钟测量从可读原文进入 B1 到最后一个派生稿落盘，包含本轮开发期间先启动 B1 留下的间隔。阶段统计没有相加。'}


def compute_coverage(root,runs):
    modes={}
    files=defaultdict(dict)
    translation_presence=defaultdict(set)
    for mode in MODES:
        selected=[row for row in runs if row['mode']==mode]
        nav=Counter()
        for row in selected:
            nav.update({k:v for k,v in row['source_navigation'].items() if isinstance(v,int)})
        axes={}
        for key in ('goal_coverage','question_item_coverage','question_raw_unit_coverage'):
            axes[key]={'total':sum(row['coverage'][key]['total'] for row in selected),
                       'covered':sum(row['coverage'][key]['covered'] for row in selected)}
        modes[mode]={'samples':len(selected),'navigation':dict(nav),'goals_answers':axes,
                     'p2':dict(sum((Counter(r['p2']) for r in selected),Counter())),
                     'translation_scope_refs':dict(sum((Counter(r['translation']) for r in selected),Counter())),
                     'terms':sum(r['terms'] for r in selected),'exercises':sum(r['exercises'] for r in selected)}
    resource_kinds=defaultdict(Counter)
    for row in runs:
        sample=row['sample_id']
        job=root/'jobs'/sample
        structure=read(job/'part-blocks.json',{})
        kinds={r['resource_id']:r['kind'] for r in structure.get('resources',[])}
        details=read(job/'resource-explanations.json',{}) or {}
        for explanation in details.get('coverage_by_resource',[]):
            rid=explanation['resource_id']
            resource_kinds[kinds.get(rid,'unknown')][explanation['status']]+=1
        source=read(root/'inputs'/sample/'source-index.json',{})
        links={r['source_id']:r for r in read(job/'source-translation-links.json',[])}
        rank={'complete':3,'uncertain':2,'unresolved':1}
        for unit in source_units(source):
            sid=unit['source_id']
            fid=unit['file_id']
            key=(fid,sid,unit['source_version'],unit['original_sha256'])
            link=links.get(sid)
            if not link:
                relation=unit['relation']
                link={'relation':relation,'status':'unresolved' if relation=='translated' else 'complete'}
            previous=files[fid].get(key,{})
            if (link['relation']=='preserved_math' or not previous or
                    rank.get(link['status'],0)>rank.get(previous['status'],0)):
                files[fid][key]=link
            if link['relation']=='translated' and link['status']=='complete':
                translation_presence[key].add(sample)
    foreign=Counter()
    unique=Counter()
    for versions in files.values():
        for link in versions.values():
            relation=link['relation']
            if relation=='translated':
                foreign[link.get('status','unresolved')]+=1
            else:
                unique[relation]+=1
    return {'scope':'ManualReader-Next STEP-08～12 隔离结果',
            'modes':modes,'p2_by_kind':{k:dict(v) for k,v in sorted(resource_kinds.items())},
            'unique_source_relations':dict(unique),
            'unique_foreign':{'needed':sum(foreign.values()),**foreign},
            'cross_mode_or_task_reused_translation_refs':sum(len(p)-1 for p in translation_presence.values()),
            'by_source_file':{fid:dict(Counter(x['relation']+':'+x.get('status','') for x in units.values()))
                              for fid,units in files.items()}}


def code_patch(workspace,baseline,destination):
    paths=[]
    fragments=[]
    for rel in workspace.rglob('*'):
        if not rel.is_file():
            continue
        name=rel.relative_to(workspace)
        if name.parts[0] not in ('src','tests','docs','tools','pyproject.toml','requirements.lock','tests.lock','README.md'):
            continue
        if any(p in ('__pycache__','build','.git') or p.endswith('.egg-info') for p in name.parts):
            continue
        prior=baseline/name
        if prior.is_file() and prior.read_bytes()==rel.read_bytes():
            continue
        try:
            old=prior.read_text('utf8').splitlines(keepends=True) if prior.is_file() else []
            new=rel.read_text('utf8').splitlines(keepends=True)
        except UnicodeError:
            continue
        label=name.as_posix()
        paths.append(name)
        fragments.extend(unified_diff(old,new,fromfile='a/'+label if prior.is_file() else '/dev/null',
                                     tofile='b/'+label))
    destination.write_text(''.join(fragments),encoding='utf8')
    return paths


def collect(root,baseline):
    delivery=root/'delivery'
    delivery.mkdir(exist_ok=True)
    runs=[read(p) for p in sorted((root/'jobs').glob('*/run.json'))]
    errors=read(root/'finish-result.json',{}).get('failures',{})
    coverage=compute_coverage(root,runs)
    metrics=measure(root,runs)
    write(delivery/'coverage.json',coverage)
    write(delivery/'stage-metrics.json',metrics)
    code=code_patch(root/'workspace',baseline/'workspace',delivery/'implementation.diff')
    service=read(root/'reader-service-ready.json',{})
    browser=read(root/'browser-summary.json',{})
    browser_checks=read(root/'browser-receipts.json',[])
    download_receipts=read(root/'browser-downloads.json',{})
    downloads={key:download_receipts[key] for key in ('total','exact_downloads','wall_seconds') if key in download_receipts}
    save_protection=read(root/'save-version-and-protection.json',{})
    tx=read(root/'commit-isolated-receipts.json',{}) or read(root/'reader-commit-receipts.json',{}) or {}
    qa=read(root/'qa-receipts.json',{})
    if not tx and browser_checks:
        tx={row['sample_id']:row['save_simulation'] for row in browser_checks if row.get('save_simulation')}
        write(delivery/'commit-isolated-receipts.json',tx)
    qa_attempts=[entry for path in sorted((root/'qa').glob('**/transport.json'))
                 for entry in read(path,[]) if entry.get('status') is not None]
    qa_usages=Counter()
    for attempt in qa_attempts:
        usage=(attempt.get('response') or {}).get('usage') or {}
        for field in ('input_tokens','output_tokens'):
            qa_usages[field]+=usage.get(field,0)
        qa_usages['cache']+=(usage.get('input_tokens_details') or {}).get('cached_tokens',0)
        qa_usages['reasoning']+=(usage.get('output_tokens_details') or {}).get('reasoning_tokens',0)
    metrics['native_resource_questions']={
        'http_requests':len(qa_attempts),'http':dict(Counter(str(e['status']) for e in qa_attempts)),
        'tokens':dict(qa_usages),'cost_usd':None}
    write(delivery/'stage-metrics.json',metrics)
    p2=sum((Counter(x['p2']) for x in runs),Counter())
    qa_by_project=[read(p) for p in sorted((root/'qa').glob('*/qa-receipt.json'))]
    qa_saved=sum(bool(row.get('answer')) for q in qa_by_project for row in q.get('answers',[]))
    unresolved=Counter()
    nav=Counter()
    for row in runs:
        nav.update({k:v for k,v in row['source_navigation'].items() if isinstance(v,int)})
        unresolved.update({k:len(v) for k,v in row.get('issues',{}).items()})
    lines=['# ManualReader-Next 全链后处理实测','',
           f'已生成 {len(runs)} 篇最终数据。正文以 STEP-07 V2 的冻结稿为输入，原项目与学习记录在本轮无写入。',
           f'按任务记录，来源导航为 {nav["linked"]}/{nav["total"]}，其中 {nav["direct_semantic_count"]} 条来源有直接讲解关系，'
           f'{nav["navigation_only_count"]} 条来源的关系仅用于导航。',
           f'P2 完整解释 {p2["explained"] + p2["covered_by_group"]} 个，部分完成 {p2["partial"]} 个，'
           f'需要核对 {p2["needs_review"]} 个，待返回 {p2["pending"]} 个。',
           f'生成 {sum(r["terms"] for r in runs)} 个术语候选，{sum(r["exercises"] for r in runs)} 道练习，'
           f'{sum(r["reading_route_items"] for r in runs)} 个阅读路线节点和 {sum(r["recaps"] for r in runs)} 组 Part 回顾。',
           '各项内容的准确性仍可从 Reader 成品和对应原件核对。','',
           '## 每篇在哪里打开','',
           '| 模式 | 篇章 | 新后处理秒 | 原文导航 | P2 有解释 | 译文缺项与不确定 |',
           '|---|---|---:|---:|---:|---:|']
    urls=service.get('samples',{})
    for r in runs:
        sid=r['sample_id']
        link=urls.get(sid)
        label=r['title'].replace('|','／')
        label=f'[{label}]({link})' if link else label
        if r['fixture_based']:
            label+='（测试构造）'
        p2_ok=sum(r['p2'].get(k,0) for k in ('explained','covered_by_group'))
        lines.append(f'| {r["mode"]} | {label} | {metrics["per_sample"].get(sid,0):.2f} | '
                     f'{r["source_navigation"]["linked"]}/{r["source_navigation"]["total"]} | '
                     f'{p2_ok}/{sum(r["p2"].values())} | '
                     f'{r["translation"].get("unresolved",0)} / {r["translation"].get("uncertain",0)} |')
    lines.extend(['','## 这轮服务实际处理了什么','',
                  f'首次产出 40 篇的墙钟为 {metrics["actual_wall_seconds"]} 秒。{metrics["note"]}',
                  f'算入随后一次技术补试，原文翻译启动至最后一个补试回执为 '
                  f'{metrics["wall_including_technical_retry_seconds"]} 秒。',
                  f'单篇 p50/p90/p95/max 秒数为 {metrics["per_sample_seconds"]}。',
                  f'外文唯一来源统计为 {coverage["unique_foreign"]}。跨任务复用 '
                  f'{coverage["cross_mode_or_task_reused_translation_refs"]} 个已译来源引用。'])
    for lane,val in metrics['providers'].items():
        t=val['tokens']
        lines.append(f'- **{lane}** 有 {val["logical_requests"]} 个逻辑请求，HTTP 回执 {val["http"]}，'
                     f'峰值 {val["peak_http"]} 路。输入 {t.get("input",0):,}，输出 {t.get("output",0):,}，'
                     f'推理 {t.get("reasoning",0):,}，缓存 {t.get("cache",0):,}。费用无金额回执。')
    lines.extend(['','## 哪些需要查看真实回执','',
                  f'浏览器回执为 {browser.get("opened",0)}/{browser.get("total",len(runs))} 篇通过该脚本的打开与交互检查。'
                  f'正式问答界面提交的资源问题中，有 {qa_saved} 个真实回答保存成功。',
                  f'原文双向跳转通过 {browser.get("navigation_reverse",0)}/{browser.get("navigation_applicable",0)} 篇。'
                  f'首轮 40 页签出现资源压力，复验以 {browser.get("8_tab_wall_seconds",0)} 秒为主，另加 '
                  f'{browser.get("recovery_wall_seconds",0)} 秒复查。',
                  f'资源问答的额外 HTTP 请求共 {len(qa_attempts)} 次，单独记入用量。',
                  '跟问和 BP 使用 Reader 原有控件，浏览器保存回执在 qa-followup-and-bp.json。',
                  (f'浏览器下载 {downloads["exact_downloads"]}/{downloads["total"]} 篇，每篇的下载字节与正文一致。'
                   f'浏览器下载实际耗时 {downloads["wall_seconds"]:.3f} 秒。') if downloads else '当前已有浏览器打开和定位回执。',
                  f'隔离保存有 {len(tx)} 篇操作回执。版本过期拒绝 {save_protection.get("stale_version_rejections",0)} 篇，'
                  f'重复提交复用已保存回执 {save_protection.get("idempotent",0)} 篇，'
                  f'作答和学习记录保持一致 {save_protection.get("unchanged_user_records",0)} 篇。'
                  f'用 {save_protection.get("progress_updated_samples",0)} 个隔离样本验证了覆盖进度更新。'
                  '正式项目保存仍需用户确认。',
                  '原始模型输入、输出和 HTTP 回执保存在本轮 requests 目录。此 ZIP 收录汇总、关键样例、截图和代码差异。'])
    (delivery/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    issues=['# 哪些内容还需要核对','']
    if isinstance(qa,dict):
        for project in qa.get('native',[]):
            for answer in project.get('answers',[]):
                if not answer.get('answer'):
                    issues.append(f'- 资源问答 {answer["kind"]}（{answer["question_id"]}）已经提交，尚未保存答案。'
                                  '实际失败原因保存在 qa-receipts.json。')
    if errors:
        issues.extend(f'- {k} {v}' for k,v in errors.items())
    for r in runs:
        counts=r['translation']
        p2_total=sum(n for name,n in r['p2'].items() if name not in ('explained','covered_by_group'))
        if not (counts.get('unresolved') or counts.get('uncertain') or p2_total or any(r['issues'].values())):
            continue
        issues.append(f'- {r["sample_id"]} 有 {counts.get("unresolved",0)} 条外文译文缺项，'
                      f'{counts.get("uncertain",0)} 条待核，{p2_total} 个资源部分完成或待解释。详见该篇结构和运行回执。')
    (delivery/'issues-for-user.md').write_text('\n'.join(issues)+'\n',encoding='utf8')
    write(delivery/'delivery-index.json',{'artifacts_by_sample':{r['sample_id']:list(FILES) for r in runs},
                                          'urls':urls,'browser':browser,'browser_checks':browser_checks,'downloads':downloads,
                                          'commit_isolated':tx})
    copy_spec=root/'spec/prompt-manifest.json'
    (delivery/'prompt-manifest.json').write_bytes(copy_spec.read_bytes())
    dest=root/'ManualReader-Next-STEP08-12-全链隔离验收包.zip'
    with ZipFile(dest,'w',ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(delivery.iterdir()):
            if p.is_file():
                z.write(p,p.name)
        for name in code:
            z.write(root/'workspace'/name,'code/'+name.as_posix())
        for name in ('reader-projections.json','reader-service-ready.json','browser-receipt.json',
                     'browser-summary.json','browser-receipts.json','browser-summary-first-40-tabs.json',
                     'commit-isolated-receipts.json','reader-commit-receipts.json','structural-verification.json',
                     'qa-receipts.json','qa-browser-resource-ids.json','qa-followup-and-bp.json',
                     'browser-downloads.json','save-version-and-protection.json','ds-technical-retry.json',
                     'deterministic-b1-reprojection.json','finish-result.json','tests.log','legacy-tests.log','model-schemas.json',
                     'special-tests.log','browser.log','commit-test.log','cold-install.log','structure-final.log'):
            if (root/name).is_file():
                z.write(root/name,'evidence/'+name)
        selected=['04-olympus-intro','06-paper-normal','07-exam2-normal',
                  next((r['sample_id'] for r in runs if r['mode']=='ANSWER'),'')]
        for sid in set(selected):
            if not sid:
                continue
            for name in FILES:
                path=root/'jobs'/sid/name
                if path.is_file():
                    z.write(path,f'samples/{sid}/{name}')
        screenshots=sorted(p for d in (root/'browser',root/'browser-screenshots',root/'screenshots')
                           for p in d.glob('*.png'))
        follow=root/'qa-followup-and-bp.png'
        if follow.is_file():
            z.write(follow,'browser/'+follow.name)
        for mode in MODES:
            first=next((p for p in screenshots if any(r['mode']==mode and r['sample_id'] in p.name for r in runs)),None)
            if first:
                z.write(first,'browser/'+first.name)
        for sid in ('04-olympus-intro','06-paper-normal'):
            shot=root/'screenshots'/f'{sid}.png'
            if shot.is_file():
                z.write(shot,'browser/'+shot.name)
    with ZipFile(dest) as archive:
        if archive.testzip() is not None:
            raise ValueError('验收 ZIP 校验失败')
    print(dest,dest.stat().st_size)


if __name__=='__main__':
    p=ArgumentParser()
    p.add_argument('root',type=Path)
    p.add_argument('baseline',type=Path)
    a=p.parse_args()
    collect(a.root,a.baseline)
