from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from shutil import copy2, copytree
from statistics import median
import subprocess
import sys
from urllib.request import urlopen
from zipfile import ZipFile, ZIP_DEFLATED


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def hashes(path):
    return {p.relative_to(path).as_posix():digest(p) for p in path.rglob('*') if p.is_file()}


def summary(rows):
    totals=Counter()
    for row in rows:
        for key in ('source_total','initial_mapped','extra_mapped','patch_sources','absorbed','unresolved',
                    'patches_a','patches_b','failed_calls','rejected_items'):
            totals[key]+=row[key]
        totals['navigation_targets']+=sum(row['body_dispositions'].values())
        totals['explanations_linked']+=row['body_dispositions'].get('source_linked',0)
        totals['body_linked']+=row['body_original_linked']
        totals['body_anchors']+=row['body_original_anchors']
        totals['patch_sources_added_to_navigation']+=row['patch_sources_added_to_navigation']
        totals['no_billing_cost_available']+=row['provider_cost_usd'] is None
    totals['tasks']=len(rows)
    return dict(totals)


def pack(root, previous, original):
    workspace=root/'workspace'
    delivery=root/'delivery'
    delivery.mkdir(exist_ok=True)
    source=delivery/'clean-source'
    ignored=('__pycache__','*.egg-info')
    from shutil import ignore_patterns
    for name in ('src','tests','tools','docs'):
        copytree(workspace/name,source/name,dirs_exist_ok=True,ignore=ignore_patterns(*ignored))
    for name in ('.gitignore','README.md','requirements.lock','tests.lock','pyproject.toml'):
        copy2(workspace/name,source/name)
    before=read(root/'baseline-code.json')
    changes=[p for p,h in before.items() if digest(source/p)!=h]
    assert set(changes)=={'README.md','pyproject.toml'}
    assert all(digest(original/p)==h for p,h in before.items())
    data_before=hashes(previous/'isolated-data')
    assert data_before==hashes(root/'isolated-data')
    initial_states=sorted((root/'jobs').glob('*/status.json'))
    assert len(initial_states)==40
    assert all(read(p)['status'] not in ('running','queued') for p in initial_states)

    def one(path):
        state=read(path)
        job=path.parent
        label=job.name
        frozen=root/'inputs'/label
        prior=previous/'delivery/runs'/label
        record=read(frozen/'step04-run.json')
        name=record['output_file']
        assert digest(frozen/name)==digest(prior/name)==record['output_hash']
        assert digest(frozen/'task-card.json')==digest(prior/'task-card.json')==record['task_card_sha256']
        export=delivery/'runs'/label
        copytree(job,export,dirs_exist_ok=True)
        for item in (name,'task-card.json','source-index.json','step04-run.json'):
            copy2(frozen/item,export/item)
        if not (job/'run-metrics.json').exists():
            return {'sample_id':label,'mode':record['mode'],'status':state['status'],'error':state['error']}
        row=read(job/'run-metrics.json')
        # Read bytes directly so CRLF offsets are evaluated against the solver input.
        body=(frozen/name).read_bytes().decode()
        anchors=read(job/'anchors.json')
        aids={a['anchor_id'] for a in anchors}
        assert all(body[a['start']:a['end']]==a['text'] for a in anchors)
        doc=read(frozen/'source-index.json')
        sids={s['source_id'] for s in doc['sources']}
        mapped=read(job/'step05-mapping.json')
        seen=[sid for b in mapped['batches'] for sid in b['source_ids']]
        assert set(seen)==sids and len(seen)==len(sids)
        initial={x['source_id'] for x in mapped['links']}
        assert sids-initial==set(read(job/'step05-unmapped.json')['source_ids'])
        graph=read(job/'step06-merged-mapping.json')
        backward={(s,a) for a,origins in graph['explanation_to_source'].items() for s in origins}
        forward={(s,a) for s,current in graph['source_to_explanation'].items() for a in current}
        assert forward==backward and sids-{s for s,_ in forward}==set(graph['unresolved_source_ids'])
        patches=read(job/'overlays.json')
        valid_anchors=aids|{'overlay:'+p['patch_id'] for p in patches}
        assert all(s in sids and a in valid_anchors for s,a in forward)
        adopted=set()
        for p in patches:
            assert (job/'overlays'/f'{p["patch_id"]}.md').read_bytes().decode()==p['markdown']
            adopted.update(p['source_ids'])
            for aid in p['asset_refs']:
                a=doc['assets'][aid]
                if sha:=a.get('sha256'):
                    media=export/'assets'/f'{sha}.png'
                    media.parent.mkdir(exist_ok=True)
                    original_image=root/'media'/media.name
                    assert digest(original_image)==sha
                    copy2(original_image,media)
        extra={p['source_id'] for p in read(job/'step06a-additions.json')['links']}
        wire_images=[]
        for receipt in (job/'model').glob('*/outcome.json'):
            result=read(receipt)
            for request in result.get('provider_responses',[]):
                wire_images.extend(request.get('image_sha256',[]))
        return {**row,'sample_id':label,'status':state['status'],'started_at':state['submitted_at'],
                'ended_at':state['ended_at'],'body_original_anchors':len(aids),
                'body_original_linked':sum(a in graph['explanation_to_source'] for a in aids),
                'patch_sources_added_to_navigation':len(adopted-initial-extra),'wire_images':wire_images}

    with ThreadPoolExecutor(max_workers=10) as pool:
        all_rows=list(pool.map(one,initial_states))
    rows=[row for row in all_rows if 'source_total' in row]
    stages={}
    intervals=[]
    for row in rows:
        intervals.extend(row['provider_intervals'])
        for call in row['calls']:
            stages.setdefault(call['stage'],[]).append(call)
    modes={mode:summary([r for r in rows if r['mode']==mode])
           for mode in ('NORMAL','INTRO','FINALE','ANSWER')}
    events=sorted([(start,1) for start,_ in intervals]+[(end,-1) for _,end in intervals])
    concurrent=peak=0
    for _,change in events:
        concurrent+=change
        peak=max(peak,concurrent)
    raw_count=Counter()
    usage=Counter()
    for row in rows:
        raw_count.update(row['http_status'])
        usage.update(row['token_usage'])
    frozen_count=Counter(r['mode'] for r in all_rows)
    assert all(v==10 for v in frozen_count.values()) and len(frozen_count)==4
    faults=[r['sample_id'] for r in all_rows if r['status']!='complete']
    metrics={'overall':summary(rows),'modes':modes,'task_status':dict(Counter(r['status'] for r in all_rows)),
         'model_stages':{s:{'count':len(calls),'succeeded':sum(c['state']=='complete' for c in calls),
                            'wall_p50':median(c['wall_seconds'] for c in calls),
                            'queue_p50':median(c['queue_seconds'] for c in calls)} for s,calls in stages.items()},
         'reported_usage':dict(usage),'provider_http':dict(raw_count),'peak_provider_requests':peak,
         'wall_seconds':max(r['ended_at'] for r in rows)-min(r['started_at'] for r in rows),
         'task_wall_p50_seconds':median(r['wall_seconds'] for r in rows),
         'provider_cost_usd':None,'unavailable_tasks':faults,
         'fixture_finale':sum(r['mode']=='FINALE' and r['fixture_based'] for r in rows),
         'unique_images':len(list((root/'media').glob('*.png'))),
         'visual_parts_in_requests':sum(len(r['wire_images']) for r in rows),
         'different_visual_parts_in_requests':len({h for r in rows for h in r['wire_images']}),
         'local_image_cache':max((r['local_media_cache'] for r in rows),key=lambda c:c['hits']),
         'two_way_navigation_verified':len(rows),'old_source_unchanged':len(before),
         'isolated_project_files_preserved':len(data_before)}
    save(delivery/'summary.json',metrics)
    save(delivery/'integrity.json',{'isolated_data':data_before,'original_workspace_file_hashes':before,
         'tasks':{r['sample_id']:{'draft':r['original_sha256'],'task_card':r['card_sha256']} for r in rows},
         'changed_existing_files_in_isolated_copy':changes})
    copy2(root/'preflight.json',delivery/'preflight.json')
    for filename in ('tests.log','baseline-tests.log','legacy-tests.log','special-tests.log','cold-install.log',
                     'browser.log','attachment-tests.log'):
        if (root/filename).exists():
            copy2(root/filename,delivery/filename)
    copytree(root/'browser',delivery/'browser',dirs_exist_ok=True)
    service=read(root/'service-ready.json')
    old=read(previous/'deployment.json')
    with urlopen(service['base']+'/health',timeout=10) as handle:
        service['health']=json.load(handle)
    try:
        with urlopen(old['base']+'/health',timeout=10) as handle:
            service['old_health']=json.load(handle)
    except OSError as exc:
        service['old_health']={'error':str(exc)}
    with urlopen(service['base']+'/v1/client',timeout=10) as handle:
        client=handle.read()
    with ZipFile(BytesIO(client)) as archive:
        assert archive.testzip() is None
        manifest=json.loads(archive.read('client-hashes.json'))
        assert all(sha256(archive.read(name)).hexdigest()==row['sha256'] for name,row in manifest.items())
        assert archive.read('manualreader/step56_client.py')==(source/'src/manualreader/step56_client.py').read_bytes()
    (delivery/'manualreader-step56-client.zip').write_bytes(client)
    save(delivery/'deployment.json',service)
    diff=[]
    new=hashes(source)
    for relative in sorted(set(new)-set(before)|set(changes)):
        pair=subprocess.run(['git','-c','core.quotepath=false','diff','--no-index','--',
                      str(original/relative) if relative in before else '/dev/null',
                      str(source/relative)],capture_output=True)
        if pair.returncode==1:
            diff.append(pair.stdout)
    assert diff
    (delivery/'implementation.diff').write_bytes(b'\n'.join(diff))
    table=['| 模式 | 任务 | 来源 | 初次映射 | 06A 补映射 | 补丁新增来源 | 吸附 | 无可跳转位置 |',
           '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for mode,r in modes.items():
        table.append('| '+mode+' | '+' | '.join(str(r[k]) for k in ('tasks','source_total','initial_mapped',
                        'extra_mapped','patch_sources_added_to_navigation','absorbed','unresolved'))+' |')
    counts=metrics['overall']
    request_total=sum(r['count'] for r in metrics['model_stages'].values())
    report=[
        '# ManualReader Next 0.6.0 实测报告','',
        f'新版隔离服务在 `{service["base"]}/step56` 提供原稿、来源映射和独立补充页面。'
        f'40 个任务按原有范围处理，状态分别为 {metrics["task_status"]}。同一任务的 06B 与 05 独立调度，06A 在所在分片的映射返回后启动。','',
        '## 来源现在能跳到哪里','',*table,'',
        f'可跳转关系由同一组数据正反计算。原稿中的 {counts["body_linked"]:,}/{counts["body_anchors"]:,} 个临时位置有可跳转的来源。'
        '未关联位置会保留原样，账本记录标题、本题上下文、有明确标记的教学示例及尚无依据的段落。INTRO 的可跳转关系按导读范围理解。',
        f'06A 保存 {counts["patches_a"]} 条补充，06B 保存 {counts["patches_b"]} 条补充。'
        '补丁、插入位置、来源和资源都可从独立文件读取，供后续 STEP-07 使用。',
        'Olympus 有一条登记来源没有可读取的文字或对应图像，在两份任务里保留原编号。FINALE 中有 9 份历史首稿测试案例，已在相应任务标明。','',
        '## 实际请求用了多久','',
        f'从首次提交到最后一个任务结束共 {metrics["wall_seconds"]:.1f} 秒，实际峰值 {peak} 路。'
        f'共记录 {request_total} 个模型工作项，HTTP 状态次数为 {dict(raw_count)}。任务耗时中位数为 {metrics["task_wall_p50_seconds"]:.1f} 秒。',
        '每个环节的调用数、排队和执行中位数见 summary.json；每份任务的实际输入、输出和服务响应编号保存在对应的 model 目录。',
        f'服务返回的输入、缓存、输出和推理 token 分别为 {usage["input_tokens"]:,}、{usage["cached_input_tokens"]:,}、'
        f'{usage["output_tokens"]:,}、{usage["reasoning_tokens"]:,}。供应商未提供实际费用。','',
        f'本机按哈希共享图像文件。当前 Codex 接口需要在各模型请求内发送像素，'
        f'{metrics["different_visual_parts_in_requests"]} 份不同图像在各次请求中共发送了 {metrics["visual_parts_in_requests"]} 次。',
        f'程序仅跳过了 {counts["rejected_items"]} 条格式不合或引用无法核对的模型输出项，详细记录在对应任务中。','',
        '## 怎样核对预览和原稿','',
        '四种模式各用 Edge 打开了一份真实结果，并下载对应文件核对原稿哈希。源码已在独立环境用锁定依赖离线安装。',
        f'检查了 40 张任务卡及原稿、{len(data_before)} 份隔离项目文件和 {len(before)} 个原工作树文件。'
        'STEP-07 与正式写回不在本次运行范围内。','',
        '本轮以已经保存的真实模型结果为准。运行目录按任务保留原件像素哈希；归档收录补丁实际引用的图片。','']
    (delivery/'report.md').write_text('\n'.join(report),encoding='utf8')
    (delivery/'rollback.md').write_text('# 怎样回到 STEP-04\n\n'
        f'停止 STEP-05/06 隔离服务后，在原地址 `{old["base"]}/step04` 继续使用 STEP-04。'
        '各任务的原稿和补充都保存在本次隔离目录，可留作核对。\n',encoding='utf8')
    index=['# 怎样查看四种模式的全部结果','']
    for r in all_rows:
        sample=r['sample_id']
        index.append(f'- [{sample}]({service["base"]}/step56/{sample}) · [任务指标](runs/{sample}/run-metrics.json)')
    (delivery/'preview-index.md').write_text('\n'.join(index)+'\n',encoding='utf8')
    manifest=hashes(delivery)
    manifest.pop('manifest-sha256.json',None)
    save(delivery/'manifest-sha256.json',manifest)
    target=root/'ManualReader-Next-STEP05-06-验收包.zip'
    token=(root/'token.txt').read_bytes().strip()
    with ZipFile(target,'w',ZIP_DEFLATED,compresslevel=6) as archive:
        for name,h in sorted(manifest.items()):
            raw=(delivery/name).read_bytes()
            assert sha256(raw).hexdigest()==h and token not in raw
            archive.writestr(name,raw)
        archive.write(delivery/'manifest-sha256.json','manifest-sha256.json')
    with ZipFile(target) as archive:
        assert archive.testzip() is None
        assert all(sha256(archive.read(path)).hexdigest()==h for path,h in manifest.items())
    print(json.dumps(metrics,ensure_ascii=False,indent=2))
    print(target,target.stat().st_size,digest(target))


if __name__=='__main__':
    pack(*(Path(x) for x in sys.argv[1:]))
