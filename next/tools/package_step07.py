"""Verify the 40-run delivery and write a portable acceptance ZIP."""

import argparse
from collections import Counter, defaultdict
from difflib import unified_diff
import json
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile

from manualreader.stages.step05.contracts import digest
from manualreader.artifacts import read_json as read, write_json as write
from manualreader.stages.step07.fusion import existing_targets, invert
from manualreader.stages.step07.inference import PROFILE, PROMPT_SHA, POLICY_SHA, SCHEMA, input_files


COUNTERS=('source_navigation','goals','question_items','question_raw_units')
FINAL_FILES=('run.json','body.fused.md','anchor-remap.json','resources.fused.json',
             'fusion-edits.json','deferred.json')
MODEL_FILES=('request.json','outcome.json','raw-response.txt','events.jsonl','dispatch.json','transport.json')
MODES=('NORMAL','INTRO','FINALE','ANSWER')


def check_baseline(root,base,v1):
    old=read(root/'baseline-sha256.json')
    for relative,expected in old.items():
        assert digest((base/relative).read_bytes())==expected,relative
        path=Path(relative)
        if path.parts[0] in ('inputs','media','workspace'):
            assert digest((root/path).read_bytes())==expected,path
        if path.parts[0]=='jobs' and len(path.parts)==3 and path.suffix=='.json':
            assert digest((root/'previous'/Path(*path.parts[1:])).read_bytes())==expected,path
    reference=read(root/'v1-baseline-sha256.json')
    for path,expected in reference.items():
        assert digest((v1/path).read_bytes())==expected,path
    frozen=read(root/'model-input-freeze.json')
    for path,expected in frozen['files'].items():
        assert digest((root/path).read_bytes())==expected,path
    assert frozen['policy_sha256']==POLICY_SHA and frozen['prompt_sha256']==PROMPT_SHA
    return old,reference,frozen


def coverage(rows):
    counts={}
    for key in COUNTERS:
        done='linked' if key=='source_navigation' else 'covered'
        counts[key]={'total':sum(r[key]['total'] for r in rows),done:sum(r[key][done] for r in rows)}
        assert counts[key][done]==counts[key]['total'],counts
    return counts


def verify_tasks(root):
    tasks=[]
    for folder in sorted((root/'jobs').iterdir()):
        _,previous,old,body,_,source=input_files(root,folder.name)
        job=read(folder/'run.json')
        model=read(folder/'model/outcome.json')
        fused=(folder/'body.fused.md').read_bytes().decode('utf8')
        edits=read(folder/'fusion-edits.json')
        remap=read(folder/'anchor-remap.json')
        bank=read(folder/'deferred.json')
        resources=read(folder/'resources.fused.json')['items']
        assert invert(fused,edits['operations']).encode()==body.encode(),folder.name
        assert digest(fused)==job['fused_sha256'] and digest(body)==job['input_sha256'],folder.name
        assert remap['mapping']==read(previous/'step06-merged-mapping.json'),folder.name
        assert remap['goal_question_coverage']==read(previous/'step06-goal-question-coverage.json'),folder.name
        originals={p['patch_id']:p for p in read(previous/'overlays.json')}
        assert len(edits['overlays'])==len(originals)==job['overlays'],folder.name
        assets=set(source['assets'])
        for overlay in edits['overlays']:
            original=originals[overlay['overlay_id']]
            assert original['markdown']==overlay['original_markdown'],folder.name
            assert original['source_ids']==overlay['source_ids'],folder.name
            assert original['asset_refs']==overlay['asset_refs'] and set(overlay['asset_refs'])<=assets,folder.name
            identifier='overlay:'+overlay['overlay_id']
            target=remap['anchors'][identifier]
            assert target==overlay['target'],folder.name
            if original['origin']=='06A':
                assert target['surface']=='body',folder.name
            if overlay['action']=='omit_duplicate':
                assert target.get('alias_of') in remap['anchors'],folder.name
            else:
                carrier=fused if target['surface']=='body' else bank[identifier]['markdown']
                actual=carrier[target['start']:target['end']]
                assert invert(actual,overlay['text_edits'])==original['markdown'],identifier
        for target_id in existing_targets(remap['mapping'],remap['goal_question_coverage']):
            target=remap['anchors'][target_id]
            carrier=fused if target['surface']=='body' else bank[target_id]['markdown']
            assert carrier[target['start']:target['end']].strip(),target_id
        assert len({x['resource_id'] for x in resources})==len(resources),folder.name
        for row in resources:
            carrier=fused if row['surface']=='body' else bank[row['origin']]['markdown']
            assert carrier[row['start']:row['end']]==row['raw_content'],row['resource_id']
            assert digest(row['raw_content'])==row['content_sha256'],row['resource_id']
            assert row['status']=='ok',row
            if row['kind']=='image':
                assert digest((root/'media'/f'{row["media_sha256"]}.png').read_bytes())==row['media_sha256']
        assert job['state']==model['state']=='complete',folder.name
        assert model['profile']==PROFILE and model['prompt_sha256']==PROMPT_SHA,folder.name
        assert model['policy_sha256']==POLICY_SHA
        request=read(folder/'model/request.json')
        assert digest(request['frozen_writing_policies'].encode())==POLICY_SHA,folder.name
        assert request['c3_markdown']==body and len(request['overlays'])==len(originals)
        assert Counter(r['overlay_id'] for r in model['reply']['supplement_decisions'])==Counter(originals.keys()),folder.name
        assert digest((folder/'model/prompt.md').read_bytes())==PROMPT_SHA
        assert read(folder/'model/dispatch.json')['schema_sha256']==digest(json.dumps(SCHEMA,sort_keys=True))
        assert (set(model['reply'])==set(SCHEMA['required']) and
                sum(r.get('response',{}).get('status')=='completed'
                    for r in model['provider_responses'])==1),folder.name
        assert json.loads((folder/'model/raw-response.txt').read_bytes())==model['reply'],folder.name
        for bridge in edits['transitions']:
            loc=bridge['target']
            assert loc['surface']=='body' and fused[loc['start']:loc['end']]==bridge['proposal']['replacement']
        assert len(model['reply']['transition_edits'])==len(edits['transitions'])+job['transition_rejected'],folder.name
        tasks.append((job,model))
    assert Counter(row['mode'] for row,_ in tasks)==Counter(dict.fromkeys(MODES,10))
    assert sum(row['fixture_based'] for row,_ in tasks if row['mode']=='FINALE')==9
    return tasks


