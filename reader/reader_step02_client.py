"""Client entry for the four STEP-02 planning modes. No writer or save is called."""
from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import json
import os
from threading import RLock
from time import perf_counter

from reader_step01_client import MaterialRound
from reader_step02_card import HERE, assemble, digest, link_sources, model_schema, policies, prompt, render


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def load(path):
    return json.loads(path.read_bytes())


def original_file(call, file, hints, mode, trace):
    fid = file['file_id']
    begin = perf_counter()
    try:
        catalog = call('catalog', file_id=fid)
        positions = catalog['positions']
        rows, views, visuals = [], {}, {}
        next_index = 0
        while next_index < len(positions):
            start = positions[next_index]
            while next_index + 1 < len(positions) and positions[next_index+1] - start < 300:
                next_index += 1
            end = positions[next_index]
            next_index += 1
            while start is not None:
                response = call('read', file_id=fid, start=start, end=end)
                if response['content_sha256'] != file['content_sha256']:
                    raise ValueError('access_blocked: 原件返回的哈希不符合 M1')
                rr = response['rows']
                if not rr:
                    raise ValueError('access_blocked: 读取接口没有返回原件内容')
                for row in rr:
                    while len(row['text']) < row['text_chars']:
                        offset = len(row['text'])
                        extra = call('read', file_id=fid, start=row['n'], end=row['n'], char_offset=offset)
                        extra_row = extra['rows'][0]
                        if extra['content_sha256'] != file['content_sha256'] or not extra_row['text']:
                            raise ValueError('access_blocked: 原件位置无法继续读取')
                        row['text'] += extra_row['text']
                        trace({'file_id':fid,'content_sha256':file['content_sha256'],'file_revision':file['file_revision'],
                               'start':row['n'],'end':row['n'],'char_offset':offset,
                               'status':'read_from_original','via':'reader_http'})
                rows.extend(rr)
                trace({'file_id': fid, 'content_sha256': file['content_sha256'],
                       'file_revision': file['file_revision'], 'start': start, 'end': end,
                       'actual_positions': [r['n'] for r in rr], 'type': catalog['type'],
                       'status': 'read_from_original', 'via': 'reader_http'})
                start = response['next_start']
        if positions != [r['n'] for r in rows]:
            raise ValueError('access_blocked: 读取后的原件位置不完整')
        by_number = {r['n']: r for r in rows}
        hint_positions = set()
        for span in hints:
            if span['type'] == catalog['type']:
                hint_positions.update(r for r in positions if span['start'] <= r <= span['end'])
        if hint_positions and mode != 'INTRO':
            pad = 2 if catalog['type'] == 'pdf_page' else 8
            for n in hint_positions:
                for i in range(n-pad, n+pad+1):
                    if i in by_number:
                        views[i] = by_number[i]
        elif mode != 'ANSWER' or hints:
            # Spread direct excerpts across the complete file; the read tool can retrieve every position.
            reach = min(12, len(positions))
            width = 0 if catalog['type'] == 'pdf_page' else 4 if catalog['type'] == 'subtitle_sid' else 2
            for j in range(reach):
                ix = j * (len(positions)-1)//max(1,reach-1)
                for n in positions[max(0,ix-width):ix+width+1]:
                    views[n] = by_number[n]
        evidence = []
        if catalog['has_page_image'] and (hint_positions or mode=='INTRO'):
            if hint_positions:
                candidates = sorted(hint_positions)
            else:
                candidates = [positions[0], positions[len(positions)//2], positions[-1]]
            for n in sorted(set(candidates if len(candidates)<=8 else
                                    [candidates[j*(len(candidates)-1)//7] for j in range(8)])):
                response = call('read', file_id=fid, start=n, end=n, visual=True)
                if response['content_sha256'] != file['content_sha256']:
                    raise ValueError('access_blocked: 页面图像已不是本轮原件')
                visuals[str(n)] = response['image']
                evidence.append(n)
                trace({'file_id': fid, 'content_sha256': file['content_sha256'], 'file_revision': file['file_revision'],
                       'start': n, 'end': n, 'image_sha256': digest(b64decode(response['image'])),
                       'status': 'read_original_image', 'via': 'reader_http'})
        return ({**catalog, 'available_image_pages': evidence},
                {'type':catalog['type'],'rows':rows,'images':visuals,'content_sha256':file['content_sha256'],
                 'file_revision':file['file_revision']}, views)
    except Exception as error:
        trace({'file_id': fid, 'content_sha256': file['content_sha256'], 'file_revision': file['file_revision'],
               'status': 'access_blocked', 'detail': str(error), 'seconds': perf_counter()-begin, 'via': 'reader_http'})
        raise


def input_payload(context, catalog, examples, layout):
    mode = context['mode']
    files = []
    for source in context['files']:
        fid = source['file_id']
        if fid not in catalog:
            continue
        cat = catalog[fid]
        positions = cat['positions']
        files.append({'file_id': fid, 'name': source['relative_path'], 'type': cat['type'],
                      'first_position': positions[0], 'last_position': positions[-1],
                      'position_count': len(positions), 'time_bounds': cat.get('time_bounds'),
                      'headings': cat['headings'], 'hints': context['scope_hints'].get(fid, []),
                      'available_page_images': cat['available_image_pages'],
                      'original_excerpts': [{**r,'text':r['text'][:8000]} for r in examples[fid].values()]})
    # A real qid, source hash, or project version is never part of the model's answer input.
    data = {'original_files': files, 'visible_images': layout,
            'read_original': 'mcp__reader_original__read accepts requests with file_id, start, end, visual.',
            'unprocessed_previous_questions': [{'ref': row['ref'], 'text': row['text']}
                                               for row in context['unprocessed_questions']],
            'inherited_question_styles': [{k: row[k] for k in ('pattern','explanation_need')}
                                          for row in context['approved_styles']]}
    fixed = context['fixed']
    if fixed['custom_instruction']:
        data['custom_instruction'] = fixed['custom_instruction']
    if mode == 'ANSWER':
        data.update(question=fixed['question']['text'], context=fixed['context'])
    else:
        data.update(current_chapter={'title': fixed['chapter']['title'], 'number': fixed['chapter']['number']},
                    neighboring_chapters=[{'title': r['title'], 'number': r['number']} for r in context['directory']],
                    previous=fixed['previous'])
        if mode == 'FINALE':
            data.update(saved_explanations=[{'chapter': v['chapter'], 'blocks': v['blocks']}
                                           for v in context['saved_explanations']],
                        saved_source_ranges=context['allowed_ranges'], independent_manual_completion=fixed['progress']['lessons'])
    return data


def plan(bridge, out, mode, *, question_id=None, explicit_finale=False, custom_instruction=None,
         cache_dir=None, invoke=None):
    from reader_execute import WRITER_ROLE, codex_executable, invoke_writer
    from reader_writer_profiles import writer_profile
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('这轮输出目录已有文件，请使用新的空目录')
    out.mkdir(parents=True, exist_ok=True)
    timer = perf_counter()
    cfg = writer_profile('answer' if mode == 'ANSWER' else 'lesson')
    report = {'step': 'STEP-02', 'mode': mode, 'status': 'preparing', 'question_id': question_id,
              'model': {k: cfg[k] for k in ('model','effort','service_tier')},
              'model_dispatches': 0, 'cache_hit': False}
    try:
        system = prompt(mode)
        value = policies()
        report.update(prompt_sha256=digest(system), policy_sha256=digest((HERE/'reader_step02_policies.json').read_bytes()))
        (out/'planner-prompt.txt').write_bytes(system)
        (out/'writing-policies.json').write_bytes((HERE/'reader_step02_policies.json').read_bytes())
        with MaterialRound(bridge, out, mode) as lifecycle:
            options = {'question_id': question_id, 'explicit_finale': explicit_finale,
                       'custom_instruction': custom_instruction}
            def call(action, **fields):
                return bridge.request('/api/step02', {'action': action, 'round_id': lifecycle.rid, **fields})
            context = call('context', **options)
            save(out/'context-fixed.json', context)
            report['preview'] = context['preview']
            out_model = out/'model'
            out_model.mkdir()
            trace_path = out/'original_read_trace.jsonl'
            lock = RLock()
            def trace(event):
                with lock, trace_path.open('a',encoding='utf8') as stream:
                    stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            catalog, staging, examples, indexes, blocked = {}, {}, {}, {}, {}
            files = context['files']
            pool = ThreadPoolExecutor(max_workers=max(1,min(12,2*len(files))))
            try:
                reads, prewarm = {}, {}
                for file in files:
                    fid = file['file_id']
                    hints = context['scope_hints'].get(fid, [])
                    if context['allowed_selection'] is not None and fid not in context['allowed_selection']:
                        continue
                    reads[pool.submit(original_file, call, file, hints, mode, trace)] = fid
                    if hints:
                        prewarm[fid] = pool.submit(call, 'index', file_id=fid)
                for fut in as_completed(reads):
                    fid = reads[fut]
                    try:
                        catalog[fid], staging[fid], examples[fid] = fut.result()
                    except Exception as error:
                        blocked[fid] = str(error)
                if mode == 'INTRO':
                    blocked.update({f['file_id']: f.get('reason') or 'identity_pending'
                                    for f in lifecycle.start['manifest']['files'] if
                                    f['status']=='unavailable' or f['identity_pending']})
                report.update(original_catalog_count=len(catalog), blocked_originals=blocked,
                              original_units_read=sum(len(v['rows']) for v in staging.values()))
                if (mode=='INTRO' and blocked) or (mode!='ANSWER' and not catalog):
                    raise ValueError('access_blocked: 本轮材料未能按要求读取')
                # Only first-pass samples travel with the prompt; complete original text is available via the tool.
                image_paths, layout = [], []
                for fid in sorted(staging):
                    data = staging[fid]
                    for number, raw in sorted(data['images'].items(),key=lambda item:int(item[0])):
                        if len(image_paths) >= 12:
                            break
                        path = out_model / f'original-image-{len(image_paths)+1:02}.png'
                        path.write_bytes(b64decode(raw, validate=True))
                        image_paths.append(path)
                        layout.append({'file_id': fid, 'position': int(number), 'attachment': len(image_paths)})
                model_input = input_payload(context, catalog, examples, layout)
                schemas = model_schema(sorted(catalog), [q['ref'] for q in context['unprocessed_questions']], mode)
                save(out_model/'output-schema.json', schemas)
                save(out/'model-input.json', model_input)
                save(out/'original-catalog.json',catalog)
                staging_file = out_model/'original-store.json'
                save(staging_file, staging)
                model_text = json.dumps(model_input, ensure_ascii=False,separators=(',',':'))
                cache_key = digest([mode, question_id, context, sorted((f['file_id'],f['content_sha256']) for f in files),
                                    report['prompt_sha256'],report['policy_sha256'],report['model'],model_input,schemas])
                report['cache_key'] = cache_key
                cached = Path(cache_dir)/f'{cache_key}.json' if cache_dir else None
                elapsed = perf_counter()
                if cached and cached.is_file():
                    draft = load(cached)
                    report['cache_hit'] = True
                else:
                    exe = codex_executable() if invoke is None else 'test-injected'
                    if not exe:
                        raise ValueError('no_model_service: 找不到已登录的本机 Codex')
                    token = WRITER_ROLE.set(cfg['role'])
                    try:
                        report['model_dispatches'] = 1
                        raw, usage, _ = (invoke or invoke_writer)(
                            out_model, model_text, image_paths, exe, system_override=system.decode('utf8'),
                            research=False, allow_image_generation=False,
                            request_limit=8,
                            original_tool=(str(staging_file),str(out/'original_model_read_trace.jsonl')))
                        (out/'model-first-response.json').write_text(raw, encoding='utf8')
                        report['model_usage'] = usage
                        draft = json.loads(raw)
                    finally:
                        WRITER_ROLE.reset(token)
                        report['model_seconds'] = perf_counter()-elapsed
                card, check = assemble(context,catalog,draft)
                save(out/'task-card-before-source-link.json',card)
                material_ids = [x['file_id'] for x in card['material_plan']]
                # An ANSWER may use only the stored question context. It still checks files actually read by its source.
                if mode == 'ANSWER':
                    material_ids = sorted(set(material_ids).union(
                        f for f in context['scope_hints'] if any(x['file_id']==f for x in files)))
                lifecycle.select(material_ids)
                for item in card['material_plan']:
                    fid = item['file_id']
                    pending = prewarm.get(fid) or pool.submit(call,'index',file_id=fid)
                    try:
                        indexes[fid] = pending.result()
                    except Exception as error:
                        indexes[fid] = {'verified':False,'reason':str(error)}
                linked, bind = link_sources(card,indexes,catalog)
                save(out/'source-link-report.json',bind)
                m2 = lifecycle.reconcile()
                latest = call('context',**options)
                if (latest['state_fingerprint'] != context['state_fingerprint']
                        or latest['selection_identity'] != context['selection_identity']):
                    raise ValueError('本轮章节、问题或已保存内容发生变化，已保留首个规划结果')
                if cached and not report['cache_hit']:
                    cached.parent.mkdir(parents=True,exist_ok=True)
                    temporary = cached.with_name(cached.name + '.' + lifecycle.rid + '.tmp')
                    save(temporary,draft)
                    os.replace(temporary,cached)
                save(out/'task-card.json',linked)
                (out/'task-card.md').write_text(render(linked,value),encoding='utf8',newline='\n')
                report.update(status='planned',diagnostics=context['diagnostics']+check, source_links=bind,
                              selected_file_ids=material_ids, m2_version=m2['manifest']['manifest_version'])
                return report
            finally:
                pool.shutdown(wait=True)
    except Exception as error:
        report.update(status='blocked' if report['model_dispatches']==0 else 'failed', error=str(error))
        return report
    finally:
        report['wall_seconds'] = perf_counter()-timer
        save(out/'run.json',report)
