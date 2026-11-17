"""Verify and package a fresh four-mode navigation run."""

from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from shutil import copy2
from statistics import median
import subprocess
import sys
from urllib.request import urlopen
from zipfile import ZIP_DEFLATED, ZipFile

from package_step56_final import digest, hashes, pairs, ratio, read, write


MODES=('NORMAL','INTRO','FINALE','ANSWER')


def observe(root,old,sample,expected,prior_ids):
    task=root/'jobs'/sample
    inputs=root/'inputs'/sample
    state=read(task/'status.json')
    assert state['status'] not in ('queued','running'),sample
    assert all(digest(inputs/name)==digest(old/'inputs'/sample/name)==sha for name,sha in expected.items())
    assert {f.name for f in inputs.iterdir()}==expected.keys()
    card=read(inputs/'task-card.json')
    source_rows=read(inputs/'source-index.json')['sources']
    sources={r['source_id']:r for r in source_rows}
    result={'sample':sample,'mode':state['mode'],'status':state['status'],
            'start':state['submitted_at'],'end':state['ended_at'],'sources':len(sources),
            'dir':str(task.resolve()),'input_hashes':expected}
    receipts=[]
    for p in sorted((task/'model').glob('*/outcome.json')):
        receipt=read(p)
        identifier=receipt.get('response_id')
        assert not identifier or identifier not in prior_ids,(sample,p.parent.name,identifier)
        assert all(r['started_at']>=state['submitted_at'] for r in receipt.get('provider_responses',[]))
        receipt.pop('reply',None)
        receipts.append({'stage':p.parent.name.split('-')[0],'partition':p.parent.name,
                         'files':str(p.parent.resolve()),**receipt})
    result['model_receipts']=receipts
    result['model_faults']=[{'partition':r['partition'],'state':r['state'],
                            'error':r.get('cli_error'),'response':r.get('response')}
                           for r in receipts if r['state']!='complete']
    result['model_faults'] += [{'partition':p.parent.name,**read(p)}
                               for p in (task/'model').glob('*/local-error.json')]
    if not (task/'run-metrics.json').exists():
        result['technical_failure']=state.get('error')
        return result
    metrics=read(task/'run-metrics.json')
    assert metrics['force_fresh_from_step05'] and not metrics['reused_calls'] and not metrics['prior_06b_patches']
    assert sum(call['stage']=='06B' for call in metrics['calls'])==1
    expected_calls={f'{c["stage"]}-{c["partition"]:02}' for c in metrics['calls']}
    assert {r['partition'] for r in receipts}|{r['partition'] for r in result['model_faults']}==expected_calls
    first=read(task/'step05-mapping.json')
    gap=read(task/'step06a-additions.json')
    assert sorted(sid for p in first['batches'] for sid in p['source_ids'])==sorted(sources)
    initial={sid for sid,_ in pairs(first['links'])}
    assert set(sources)-initial==set(read(task/'step05-unmapped.json')['source_ids'])
    anchors=read(task/'anchors.json')
    body=read(inputs/'step04-run.json')['output_file']
    raw=(inputs/body).read_bytes().decode()
    assert all(raw[a['start']:a['end']]==a['text'] for a in anchors)
    overlays=read(task/'overlays.json')
    for patch in overlays:
        assert (task/'overlays'/f'{patch["patch_id"]}.md').read_bytes().decode()==patch['markdown']
    targets={r['anchor_id'] for r in anchors}|{'overlay:'+r['patch_id'] for r in overlays}
    graph=read(task/'step06-merged-mapping.json')
    forward={(sid,aid) for sid,aids in graph['source_to_explanation'].items() for aid in aids}
    reverse={(sid,aid) for aid,sids in graph['explanation_to_source'].items() for sid in sids}
    assert forward==reverse==pairs(graph['links'])
    assert all(sid in sources and aid in targets for sid,aid in forward)
    direct=pairs(first['links'])|pairs(gap['links'])|{(sid,'overlay:'+p['patch_id'])
                                                       for p in overlays for sid in p['source_ids']}
    assert direct==pairs(p for p in graph['links'] if p['relation_type']=='direct_semantic')
    assert all(p['substantive_coverage']==(p['relation_type']=='direct_semantic') for p in graph['links'])
    semantic={sid for sid,_ in direct}
    nav={sid for sid,_ in forward}
    assert set(sources)-nav==set(graph['unresolved_source_ids'])
    assert nav-semantic==set(graph['navigation_only_source_ids'])
    for relation in graph['navigation_only_links']:
        sender=sources[relation['source_id']]
        assert relation['source_position']==sender.get('position')
        if parent:=relation['parent_source_id']:
            assert sources[parent]['file_id']==sender['file_id']
            assert relation['parent_position']==sources[parent].get('position')
    coverage=read(task/'step06-goal-question-coverage.json')
    assert [(item['role'],item['goal_index'],item['topic']) for item in coverage['goal_items']]==[
        (role,i,value) for role in ('reader','teacher') for i,value in enumerate(card['goals'].get(role,[]))]
    for row in coverage['goal_items']+coverage['question_items']+coverage['question_raw_units']:
        assert set(row['target_ids'])<=targets and row['covered']==bool(row['target_ids'])
    for row in coverage['question_items']+coverage['question_raw_units']:
        quote=row.get('question_quote',row.get('text'))
        assert card['question']['text'][row['start']:row['end']]==quote
    result.update(metrics=metrics,source_05=len(initial),semantic=len(semantic),nav=len(nav),
                  nav_only=len(nav-semantic),unmapped=graph['unresolved_source_ids'],
                  nav_fallback=sum(p['strategy']=='body_context_fallback' for p in graph['navigation_only_links']),
                  raw_navigation_only=sum(r['navigation_only'] for r in coverage['question_raw_units']),
                  reader_goals={'total':sum(r['role']=='reader' for r in coverage['goal_items']),
                                'covered':sum(r['role']=='reader' and r['covered'] for r in coverage['goal_items'])},
                  teacher_goals={'total':sum(r['role']=='teacher' for r in coverage['goal_items']),
                                 'covered':sum(r['role']=='teacher' and r['covered'] for r in coverage['goal_items'])},
                  questions=coverage['question_item_coverage'],raw_units=coverage['question_raw_unit_coverage'])
    return result


