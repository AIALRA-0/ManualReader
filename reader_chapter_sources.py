"""读取当前章节的原件、版本与媒体，并渲染冻结任务卡。"""
import base64
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit

VERSION = 'next-teach-v1'
PROGRESS_FORMULA = '(files.coverage_pct + lessons.completion_pct) / 2'
RECAP_KEYS = ('context', 'problem', 'claim', 'reason', 'evidence', 'implication')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def pct(value):
    return float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def file_progress(history, size):
    if history is None:
        return 0.0
    old_size, covered = history.get('file_size'), history.get('covered_bytes')
    if any(type(n) is not int or n < 0 for n in (old_size, covered, size)):
        raise ValueError('已有文件覆盖记录缺少有效的文件大小或已覆盖字节')
    if old_size and covered >= old_size and size <= old_size:
        return 100.0
    if not size:
        return 0.0
    return pct(100 * min(covered, size) / size)


def project_progress(records, paths, material, stored, plan):
    from reader_completion import completion_payload

    history = records.get('file_coverage', {})
    files = {fid: file_progress(history.get(fid), path.stat().st_size) for fid, path in paths.items()}
    nodes = plan['nodes']
    completions = records.get('manual_completions', {})
    completed = sum(
        completion_payload(material, stored, records, plan={**plan, 'selected_id': node['id']})['completed']
        for node in nodes
        if completions.get(node['id'], {}).get('origin', 'manual_ui') == 'manual_ui'
        and completions.get(node['id'], {}).get('completed') is True
    )
    file_pct = pct(sum(files.values()) / len(files)) if files else 0.0
    lesson_pct = pct(100 * completed / len(nodes)) if nodes else 0.0
    return files, {
        'files': {'coverage_pct': file_pct, 'status': 'ready' if history.keys() & paths.keys() else 'initialized'},
        'lessons': {'completed': completed, 'total': len(nodes), 'completion_pct': lesson_pct},
        'overall': {'coverage_pct': pct((Decimal(str(file_pct)) + Decimal(str(lesson_pct))) / 2),
                    'status': 'ready', 'calculation': PROGRESS_FORMULA},
    }


def previous_content(node, stored, folder):
    if node is None:
        return {'title': None, 'bridges': [], 'recaps': [], 'forecast': None}, []

    from reader_framework_legacy import fingerprint, read

    explanations = {entry['id']: entry for entry in stored['explanations']}
    metadata = read(folder).get('explanations', {})
    previous = {'title': node['title'], 'bridges': [], 'recaps': [], 'forecast': None}
    for identity in node.get('explanation_ids', []):
        saved = explanations.get(identity)
        if not saved:
            continue
        next_metadata = Path(folder) / 'postprocess-preview' / identity / 'manifest.json'
        if saved.get('generation_mode') and next_metadata.is_file():
            next_saved = read_json(next_metadata)
            blocks = {block['id']: block['markdown'] for block in saved.get('blocks', [])}
            for bid in ('framework-previous', 'framework-start-1'):
                if blocks.get(bid):
                    previous['bridges'].append(blocks[bid])
            groups = [(row['title'], row['items']) for row in next_saved.get('chapter_recaps', [])
                      if all(key in row.get('items', {}) for key in RECAP_KEYS)]
            from reader_connected import recap_markdown
            if groups and blocks.get('framework-outro') == recap_markdown(groups):
                previous['recaps'].extend({'part_title': title, **fields} for title, fields in groups)
            previous['forecast'] = blocks.get('framework-next') or previous['forecast']
            continue
        framing = saved.get('framing') or {}
        parts = {p['id']: p.get('reading_heading') for p in saved.get('blocks', [])}
        legacy = metadata.get(identity, {})
        if saved.get('handoff_layout'):
            layout = saved['handoff_layout']
            for block in saved.get('blocks', []):
                item = layout.get(block['id'], {})
                role = item.get('frame')
                if role in ('previous', 'current'):
                    previous['bridges'].append(block['markdown'])
                if role == 'next':
                    previous['forecast'] = block['markdown']
                if recap := item.get('recap'):
                    title = block.get('reading_heading') or item.get('title')
                    if title and all(key in recap for key in RECAP_KEYS):
                        previous['recaps'].append({'part_title': title, **{key: recap[key] for key in RECAP_KEYS}})
        elif legacy.get('fingerprint') == fingerprint(saved) and not framing:
            previous['bridges'].extend(row['markdown'] for row in legacy.get('start', []))
            for part in legacy.get('parts', []):
                if part.get('recap'):
                    previous['recaps'].append({'part_title': part['heading'],
                                               **{key: part['recap'][key] for key in RECAP_KEYS}})
            previous['forecast'] = legacy.get('next') or previous['forecast']
        else:
            previous['bridges'].extend(row['markdown'] for row in framing.get('start', []))
            for row in framing.get('end', []):
                for recap in row.get('bullets', []):
                    if all(key in recap for key in RECAP_KEYS):
                        title = recap.get('part_title') or parts.get(recap.get('part_id'))
                        if not title:
                            raise ValueError('上一节已存回顾缺少对应的正文标题')
                        previous['recaps'].append({'part_title': title, **{key: recap[key] for key in RECAP_KEYS}})
                if row.get('role') == 'next':
                    previous['forecast'] = row.get('markdown') or previous['forecast']
    missing = [key for key in ('bridges', 'recaps', 'forecast') if not previous[key]]
    return previous, ([] if not missing else [{
        'previous_node_id': node['id'], 'missing_saved_fields': missing,
    }])