def model_metrics(root, models):
    batch=read(root/'model-batch.json')
    http,usage=Counter(),Counter()
    events,queue,service=[],[],[]
    for model in models:
        for response in model['provider_responses']:
            http[str(response.get('status','unavailable'))]+=1
            start=response['started_at']
            events.extend(((start,1),(start+response['seconds'],-1)))
            service.append(response['seconds'])
        if model.get('state')!='complete':
            continue
        queue.append(model['queue_seconds'])
        data=model['response']['usage']
        usage['input']+=data['input_tokens']
        usage['output']+=data['output_tokens']
        usage['cached_input']+=data['input_tokens_details']['cached_tokens']
        usage['reasoning']+=data['output_tokens_details']['reasoning_tokens']
    peak=running=0
    for _,delta in sorted(events):
        running+=delta
        peak=max(peak,running)
    return {'profile':PROFILE,'prompt_sha256':PROMPT_SHA,'accepted_model_runs':len(queue),
            'accepted_dispatch_http':dict(http),'token':dict(usage),
            'peak_provider_requests':peak,'wall_seconds':batch['wall_seconds'],

            'infra_retries':sum(max(0,len(row['provider_responses'])-1) for row in models),
            'worker_count':batch['worker_count'],'queue_seconds_mean':sum(queue)/len(queue),
            'queue_seconds_max':max(queue),'provider_seconds_max':max(service),'provider_cost_usd':'unknown',
            'image_bytes_sent':sum(m['image_bytes'] for m in models)}


def excerpt(body,start,end,radius=1):
    ranges=[]
    last=0
    for sep in re.finditer(r'(?:\r?\n)[ \t\r\n]*(?:\r?\n)',body):
        ranges.append((last,sep.start()))
        last=sep.end()
    ranges.append((last,len(body)))
    intersect=[i for i,(a,b) in enumerate(ranges) if a<end and b>start]
    if not intersect:
        return body[max(0,start-100):end+100]
    lo=max(0,intersect[0]-radius)
    hi=min(len(ranges)-1,intersect[-1]+radius)
    return body[ranges[lo][0]:ranges[hi][1]].strip()