def summarize(rows):
    modes={mode:Counter() for mode in MODES}
    usage=Counter()
    calls=Counter()
    http=Counter()
    intervals=[]
    timings=[]
    for row in rows:
        m=modes[row['mode']]
        m['tasks']+=1
        for key in ('sources','source_05','semantic','nav','nav_only','nav_fallback','raw_navigation_only'):
            m[key]+=row.get(key,0)
        for key in ('reader_goals','teacher_goals','questions','raw_units'):
            value=row.get(key,{})
            m[key+'_total']+=value.get('total',0)
            m[key+'_covered']+=value.get('covered',0)
        for receipt in row['model_receipts']:
            calls[receipt['stage']]+=1
        usage.update(row.get('metrics',{}).get('token_usage',{}))
        http.update(row.get('metrics',{}).get('http_status',{}))
        intervals.extend(row.get('metrics',{}).get('provider_intervals',[]))
        timings.extend(r['wall_seconds'] for r in row.get('metrics',{}).get('calls',[]))
    current=peak=0
    for _,change in sorted([(t,1) for t,_ in intervals]+[(t,-1) for _,t in intervals]):
        current+=change
        peak=max(peak,current)
    assert all(m['tasks']==10 for m in modes.values())
    return {'modes':modes,'stage_outcomes':calls,'http_status':http,'usage':usage,
            'status':Counter(r['status'] for r in rows),'peak_provider_requests':peak,
            'wall_seconds':max(r['end'] for r in rows)-min(r['start'] for r in rows),
            'model_wall_p50_seconds':median(timings),'reused_model_outputs':0,
            'provider_cost_usd':None,
            'failed_items':[{'sample':r['sample'],'status':r['status'],'model_faults':r['model_faults'],
                             'technical_failure':r.get('technical_failure')} for r in rows
                            if r['status']!='complete' or r['model_faults']]}


