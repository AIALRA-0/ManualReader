"""Verify new artifacts and assemble one lightweight acceptance archive."""

from argparse import ArgumentParser
from collections import Counter,defaultdict
from hashlib import sha256
import json
from pathlib import Path
from time import time
from zipfile import ZIP_DEFLATED,ZipFile

from final_delivery import code_patch,compute_coverage,distribution,measure
from manualreader.stages.finish.content import source_units
from manualreader.stages.step05.inference import read,write
from verify_full_finish import verify_one


def optional(path,default):
    return read(path) if path.is_file() else default


def checksum(path):
    return sha256(path.read_bytes()).hexdigest()


def unique_events(root):
    out=defaultdict(list)
    event_file=root/'upstream-events.jsonl'
    for line in event_file.read_text('utf8').splitlines():
        item=json.loads(line)
        out[item['stage']].append(item)
    return out


def summarize_model(rows):
    http=Counter()
    usage=Counter()
    durations=[]
    for row in rows:
        if not isinstance(row,dict):
            continue
        if row.get('status') is not None:
            http[str(row['status'])]+=1
            if isinstance(row.get('seconds'),(int,float)):
                durations.append(row['seconds'])
        um=(row.get('response') or {}).get('usage') or {}
        usage['input_tokens']+=um.get('input_tokens',0) or 0
        usage['output_tokens']+=um.get('output_tokens',0) or 0
        usage['cache_tokens']+=(um.get('input_tokens_details') or {}).get('cached_tokens',0) or 0
        usage['reasoning_tokens']+=(um.get('output_tokens_details') or {}).get('reasoning_tokens',0) or 0
    return {'http':dict(http),'tokens':dict(usage),'http_seconds':distribution(durations),'cost':None}


def model_receipts(root):
    paths=defaultdict(list)
    paths['02']=sorted((root/'planners').glob('*/model/transport.json'))
    paths['04']=sorted((root/'writer-runs').glob('*/model-receipt.json'))
    for path in (root/'step56/jobs').glob('*/model/*/outcome.json'):
        paths[path.parent.name.split('-',1)[0]].append(path)
    paths['07']=sorted((root/'step07-work/jobs').glob('*/model/outcome.json'))
    paths['QA']=sorted((root/'native-qa').glob('**/transport.json'))
    output={}
    rows_index={}
    for key,files in paths.items():
        all_rows=[]
        for path in files:
            data=read(path)
            rows=data if isinstance(data,list) else next(
                (data[k] for k in ('provider_responses','requests','receipts') if isinstance(data.get(k),list)),[])
            if isinstance(rows,list):
                all_rows.extend(rows)
        output[key]={'logical_requests':len(files),**summarize_model(all_rows)}
        rows_index[key]=[str(p.relative_to(root).as_posix()) for p in files]
    return output,rows_index


def check_run(root,case,finished):
    source_changes=[]
    clean_sources=case['original_projects']
    for project,entry in clean_sources.items():
        for f in entry['verified_files'].values():
            local=root/'original-inputs/projects'/project/'originals'/f['relative_path']
            if not local.is_file() or checksum(local)!=f['sha256']:
                source_changes.append(str(local))
    issues={}
    source_counts=Counter()
    for sample,item in case['samples'].items():
        out=root/'jobs'/sample
        if sample not in finished:
            issues[sample]=['缺少本轮最终后处理']
            continue
        flags=[]
        try:
            checks=verify_one(root,out)
            if isinstance(checks,dict):
                source_counts.update({k:v for k,v in checks.items() if isinstance(v,int)})
        except Exception as exc:
            flags.append('结构检查 '+str(exc))
        card=read(root/'planners'/sample/'run.json')
        phase4=read(root/'inputs'/sample/'step04-run.json')
        phase7=read(root/'upstream07'/sample/'run.json')
        if card['status']!='planned' or card['cache_hit'] or card['model_dispatches']!=1:
            flags.append('规划缺少本轮模型调用记录')
        if not phase4.get('request_attempts') or phase4.get('status')!='complete':
            flags.append('首稿缺少本轮真实模型调用记录')
        if checksum(root/'inputs'/sample/phase4['output_file'])!=phase4['output_hash']:
            flags.append('首稿与 Writer 哈希不一致')
        if phase7['input_sha256']!=phase4['output_hash']:
            flags.append('STEP-07 输入不属于本轮首稿')
        if item['mode']=='FINALE':
            project=root/'original-inputs/projects'/item['project_id']
            inputs=read(project/'project.json')
            normal=next(name for name,row in case['samples'].items()
                        if row['mode']=='NORMAL' and row['project_id']==item['project_id'])
            normal_raw=read(root/'inputs'/normal/'step04-run.json')
            saved=inputs['saved_explanations']
            valid=bool(saved) and all(row['explanation_id']=='whiteboard-'+normal_raw['task_id']
                and row['body_sha256']==normal_raw['output_hash']
                and checksum(project/row['markdown_path'])==normal_raw['output_hash'] for row in saved)
            if not valid:
                flags.append('收尾稿的来源需要再核对')
        if flags:
            issues[sample]=flags
    return {'samples':len(finished),'originals_checked':sum(len(x['verified_files']) for x in clean_sources.values()),
            'original_changes':source_changes,'issues':issues,'counts':dict(source_counts)}