def position(unit, material, index):
    source = unit.get('source_location') or {}
    page_numbers = [material['pages'][key]['number'] for ref in unit.get('refs', ())
                    if (key := ref.get('page_id')) in material.get('pages', {})
                    and 'number' in material['pages'][key]]
    if page_numbers:
        return {'type': 'pdf_page', 'start': min(page_numbers), 'end': max(page_numbers)}
    for names, kind, pattern in (
        ('dom_units', 'html_dom_unit', r'dom(\d+)'),
        ('source_unit_ids', 'subtitle_sid', r'sid[-:]?(\d+)'),
        ('source_unit_ids', 'text_line', r'(?:line|l)[-:]?(\d+)'),
    ):
        values = [int(match[1]) for name in source.get(names, [])
                  if (match := re.fullmatch(pattern, str(name)))]
        if values:
            return {'type': kind, 'start': min(values), 'end': max(values)}
    if type(source.get('start_line')) is int:
        return {'type': 'text_line', 'start': source['start_line'], 'end': source.get('end_line', source['start_line'])}
    return {'type': 'source_unit_index', 'start': index + 1, 'end': index + 1}


def snapshot(material, stored, records, files, folder, version, requested=None):
    """只读取得正式任务；旧讲解任务和练习写回合同不参与准备。"""
    from reader_learning import learning_payload
    from reader_media import source_media
    from reader_teaching import recent_reader_questions

    plan = learning_payload(material, stored, records)
    identity = plan['selected_id']
    if requested and requested != identity:
        raise ValueError('当前选中的章节已经变化，请在 Reader 中确认要写的章节')
    if not identity or not plan['scope_active']:
        raise ValueError('请在 Reader 中选择已经登记的章节')
    i = next(i for i, row in enumerate(plan['nodes']) if row['id'] == identity)
    node = plan['nodes'][i]
    previous_node = plan['nodes'][i - 1] if i else None
    previous, diagnostics = previous_content(previous_node, stored, folder)
    # 读取已登记的路径。复制原件及生成文件树仍由原有项目文件入口负责。
    config = read_json(files.config) if files.config.is_file() else files.ensure()
    root = Path(config['root']).resolve(strict=True)
    manifest = {row['id']: row for row in material['reading_files']}
    paths = {}
    for fid, name in config.get('sources', {}).items():
        if fid not in manifest:
            continue
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError('登记的原件路径超出了项目目录：'+name)
        if path.is_file():
            paths[fid] = path
    progress_by_file, progress = project_progress(records, paths, material, stored, plan)
    versions = {
        'material_schema': material['schema_version'], 'workspace_revision': stored['revision'],
        'records_revision': records['revision'], 'selection_revision': records.get('learning_selection_revision', 0),
        'file_manifest_revision': config['version'],
    }
    if not all(type(value) is int and value >= 0 for value in versions.values()):
        raise ValueError('当前项目的版本记录尚未核对完整')
    by_id = {p['id']: p for p in material['passages'] if not p.get('explanation_id')
             and p.get('content_kind') != 'explanation'}
    candidates = {link['passage_id'] for link in node.get('effective_source_links', [])
                  if link['passage_id'] in by_id}
    if not candidates:
        raise ValueError('当前章节没有可读的真实来源，请先在 Reader 中关联原件')
    excluded = set(material.get('learning_excluded_source_ids', []))
    grouped = defaultdict(list)
    for fid, ids in manifest.items():
        grouped[fid] = [by_id[pid] for pid in ids.get('passage_ids', []) if pid in by_id]
    file_rows = []
    source_rows = []
    for fid, rows in grouped.items():
        wanted = {j for j, row in enumerate(rows) if row['id'] in candidates and row['id'] not in excluded}
        if not wanted:
            continue
        if fid not in paths:
            raise ValueError('当前章节指向未登记或找不到的原件：'+fid)
        relative_path = config['sources'][fid]
        file_rows.append({'file_id': fid, 'relative_path': relative_path, 'status': 'active',
                          'added_round': 1, 'progress_before_pct': progress_by_file[fid]})
        boundary = {j + k for j in wanted for k in (-1, 1) if 0 <= j + k < len(rows)} - wanted
        for j in sorted(wanted | boundary):
            row = rows[j]
            original = row.get('raw_text') or row.get('en') or row.get('zh') or ''
            locator = row.get('source_location') or {}
            source_rows.append({'id': row['id'], 'file_id': fid, 'kind': row.get('kind'),
                                'original': original, 'position': position(row, material, j),
                                'document_index': j, 'context_only': j not in wanted,
                                **({'time_seconds': [locator['start_seconds'], locator['end_seconds']]}
                                   if 'start_seconds' in locator and 'end_seconds' in locator else {}),
                                'media_items': source_media(material, row, by_id=by_id, classify=True) if j in wanted else []})
    if candidates - excluded - {row['id'] for row in source_rows}:
        raise ValueError('当前章节的部分来源没有映射到真实项目文件')
    earlier = set(previous_node.get('passage_ids', [])) if previous_node else set()
    if previous_node:
        for other in plan['nodes']:
            if other['id'] != previous_node['id']:
                earlier.difference_update(other.get('passage_ids', []))
    questions = recent_reader_questions(records, previous_node['id'] if previous_node else None, earlier,
                                        limit=100, char_budget=24000)
    reference = {
        'project': {'id': material['id'], 'name': material['title'], 'root_path': str(root)},
        'chapter': {'node_id': identity, 'parent_node_id': node['parent_id'],
                    'number': node['number'], 'title': node['title']},
        'versions': versions, 'progress': progress, 'previous': previous,
    }
    signatures = {}
    for fid, path in paths.items():
        stat = path.stat()
        signatures[fid] = [stat.st_size, stat.st_mtime_ns]
    return {'workflow_version': VERSION, 'reference': reference, 'files': file_rows, 'sources': source_rows,
            'questions': questions, 'file_signatures': signatures,
            'snapshot_revision': str(version), 'diagnostics': diagnostics}


