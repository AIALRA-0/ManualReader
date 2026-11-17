"""Verify and package the incremental run without copying historical provider payloads."""

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

from manualreader.stages.step05.contracts import consolidate


MODES=('NORMAL','INTRO','FINALE','ANSWER')


def read(path):
    return json.loads(path.read_bytes())


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def hashes(path):
    return {p.relative_to(path).as_posix():digest(p) for p in path.rglob('*') if p.is_file()}


def pairs(rows):
    return {(r['source_id'],r['body_anchor_id']) for r in rows}


def ratio(a,b):
    return f'{a:,}/{b:,}（{a/b:.2%}）' if b else '无此项'


def update_scope(totals,key,items):
    totals[key+'_covered']+=items['covered']
    totals[key+'_total']+=items['total']


def evidence(root,previous,delivery):
    protected=read(root/'protected-inputs.json')
    result=[]
    for sample,expected in protected.items():
        old=previous/'delivery/runs'/sample
        inputs=root/'inputs'/sample
        new=root/'jobs'/sample
        record=read(inputs/'step04-run.json')
        state=read(new/'status.json')
        assert state['status'] in ('complete','partial')
        assert all(digest(old/n)==h for n,h in expected.items()),sample
        for name, h in expected.items():
            if name in ('step05-mapping.json','step05-unmapped.json','step06a-additions.json'):
                got=new/name
            elif name=='step06b-additions.json':
                got=inputs/'previous-step06b-additions.json'
            else:
                got=inputs/name
            assert digest(got)==h, (sample,name)
        first=read(new/'step05-mapping.json')
        gap=read(new/'step06a-additions.json')
        source_pool={r['source_id']:r for r in read(inputs/'source-index.json')['sources']}
        anchors=read(new/'anchors.json')
        prior=read(old/'overlays.json')
        old_artifacts={p['patch_id']:p for p in prior}
        replay=consolidate(source_pool,anchors,pairs(first['links']),pairs(gap['links']),
                           prior,gap['absorb_candidates'],record['mode'])
        old_graph=read(old/'step06-merged-mapping.json')
        graph=read(new/'step06-merged-mapping.json')
        old_source_ids=set(old_graph['source_to_explanation'])
        replay_ids=set(replay['source_to_explanation'])
        source_ids=set(graph['source_to_explanation'])
        assert old_source_ids<=replay_ids<=source_ids<=source_pool.keys()
        assert set(source_pool)-source_ids==set(graph['unresolved_source_ids'])
        assert {sid for ids in graph['explanation_to_source'].values() for sid in ids}==source_ids
        assert (record['mode']=='NORMAL' or not graph['absorption'])
        overlays={p['patch_id']:p for p in read(new/'overlays.json')}
        for pid,p in old_artifacts.items():
            on_disk=(new/'overlays'/f'{pid}.md').read_bytes()
            assert on_disk==p['markdown'].encode() and overlays[pid]['markdown']==p['markdown']
            assert digest(old/'overlays'/f'{pid}.md')==digest(new/'overlays'/f'{pid}.md')
        targets={a['anchor_id'] for a in anchors}|{'overlay:'+p for p in overlays}
        coverage=read(new/'step06-goal-question-coverage.json')
        card=read(inputs/'task-card.json')
        assert [(a['role'],a['goal_index'],a['topic']) for a in coverage['goal_items']]==[
            (role,i,value) for role in ('reader','teacher') for i,value in enumerate(card['goals'].get(role,[]))]
        for item in coverage['goal_items']+coverage['question_items']:
            assert set(item['target_ids'])<=targets and item['covered']==bool(item['target_ids'])
        for item in coverage['question_items']:
            question=card['question']['text']
            assert question[item['start']:item['end']]==item['question_quote']
        metrics=read(new/'run-metrics.json')
        assert [(c['stage'],c['partition']) for c in metrics['calls']]==[('06B',0)]
        assert all(digest(new/'model'/k/'outcome.json')==v['sha256']==digest(old/'model'/k/'outcome.json')
                   for k,v in metrics['reused_calls'].items())
        result.append({'sample':sample,'mode':record['mode'],'status':state['status'],
                       'start':state['submitted_at'],'end':state['ended_at'],
                       'source_total':len(source_pool),'source_old':len(old_source_ids),
                       'source_replay':len(replay_ids),'source_final':len(source_ids),
                       'new_patches':len(set(overlays)-set(old_artifacts)),
                       'absorption':len(graph['absorption']),
                       'goals':coverage['goal_coverage'],'questions':coverage['question_item_coverage'],
                       'goals_by_role':{
                           role:{'total':sum(item['role']==role for item in coverage['goal_items']),
                                 'covered':sum(item['role']==role and item['covered']
                                               for item in coverage['goal_items'])}
                           for role in ('reader','teacher')},
                       'raw_question_fragments':coverage['question_raw_unit_coverage'],
                       'model':metrics,'dir':str(new.resolve())})
        location=delivery/'runs'/sample
        location.mkdir(parents=True,exist_ok=True)
        for name in ('step06-goal-question-coverage.json','run-metrics.json'):
            copy2(new/name,location/name)
        write(location/'source-changes.json',{
            'absorbed':graph['absorption'],'new_source_ids':sorted(source_ids-old_source_ids),
            'unresolved':[source_pool[sid] for sid in graph['unresolved_source_ids']] if record['mode']=='NORMAL' else [],
            'new_overlay_ids':sorted(set(overlays)-set(old_artifacts)),
            'step04_digests':expected})
    return result


