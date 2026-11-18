"""从 Reader 的当前章节准备 V2.1 任务，生成正文并保存到原有文件预览。"""
import base64
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4

from reader_v21_prompts import PLANNER, WRITER

VERSION = 'v2.1'
POLICY_FILE = Path(__file__).with_name('reader_v21_policies.json')
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


def planner_schema():
    def obj(**fields):
        return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}

    def array(items):
        return {'type': 'array', 'items': items}

    text = {'type': 'string'}
    return obj(
        question_styles=array(obj(pattern=text, explanation_need=text, evidence_question_ids=array(text))),
        goals=obj(reader=array(text), teacher=array(text)),
        material_plan=array(obj(file_id=text, description=text, spans=array(obj(first=text, last=text)))),
    )


def locations(rows):
    runs = []
    for row in rows:
        loc = row['position']
        if (runs and runs[-1]['type'] == loc['type'] and
                loc['start'] >= runs[-1]['start'] and loc['start'] <= runs[-1]['end'] + 1):
            runs[-1]['end'] = max(runs[-1]['end'], loc['end'])
        else:
            runs.append(dict(loc))
    if all('time_seconds' in row for row in rows):
        runs.append({'type':'time_seconds','start':rows[0]['time_seconds'][0],'end':rows[-1]['time_seconds'][1]})
    return runs


def complete_card(packet, draft, custom_instruction=''):
    files = {row['file_id']: row for row in packet['files']}
    scopes = defaultdict(list)
    for row in packet['sources']:
        scopes[row['file_id']].append(row)
    forms = draft['question_styles']
    questions = {q['question_id'] for q in packet['questions']}
    if questions and not forms:
        raise ValueError('读到了上一节的真实提问，请从中提炼本章可以参考的问法')
    for row in forms:
        if (not row['pattern'].strip() or not row['explanation_need'].strip()
                or not row['evidence_question_ids'] or set(row['evidence_question_ids']) - questions):
            raise ValueError('历史问法须来自上一节的真实问题，并带解释方向')
    goals = draft['goals']
    for role in ('reader', 'teacher'):
        titles = [value.strip() for value in goals[role]]
        if not titles or not all(titles) or len(set(titles)) != len(titles):
            raise ValueError('本章的读者和教师目标需要是互不重复的知识点')

    plan, selected, used_files = [], [], set()
    for row in draft['material_plan']:
        fid = row['file_id']
        if fid not in files or fid in used_files or not row['spans']:
            raise ValueError('材料计划需要引用本章实际存在的文件，每个文件登记一次')
        used_files.add(fid)
        units = scopes[fid]
        indices = {source['id']: j for j, source in enumerate(units)}
        source_ids, ranges = [], []
        previous_end = -1
        for span in row['spans']:
            start, end = indices.get(span['first'], -1), indices.get(span['last'], -1)
            if start <= previous_end or end < start:
                raise ValueError('来源范围需要按原件顺序登记，首尾编号必须属于同一文件')
            scope = units[start:end + 1]
            if any(source['context_only'] for source in scope) or scope[-1]['document_index'] - scope[0]['document_index'] != end - start:
                raise ValueError('材料范围包含当前节之外的来源，或把不相邻的原文连成了一个范围')
            previous_end = end
            source_ids.extend(s['id'] for s in scope)
            selected.extend(scope)
            ranges.extend(locations(scope))
        description = row['description'].strip()
        if not description or '\n' in description:
            raise ValueError('材料简介需要一句描述原件内容的话')
        plan.append({**files[fid], 'description': description, 'source_ranges': ranges, 'source_ids': source_ids})
    if not selected:
        raise ValueError('任务卡还没有选中当前章节的原文')
    card = {**packet['reference'], 'question_styles': forms, 'goals': goals, 'material_plan': plan,
            'policy_refs': {'writing_preferences': 'writing-policies.json', 'format_rules': 'writing-policies.json',
                            'policy_version': VERSION},
            'custom_instruction': custom_instruction}
    return card, selected


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


def writer_input(card, policies, material, boundary_context):
    """把讲解所需的内容给作者，完整任务卡和规则仍按原样保存。"""
    context = {
        'chapter': {key: card['chapter'][key] for key in ('number', 'title')},
        'goals': card['goals'],
        'question_styles': [
            {key: row[key] for key in ('pattern', 'explanation_need')}
            for row in card['question_styles']
        ],
    }
    previous = {key: value for key, value in card['previous'].items() if value}
    if previous:
        context['previous'] = previous
    if card['custom_instruction']:
        context['custom_instruction'] = card['custom_instruction']
    rules = {'version': policies['version']}
    for group in ('writing_preferences', 'format_rules'):
        rules[group] = [rule['requirement'] for rule in policies[group]]
    return {'writing_context': context, 'writing_policies': rules,
            'original_material': material, 'boundary_context': boundary_context}


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


def original_images(bridge, sources, folder, preview_folder):
    from reader_execute import source_images
    from urllib.error import URLError

    images, assets, failures, by_url, by_source = [], {}, [], {}, defaultdict(list)
    for source in sources:
        for ref in source['media_items']:
            url = ref['src']
            if url not in by_url:
                directory = folder / f'original-{len(by_url)+1:03d}'
                directory.mkdir()
                try:
                    path, = source_images(bridge, {'mode': 'lesson', 'sources': [{'media_items': [ref]}]}, directory)
                except (ValueError, OSError, URLError) as error:
                    failures.append({'source_id': source['id'], 'message': str(error)})
                    by_url[url] = None
                else:
                    name = f'source-{len(images)+1:03d}{path.suffix}'
                    preview_path = preview_folder / name
                    preview_path.write_bytes(path.read_bytes())
                    images.append(path)
                    assets[name] = {'type': ref.get('kind', 'original'), 'source_ids': []}
                    by_url[url] = name
            name = by_url[url]
            if name and source['id'] not in assets[name]['source_ids']:
                assets[name]['source_ids'].append(source['id'])
                reference = assets[name]['type']=='reference_page'
                by_source[source['id']].append({'kind': assets[name]['type'], 'caption': ref.get('alt', ''),
                                                'attachment_number': list(assets).index(name)+1,
                                                **({} if reference else {'markdown_path': 'media/'+name})})
    return images, assets, by_source, failures


MARKDOWN_IMAGE = re.compile(r'!\[([^\]\n]*)\]\((<[^>\n]+>|[^\s<>]+?)(?: "[^"\n]*")?\)')


def writer_instructions():
    return (WRITER+'\n输入中的原图按 attachment_number 提供。可引用的原图用对应的 images.markdown_path。'
            '标记为 reference_page 的原页只用于核对原文。实际生成新图时，'
            '把工具返回的真实本地路径写进 Markdown，Reader 会复制该文件。\n')


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
