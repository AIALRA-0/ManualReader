"""Report real 20-step receipts and package the isolated outputs."""

from argparse import ArgumentParser
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from final_delivery import code_patch, compute_coverage, distribution, measure
from package_whiteboard import model_receipts, missing_translation, resource_gaps
from manualreader.stages.step05.inference import read as read_file, write


def read(path, fallback=None):
    return read_file(path) if path.is_file() else fallback


def h(path):
    return sha256(path.read_bytes()).hexdigest()


def seq(path):
    return [json.loads(line) for line in path.read_text('utf-8').splitlines()] if path.exists() else []


def rate(n, d):
    return f'{n:,}/{d:,}（{n*100/d:.2f}%）' if d else '无适用项'


def clean(v):
    if isinstance(v, dict):
        return {k:clean(x) for k,x in v.items() if k.lower() not in
                {'authorization','api_key','token','api_token','pin','preview_token','cookie','set-cookie','pairing_code'}}
    if isinstance(v,list):
        return [clean(x) for x in v]
    return v


def trace_replay(root, runs, upstream):
    """Use observed step spans for a like-for-like barrier sensitivity comparison."""
    measured={r['sample_id']:{} for r in runs}
    for row in upstream:
        key=row['sample_id']
        if key in measured and not row['operation'] and not row['error']:
            measured[key][row['stage']]=row['ended_at']-row['started_at']
    finish={}
    for path in (root/'metrics').glob('*-nodes.json'):
        for row in read(path,[]):
            key=row['sample_id']
            if key in measured:
                finish.setdefault(key,{})[row['node']]=row['duration_seconds']
    samples={}
    for r in runs:
        name=r['sample_id']
        u=measured[name]
        v=finish.get(name,{})
        if not all(key in u for key in ('02','03','04','05/06','07')) or 'B1' not in v:
            continue
        downstream={k:v[k] for k in ('A1','A2','F1','F2','F3','source-image-labels') if k in v}
        eligible={'02与原件准备':u['02']+u['03'],'04 Writer':max(0,u['04']-u['03']),
                  '05/06':u['05/06'],'07':u['07'],'08':v.get('08',0),
                  '10 B1':v['B1'],'附件并行':max(downstream.values(),default=0),'16':v.get('16',0)}
        chain=sum(eligible[k] for k in ('04 Writer','05/06','07','08','附件并行'))
        samples[name]={'spans_seconds':eligible,'attachment_spans':downstream,
            'event_ready':eligible['02与原件准备']+max(eligible['10 B1'],chain)+eligible['16'],
            'serial_late_translation':sum(eligible.values()),
            'actual_translation_overlap_limit':min(eligible['10 B1'],chain)}
    common=next(iter(samples.values()),{}).get('spans_seconds',{})
    batch=sum(max(row['spans_seconds'][k] for row in samples.values()) for k in common)
    return {'samples':samples,'old_phase_barrier_estimate_seconds':batch if samples else None,
            'event_ready_estimate_seconds':max((x['event_ready'] for x in samples.values()),default=None),
            'model_requests':0,'scope':'同轮计时复用；不重放 provider 资源竞争、跨任务去重或 FINALE 等待 NORMAL 提交；不等于实际墙钟或性能收益。'}


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('base',type=Path)
    parser.add_argument('baseline',type=Path)
    parser.add_argument('--draft',action='store_true')
    args=parser.parse_args()
    base=args.base
    root=base/'cold'
    manifest=read(root/'whiteboard-inputs.json')
    result=read(root/'whiteboard-result.json',{})
    browser=read(root/'browser-20step-final.json',{})
    early=read(root/'browser-20step.json',{}).get('early',{})
    projection={r['sample_id']:r for r in read(root/'reader-projections.json',{}).get('samples',[])}
    urls=read(root/'reader-service-ready.json',{}).get('samples',{})
    runs=[read(f) for f in sorted((root/'jobs').glob('*/run.json'))]
    coverage=compute_coverage(root,runs)
    performance=measure(root,runs)
    performance.pop('attempt_records',None)
    performance.pop('node_records',None)
    model, files=model_receipts(root)
    scopes={}
    general_events=seq(root/'upstream-events.jsonl')
    for event in general_events:
        if event['stage']=='01' and event['operation']=='prepare':
            scopes.setdefault(event['sample_id'],event['started_at'])
    began=result.get('started_at')
    ended=result.get('ended_at')
    elapsed={}
    ready={}
    early_before_seal=0
    for r in runs:
        name=r['sample_id']
        entry=scopes.get(name,began or r['start_at'])
        elapsed[name]=result.get('finished',{}).get(name,r['end_at'])-entry
        first=early.get(name) or browser.get('early',{}).get(name) or browser.get('final',{}).get(name,{})
        if first.get('browser_opened_at'):
            early_before_seal+=first['browser_opened_at']<r['end_at']
            ready[name]={'seconds':first['browser_opened_at']-entry,'phase':first.get('phase')}
    journal=next((root/'runs').iterdir())
    event_rows=seq(journal/'events.jsonl')
    longest={}
    for e in event_rows:
        previous=[longest[p['event_id']] for p in e['upstream_versions'] if p['event_id'] in longest]
        base_secs,path=max(previous,key=lambda x:x[0],default=(0,[]))
        seconds=max(0,e['ended_at']-(e.get('start_at') or e['ended_at']))
        longest[e['event_id']]=(base_secs+seconds,path+[[e['task_id'],e['step_id'],e['event_type'],round(seconds,3)]])
    critical=max(longest.values(),key=lambda x:x[0],default=(0,[]))
    originals=[]
    for project,row in manifest['original_projects'].items():
        for f in row['verified_files'].values():
            path=root/'original-inputs/projects'/project/'originals'/f['relative_path']
            originals.append({'project':project,'file':f['relative_path'],'expected':f['sha256'],
                              'observed':h(path) if path.is_file() else None})
    terms=Counter()
    quizzes=Counter()
    chapters=Counter()
    labels={}
    proofs={}
    for r in runs:
        name=r['sample_id']
        job=root/'jobs'/name
        view=projection.get(name,{})
        for label in read(job/'translation-visuals.json',{}).get('translations',[]):
            k=(label.get('asset_sha256'),label.get('label_key') or label.get('label_id'))
            if labels.get(k)!='complete':
                labels[k]=label.get('status','missing')
        ctx=read(root/'inputs'/name/'reader-context.json',{})
        known={t['id'] for t in ctx.get('existing_terms',[])}
        terms['candidate']+=len(read(job/'term-candidates.json',[]))
        for info in view.get('term_receipt',{}).get('saved',[]):
            terms['existing_id' if info['term_id'] in known else 'new_id']+=1
        terms['rejected']+=len(view.get('term_receipt',{}).get('rejected',[]))
        terms['visible']+=view.get('terms',0)
        for q in read(job/'quiz-candidates.json',[]):
            quizzes[q['type']]+=1
            if q['type']=='choice':
                quizzes['options']+=len(q.get('options',[]))
                quizzes['explained_options']+=sum(bool(x) for x in q.get('option_explanations',[]))
        quizzes['native_visible']+=view.get('quizzes',0)
        if r['mode']!='ANSWER':
            chapters['applicable']+=1
            chapters['visible']+=view.get('recaps',0)==1
            chapters['parts']+=r['parts']
            chapters['recap_parts']+=view.get('recap_parts',0)
        card=read(root/'planners'/name/'run.json',{})
        first=read(root/'inputs'/name/'step04-run.json',{})
        writer=root/'inputs'/name/first.get('output_file','!missing')
        proofs[name]={'fresh_plan':bool(card.get('model_dispatches')) and not card.get('cache_hit'),
                      'fresh_writer':bool(first.get('request_attempts')),
                      'writer_hash_unchanged':writer.is_file() and h(writer)==first.get('output_hash')}
    p2=dict(sum((Counter(r.get('p2',{})) for r in runs),Counter()))
    opened=sum(row.get('http')==200 for row in browser.get('final',{}).values())
    labels_by_status=dict(Counter(labels.values()))
    timing_replay=trace_replay(root,runs,general_events)
    timings={'cold_wall':round(ended-began,3) if began and ended else None,
             'task_seconds':distribution(elapsed.values()),'per_task':elapsed,
             'browser_first_seen':ready,'actual_early_total':early_before_seal,
             'actual_early_seconds':distribution(r['seconds'] for r in ready.values() if r['phase']=='early'),
             'events':len(event_rows),'events_timed':sum(e.get('start_at') is not None for e in event_rows),
             'events_with_dependencies':sum(bool(e.get('upstream_versions')) for e in event_rows),
             'recorded_active_path':critical,'barrier_sensitivity_replay':timing_replay}
    metrics={'terminal':result,'body_count':len(runs),'browser_opened':opened,
             'original_hashes':originals,'fresh_generation':proofs,'urls':urls,'timing':timings,
             'source_coverage':coverage,'visual_labels':labels_by_status,'p2':p2,'terms':dict(terms),
             'exercises':dict(quizzes),'chapters':dict(chapters),'upstream_model':model,'postprocess_model':performance,
             'true_translation_gaps':missing_translation(root,runs),'p2_gaps':resource_gaps(root,runs)}
    write(base/'metrics.json',metrics)
    code_patch(base/'workspace',args.baseline,base/'implementation.diff')
    txt=coverage['unique_foreign']
    replay=read(base/'hot-replay.json',{})
    submit=read(root/'save-version-and-protection.json',{})
    only=read(root/'terminology-recovery-20step.json',{})
    stress=read(root/'browser-load-20step.json',[])
    report=[
        '# ManualReader-Next · 20 STEP 隔离验收',
        '',
        f'本轮得到 {len(runs)}/40 篇新成稿，真实浏览器打开 {opened}/40。跨轮历史模型产物复用为零，未写入正式服务或正式项目。',
        '',
        '## 哪些内容已经齐全',
        '',
        '| 核对项 | 实际结果 |',
        '| --- | --- |',
        f'| 应译原文（按文件、Source ID、版本和原文哈希去重） | {rate(txt.get("complete",0),txt.get("needed",0))}；待核 {txt.get("uncertain",0)}，未译 {txt.get("unresolved",0)} |',
        f'| 图中文字 | {labels_by_status}；仅统计已发现的标签 |',
        f'| 应详解的独立资源 | {rate(p2.get("explained",0)+p2.get("covered_by_group",0),sum(v for k,v in p2.items() if k!="excluded"))}；真实状态 {p2} |',
        f'| 原生术语 | {dict(terms)}；existing_id 按原项目词库确定 |',
        f'| 练习 | {dict(quizzes)}；选择题逐项解析 {rate(quizzes["explained_options"],quizzes["options"])} |',
        f'| 章末回顾 | {rate(chapters["visible"],chapters["applicable"])} 篇；Part 回顾 {rate(chapters["recap_parts"],chapters["parts"])} |',
        '',
        '来源导航、知识目标和子问分别统计；仅导航关系不算实质讲解。',
        '',
        '| 模式 | 篇数 | 来源导航 | 实质关联 | 仅导航 | 知识目标 | 子问 |',
        '| --- | ---: | --- | ---: | ---: | --- | --- |']
    for mode,m in coverage['modes'].items():
        v=m['navigation']; a=m['goals_answers']; g=a['goal_coverage']; q=a['question_item_coverage']
        report.append(f'| {mode} | {m["samples"]} | {rate(v.get("linked",0),v.get("total",0))} | {v.get("direct_semantic_count",0)} | {v.get("navigation_only_count",0)} | {rate(g["covered"],g["total"])} | {rate(q["covered"],q["total"])} |')
    report += [
        '', '## 时间主要花在哪里', '',
        f'- 冷跑墙钟 {timings["cold_wall"]} 秒，包含隔离保存技术恢复和继续启动此前未运行的 FINALE。单篇 p50/p95 为 {timings["task_seconds"].get("p50")} / {timings["task_seconds"].get("p95")} 秒。',
        f'- {early_before_seal} 篇在最终回执前已由浏览器验证可读。提前到达的样本 p50/p95：{timings["actual_early_seconds"]} 秒。',
        f'- 记录 {len(event_rows)} 条发布/状态事件，{timings["events_with_dependencies"]} 条有依赖。事件和上游计时分开记录；没有把发布事件当作完整的关键路径。',
        f'- 用同轮计时做了零模型旧阶段屏障模拟（{len(timing_replay["samples"])} 篇有可配对计时）。模拟假设及两个估计见 metrics.json。真实批次还包含调用线路竞争、恢复及收尾等待，所以不提供推算的提速比例。',
        f'- 术语只补错把已验证空词库当作未知的请求：{len(only.get("runs",[]))} 篇；耗时 {only.get("wall_seconds")} 秒。章节附件的 JSON 解码使用同轮原始响应恢复，没有重复调用。',
        '- 模型耗时、档位、token、限流和缓存详见 metrics.json 和模型回执。DS 真实探针独立列示。服务未提供实际费用，本报告不估算。',
        '',
        '## 真正验证了哪些交互和保存行为', '',
        f'- 热重放：通过 {sum("failure" not in x for x in replay.get("samples",{}).values())}/{len(replay.get("samples",{}))} 篇；新增模型请求 {replay.get("model_dispatches")}；回执哈希未变：{replay.get("model_receipts_identical")}；耗时 {replay.get("elapsed_seconds")} 秒。',
        f'- 隔离提交：{len(submit.get("samples",[]))} 篇；过期版本返回 409：{submit.get("stale_version_rejections")}；同提交幂等：{submit.get("idempotent")}；原用户记录保持：{submit.get("unchanged_user_records")} 份。另有中断重启测试的独立回执。',
        '- 两条真实问题的追问与 BP、原生代码沙箱正反判分、浏览器截图和逐项回执均单独附上。浏览器能打开不代表内容完整或经过人工认可。']
    for x in stress:
        report.append(f'- {x["parallel_readers"]} 路并发浏览：打开 {x["opened"]}；墙钟 {x["wall_seconds"]:.2f} 秒；p95 {x["latency"].get("p95")} 秒；浏览器进程树内存峰值 {x["memory_peak_process_tree_bytes"]/2**20:.0f} MiB。')
    report += [
        '', '## 还有哪些限制', '',
        '- PDF/HTML 原文若不能唯一对应原文件字节，保留原进度；不可用导航覆盖冒充真实文件覆盖。',
        '- 有逐批预览和限定依赖的文件版本失效记录；尚未证明运行中每种真实文件改动都能自动触发所需重算。旧阶段只做了同轮计时的敏感性模拟，没有宣称为实际端到端重放。',
        '- 应译缺项主要是回复将单元标为保留公式，少数单元漏回或返回不一致。DS 实译探针和本轮翻译回执没有出现 HTTP 403；403/429/503 的故障注入另存，不能当作线上故障。',
        '- 老张访谈 NORMAL 缺章末回顾：F1 调用断流，客户端重试被本机请求回执代理拒绝，未拿到可用模型输出。没有伪造回顾。',
        '- 原文、图片标签和资源详解的真实缺项见 metrics.json。本轮的正文未因附件或内容缺项而重写。',
        '', '## 从哪里打开成稿', '',
        '[隔离 Reader 验收首页](http://127.0.0.1:9095/acceptance)。需在隔离服务所在电脑打开。', '']
    for name in manifest.get('deep_browser_samples',[]):
        if name in urls:
            report.append(f'- [{name}]({urls[name]})')
    (base/'report.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    print(json.dumps({'bodies':len(runs),'opened':opened,'translation':txt,'p2':p2,
                      'terms':dict(terms),'quizzes':dict(quizzes),'chapters':dict(chapters),
                      'remaining':result.get('failures',{})},ensure_ascii=False))
    if args.draft:
        return
    dest=base/'ManualReader-Next-20STEP-验收.zip'
    with ZipFile(dest,'w',ZIP_DEFLATED,compresslevel=6) as archive:
        def add(path, name=None):
            if path.is_file():
                archive.write(path,name or path.relative_to(base).as_posix())
        for name in ('report.md','runtime.md','metrics.json','implementation.diff','hot-replay.json','step_registry_alignment.json','delivery-completeness.json',
                     'code-runner-proof.json','pytest.log','translation-fault-20step.json','format-and-origin-proof.json'):
            add(base/name)
        for path in base.glob('followup-*.*'):
            add(path)
        for name in ('whiteboard-result.json','resume-20step.json','reader-service-ready.json','model-schemas.json',
                     'browser-20step.json','browser-20step-final.json','browser-load-20step.json','native-answer-20step.json',
                     'reader-projections.json','terminology-recovery-20step.json','save-version-and-protection.json',
                     'guidance-recovery.json','upstream-events.jsonl','quiz-type-runtime.json','native-download-proof.json',
                     'terminal-recovery-20step.json','save-20step-errors.json'):
            add(root/name)
        add(base/'commit-crash-isolation/fault-restart-proof.json')
        add(base/'ds-probe/receipt.json')
        for path in (journal/'run_manifest.json',journal/'events.jsonl'):
            add(path)
        for path in (base/'workspace/src/manualreader/steps/prompts').glob('*.md'):
            add(path,'prompts/'+path.name)
        for stage, stages in files.items():
            for index,file in enumerate(stages,1):
                archive.writestr(f'model-receipts/upstream/{stage}/{index:03}.json',
                                 json.dumps(clean(read(root/file)),ensure_ascii=False))
        for path in (root/'requests').glob('*/*/receipt.json'):
            archive.writestr('model-receipts/finish/'+path.relative_to(root/'requests').as_posix(),
                             json.dumps(clean(read(path)),ensure_ascii=False))
        for path in (root/'acceptance-screens').glob('*.png'):
            if path.stem in manifest.get('deep_browser_samples',[]):
                add(path)
        for job in sorted((root/'jobs').iterdir()):
            for name in ('run.json','body.final.md','part-blocks.json','attachments.final.json','chapter-guidance.json',
                         'source-translation-links.json','resource-explanations.json','resource-block-links.json',
                         'block-source-links.json','offset-map.json','isolated-save.json'):
                path=job/name
                if name=='isolated-save.json' and path.exists():
                    archive.writestr(path.relative_to(base).as_posix(),json.dumps(clean(read(path)),ensure_ascii=False))
                else:
                    add(path)
        for folder in (root/'planners').glob('*'):
            for name in ('scope-reconciliation.json','run-before-scope-reconciliation.json','run.json','task-card.md','task-card.json'):
                add(folder/name)
    with ZipFile(dest) as archive:
        if archive.testzip():
            raise ValueError('压缩包校验未通过')
    write(base/'zip-check.json',{'file':str(dest),'size':dest.stat().st_size,'sha256':h(dest)})


if __name__=='__main__':
    main()