def summary(rows):
    modes={mode:Counter() for mode in MODES}
    usage=Counter()
    calls=Counter()
    reused=Counter()
    events=[]
    for row in rows:
        m=modes[row['mode']]
        m['tasks']+=1
        for key in ('source_total','source_old','source_replay','source_final','new_patches','absorption'):
            m[key]+=row[key]
        for key in ('goals','questions','raw_question_fragments'):
            update_scope(m,key,row[key])
        for role in ('reader','teacher'):
            update_scope(m,role+'_goals',row['goals_by_role'][role])
        metrics=row['model']
        usage.update(metrics['token_usage'])
        calls.update(metrics['http_status'])
        reused.update(k.split('-')[0] for k in metrics['reused_calls'])
        for start,end in metrics['provider_intervals']:
            events.extend(((start,1),(end,-1)))
    active=peak=0
    for _,change in sorted(events):
        active+=change
        peak=max(peak,active)
    assert reused=={'05':63,'06A':59}
    assert all(v['tasks']==10 for v in modes.values())
    return {'modes':modes,'status':Counter(r['status'] for r in rows),'reused_calls':reused,
            'new_model_http':calls,'new_usage':usage,'peak_provider_requests':peak,
            'total_wall_seconds':max(row['end'] for row in rows)-min(row['start'] for row in rows),
            '06b_wall_p50_seconds':median(row['model']['calls'][0]['wall_seconds'] for row in rows),
            'provider_cost_usd':None,'tasks':rows}