def task_markdown(card, policies, *, policy_names=None):
    def cell(value):
        return str(value if value is not None else '—').replace('|', '\\|').replace('\n','<br>')

    def table(rows, labels=('字段','内容')):
        return ['| '+' | '.join(labels)+' |', '|---|---|',
                *(f'| {cell(key)} | {cell(value)} |' for key, value in rows)]

    chapter, project = card['chapter'], card['project']
    lines = [f"# {project['name']} · 第 {chapter['number']} 节写作任务卡", '', '## 项目与章节', '']
    project_rows = [('项目', project['name']), ('项目编号', project['id'])]
    if policy_names is not None:
        project_rows.append(('项目目录', project['root_path']))
    lines.extend(table([*project_rows, ('章节', chapter['number']),
                        ('目录节点', chapter['node_id']), ('父节点', chapter['parent_node_id']),
                        ('正式标题', chapter['title'])]))
    lines.extend(['', '## 版本', ''])
    lines.extend(table(zip(('材料结构版本','工作区修订','用户记录修订','目录选择修订','文件清单版本'),
                           card['versions'].values())))
    progress = card['progress']
    lessons = progress['lessons']
    lines.extend(['', '## 当前状态与进度', '', f"文件覆盖进度：{progress['files']['coverage_pct']:.2f}%  ",
                  f"讲解完成进度：{lessons['completion_pct']:.2f}%（{lessons['completed']}/{lessons['total']}）  ",
                  f"总进度：{progress['overall']['coverage_pct']:.2f}%"])
    previous = card['previous']
    bridge_lines = [text for part in previous['bridges'] for text in ('> '+part.replace('\n','\n> '), '')] if policy_names is not None else [
        '> '+part.replace('\n','\n> ')+'\n' for part in previous['bridges']]
    lines.extend(['', '## 上一节内容', '', '### 上一节标题', '', previous['title'] or '本项目的第一节',
                  '', '### 上一节承接', '', *bridge_lines])
    if policy_names is None or not bridge_lines:
        lines.append('')
    lines.append('### 上一节回顾')
    for item in previous['recaps']:
        lines.extend(['', '#### '+item['part_title'], ''])
        lines.extend(item[key]+'  ' for key in RECAP_KEYS)
    forecast = previous['forecast'] or ''
    if forecast and policy_names is not None:
        forecast = '> ' + forecast.replace('\n','\n> ')
    lines.extend(['', '### 上一节预测', '', forecast, '',
                  '## 上次的提问告诉我们，哪些地方值得讲细一点', ''])
    if card['question_styles']:
        lines.extend(table([(item['pattern'], item['explanation_need']) for item in card['question_styles']],
                           ('提问风格','解释强化方向')))
    lines.extend(['', '## 本章标题', '', card['chapter']['title'], '', '## 本章宏观目标'])
    for role, title in (('reader', '读者想弄懂的'), ('teacher', '教师希望教授的')):
        title += '知识点' if policy_names is not None else '内容'
        lines.extend(['', '### '+title, '', *['- '+term for term in card['goals'][role]]])
    lines.extend(['', '## 本节依据的材料'])
    for item in card['material_plan']:
        ranges = [f"{span['type']} {span['start']}–{span['end']}" for span in item['source_ranges']]
        base = [('文件编号', item['file_id']), ('相对路径', item['relative_path']),
                ('状态', '有效' if item['status']=='active' else item['status']), ('加入轮次', item['added_round']),
                ('本轮开始前的覆盖进度' if policy_names is not None else '本轮开始前覆盖率',
                 f"{item['progress_before_pct']:.2f}%"), ('内容简介', item['description'])]
        if policy_names is not None:
            lines.extend(['', '### '+Path(item['relative_path']).name, '', *table(base)])
            if item['source_ranges'] and all(r['type']=='pdf_page' for r in item['source_ranges']):
                pages = ', '.join(f"第 {r['start']} 页" if r['start']==r['end'] else f"第 {r['start']}–{r['end']} 页"
                                  for r in item['source_ranges'])
                lines.extend(['', '来源页码：'+pages])
            else:
                lines.extend(['', '来源范围：'+('、'.join(ranges) or '待核对')])
            lines.extend(['', f"来源编号：{len(item['source_ids'])} 个已登记单元（完整 ID 列表见 JSON 中 "+chr(96)+"material_plan[].source_ids"+chr(96)+"）"])
        else:
            lines.extend(['', *table([('文件', Path(item['relative_path']).name), *base, ('来源范围', '、'.join(ranges)),
                                     ('来源编号', f"{len(item['source_ids'])} 个来源 ID，完整编号见 JSON")])])
    names = ('主体与动作明确', '事实自然推动下一步', '用实际过程建立理解', '避免无必要的防御性解释',
             '解释必要条件而不编造原因', '主动使用可视化帮助理解', '中文标点', '中文冒号', '并列项与缩进',
             '单一事项不使用并列格式', '中英文与数字间距', 'Markdown 标题', 'LaTeX 公式', '代码与日志',
             '表格', '图片与媒体', 'Mermaid 与图示方向', '原文与受保护内容')
    offset = 0
    for domain, heading in (('writing_preferences', '写作偏好'), ('format_rules', '格式规范')):
        lines.extend(['', '## '+heading])
        for rule in policies[domain]:
            title = policy_names[rule['rule_id']] if policy_names is not None else names[offset]
            requirement = ('需求定义：' if policy_names is not None else '') + rule['requirement']
            lines.extend(['', '### '+title, '', requirement, '', 'Bad：' if policy_names is not None else '**Bad**',
                          '', rule['bad'], '', 'Good：' if policy_names is not None else '**Good**', '', rule['good']])
            offset += 1
    custom = card['custom_instruction']
    lines.extend(['', '## 用户特殊写作要求', '', (custom or '无') if policy_names is not None else custom])
    return '\n'.join(lines)+'\n'


