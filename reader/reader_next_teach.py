"""从当前项目生成导读或当前章，并把首稿接入 Reader 讲解栏。"""
from hashlib import sha256
import base64
import json
from pathlib import Path
import re
from time import perf_counter
from urllib.parse import quote, urlsplit
from uuid import uuid4

VERSION = 'next-teach-v1'
PROMPTS = {
    'NORMAL': '3f9ae5292bd68d8e7f7d39dadd01c63ce3b3c902a9a65fee78a8d42bbe37a8db',
    'INTRO': 'c66e26bd9627ce90bb5dbba9aee186f015aba847f1ab1bdbf0be9e043366e47f',
    'FINALE': '5e3a072c3ff6e5c7b20a6a52ebaa37f18bf2c8e5ded3011e0ff0cf19eb8b2a63',
}
INTRO_TITLES = {'导读', '0. 导读', '宏观导读', '第 0 章 导读', '第 0 章：导读'}


def runtime_archive():
    import io
    import zipfile
    root = Path(__file__).resolve().parent.parent / 'next/src'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((root / 'manualreader').rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                archive.write(path, path.relative_to(root).as_posix())
    return buffer.getvalue()


def load_runtime(bridge, out):
    import io
    import sys
    import zipfile
    from urllib.request import Request
    route = '/agent/reader_next_runtime.zip' if bridge.remote else '/api/agent/reader_next_runtime.zip'
    with bridge.opener.open(Request(bridge.base + route, headers=bridge.headers), timeout=60) as response:
        data = response.read(12_000_001)
    if len(data) > 12_000_000:
        raise ValueError('Next 运行包超过客户端允许的大小')
    target = out / 'runtime'
    target.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for item in archive.infolist():
            destination = (target / item.filename).resolve()
            if not destination.is_relative_to(target.resolve()) or not item.filename.startswith('manualreader/'):
                raise ValueError('Next 运行包包含项目运行目录以外的路径')
        archive.extractall(target)
    sys.path.insert(0, str(target))
    from manualreader.connected import run
    return run


def is_intro(node):
    return node.get('generation_mode') == 'INTRO' or node.get('title') in INTRO_TITLES


def prepare(workspace, material, records, mode=None):
    from reader_learning import learning_payload, validate_outline
    value = workspace.read()
    plan = learning_payload(material, value, records)
    registered = value.get('learning_outline') or []
    intro = next((node for node in registered if is_intro(node)), None)
    has_chapters = bool(value.get('explanations') or
                        any(node.get('legacy_section_ids') for node in registered))
    if mode not in (None, 'INTRO'):
        raise ValueError('当前入口自动沿用章节模式；补建导读时可明确指定 INTRO')
    if (not has_chapters or mode == 'INTRO') and intro is None:
        excluded = set(material.get('learning_excluded_source_ids', []))
        links = [{'file_id': p.get('file_id', 'primary'), 'passage_id': p['id']}
                 for p in material['passages']
                 if p.get('content_kind') != 'explanation' and not p.get('explanation_id')
                 and p['id'] not in excluded]
        if not links:
            raise ValueError('请先把原件登记到当前项目，再生成导读')
        used = {node['id'] for node in registered}
        identity = 'chapter-intro'
        while identity in used:
            identity = 'chapter-intro-' + uuid4().hex[:8]
        intro = {'id': identity, 'parent_id': None, 'title': '导读',
                 'generation_mode': 'INTRO', 'source_links': links, 'explanation_ids': []}
        nodes = [intro, *[{k: v for k, v in node.items() if k != 'number'}
                         for node in registered]]
        value['learning_outline'] = validate_outline({'nodes': nodes}, material,
                                                    defer_unassigned=True)
        value['planning_mode'] = 'chapterwise'
        workspace.write(value, value['revision'])
    selected = intro['id'] if intro and (not has_chapters or mode == 'INTRO') else plan['selected_id']
    node = next((n for n in workspace.read().get('learning_outline', [])
                 if n['id'] == selected), None)
    if node is None:
        raise ValueError('请先按原文顺序登记当前正式章节')
    return {'workflow_version': VERSION, 'learning_node_id': selected,
            'mode': 'INTRO' if is_intro(node) else node.get('generation_mode', 'NORMAL'),
            'title': node['title'], 'number': node.get('number'),
            'explanation_ids': node.get('explanation_ids', [])}


def writer_prompt(mode):
    here = Path(__file__).resolve().parent
    exported = here / ('reader_step04_' + mode.lower() + '.txt')
    path = exported if exported.is_file() else here.parent / 'next/src/manualreader/stages/step04' / (mode + '.txt')
    raw = path.read_bytes()
    # 冻结基线按 LF 计算；发布包或客户端使用 CRLF 时仍核对同一份提示词内容。
    if sha256(raw.replace(b'\r\n', b'\n')).hexdigest() != PROMPTS[mode]:
        raise ValueError('请从当前 Reader 下载与本轮模式对应的冻结 Writer 提示词')
    return raw.decode('utf8')


def save_preview(workspace, material, records, data):
    from reader_learning import learning_payload
    if data.get('workflow_version') != VERSION:
        raise ValueError('请从当前 Reader 更新 teach 客户端')
    value = workspace.read()
    request_id = data.get('request_id')
    if not isinstance(request_id, str) or not re.fullmatch(r'next-preview-[a-f0-9]{24}', request_id):
        raise ValueError('讲解预览需要本次提交编号')
    identity = 'draft-' + request_id.removeprefix('next-preview-')
    delivered = workspace.folder / 'next-runs' / request_id / 'delivery.json'
    if delivered.is_file():
        prior_delivery = json.loads(delivered.read_bytes())
        signature = sha256(data.get('next_artifacts', '').encode()).hexdigest()
        if prior_delivery['payload_sha256'] != signature:
            raise ValueError('同一提交编号已经保存了另一份 Next 成品')
        return prior_delivery['receipt']
    prior = next((row for row in value['explanations'] if row['id'] == identity), None)
    if prior is not None:
        return {'status': 'preview_ready', 'formal_saved': False,
                'explanation_id': identity, 'version': prior['version']}
    chapter = data.get('chapter', {})
    plan = learning_payload(material, value, records)
    node = next((row for row in plan['nodes'] if row['id'] == chapter.get('node_id')), None)
    if (node is None or node['id'] != plan['selected_id']
            or any(node[key] != chapter.get(target) for key, target in
                   (('title', 'title'), ('number', 'number'), ('parent_id', 'parent_node_id')))
            or value['revision'] != data.get('expected_revision')
            or records.get('learning_selection_revision', 0) != data.get('selection_revision')):
        raise ValueError('章节或材料版本已经更新，首稿仍在原运行目录，请按实际章节重新挂接')
    if node.get('explanation_ids'):
        raise ValueError('当前章节已有讲解，请保留已有版本')
    mode = data.get('mode')
    if mode not in PROMPTS or (mode == 'INTRO') != is_intro(node):
        raise ValueError('任务的写作模式与当前章节不一致')
    if data.get('next_artifacts'):
        return save_next_artifacts(workspace, material, data)
    # 原文编号只标记本章取材范围，每个正文块的精细关系由后续来源映射保存。
    saved = workspace.explain({
        'id': identity, 'title': node['title'], 'learning_node_id': node['id'],
        'markdown': data.get('markdown'), 'source_links': data.get('source_links', []),
        'expected_revision': value['revision'],
    }, material, stage=lambda staged, row: _store_preview(
        workspace, staged, row, value['revision'], mode, data.get('archive_path')))
    workspace.present({'explanation_id': identity, 'layout': 'lesson',
                       'columns': [['original', 'translation'], ['explanation']]},
                      {**material, 'explanations': [*material.get('explanations', []), saved]})
    return {'status': 'preview_ready', 'formal_saved': False, 'mode': mode,
            'explanation_id': saved['id'], 'version': saved['version'],
            'learning_node_id': node['id']}


def _store_preview(workspace, staged, row, revision, mode, archive):
    row.update(delivery_status='preview', generation_mode=mode, archive_path=archive)
    workspace.write(staged, revision)


def save_next_artifacts(workspace, material, data):
    import io
    import stat
    import zipfile
    import reader_server as backend
    from manualreader.stages.finish.preview import project_native
    root = workspace.folder / 'next-runs' / data['request_id']
    root.mkdir(parents=True, exist_ok=True)
    raw = base64.b64decode(data['next_artifacts'], validate=True)
    if len(raw) > 45_000_000:
        raise ValueError('本轮 Next 产物超过上传大小限制')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 180_000_000:
            raise ValueError('本轮 Next 产物解压后超过目录大小限制')
        for item in archive.infolist():
            path = (root / item.filename).resolve()
            if (not path.is_relative_to(root.resolve()) or stat.S_ISLNK(item.external_attr >> 16)
                    or (item.filename!='connected-run.json' and not item.filename.startswith(('inputs/chapter/', 'jobs/chapter/', 'media/', 'metrics/')))
                    or path.suffix.lower() not in ('.json', '.md', '.png', '.jpg', '.webp')):
                raise ValueError('Next 产物中含有运行目录以外的文件')
        archive.extractall(root)
    card = json.loads((root / 'inputs/chapter/task-card.json').read_bytes())
    if card.get('project', {}).get('id') != material['id'] or card.get('chapter') != data['chapter']:
        raise ValueError('Next 成品的任务卡与当前项目或章节不一致')
    record = json.loads((root / 'inputs/chapter/step04-run.json').read_bytes())
    if record['mode'] != data['mode'] or record.get('fixture_based'):
        raise ValueError('请提交当前模式的实际 Next 运行产物')
    result = project_native(root, 'chapter', workspace.folder.parent.parent, backend, None,
                            destination_library=workspace.folder.parent,
                            existing_identity=material['id'])
    result.update(status='preview_ready', formal_saved=False)
    (root / 'delivery.json').write_text(json.dumps({
        'payload_sha256': sha256(data['next_artifacts'].encode()).hexdigest(),
        'receipt': result}, ensure_ascii=False, indent=2), 'utf8')
    return result


def align_guide(guide):
    from reader_task_prompts import OUTLINE_PLANNING
    guide['endpoints']['lesson_task'] = 'GET /api/work?mode=lesson-next'
    guide['endpoints']['prepare_lesson'] = 'POST /api/teaching/prepare'
    guide['endpoints']['preview_lesson'] = 'POST /api/teaching/preview'
    guide['endpoints'].pop('teaching_result', None)
    guide['endpoints'].pop('explanation', None)
    guide.pop('teaching_result_contract', None)
    guide.pop('lecture_contract', None)
    guide['writing']['scope'] = '本字段是 daily 问答作者的合同；章节由 teach 使用 Next 独立冻结 Writer 提示词'
    guide['writing']['analysis']['modes'] = ['answer']
    guide['writing']['batching'] = 'daily 按真实问题分组；teach 由 Next 按实际依赖启动各阶段，不套用问答分析和输出结构'
    guide['learning_contract']['outline_request']['nodes'][0].update(title='导读', generation_mode='INTRO')
    guide['learning_contract']['explanation_request'] = {
        'command': 'reader_bridge.py teach --out <本轮空目录>',
        'save': '客户端按真实章节运行 Next 并保存讲解预览，无需另手写 parts 或上传正文'}
    guide['content_contract']['explanations'] = {
        'read': 'GET /api/content 返回讲解、稳定块编号、版本和来源关系',
        'generate': 'teach 使用冻结 Writer 生成自然 Markdown，结构和附件由后续 Next 阶段装配',
        'preview': 'POST /api/teaching/preview 接收本轮 Next 成品并保留当前章节身份'}
    guide['reading_contract']['explanation_rule'] = 'Next 在融合后确定 Part/Block 和真实来源关系；Writer 只输出自然 Markdown，不在写作前填写固定正文结构'
    guide['steps'] = [s.replace('你用 /api/learning/outline 保存学习目录，写入 /api/content/explanations 时用 learning_node_id 登记正文属于哪一章',
                                 '学习目录沿用真实节点编号；章节运行 teach，客户端把 Next 成品接到该章节的讲解栏') for s in guide['steps']]
    guide['chapter_generation'] = {
        'version': VERSION, 'command': 'reader_bridge.py teach --out <本轮空目录>',
        'first_chapter': '新项目首次生成自动登记「0. 导读」并使用冻结的 INTRO 规划及写作提示词',
        'continuation': '已有讲解时沿用当前章节，用户手动完成后再按原文顺序确定下一章',
        'writer_prompts': {mode: {'file': 'reader_step04_' + mode.lower() + '.txt',
                                 'content_baseline_sha256': digest,
                                 'baseline_newlines': 'LF'} for mode, digest in PROMPTS.items()},
        'delivery': '首稿在 Reader 讲解栏显示，项目文件同时保存按章节编号和标题命名的 Markdown 副本',
        'receipt': 'preview_ready 包含 explanation_id；formal_saved=false 表示尚未正式交稿',
        'pipeline': '当前客户端运行 STEP-01～18 的 Next 实现，STEP-19 核对实际装配，STEP-20 按用户确认提交正式版本和进度',
        'original_markdown': 'explanations/<章节编号>. <章节标题>.raw.md 保存模型首稿，同目录的同名 .md 保存融合稿',
    }
    guide['initial_action'] = (
        '先核对本项目的原件与问题池。需要导入时，使用 project_source_import。'
        '每轮从 client_files 下载当前脚本与冻结提示词，再运行 teach。'
        '新项目先生成「0. 导读」，使用 INTRO 模式通览整份材料。'
        '客户端把首稿放入讲解栏，按章节标题保存文件副本，核对回执后交付阅读链接。'
        '用户手动完成当前章后，再继续下一章。\n\n' + OUTLINE_PLANNING)
    guide['lesson_writeback'] = {
        'command': guide['chapter_generation']['command'],
        'prepare': 'POST /api/teaching/prepare 自动确定导读或当前章，沿用实际章节 ID',
        'preview': 'POST /api/teaching/preview 将自然 Markdown 挂到当前章节的讲解栏',
        'archive': '项目内 explanations 目录按章节编号和正式标题保存融合稿及 raw.md 原始首稿',
        'formal_saved': False, 'navigation': '用户手动勾选完成后，再推进下一章',
    }
    guide['reader_feedback_contract']['lesson'] = (
        'teach 读取真实原件和本轮反馈，按当前模式规划与写作，并在讲解栏显示首稿')
    guide['framework']['lesson_command'] = guide['chapter_generation']['command']
    guide['framework']['generation'] = (
        '章节沿用 chapter_generation 的 Next 任务卡和自然 Markdown；'
        '问答沿用当前问题、术语和练习的原生合同')
    guide['framework']['writer_execution'] = (
        'teach 选择对应模式的冻结 Writer 提示词；daily 继续使用当前问答规则及真实工具')
    guide['framework'].pop('lesson_evidence', None)
    guide['framework'].pop('framing', None)
    guide['framework']['exercise_owner'] = '章节练习由 Next STEP-14 生成并关联真实 Part；daily 的回答练习沿用原生 placement 合同'
    guide['framework']['validation'] = (
        '脚本核对当前项目、章节、原件版本和真实来源编号，并保留首稿及实际回执')
    guide['media_contract']['verification'] = (
        '章节向 Writer 提供已验证的真实原图，并在首稿对应位置保存有效媒体；'
        '问答沿用实际的 visual_checks')
    guide['main_source'] = {
        'purpose': '主原文用于展示当前章节的核心材料，Agent 可以选择本项目中任一已登记文件',
        'select': 'POST /api/content/presentation 提交真实 file_id；保留原布局、其他栏和当前章节',
        'linked_files': '讲解使用多个文件时，为各块保留真实 source_ranges；点击来源即可打开对应原文。项目文件链接使用 project-file:source/文件名，在主原文旁的标签页打开',
        'material_roots': '默认同时扫描 source 与已有 sources 目录；explanations 是生成目录，不计入材料分母',
    }
    return guide


def chapter_filename(number, title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', title).strip().rstrip('. ')
    name = re.sub(r'\s+', ' ', name)[:120].rstrip('. ') or '章节'
    return (str(number) + '. ' if number is not None else '') + name + '.md'


def archive_file(bridge, path, raw):
    """同一次提交可以重试；已有不同内容的文件继续保留。"""
    directory, existing = '', None
    for name in path.split('/'):
        offset, existing = 0, None
        while True:
            listing = bridge.request('/api/project-files/tree?path=' + quote(directory, safe='')
                                     + '&offset=' + str(offset))
            existing = next((row for row in listing.get('entries', []) if row['name'] == name), None)
            if existing or listing.get('next_offset') is None:
                break
            offset = listing['next_offset']
        if existing is None:
            break
        directory = directory + '/' + name if directory else name
    if existing:
        from urllib.request import Request
        route = ('/project-files/raw' if bridge.remote else '/api/project-files/raw')
        request = Request(bridge.base + route + '?path=' + quote(path, safe=''), headers=bridge.headers)
        with bridge.opener.open(request, timeout=60) as response:
            if response.read(len(raw) + 1) == raw:
                return
        raise ValueError('同名讲解文件已有不同内容，已保留原文件和本轮本机产物：' + path)
    bridge.request('/api/project-files/file', {
        'path': path, 'content_base64': base64.b64encode(raw).decode()})


def run(bridge, out, reader_context=None, *, plan_only=False):
    from reader_execute import WRITER_ROLE, codex_executable, invoke_writer, save
    from reader_step02_client import plan
    from reader_chapter_sources import verified_images, preview_body, media_summary
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('本轮输出目录已有内容，请保留原稿并选择新目录')
    out.mkdir(parents=True, exist_ok=True)
    began = perf_counter()
    report = {'workflow_version': VERSION, 'state': 'preparing', 'formal_saved': False}
    try:
        if bridge.remote:
            guide=bridge.request('/api/guide?view=bootstrap')
            if not guide.get('capabilities',{}).get('project_files',{}).get('create'):
                raise ValueError('请在 Reader「连接 Codex」中为本连接勾选新增文件，再生成本章')
        prepared = bridge.request('/api/teaching/prepare', {})
        report.update(prepared)
        if prepared['explanation_ids'] and not plan_only:
            report.update(state='already_prepared', next_action='阅读当前章并处理本章问题')
            return report
        feedback = (reader_context or {}).get('reader_feedback_latest', '')
        if not isinstance(feedback, str):
            feedback = json.dumps(feedback, ensure_ascii=False)
        planned = plan(bridge, out / 'planner', prepared['mode'], custom_instruction=feedback)
        report['planner'] = planned
        if planned['status'] != 'planned':
            raise ValueError(planned.get('error') or '本轮任务卡尚未生成')
        for name in ('task-card.json', 'task-card.md', 'writing-policies.json'):
            (out / name).write_bytes((out / 'planner' / name).read_bytes())
        card = json.loads((out / 'task-card.json').read_bytes())
        if plan_only:
            report['state'] = 'planned'
            return report
        packet = bridge.request('/api/work?mode=lesson-next')
        if packet['reference']['chapter'] != card['chapter']:
            raise ValueError('当前章节已经变化，任务卡保留在本轮目录')
        selected = {sid for item in card['material_plan'] for sid in item['source_ids']}
        sources = [row for row in packet['sources'] if row['id'] in selected]
        if selected != {row['id'] for row in sources}:
            raise ValueError('任务卡引用的来源与当前原件编号不一致，请核对本轮材料版本')
        images = verified_images(bridge, packet, out / 'source-images')
        media = out / 'media'
        media.mkdir()
        assets = {}
        for row in sources:
            row['images'] = []
            for ref in row.get('source_evidence', []):
                if ref['kind'] in ('image', 'vector', 'figure', 'visual'):
                    image = images[ref['attachment_number'] - 1].read_bytes()
                    name = 'source-' + sha256(image).hexdigest()[:20] + '.png'
                    (media / name).write_bytes(image)
                    assets.setdefault(name, {'type': 'original', 'source_ids': []})['source_ids'].append(row['id'])
                    row['images'].append({'attachment_number': ref['attachment_number'],
                                          'markdown_path': 'media/' + name})
        payload = {'mode': prepared['mode'], 'task_card': card,
                   'writing_policies': json.loads((out / 'writing-policies.json').read_bytes()),
                   'original_material': sources,
                   'boundary_context': [row for row in packet['sources'] if row['id'] not in selected]}
        save(out / 'writer-input.json', payload)
        folder = out / 'writer'
        folder.mkdir()
        executable = codex_executable()
        if not executable:
            raise ValueError('请先登录本机 Codex，再运行 teach')
        role = WRITER_ROLE.set('lesson')
        try:
            raw, usage, session = invoke_writer(
                folder, json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                images, executable, system_override=writer_prompt(prepared['mode']),
                research=False, allow_image_generation=__import__('os').environ.get(
                    'MANUAL_READER_V21_IMAGE_GENERATION', '1') == '1')
        finally:
            WRITER_ROLE.reset(role)
        (out / 'chapter.md').write_bytes((folder / 'final.txt').read_bytes())
        if not raw.strip():
            raise ValueError('写作者没有返回正文，本轮回执已保留')
        body, issues = preview_body(raw, assets, media, session)
        (out / 'preview.md').write_text(body, 'utf8')
        report['writer'] = usage
        save(out/'metrics/step02.json', planned)
        save(out/'metrics/step04.json', usage)
        report['media'] = media_summary(body, assets, issues)
        terms = bridge.request('/api/terms').get('terms', [])
        neighbors = {}
        if card.get('previous', {}).get('title'):
            neighbors['previous'] = card['previous']
        outline = bridge.request('/api/learning')['nodes']
        at = next((i for i, node in enumerate(outline) if node['id'] == card['chapter']['node_id']), None)
        if at is not None and at + 1 < len(outline):
            neighbors['next'] = {key: outline[at + 1].get(key) for key in ('id', 'number', 'title')}
        context = {'mode': prepared['mode'], 'existing_terms': terms, 'adjacent_chapters': neighbors}
        finish = load_runtime(bridge, out)
        report['postprocess'] = finish(out, packet, card, context)
        body = (out / 'jobs/chapter/body.final.md').read_text('utf8')
        for match in re.finditer(r'!\[[^\]]*\]\((?:assets|media)/([a-f0-9]{64}\.png)\)',body):
            name = match[1]
            assets[name] = {'type': 'generated', 'source_ids': []}
        body = re.sub(r'(!\[[^\]]*\]\()assets/([a-f0-9]{64}\.png\))', r'\1media/\2',body)
        report['media'] = media_summary(body, assets, issues)
        (out / 'preview.md').write_text(body, 'utf8')
        filename = chapter_filename(card['chapter']['number'], card['chapter']['title'])
        destination = 'explanations'
        for name in report['media']['assets']:
            archive_file(bridge, destination + '/media/' + name, (media / name).read_bytes())
        archive = destination + '/' + filename
        archive_file(bridge, archive, body.encode())
        archive_file(bridge, destination + '/' + filename[:-3] + '.raw.md', (out / 'chapter.md').read_bytes())
        for name in report['media']['assets']:
            body = body.replace('](media/' + name + ')',
                                '](/api/project-files/raw?path=' + quote(destination + '/media/' + name, safe='') + ')')
        receipt = bridge.request('/api/teaching/preview', {
            'request_id': 'next-preview-' + uuid4().hex[:24],
            'workflow_version': VERSION, 'mode': prepared['mode'],
            'chapter': card['chapter'], 'expected_revision': packet['reference']['versions']['workspace_revision'],
            'selection_revision': packet['reference']['versions']['selection_revision'],
            'file_signatures': {row['file_id']: packet['file_signatures'][row['file_id']]
                                for row in sources},
            'markdown': body, 'archive_path': archive,
            'next_artifacts': pack_artifacts(out),
            'source_links': [{'file_id': row['file_id'], 'passage_id': row['id']} for row in sources]})
        parsed = urlsplit(bridge.base)
        report.update(state='preview_ready', preview_path=archive, receipt=receipt,
                      reader_url=f'{parsed.scheme}://{parsed.netloc}/?material=' +
                      quote(card['project']['id'], safe=''))
        return report
    except Exception as error:
        report.update(state='failed', error=str(error))
        raise
    finally:
        report['seconds'] = round(perf_counter() - began, 3)
        save(out / 'run.json', report)


def pack_artifacts(out):
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        if (out/'connected-run.json').is_file():
            archive.write(out/'connected-run.json','connected-run.json')
        for group in ('inputs/chapter', 'jobs/chapter', 'media', 'metrics'):
            for path in sorted((out / group).glob('*')):
                if path.is_file() and path.suffix.lower() in ('.json', '.md', '.png', '.jpg', '.webp'):
                    archive.write(path, path.relative_to(out).as_posix())
    return base64.b64encode(buffer.getvalue()).decode()