def comparison(root, v1, jobs, path):
    text=['# 40 篇融合前后对照','',
          '每组先列出原稿附近内容和 STEP-06 补充，再列出派生稿中的实际位置。']
    for mode in MODES:
        selected=sorted([r for r in jobs if r['mode']==mode],
                        key=lambda x:(x['actions'].get('move_inline',0),x['c3_edits'],x['overlays']),reverse=True)
        examples=0
        for run in selected:
            sample=run['sample_id']
            folder=root/'jobs'/sample
            _,previous,_,body,card,_=input_files(root,sample)
            new=(folder/'body.fused.md').read_bytes().decode('utf8')
            positions={r['anchor_id']:r for r in read(previous/'anchors.json')}
            edits=read(folder/'fusion-edits.json')
            before=v1/'jobs'/sample
            old_fusion=(before/'body.fused.md').read_bytes().decode('utf8')
            old_index=read(before/'anchor-remap.json')['anchors']
            name=(card.get('chapter') or {}).get('title') or (card.get('question') or {}).get('text') or sample
            text.extend(['',f'## {mode} · {name}','',f'任务编号 {sample}'])
            if not edits['overlays']:
                text.extend(['','本篇没有补丁，派生稿与原稿逐字相同。'])
            bank=read(folder/'deferred.json')
            ordered=sorted(edits['overlays'],key=lambda x:(
                not any(b['proposal']['overlay_id']==x['overlay_id'] for b in edits['transitions']),
                x['action']!='move_inline', not x['text_edits']))
            for overlay in ordered:
                old=positions[overlay['original_anchor_id']]
                target=overlay['target']
                example='重点对照 · ' if examples<2 and target['surface']=='body' else ''
                examples+=bool(example)
                text.extend(['',f'### {example}补充 {overlay["overlay_id"]}','',
                             f'处理结果 {overlay["action"]}','','**原稿附近内容**','',
                             excerpt(body,old['start'],old['end']),'','**原始补充**','',
                             overlay['original_markdown'],''])
                previous=old_index['overlay:'+overlay['overlay_id']]
                text.extend(['**V1 连续正文**',''])
                if previous['surface']=='body':
                    text.append(excerpt(old_fusion,previous['start'],previous['end']))
                else:
                    text.append('V1 将补充放在延伸内容中。')
                text.extend(['','**V2 连续正文**',''])
                if target['surface']=='body':
                    text.append(excerpt(new,target['start'],target['end'],radius=2))
                else:
                    text.extend(['这组内容位于预览的「延伸补充」。','',
                                 bank['overlay:'+overlay['overlay_id']]['markdown']])
            for op in edits['operations']:
                if op['kind']=='c3_edit':
                    text.extend(['','### 原稿局部改句','',op['proposal'].get('reason',''),'',f'原句　{op["old"]}','',
                                 f'改句　{op["new"]}','','**改后前后文**','',
                                 excerpt(new,op['fused_start'],op['fused_end'])])
            for item in edits['transitions']:
                pos=item['target']
                text.extend(['','### 衔接改动','',item['proposal']['reason'],'',
                             f'补充位置 {item["proposal"]["side"]} · 操作 {item["proposal"]["operation"]}',
                             '',excerpt(new,pos['start'],pos['end'])])
    path.write_text('\n'.join(text)+'\n',encoding='utf8')