def pack(root,previous):
    delivery=root/'delivery'
    delivery.mkdir(exist_ok=True)
    workspace=root/'workspace'
    baseline=read(root/'baseline-code.json')
    assert all(digest(previous/'workspace'/p)==h for p,h in baseline.items())
    assert hashes(previous/'isolated-data')==hashes(root/'isolated-data')
    rows=evidence(root,previous,delivery)
    totals=summary(rows)
    browser=read(root/'browser/evidence.json')
    assert {r['mode'] for r in browser}==set(MODES)
    write(delivery/'summary.json',totals)
    service=read(root/'service-ready.json')
    with urlopen(service['base']+'/health',timeout=10) as response:
        service['health']=json.load(response)
    assert service['health']['version']=='ManualReader-Next/0.6.1'
    with urlopen(read(previous/'service-ready.json')['base']+'/health',timeout=10) as response:
        service['previous_health']=json.load(response)
    with urlopen(service['base']+'/v1/client',timeout=10) as response:
        client=response.read()
    with ZipFile(BytesIO(client)) as archive:
        manifest=json.loads(archive.read('client-hashes.json'))
        assert archive.testzip() is None
        assert all(sha256(archive.read(n)).hexdigest()==row['sha256'] for n,row in manifest.items())
    (delivery/'manualreader-step56-client.zip').write_bytes(client)
    write(delivery/'deployment.json',service)
    patch_paths=list(read(root/'spec/baseline-patch-hashes.json'))+[
        'pyproject.toml','tools/package_step56_final.py','tools/verify_step56_browser.py']
    diff=[]
    for p in patch_paths:
        export=delivery/'patch'/p
        export.parent.mkdir(parents=True,exist_ok=True)
        copy2(workspace/p,export)
        base=previous/'workspace'/p
        result=subprocess.run(['git','-c','core.quotepath=false','diff','--no-index','--',
                               str(base) if base.exists() else '/dev/null',str(workspace/p)],capture_output=True)
        assert result.returncode in (0,1),result.stderr.decode(errors='replace')
        diff.append(result.stdout)
    (delivery/'implementation.diff').write_bytes(b'\n'.join(diff))
    for log in ('tests.log','legacy-tests.log','special-tests.log','cold-install.log','browser.log'):
        copy2(root/log,delivery/log)
    (delivery/'browser').mkdir(exist_ok=True)
    for source in (root/'browser').iterdir():
        if source.suffix in ('.json','.png'):
            copy2(source,delivery/'browser'/source.name)
    for sample in {r['sample_id'] for r in browser if r['mode'] in ('NORMAL','ANSWER')}:
        new=root/'jobs'/sample
        out=delivery/'examples'/sample
        out.mkdir(parents=True,exist_ok=True)
        new_ids=read(delivery/'runs'/sample/'source-changes.json')['new_overlay_ids']
        for pid in new_ids[:3]:
            copy2(new/'overlays'/f'{pid}.md',out/f'{pid}.md')
    total=totals['modes']
    modes=['| 模式 | 来源跳转，旧 → 仅重算 → 新 06B | 读者目标有位置 | 教师目标有位置 | 原话子问有位置 |',
           '| --- | --- | --- | --- | --- |']
    for mode in MODES:
        row=total[mode]
        navigation=' → '.join(ratio(row[k],row['source_total']) for k in ('source_old','source_replay','source_final'))
        modes.append(f'| {mode} | {navigation} | '
                     f'{ratio(row["reader_goals_covered"],row["reader_goals_total"])} | '
                     f'{ratio(row["teacher_goals_covered"],row["teacher_goals_total"])} | '
                     f'{ratio(row["questions_covered"],row["questions_total"])} |')
    usage=totals['new_usage']
    answer=total['ANSWER']
    report=['# ManualReader Next STEP-05/06 终修实测','',
            f'隔离服务为 `{service["base"]}/step56`。40 个任务各提交一次新 06B。'
            '经哈希复核，40 份首稿、任务卡、来源索引以及 05 和 06A 的已有结果完整保留。'
            '63 个 05 回执和 59 个 06A 回执计入复用，旧的 88 个 06A 补丁和 124 个 06B 补丁均可按原哈希取回。','',
            *modes,'',
            f'ANSWER 原问题按标点切分的片段有 {ratio(answer["raw_question_fragments_covered"],answer["raw_question_fragments_total"])} '
            '与已关联子问重叠。该检查把部分陈述或列举片段也计入分母，逐题文件保留文字、偏移量和跳转位置。',
            '目标和子问的数字统计有合法位置的链接。每条链接都能回到实际原稿段落或补丁。',
            'NORMAL 统计来源能否跳转。吸附只提供导航位置，正文讲解和补丁仍各自保存。','',
            f'服务状态为 {dict(totals["status"])}，本轮 HTTP 回执为 {dict(totals["new_model_http"])}。'
            f'总耗时 {totals["total_wall_seconds"]:.1f} 秒，峰值并行 {totals["peak_provider_requests"]} 路。'
            f'输入 {usage["input_tokens"]:,} token，缓存输入 {usage["cached_input_tokens"]:,} token，'
            f'输出 {usage["output_tokens"]:,} token，其中推理 {usage["reasoning_tokens"]:,} token。实际费用未返回。','',
            '四种模式各在 Edge 打开相应预览，点击知识目标或子问位置，加载原图并核对下载中的首稿和覆盖统计。'
            '完整模型请求和图片保存在各任务本机目录，summary.json 提供入口。','',
            '若需要回到旧版，打开 deployment.json 中上一版服务的地址即可。两版的运行目录各自独立。','']
    (delivery/'report.md').write_text('\n'.join(report),encoding='utf8')
    manifest=hashes(delivery)
    manifest.pop('manifest-sha256.json',None)
    write(delivery/'manifest-sha256.json',manifest)
    target=root/'ManualReader-Next-STEP05-06-终修验收包.zip'
    secret=(root/'token.txt').read_bytes().strip()
    with ZipFile(target,'w',ZIP_DEFLATED,compresslevel=6) as archive:
        for name,h in sorted(manifest.items()):
            raw=(delivery/name).read_bytes()
            assert sha256(raw).hexdigest()==h and secret not in raw
            archive.writestr(name,raw)
        archive.write(delivery/'manifest-sha256.json','manifest-sha256.json')
    with ZipFile(target) as archive:
        assert archive.testzip() is None
        assert all(sha256(archive.read(p)).hexdigest()==h for p,h in manifest.items())
    print(json.dumps({k:v for k,v in totals.items() if k!='tasks'},ensure_ascii=False,indent=2))
    print(target,target.stat().st_size,digest(target))


if __name__=='__main__':
    pack(*(Path(arg) for arg in sys.argv[1:]))
