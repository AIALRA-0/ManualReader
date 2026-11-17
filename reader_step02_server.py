"""Read Reader's real chapter, question, and source state for STEP-02."""
from copy import deepcopy
from hashlib import file_digest
import json
from pathlib import Path
import re
from decimal import Decimal, ROUND_HALF_UP

from reader_step01 import SQLiteLedger
from reader_step01_scan import inside
from reader_step02_card import MODES, digest
from reader_step02_original import inventory, read_region


def history(records):
    return {q['id']: q for row in records.get('rounds', []) for q in row.get('questions', [])
            if q.get('id') and not q.get('deleted_at') and isinstance(q.get('text'), str) and q['text'].strip()}


def empty_previous():
    return {'title': None, 'bridges': [], 'recaps': [], 'forecast': None}


def chapter_shape(node, title=None):
    if node is None:
        return {'node_id': None, 'parent_node_id': None, 'number': '0' if title == '导读' else None, 'title': title}
    return {'node_id': node['id'], 'parent_node_id': node.get('parent_id'),
            'number': node['number'], 'title': node['title']}


def approved_styles(folder, questions, chapter):
    path = Path(folder) / 'step02-approved-question-styles.json'
    if not path.is_file():
        return []
    all_styles = json.loads(path.read_bytes())
    result = []
    for row in all_styles.get(chapter, []):
        ids = row.get('evidence_question_ids')
        if (isinstance(ids, list) and ids and set(ids).issubset(questions)
                and all(isinstance(row.get(k), str) and row[k] for k in ('pattern', 'explanation_need'))):
            result.append({k: deepcopy(row[k]) for k in ('pattern', 'explanation_need', 'evidence_question_ids')})
    return result


def source_position(p, document):
    loc = p.get('source_location') or {}
    from reader_v21 import position
    located = position(p, document, 0)
    pos = located if located['type'] != 'source_unit_index' else None
    if loc.get('line_start') is not None:
        pos = {'type': 'text_line', 'start': loc['line_start'], 'end': loc.get('line_end', loc['line_start'])}
    timed = ({'type': 'subtitle_time', 'start': loc['start_seconds'], 'end': loc['end_seconds']}
             if all(key in loc for key in ('start_seconds', 'end_seconds')) else None)
    return pos, timed


def question_context(material, q, questions):
    from reader_questions import source_for
    part = next((p for p in material['passages'] if p['id'] == q.get('passage_id')), {})
    src = source_for(material, q, passage=part)
    target = q.get('target') or {}
    original = target.get('original') or src.get('original') or part.get('raw_text') or ''
    answer_source = target.get('text') or src.get('translation') or original
    selected = answer_source if part.get('explanation_id') or src.get('kind') == 'explanation' else (original or answer_source)
    data = {'选中的原文或讲解': selected}
    parent = q.get('parent_question_id')
    parents = []
    seen = {q['id']}
    while parent and parent in questions and parent not in seen:
        seen.add(parent)
        linked = questions[parent]
        answer = linked.get('answer')
        if isinstance(answer, dict):
            answer = answer.get('text') or answer.get('markdown') or ''
        parents.append({'question': linked['text'], 'answer': answer or ''})
        parent = linked.get('parent_question_id')
    if parents:
        data['父问题及相关回答'] = parents
    if own := q.get('answer'):
        if isinstance(own, dict):
            own = own.get('text') or own.get('markdown')
        if own:
            data['这条问题已有的回答'] = own
    if q.get('resource_id') or target.get('resource_id'):
        data['选中资源'] = {'resource_id': target.get('resource_id') or q.get('resource_id'),
                          '说明': target.get('resource_text') or selected}
    return data, part