def pack(root,old):
    workspace=root/'workspace'
    delivery=root/'delivery'
    delivery.mkdir(exist_ok=True)
    baseline=read(root/'baseline-code.json')
    assert all(digest(old/'workspace'/p)==h for p,h in baseline.items())
    patch_manifest=read(root/'spec/manifest.json')['changed_paths']
    changed=set(patch_manifest)|{'tools/verify_step56_browser.py'}
    assert all(digest(workspace/p)==h for p,h in baseline.items() if p not in changed)
    assert hashes(old/'isolated-data')==hashes(root/'isolated-data')
    protected=read(root/'protected-inputs.json')
    assert len(protected)==40
    prior_ids={r['response_id'] for path in (old/'jobs').glob('*/model/*/outcome.json')
               if (r:=read(path)).get('response_id')}
    rows=[observe(root,old,sample,expected,prior_ids) for sample,expected in protected.items()]
    ids=[r['response_id'] for row in rows for r in row['model_receipts'] if r.get('response_id')]
    assert len(ids)==len(set(ids))
    summary=summarize(rows)
    write(delivery/'summary.json',summary)
    write(delivery/'runs-index.json',rows)
    for row in rows:
        sample=row['sample']
        source=root/'jobs'/sample
        target=delivery/'runs'/sample
        target.mkdir(parents=True,exist_ok=True)
        for name in ('step05-mapping.json','step06-merged-mapping.json','step06-goal-question-coverage.json',
                     'run-metrics.json','rejected-model-items.json'):
            if (source/name).exists():
                copy2(source/name,target/name)
        write(target/'receipts.json',row['model_receipts'])
    service=read(root/'service-ready.json')
    with urlopen(service['base']+'/health',timeout=10) as response:
        service['health']=json.load(response)
    assert service['health']['version']=='ManualReader-Next/0.6.2'
    with urlopen(read(old/'service-ready.json')['base']+'/health',timeout=10) as response:
        service['previous_health']=json.load(response)
    with urlopen(service['base']+'/v1/client',timeout=10) as response:
        client=response.read()
    with ZipFile(BytesIO(client)) as archive:
        manifest=json.loads(archive.read('client-hashes.json'))
        assert archive.testzip() is None
        assert all(sha256(archive.read(name)).hexdigest()==item['sha256'] for name,item in manifest.items())
    (delivery/'manualreader-step56-client.zip').write_bytes(client)
    write(delivery/'deployment.json',service)
    proof=read(root/'browser/evidence.json')
    assert {r['mode'] for r in proof}==set(MODES)
    (delivery/'browser').mkdir(exist_ok=True)
    for path in (root/'browser').iterdir():
        if path.suffix in ('.png','.json'):
            copy2(path,delivery/'browser'/path.name)
    for log in ('tests-final.log','legacy-tests.log','special-tests.log','cold-install.log','browser.log'):
        copy2(root/log,delivery/log)
    paths=list(patch_manifest)+[
        'tools/package_step56_navigation.py','tools/verify_step56_browser.py']
    diff=[]
    for name in paths:
        path=workspace/name
        target=delivery/'patch'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        copy2(path,target)
        old_path=old/'workspace'/name
        compare=subprocess.run(['git','-c','core.quotepath=false','diff','--no-index','--',
                                str(old_path) if old_path.exists() else '/dev/null',str(path)],
                               capture_output=True)
        assert compare.returncode in (0,1),compare.stderr.decode(errors='replace')
        diff.append(compare.stdout)
    (delivery/'implementation.diff').write_bytes(b'\n'.join(diff))
    workspace_prompts={}
    for relative in baseline:
        if not (relative.startswith('src/manualreader/') and relative.endswith('.txt')):
            continue
        prompt_path=workspace/relative
        current=digest(prompt_path)
        assert baseline[relative]==current
        attachment=root/'spec/prompts'/prompt_path.name
        workspace_prompts[relative]={'workspace':current,
                                     'attachment':digest(attachment) if attachment.exists() else None}
    write(delivery/'integrity.json',{'prompt_hashes':workspace_prompts,'input_count':len(protected),
                                   'frozen_inputs':protected,'formal_writeback':False,
                                   'isolated_data':hashes(root/'isolated-data')})
    modes=summary['modes']
    table=['| 模式 | 来源可跳转 | 有实质讲解关联 | 仅导航 | 读者目标 | 教师目标 |',
           '| --- | ---: | ---: | ---: | ---: | ---: |']
    for mode in MODES:
        m=modes[mode]
        table.append(f'| {mode} | {ratio(m["nav"],m["sources"])} | {m["semantic"]:,} | {m["nav_only"]:,} | '
                     f'{ratio(m["reader_goals_covered"],m["reader_goals_total"])} | '
                     f'{ratio(m["teacher_goals_covered"],m["teacher_goals_total"])} |')
    answer=modes['ANSWER']
    usage=summary['usage']
    report=['# STEP-05/06 四模式导航实测','',
        f'0.6.2 隔离预览在 `{service["base"]}/step56`。40 个任务都从新的模型目录提交。'
        '可读取的原件、来源索引、任务卡和首稿逐件核对。旧版可以在原端口继续查看。','',
        *table,'',
        f'ANSWER 识别出的子问有 {ratio(answer["questions_covered"],answer["questions_total"])} 可跳转。'
        f'原话片段有 {ratio(answer["raw_units_covered"],answer["raw_units_total"])} 可跳转，'
        f'其中 {answer["raw_navigation_only"]} 段沿用相邻回答的位置，仍保留原文和偏移。','',
        '仅导航的来源优先随同文件中已有关联的来源跳转。没有这种父来源时，程序会指向已有的正文位置，'
        f'并记录缺少同文件父来源。这类正文入口共有 {sum(r["nav_fallback"] for r in modes.values())} 条。','',
        f'新版状态为 {dict(summary["status"])}，实际请求回执为 {dict(summary["http_status"])}。'
        f'从首个提交到末个产物落盘共 {summary["wall_seconds"]:.1f} 秒，峰值并行 {summary["peak_provider_requests"]} 路。',
        f'输入 {usage["input_tokens"]:,} token，输出 {usage["output_tokens"]:,} token，'
        f'其中推理 {usage["reasoning_tokens"]:,} token。计费缓存命中 {usage["cached_input_tokens"]:,} 输入 token，'
        '供应商未返回实际费用。旧模型输出复用数为 0。','',
        f'本轮模型结果数为 {dict(summary["stage_outcomes"])}。'
        '没有来源记录的 ANSWER 仍运行 06B，并由 STEP-05 记录为空映射。','',
        '附件与已验收工作区的 STEP-05、06A 提示词哈希一致。附件的 06B 提示词为较早版本，'
        '本次按执行说明使用 0.6.1 已冻结的版本，实际提示词哈希见 integrity.json。',
        'Edge 实际点击四模式的跳转链接，核对原图加载与下载。测试真实输出在同目录。','']
    (delivery/'report.md').write_text('\n'.join(report),encoding='utf8')
    manifest=hashes(delivery)
    manifest.pop('manifest-sha256.json',None)
    write(delivery/'manifest-sha256.json',manifest)
    filename=root/'ManualReader-Next-STEP05-06-四模式导航验收包.zip'
    secret=(root/'token.txt').read_bytes().strip()
    with ZipFile(filename,'w',ZIP_DEFLATED,compresslevel=6) as archive:
        for name,h in sorted(manifest.items()):
            raw=(delivery/name).read_bytes()
            assert sha256(raw).hexdigest()==h and secret not in raw
            archive.writestr(name,raw)
        archive.write(delivery/'manifest-sha256.json','manifest-sha256.json')
    with ZipFile(filename) as archive:
        assert archive.testzip() is None
        assert all(sha256(archive.read(name)).hexdigest()==h for name,h in manifest.items())
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print(filename,filename.stat().st_size,digest(filename))


if __name__=='__main__':
    pack(*(Path(x) for x in sys.argv[1:]))