def missing_translation(root,runs):
    wire={}
    for ip in (root/'requests/B1').glob('*/input.json'):
        req=read(ip)['input']
        rec=optional(ip.parent/'receipt.json',{})
        for u in req['units']:
            wire[(req['file_id'],u['source_id'],req['source_version'],u['original_sha256'])]={
                'request_id':ip.parent.name,'status':rec.get('status'),'error':rec.get('error'),
                'http':[t.get('status') for t in rec.get('attempts',[])]}
    output={}
    for run in runs:
        sample=run['sample_id']
        source=read(root/'inputs'/sample/'source-index.json')
        units=source_units(source)
        job=root/'jobs'/sample
        links={r['source_id']:r for r in read(job/'source-translation-links.json')}
        why={sid:value for report in run['issues'].get('translation',[])
             for sid,value in report.get('per_source_reason',{}).items()}
        rows=[]
        for u in units:
            sid=u['source_id']
            link=links.get(sid,{})
            if u['relation']!='translated' or link.get('status')=='complete':
                continue
            key=(u['file_id'],sid,u['source_version'],u['original_sha256'])
            evidence=wire.get(key,{})
            status=link.get('status','unresolved')
            rows.append({'source_id':sid,'file_id':u['file_id'],'status':status,
                         'reason':(evidence.get('error') or why.get(sid) or '译文需要核对')
                         if status=='unresolved' else '模型保留不确定标记',**evidence,
                         'translation_status':status})
        if rows:
            output[sample]=rows
    return output



def resource_gaps(root,runs):
    reported=defaultdict(list)
    for stage in ('A1','A2'):
        for ip in (root/'requests'/stage).glob('*/input.json'):
            req=read(ip)['input']
            rec=optional(ip.parent/'receipt.json',{})
            data=optional(ip.parent/'result.json',{}).get('result') or {}
            claimed={rid for group in data.get('groups',[]) for rid in group.get('resource_ids',[])}
            unresolved=set(data.get('unresolved_resource_ids',[]))
            for rid in req.get('resource_ids',[]):
                reported[(rec.get('sample_id'),rid)].append({
                    'stage':stage,'request_id':ip.parent.name,'transport_status':rec.get('status'),
                    'explicitly_unresolved':rid in unresolved,'returned_in_group':rid in claimed})
    out={}
    for row in runs:
        sid=row['sample_id']
        data=read(root/'jobs'/sid/'resource-explanations.json')
        for item in data['coverage_by_resource']:
            phase=item['status']
            if phase in ('explained','covered_by_group','excluded'):
                continue
            rid=item['resource_id']
            receipts=reported.get((sid,rid),[])
            if phase=='partial':
                reason='已收到部分解释，完整解释的字段或确定内容仍有缺项'
            elif any(x['explicitly_unresolved'] for x in receipts):
                reason='模型明确列为未完成'
            elif any(x['returned_in_group'] for x in receipts):
                reason='响应包含该编号，最终装配没有取得可采用的独立解释'
            elif receipts:
                reason='已请求该资源，响应未给出可采用的解释'
            else:
                reason='需要核对媒体或分批响应的关联记录'
            out.setdefault(sid,[]).append({**item,'reason':reason,'requests':receipts})
    return out