def prompt_input(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def verified_images(bridge, packet, directory):
    from urllib.request import Request
    from reader_source_wire import MAX_IMAGE_BYTES, digest, put
    from concurrent.futures import ThreadPoolExecutor

    def fetch(entry):
        index,row=entry
        path=row['src']
        route=path.removeprefix('/api') if bridge.remote else path
        with bridge.opener.open(Request(bridge.base+route,headers=bridge.headers),timeout=60) as response:
            data=response.read(MAX_IMAGE_BYTES+1)
        if len(data)>MAX_IMAGE_BYTES or digest(data)!=row['sha256']:
            raise ValueError('写作请求下载的图片和原文校验记录不同')
        destination=directory/f'attachment-{index:03d}.png'
        put(destination,data)
        return destination

    with ThreadPoolExecutor(max_workers=8) as readers:
        return list(readers.map(fetch,enumerate(packet['source_media_attachments'],1)))


MARKDOWN_IMAGE = re.compile(r'!\[([^\]\n]*)\]\((<[^>\n]+>|[^\s<>]+?)(?: "[^"\n]*")?\)')


def media_summary(markdown, assets, issues):
    from reader_media import FENCE

    used = {path[6:] for match in MARKDOWN_IMAGE.finditer(FENCE.sub('', markdown))
            if (path := match[2].strip('<>')).startswith('media/') and path[6:] in assets
            and assets[path[6:]]['type']!='reference_page'}
    return {'mermaid': sum(fence[2].strip().lower() == 'mermaid' for fence in FENCE.finditer(markdown)),
            'assets': {name: assets[name] for name in sorted(used)}, 'unavailable': issues}


def preview_body(markdown, assets, folder, session_id):
    """保留完整原稿，预览仅归正 Mermaid 并处理实际可读的图片引用。"""
    from os import environ
    from PIL import Image
    from reader_media import FENCE, normalize_flowcharts

    issues = []
    homes = {Path.home()/'.codex', Path(environ.get('CODEX_HOME', Path.home()/'.codex'))}
    directories = [(home/'generated_images'/session_id).resolve() for home in homes
                   if session_id and re.fullmatch(r'[\w-]+', session_id)]

    def image(match):
        alt, url = match.groups()
        url = url.strip('<>')
        if url.startswith('media/') and url[6:] in assets:
            if assets[url[6:]]['type']=='reference_page':
                issues.append('PDF 原页仅用于核对原文：'+url)
                return ''
            return match[0]
        if directories:
            if url.startswith('file:'):
                local = urlsplit(url)
                url_path = local.path if local.netloc in ('','localhost') else ''
            else:
                url_path = url
            path = Path(re.sub(r'^/(?=[A-Za-z]:/)', '', unquote(url_path)))
            if path.is_absolute() and path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
                path = path.resolve()
                if any(path.is_relative_to(directory) for directory in directories) and path.is_file():
                    try:
                        if path.stat().st_size > 12_000_000:
                            raise ValueError('生成图像超过 12 MB')
                        with Image.open(path) as loaded:
                            loaded.verify()
                        raw = path.read_bytes()
                        name = 'generated-'+sha256(raw).hexdigest()[:20]+path.suffix.lower()
                        (folder/name).write_bytes(raw)
                        assets[name] = {'type': 'generated', 'source_ids': []}
                        return f'![{alt}](media/{name})'
                    except (ValueError, OSError, Image.DecompressionBombError) as error:
                        issues.append(str(error))
        issues.append('略过无法核实的图片引用：'+url)
        return ''

    # 围栏里的 Markdown 是作者引用的代码，原样保留。
    body = normalize_flowcharts(markdown)
    parts, offset = [], 0
    for fence in FENCE.finditer(body):
        parts.extend((MARKDOWN_IMAGE.sub(image, body[offset:fence.start()]), fence[0]))
        offset = fence.end()
    parts.append(MARKDOWN_IMAGE.sub(image, body[offset:]))
    return ''.join(parts), issues