def build_context(mode, workspace, material, state, manifest, root, request):
    from reader_learning import learning_payload
    from reader_v21 import previous_content
    from reader_teaching import recent_reader_questions

    if mode not in MODES:
        raise ValueError('未指定实际规划模式')
    plan = learning_payload(material, workspace, state)
    registered = workspace.get('learning_outline')
    formal_ids = {r['id'] for r in registered} if registered is not None else None
    nodes = [n for n in plan['nodes'] if formal_ids is None or n['id'] in formal_ids]
    by_id = {p['id']: p for p in material['passages']}
    records = history(state)
    generated = {r['id'] for r in workspace.get('files',[]) if r.get('purpose') in ('illustration','generated')
                 or r.get('original') is False}
    file_rows = [f for f in manifest['files'] if f['status'] == 'active' and not f['identity_pending']
                 and f['file_id'] not in generated]
    selected_id = plan['selected_id']
    node = next((n for n in nodes if n['id'] == selected_id), None)
    if request.get('node_id') not in (None, selected_id) and mode == 'NORMAL':
        raise ValueError('Reader 当前选中的章节已经变化')
    local = Path(root)
    project_dir = request['_project_dir']
    recent, diagnostics = [], []
    fixed = {'custom_instruction': request.get('custom_instruction')}
    if fixed['custom_instruction'] is not None and (not isinstance(fixed['custom_instruction'], str) or len(fixed['custom_instruction']) > 24000):
        raise ValueError('本轮特殊写作要求必须是实际用户原话')
    fixed['custom_instruction'] = fixed['custom_instruction'] or None
    previous_node = None
    anchor_nodes = []
    directory = []
    allowed_selection = allowed_ranges = None
    preview = None
    saved = []
    if mode == 'NORMAL':
        if node is None or not plan['scope_active']:
            raise ValueError('当前项目没有选定已登记的学习章节')
        i = nodes.index(node)
        previous_node = nodes[i-1] if i else None
        fixed['chapter'] = chapter_shape(node)
        anchor_nodes = [node]
        directory = [chapter_shape(x) for x in nodes[max(0,i-2):i+3]]
    elif mode == 'INTRO':
        intro = next((n for n in nodes if n.get('kind') == 'intro' or
                      n.get('title') in ('导读', '0. 导读', '宏观导读', '第 0 章 导读', '第 0 章：导读')), None)
        fixed['chapter'] = chapter_shape(intro, '导读')
        preview = None if intro else {'identity': 'preview-intro-' + digest(manifest['project_id'])[:16], 'persisted': False}
        directory = [chapter_shape(x) for x in nodes]
    elif mode == 'FINALE':
        if request.get('explicit_finale') is not True:
            raise ValueError('收尾规划需要用户明确请求；双 100% 只提供选择')
        indexed = {e['id']: e for e in workspace.get('explanations', [])}
        for n in nodes:
            verified = [indexed[x] for x in n.get('explanation_ids', []) if x in indexed
                        and indexed[x].get('blocks')]
            if not verified:
                continue
            associated = []
            for entry in verified:
                links = entry.get('source_links', [])
                if not links or any(l.get('passage_id') not in by_id for l in links):
                    diagnostics.append({'chapter': n['id'], 'reason': 'saved_chapter_links_unverified'})
                    continue
                blocks = [b['markdown'] for b in entry.get('blocks', []) if b.get('markdown')]
                if not blocks:
                    continue
                associated += links
                saved.append({'chapter': n['title'], 'explanation_id': entry['id'],
                              'version': entry.get('version'), 'blocks': blocks})
            if associated:
                anchor_nodes.append({'effective_source_links': associated})
        if not saved:
            raise ValueError('no_saved_chapter: 当前项目没有带有效原文关系的已保存讲解')
        fixed['chapter'] = chapter_shape(next((n for n in nodes if n.get('title') in ('最后一章', '收尾', '全篇回顾与收尾')), None), '阶段性收束')
        preview = None if fixed['chapter']['node_id'] else {
            'identity': 'preview-finale-' + digest(manifest['project_id'])[:16], 'persisted': False}
        directory = [chapter_shape(x) for x in nodes]
        previous_node = next((n for n in reversed(nodes)
                              if any(x['explanation_id'] in n.get('explanation_ids', []) for x in saved)), None)
    else:
        qid = request.get('question_id')
        if not isinstance(qid, str) or qid not in records:
            raise ValueError('当前 Reader 没有找到这条真实有效的问题')
        q = records[qid]
        q_context, part = question_context(material, q, records)
        fixed['question'] = {'id': q['id'], 'text': q['text'],
                             'version': q.get('version', q.get('revision',1)),
                             'status': q.get('status') or ('answered' if q.get('answer') else 'pending'),
                             'learning_node_id': q.get('learning_node_id') or (q.get('target') or {}).get('learning_node_id')}
        fixed['context'] = q_context
        if part.get('id'):
            anchor_nodes = [{'effective_source_links': [{'passage_id': part['id']}]}]
        previous_node = next((nodes[i-1] for i, n in enumerate(nodes) if i and n['id'] == fixed['question']['learning_node_id']), None)
    if mode != 'ANSWER':
        old_progress = manifest['progress']
        fixed['project'] = {'id': manifest['project_id'], 'name': material['title'], 'root_path': str(local)}
        fixed['versions'] = {'material_schema': material['schema_version'], 'workspace_revision': workspace['revision'],
                             'records_revision': state['revision'], 'selection_revision': state.get('learning_selection_revision', 0),
                             'file_manifest_revision': manifest['manifest_version']}
        fixed['progress'] = {k: {field: old_progress[k][field] for field in fields} for k, fields in (
            ('files', ('coverage_pct', 'status')), ('lessons', ('completed', 'total', 'completion_pct')),
            ('overall', ('coverage_pct', 'status', 'calculation')))}
        if formal_ids is not None and len(nodes) != old_progress['lessons']['total']:
            # Dynamic source groups in the reading view do not become user-approved chapters.
            from reader_completion import completion_payload
            manual = state.get('manual_completions',{})
            plan_with_formal = {**plan,'nodes':nodes}
            done = sum(completion_payload(material,workspace,state,
                                          plan={**plan_with_formal,'selected_id':n['id']})['completed']
                       for n in nodes if manual.get(n['id'],{}).get('origin','manual_ui') == 'manual_ui')
            ratio = (Decimal(done*100)/len(nodes)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) if nodes else Decimal(0)
            fixed['progress']['lessons'] = {'completed':done,'total':len(nodes),'completion_pct':float(ratio)}
            combined = (Decimal(str(fixed['progress']['files']['coverage_pct']))+ratio)/2
            fixed['progress']['overall']['coverage_pct'] = float(combined.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        fixed['previous'], missing = previous_content(previous_node, workspace, Path(project_dir))
        diagnostics += missing
    if previous_node and mode != 'INTRO':
        earlier = set(previous_node.get('passage_ids', []))
        for n in nodes:
            if n['id'] != previous_node['id']:
                earlier.difference_update(n.get('passage_ids', []))
        recent = recent_reader_questions(state, previous_node['id'], earlier, limit=100, char_budget=24000)
    approved = approved_styles(project_dir, records, previous_node['id'] if previous_node else None)
    used = {qid for row in approved for qid in row['evidence_question_ids']}
    pending = [{'ref': 'qref-' + str(i+1), 'question_id': q['question_id'], 'text': records[q['question_id']]['text']}
               for i, q in enumerate(recent) if q['question_id'] not in used and q['question_id'] in records]
    hints = {}
    for anchor in anchor_nodes:
        for link in anchor.get('effective_source_links', []):
            row = by_id.get(link.get('passage_id'))
            if not row:
                continue
            position, _ = source_position(row, material)
            if position:
                values = hints.setdefault(row['file_id'], [])
                if position not in values:
                    values.append(position)
    if mode == 'FINALE':
        def coalesce(rows):
            out = []
            for r in sorted(rows, key=lambda r: (r['type'], r['start'], r['end'])):
                if out and out[-1]['type'] == r['type'] and r['start'] <= out[-1]['end'] + 1:
                    out[-1]['end'] = max(out[-1]['end'], r['end'])
                else:
                    out.append(dict(r))
            return out
        allowed_ranges = {fid: coalesce(rows) for fid, rows in hints.items()}
        allowed_selection = sorted(allowed_ranges)
        if not allowed_selection:
            raise ValueError('no_saved_chapter: 已保存讲解没有可核对的原件范围')
    return {'mode': mode, 'fixed': fixed, 'files': file_rows, 'approved_styles': approved,
            'unprocessed_questions': pending, 'directory': directory, 'scope_hints': hints,
            'added_rounds': {r['file_id']: 1 for r in file_rows}, 'saved_explanations': saved,
            'allowed_selection': allowed_selection, 'allowed_ranges': allowed_ranges, 'preview': preview,
            'diagnostics': diagnostics, 'selection_identity': selected_id,
            'state_fingerprint': digest([state['revision'], state.get('learning_selection_revision'), workspace['revision']])}


def index_for(project, material, stored, file):
    from reader_workspace import Workspace
    fid = file['file_id']
    entry = next((r for r in stored['files'] if r['id'] == fid), None)
    if entry:
        try:
            parent = inside(Workspace(project.folder).files.resolve(), fid)
            archived = inside(parent, entry['stored_name'])
        except OSError:
            return {'verified': False, 'reason': 'archived_original_missing'}
        document = entry['document']
        rows = document['passages']
    elif fid == 'primary' and project.original.get('original_file'):
        archived = (project.folder / project.original['original_file']).resolve()
        if not archived.is_relative_to(project.folder.resolve()):
            return {'verified': False, 'reason': 'archived_original_outside_project'}
        document = material
        rows = [r for r in material['passages'] if r.get('file_id') == 'primary' and not r.get('explanation_id')]
    else:
        return {'verified': False, 'reason': 'no_parsed_source_index'}
    try:
        with archived.open('rb') as stream:
            match = file_digest(stream, 'sha256').hexdigest() == file['content_sha256']
    except OSError:
        match = False
    if not match:
        return {'verified': False, 'reason': 'parsed_original_version_differs'}
    found = []
    for row in rows:
        position, timed = source_position(row, document)
        if position or timed:
            found.append({'id': row['id'], 'position': position, 'time': timed})
    return {'verified': bool(found), 'reason': None if found else 'known_index_has_no_original_positions', 'rows': found}


def route(data, project, read_context, connect, initial):
    rid = data.get('round_id')
    if not isinstance(rid, str):
        raise ValueError('STEP-02 请求需要本轮 M1 编号')
    ledger = SQLiteLedger(connect, initial)
    row = ledger(lambda l, _s: l['rounds'].get(rid))
    if not row:
        raise ValueError('找不到属于当前 Reader 项目的 M1')
    manifest = row['m1']['manifest']
    action = data.get('action')
    if action == 'context':
        material, stored, records = read_context()
        request = {**data, '_project_dir': str(project.folder)}
        return build_context(row['mode'], stored, material, records, manifest, project.ensure()['root'], request)
    fid = data.get('file_id')
    file = next((r for r in manifest['files'] if fid == r['file_id'] and r['status'] == 'active'), None)
    if file is None or file['identity_pending']:
        raise ValueError('access_blocked: 文件不在本轮已核验的 M1 白名单')
    root = project.ensure()['root']
    try:
        if action == 'catalog':
            return inventory(root, file)
        if action == 'read':
            return read_region(root, file, data.get('start'), data.get('end'), data.get('visual') is True,
                               data.get('char_offset', 0))
        if action == 'index':
            material, stored, _ = read_context()
            return index_for(project, material, stored, file)
    except (OSError, RuntimeError) as exc:
        raise ValueError('access_blocked: 原件无法打开或读取') from exc
    raise ValueError('STEP-02 不支持这个请求')