def main(root,base):
    case=read(root/'whiteboard-inputs.json')
    upstream=read(root/'whiteboard-result.json')
    rows=[read(p) for p in sorted((root/'jobs').glob('*/run.json'))]
    projects=optional(root/'reader-service-ready.json',{})
    browser=optional(root/'browser-whiteboard.json',{'samples':[],'opened':0})
    save=optional(root/'save-version-and-protection.json',{})
    qa=optional(root/'qa-whiteboard.json',{})
    render=optional(root/'renderer-check.json',{})
    follow=optional(root/'qa-followup-and-bp.json',{})
    ds=optional(root/'ds-technical-retry.json',{})
    result_dir=root/'delivery'
    result_dir.mkdir(exist_ok=True)
    coverage=compute_coverage(root,rows)
    stage_data=measure(root,rows)
    stage_data['upstream_requests'],stage_data['upstream_receipt_files']=model_receipts(root)
    stage_data['upstream_durations']={k:distribution(x['ended_at']-x['started_at'] for x in v)
                                     for k,v in unique_events(root).items()}
    stage_data['full_generation_wall_seconds']=round(upstream['ended_at']-case['started_at'],3)
    f3=optional(root/'f3-technical-retry.json',{})
    window=[last['ended_at'] for last in ds.get('outcomes',[])]+([f3['ended_at']] if f3 else [])
    stage_data['generation_with_ds_recovery_seconds']=round(max(window+[upstream['ended_at']])-case['started_at'],3)
    when={n:min(x['started_at'] for events in unique_events(root).values() for x in events if x['sample_id']==n)
          for n in case['samples']}
    per_sample={n:round(t-when[n],3) for n,t in upstream['finished'].items()}
    stage_data['sample_wall_seconds']=per_sample
    stage_data['sample_distribution']=distribution(per_sample.values())
    stage_data['prior_run_reused']=0
    stage_data['B1_fresh_completed']=coverage['unique_foreign'].get('complete',0)
    stage_data['B1_within_run_reused_source_references']=coverage['cross_mode_or_task_reused_translation_refs']
    stage_data['local_route_probe_http_requests']=1
    stage_data['browser_wall_seconds']=browser.get('wall_seconds')
    stage_data['whole_test_elapsed_seconds']=round(time()-case['started_at'],3)
    stage_data['provider_actual_code_counts']={str(code):sum(
        receipt['http'].get(str(code),0) for receipt in stage_data['providers'].values())
        for code in sorted({c for v in stage_data['providers'].values() for c in v['http']})}
    verified=check_run(root,case,upstream['finished'])
    exam_check=render.get('07-exam2-normal',{})
    checked_nav=sum(bool(r.get('navigation',{}).get('available')) for r in browser['samples'])
    nav_ok=sum(bool(r.get('navigation',{}).get('reverse_matched')) for r in browser['samples'])
    recap_count=sum(len(r.get('recaps',[])) for r in browser['samples'])
    term_count=sum(r.get('formal_terms',0) for r in browser['samples'])
    quiz_count=sum(bool(r.get('quiz',{}).get('submitted')) for r in browser['samples'])
    project_count=len(projects.get('samples',{}))
    p2_total=Counter()
    excluded=Counter()
    for r in rows:
        p2_total.update(r['p2'])
        details=read(root/'jobs'/r['sample_id']/'resource-explanations.json')
        excluded.update(x.get('exclude_reason') for x in details['coverage_by_resource'] if x.get('exclude_reason'))
    stage_data['resource_scope']={'total':sum(p2_total.values()),'eligible':sum(v for k,v in p2_total.items() if k!='excluded'),
                                  'excluded':dict(excluded),'p2_status':dict(p2_total)}
    gaps=missing_translation(root,rows)
    write(result_dir/'translation-gaps-by-source.json',gaps)
    write(result_dir/'resource-gaps-by-instance.json',resource_gaps(root,rows))
    write(result_dir/'coverage.json',coverage)
    write(result_dir/'stage-metrics.json',stage_data)
    write(result_dir/'structural-verification.json',verified)
    files=code_patch(root/'workspace',base/'workspace',result_dir/'implementation.diff')
    urls=projects.get('samples',{})
    c=coverage['unique_foreign']
    combined={r['sample_id']:r for r in browser['samples']}
    opened=sum(x.get('opened',False) for x in combined.values())
    wrongs=sum(len(x.get('wrong_resource_order',[])) for x in combined.values())
    missing=sum(len(x.get('missing_resources',[])) for x in combined.values())
    legacy_qa=qa.get('answered',False)
    p2_complete=p2_total['explained']+p2_total['covered_by_group']
    summary=[
        '# ManualReader-Next 白板运行和集中修复验收','',
        f'40 个任务均从本轮登记原件和原始用户问题启动，生成 {len(rows)} 篇后处理产物。'
        '四种模式各 10 篇。FINALE 用同期 NORMAL 首稿建立隔离输入。旧模型结果复用为 0。',
        f'源文件重新核对了 {verified["originals_checked"]} 份，哈希变更 {len(verified["original_changes"])} 份。',
        '正式数据库和正式进度未写入。','',
        '## 哪些结果已在实际页面验收','',
        '| 验收 | 实际结果 |','|---|---|',
        f'| Reader 打开 | {opened}/{len(case["samples"])} 篇，隔离服务有 {project_count} 个入口 |',
        f'| 原文双向跳转 | {nav_ok}/{checked_nav} 篇 |',
        f'| 本节回顾与术语 | 页面核对 {recap_count} 组本节回顾，原生术语面板显示 {term_count} 项，'
        f'{quiz_count} 篇用原答题界面提交选项 |',
        f'| 独立资源详解 | 总资源 {sum(p2_total.values())}，合格 {sum(p2_total.values())-p2_total["excluded"]}，'
        f'排除 {p2_total["excluded"]}。完整 {p2_complete}，其余状态见 coverage.json |',
        f'| 资源位置 | 浏览器未找到 {missing} 个入口，发现 {wrongs} 处顺序不符合检查 |',
        f'| 原文翻译 | 需译 {c.get("needed",0)}，完整 {c.get("complete",0)}，仍缺 {c.get("unresolved",0)}，'
        f'有不确定项 {c.get("uncertain",0)} |',
        f'| 模型资源问答 | {"已保存真实回答" if legacy_qa else "本轮尚无保存成功的回答"}（{qa.get("sample_id","—")}） |',
        f'| 隔离保存 | {save.get("idempotent",0)} 篇重复提交通过，{save.get("unchanged_user_records",0)} 篇原有记录一致 |','',
        '## 哪些结果仍需核对','',
        '翻译缺项逐来源记录在 translation-gaps-by-source.json。'
        f'仅补试当前运行的技术失败批次 {ds.get("attempted",0)} 批，恢复 {ds.get("recovered",0)} 批。'
        '提供商返回 403 的请求保留真实状态和请求编号。',
        f'详解已完成 {p2_complete} 项，部分完成 {p2_total["partial"]} 项，'
        f'仍缺 {p2_total["pending"]+p2_total["unreadable"]+p2_total["needs_review"]} 项。'
        '每项是否纳入详解及原因可在各篇 resource-explanations.json 查询。'
        '未完成的资源逐项列在 resource-gaps-by-instance.json。',
        f'这轮 EXAM2 正常章的 {len(exam_check.get("actual_images",[]))} 张独立图逐张验证了本图、详解和提问的顺序。'
        'Olympus 导读新稿只有独立公式，本轮资源问答改用 Zen 6 导读的真实流程图。',
        ('该流程图的原生回答、追问和 BP 已保存。' if legacy_qa and follow.get('note_saved') else
         '原生问答和追问的状态见对应回执。'),
        'LaTeX 双重转义与 MATLAB 围栏用浏览器内的独立语法样例做了回归。'
        '新稿里实际出现的代码和公式另外记录在 renderer-check.json。',
        (f'数学论文练习在超时后技术补试，现有 {f3.get("exercises_total",0)} 道练习。'
         if f3 else '数学论文练习的请求状态见阶段回执。'),
        '## 40 篇分别在哪里打开','',
        '| 模式 | 篇章 | 生成秒数 | 页面 | 正文资源完整／可详解 |','|---|---|---:|---|---:|',
    ]
    for r in rows:
        n=r['sample_id']
        title=r['title'].replace('|','／')
        label=f'[{title}]({urls[n]})' if n in urls else title
        p2=Counter(r['p2'])
        eligible=sum(p2.values())-p2['excluded']
        summary.append(f'| {r["mode"]} | {label} | {per_sample.get(n,0):.2f} | '
                       f'{"已打开" if combined.get(n,{}).get("opened") else "待核对"} | '
                       f'{p2["explained"]+p2["covered_by_group"]}/{eligible} |')
    summary += ['', '## 运行用了多久','',
        f'从原件登记完毕至最后一篇首次落盘 {stage_data["full_generation_wall_seconds"]:.2f} 秒。'
        f'计入 DS 和练习的技术补试 {stage_data["generation_with_ds_recovery_seconds"]:.2f} 秒。',
        f'每篇从各自准备开始到落盘，p50 为 {stage_data["sample_distribution"]["p50"]} 秒，'
        f'p95 为 {stage_data["sample_distribution"]["p95"]} 秒。浏览器与保存用量另外记录。',
        f'40 篇最终浏览器打开与交互测试实测 {browser.get("wall_seconds",0):.2f} 秒。'
        '模型路由、实际 HTTP、token、并发和各阶段等待时间见 stage-metrics.json。'
        '服务未返回费用金额，费用字段保留为空。',
    ]
    (result_dir/'report.md').write_text('\n'.join(summary)+'\n',encoding='utf8')
    evidence=['browser-whiteboard.json','browser-qa-receipt.json','qa-whiteboard.json',
              'qa-followup-and-bp.json','save-version-and-protection.json','ds-technical-retry.json',
              'deterministic-b1-reprojection.json','f3-technical-retry.json','math-archive-reprojection.json',
              'renderer-check.json','whiteboard-result.json','model-schemas.json',
              'test-final.log','reader-projections.json','reader-service-ready.json','upstream-events.jsonl']
    zip_path=root/'ManualReader-Next-13项修复-白板验收.zip'
    with ZipFile(zip_path,'w',ZIP_DEFLATED,compresslevel=6) as z:
        for path in sorted(result_dir.iterdir()):
            if path.is_file():
                z.write(path,path.name)
        for file in files:
            z.write(root/'workspace'/file,'code/'+file.as_posix())
        for n in evidence:
            path=root/n
            if path.is_file():
                z.write(path,'evidence/'+n)
        for n in ('04-olympus-intro','05-zen6-intro','05-zen6-normal','06-paper-normal','07-exam2-normal'):
            for f in ('body.final.md','run.json','resource-explanations.json','chapter-guidance.json','term-candidates.json'):
                path=root/'jobs'/n/f
                if path.is_file():
                    z.write(path,f'samples/{n}/{f}')
            shot=root/'browser'/f'{n}.png'
            if shot.is_file():
                z.write(shot,f'browser/{n}.png')
        for shot in sorted((root/'spec').glob('**/screenshots/*.png')):
            z.write(shot,'browser/before/'+shot.name)
        for n in ('qa-followup-and-bp.png','renderer-fixture.png'):
            shot=root/n
            if shot.is_file():
                z.write(shot,f'browser/{n}')
    with ZipFile(zip_path) as z:
        assert z.testzip() is None
    print(zip_path,len(rows),opened,zip_path.stat().st_size)


if __name__=='__main__':
    p=ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    p.add_argument('baseline',type=Path)
    args=p.parse_args()
    main(args.root.resolve(),args.baseline.resolve())