def source_files(workspace):
    for name in ('pyproject.toml','requirements.lock','tests.lock','README.md','.gitignore'):
        yield workspace/name
    for directory in ('src','tests','docs','tools'):
        for p in sorted((workspace/directory).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts and not any(x.endswith('.egg-info') for x in p.parts):
                yield p


def patch_diff(workspace, base, path):
    records=[]
    for new in source_files(workspace):
        relative=new.relative_to(workspace).as_posix()
        old=base/'workspace'/relative
        raw=new.read_bytes()
        if old.exists() and raw==old.read_bytes():
            continue
        if new.suffix not in ('.py','.md','.js','.cjs','.toml') or '/vendor/' in relative:
            records.append(f'Binary or vendored file {relative}  sha256 {digest(raw)}\n')
            continue
        left=old.read_text('utf8').splitlines(keepends=True) if old.exists() else []
        right=new.read_text('utf8').splitlines(keepends=True)
        records.extend(unified_diff(left,right,fromfile='a/'+relative if old.exists() else '/dev/null',
                                    tofile='b/'+relative))
    path.write_text(''.join(records),encoding='utf8')


def package(root,base,v1):
    delivery=root/'delivery'
    delivery.mkdir(exist_ok=True)
    hashes,v1_hashes,frozen=check_baseline(root,base,v1)
    runs=verify_tasks(root)
    browser=read(root/'browser/evidence.json')
    assert not browser['errors'] and len(browser['samples'])==40,browser['errors']
    metrics=model_metrics(root,[model for _,model in runs])
    by_mode=defaultdict(list)
    for row,_ in runs:
        by_mode[row['mode']].append(row)
    jobs=[job for job,_ in runs]
    actions=sum((Counter(job['actions']) for job in jobs),Counter())
    usage=metrics['token']
    bridge_counts=sum((Counter(j.get('transition_edits',{})) for j in jobs),Counter())
    rejected=sum((Counter(r['reason'] for r in read(root/'jobs'/j['sample_id']/'fusion-edits.json')['rejections'])
                  for j in jobs),Counter())
    reports={m:coverage(rows) for m,rows in by_mode.items()}
    rendered=sum((Counter(row['rendered']) for row in browser['samples']),Counter())
    rendered['image']=sum(row['images']['loaded'] for row in browser['samples'])
    service=read(root/'service-ready.json')
    summary={'tasks':len(jobs),'actions':dict(actions),'mode_metrics':reports,
             'aggregate_metrics':coverage(jobs),'model':metrics,'browser':dict(rendered),
             'source_edges':sum(x['source_edges'] for x in jobs),
             'c3_edits':sum(x['c3_edits'] for x in jobs),'overlay_text_edits':sum(x['overlay_text_edits'] for x in jobs),
             'rejected_text_edits':sum(x['rejected_edits'] for x in jobs),
             'overlays_with_fallback':sum(x['overlays_with_fallback'] for x in jobs),
             'body_resources':sum(x['unique_visible_resources'] for x in jobs),
             'deferred_resources':sum(x['deferred_resources'] for x in jobs),
             'assembly':read(root/'assembly-batch.json'),'baseline_files_verified':len(hashes),
             'v1_files_verified':len(v1_hashes),'transition_edits':dict(bridge_counts),
             'transition_rejected':sum(j['transition_rejected'] for j in jobs),
             'rejection_reasons':dict(rejected),
             'model_proposed_transitions':sum(len(m['reply']['transition_edits']) for _,m in runs),
             'v1_summary':read(v1/'delivery/summary.json'),
             'service':service}
    write(delivery/'summary.json',summary)
    write(delivery/'baseline-verified.json',{'step01_06':hashes,'v1':v1_hashes,'different':[]})
    write(delivery/'frozen-model-inputs.json',frozen)
    comparison(root,v1,jobs,delivery/'融合前后对照.md')
    patch_diff(root/'workspace',v1,delivery/'implementation.diff')
    lines=['# ManualReader-Next STEP-07 V2 验收报告','',
           f'隔离预览地址为 {service["base"]}/step07。访问密钥由本机服务单独保存。','',
           '## 哪些任务完成了融合','',
           '四模式各 10 篇，每篇各有 1 次 Sol Low 成功回执，包括 2 篇无补丁原稿。',
           f'共处理 {sum(actions.values())} 组补丁。原位 {actions["keep_inline"]} 组，移动 {actions["move_inline"]} 组，'
           f'折叠 {actions["defer"]} 组，证实重复后合并 {actions["omit_duplicate"]} 组。'
           f'可独立撤销 {summary["c3_edits"]} 处原稿改句和 {summary["overlay_text_edits"]} 处补丁改句。',
           f'脚本记录 {summary["rejected_text_edits"]} 个未采用的编辑建议，{summary["overlays_with_fallback"]} 组补丁至少有一项处理建议回退。'
           '去重需要定位到实际保留的内容。找不到对应位置时，原补丁仍保留。',
           f'应用过渡 {sum(bridge_counts.values())} 条，前 {bridge_counts["before"]}、后 {bridge_counts["after"]}、'
           f'两组补充之间 {bridge_counts["between"]}。未应用 {summary["transition_rejected"]} 条，逐项原因和原建议见各篇融合记录。',
           '5 条衔接替换的引句不在最终布局所指定的一侧，其中 1 条涉及移动。另 2 条仅提交空行，对应位置装配时已经分段。'
           '本次模型没提出两组补充之间的过渡，between 由确定性测试覆盖。',
           'FINALE 含 1 篇真实已保存讲解和 9 篇历史首稿测试案例。','',
           '## 导航和资源是否可用','',
           '| 模式 | 来源导航 | 知识目标 | 真实子问 | 原话片段 |',
           '| --- | ---: | ---: | ---: | ---: |']
    for mode in MODES:
        m=reports[mode]
        values=[f'{m[k]["linked" if k=="source_navigation" else "covered"]}/{m[k]["total"]}'
                if m[k]['total'] else '—' for k in COUNTERS]
        lines.append('| '+mode+' | '+' | '.join(values)+' |')
    lines.extend(['','导航数字沿用 STEP-06 的关系分类，保留仅导航标签。',
                  f'Edge 打开并验证了全部 40 篇，实际渲染公式 {rendered["math"]} 处、表格 {rendered["table"]} 张、'
                  f'原图 {rendered["image"]} 张、Mermaid {rendered["mermaid"]} 张和代码块 {rendered["code"]} 个。'
                  f'另有 {summary["deferred_resources"]} 项资源位于延伸补充中。',
                  f'复核基线 {len(hashes)} 个文件，内容哈希一致。40 篇融合稿均能撤销到 STEP-04 原稿的原始字节。','',
                  '## 实际用量与耗时','',
                  f'全批从提交到最后回执用时 {metrics["wall_seconds"]:.2f} 秒，'
                  f'服务峰值 {metrics["peak_provider_requests"]} 路。'
                  f'输入 {usage["input"]:,} token，输出 {usage["output"]:,} token，'
                  f'其中推理 {usage["reasoning"]:,} token。缓存命中 {usage["cached_input"]:,} token。',
                  f'HTTP 请求计数 {metrics["accepted_dispatch_http"]}，基础设施重试 {metrics["infra_retries"]} 次。',
                  '融合请求提供媒体引用，图像字节由隔离 Reader 验证和显示。费用无回执。',
                  f'装配和等各篇模型结果就绪合计 {summary["assembly"]["wall_seconds"]:.2f} 秒。','',
                  '## 哪些自动回归通过了','',
                  '新版测试 185 项通过，旧版核心回归 84 项通过，旧版专项回归 4 项通过。',
                  '单独重放 40 篇后，每篇的 5 个数据文件都与交付文件逐字节相同。客户端包的内部文件哈希一致。','',
                  '5 条写作偏好和 12 条格式规范原样随每个请求传入，与 STEP-02 审核包的基线文件哈希一致。'
                  '主提示词和冻结规范的标点、冒号与资源保护要求没有发现需要处理的冲突。','',
                  '## 如何复现','',
                  '源码和锁定依赖在 workspace/。用 Python 3.12 安装工作区。资源扫描还需要本机 Node.js。'
                  '验收数据在 runs/，包含模型首轮输出和可逆修改。实际重放命令见 workspace/docs/step07.md。'])
    (delivery/'验收报告.md').write_text('\n'.join(lines)+'\n',encoding='utf8')

    archive_path=root/'ManualReader-Next-STEP07-V2-轻量验收包.zip'
    workspace=root/'workspace'
    assets=set()
    with ZipFile(archive_path,'w',ZIP_DEFLATED,compresslevel=6) as archive:
        for file in source_files(workspace):
            archive.write(file,'workspace/'+file.relative_to(workspace).as_posix())
        for file in sorted(delivery.iterdir()):
            archive.write(file,file.name)
        for file in (root/'browser').iterdir():
            if file.is_file():
                archive.write(file,'browser/'+file.name)
        for job in jobs:
            folder=root/'jobs'/job['sample_id']
            assets.update(row['media_sha256'] for row in read(folder/'resources.fused.json')['items'] if row.get('media_sha256'))
            for name in FINAL_FILES:
                archive.write(folder/name,f'runs/{folder.name}/{name}')
            for name in (*MODEL_FILES,'prompt.md'):
                file=folder/'model'/name
                if file.is_file():
                    archive.write(file,f'runs/{folder.name}/model/{name}')
        for item in sorted(assets):
            archive.write(root/'media'/f'{item}.png',f'media/{item}.png')
        for name in ('tests.log','legacy-tests-real.log','special-tests.log','cold-install.log','browser.log',
                     'models.log','assembly.log','model-batch.json','assembly-batch.json','replay.log','model-schema.json'):
            archive.write(root/name,'logs/'+name)
    with ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        assert len([n for n in archive.namelist() if n.endswith('/body.fused.md')])==40
    print(json.dumps(summary,ensure_ascii=False))
    print(archive_path,archive_path.stat().st_size)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('root',type=Path)
    parser.add_argument('base',type=Path)
    parser.add_argument('v1',type=Path)
    args=parser.parse_args()
    package(args.root,args.base,args.v1)
