"""Pure planning contracts and assembly. Models only decide goals and evidence ranges."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
MODES = ('NORMAL', 'INTRO', 'FINALE', 'ANSWER')
VERSION = 'STEP-02'
CARD_KEYS = ('project', 'chapter', 'versions', 'progress', 'previous', 'question_styles',
             'goals', 'material_plan', 'policy_refs', 'custom_instruction')
MATERIAL_KEYS = ('file_id', 'relative_path', 'status', 'added_round', 'progress_before_pct',
                 'description', 'source_ranges', 'source_ids')
POLICY_REFS = {'writing_preferences': 'writing-policies.json', 'format_rules': 'writing-policies.json',
               'policy_version': 'v2-frozen-1'}
RULE_TITLES = {
    'explicit-subject': '主体与动作明确', 'fact-led-transition': '事实自然推动下一步',
    'concrete-process': '用实际过程建立理解', 'no-unneeded-defensiveness': '避免无必要的防御性解释',
    'evidence-for-causes': '解释必要条件而不编造原因', 'chinese-punctuation': '中文标点',
    'chinese-colon': '中文冒号', 'parallel-item-indentation': '并列项与缩进',
    'no-single-item-list': '单一事项不使用并列格式', 'cjk-latin-spacing': '中英文与数字间距',
    'markdown-headings': 'Markdown 标题', 'latex': 'LaTeX 公式', 'code-and-logs': '代码与日志',
    'tables': '表格', 'images-and-media': '图片与媒体', 'mermaid-direction': 'Mermaid 与图示方向',
    'protected-content': '原文与受保护内容',
}


def digest(value):
    return sha256(value if isinstance(value, bytes) else json.dumps(value, sort_keys=True,
                          ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def prompt(mode):
    if mode not in MODES:
        raise ValueError('未知的规划模式')
    return (HERE / f'reader_step02_{mode.lower()}.txt').read_bytes()


def policies():
    value = json.loads((HERE / 'reader_step02_policies.json').read_bytes())
    rules(value)
    return value


def rules(value):
    ids = list(RULE_TITLES)
    if (value.get('version') != POLICY_REFS['policy_version']
            or [r.get('rule_id') for r in value.get('writing_preferences', [])] != ids[:5]
            or [r.get('rule_id') for r in value.get('format_rules', [])] != ids[5:]):
        raise ValueError('规则版本或顺序不符：STEP-02 需要用户冻结的 5 项写作偏好与 12 项格式规范')
    return value


def model_schema(file_ids, citations, mode):
    def obj(**props):
        return {'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False}
    def arr(item):
        return {'type': 'array', 'items': item}
    s = {'type': 'string'}
    coord = {'type': ['number', 'null']}
    styles = arr(obj(pattern=s, explanation_need=s,
                     evidence_refs=arr({'type': 'string', **({'enum': citations} if citations else {})})))
    if not citations:
        styles['maxItems'] = 0
    return obj(question_styles=styles,
               goals=obj(reader=arr(s), teacher=arr(s)),
               material_plan=arr(obj(file_id={'type': 'string', **({'enum': file_ids} if file_ids else {})},
                                     description=s, source_ranges=arr(obj(type=s, start=coord, end=coord)))))


def exact_keys(value, names, field):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError(f'{field} 有缺失或未授权字段')


def short(value, field, limit):
    if not isinstance(value, str) or not value.strip() or '\n' in value or len(value.strip()) > limit:
        raise ValueError(f'{field} 应是一条简短的内容')
    return value.strip()


def range_checked(r, entry):
    exact_keys(r, ('type', 'start', 'end'), 'source_ranges')
    kind, start, end = r['type'], r['start'], r['end']
    if kind == 'whole_file' and start is None and end is None:
        return dict(r)
    if kind == 'subtitle_time' and entry['type'] == 'subtitle_sid':
        bounds = entry.get('time_bounds')
        if (bounds and type(start) in (int, float) and type(end) in (int, float)
                and bounds[0] <= start <= end <= bounds[1]):
            return dict(r)
    elif kind == entry['type'] and type(start) is int and type(end) is int:
        if start <= end and start in entry['positions'] and end in entry['positions']:
            return dict(r)
    raise ValueError(f"source_ranges: {entry['file_id']} 没有这组原件位置 ({kind}: {start}–{end})")


def assemble(context, catalog, draft):
    mode = context['mode']
    exact_keys(draft, ('question_styles', 'goals', 'material_plan'), 'model_output')
    if not isinstance(draft['question_styles'], list) or not isinstance(draft['material_plan'], list):
        raise ValueError('question_styles / material_plan 必须是数组')
    inherited = deepcopy(context['approved_styles'])
    evidence = {x['ref']: x['question_id'] for x in context['unprocessed_questions']}
    for index, raw in enumerate(draft['question_styles']):
        exact_keys(raw, ('pattern', 'explanation_need', 'evidence_refs'), 'question_styles')
        pattern = short(raw['pattern'], f'question_styles[{index}].pattern', 130)
        need = short(raw['explanation_need'], f'question_styles[{index}].explanation_need', 105)
        if re.search(r'\b[A-Z][A-Za-z0-9]{0,30}\b|\d|\\[A-Za-z]+|[=\\$]', need):
            raise ValueError('question_styles.explanation_need 应描述能在其他材料中使用的解释偏好')
        refs = raw['evidence_refs']
        if not isinstance(refs, list) or not refs or any(type(ref) is not str or ref not in evidence for ref in refs):
            raise ValueError('question_styles.evidence_refs 引用了本轮没有提供的真实提问')
        inherited.append({'pattern': pattern, 'explanation_need': need,
                          'evidence_question_ids': list(dict.fromkeys(evidence[r] for r in refs))})
    goals = draft['goals']
    exact_keys(goals, ('reader', 'teacher'), 'goals')
    for group in ('reader', 'teacher'):
        if not isinstance(goals[group], list) or not goals[group]:
            raise ValueError(f'goals.{group} 需要至少一个知识主题')
        group_items = [short(v, f'goals.{group}', 150) for v in goals[group]]
        if len(group_items) != len(set(group_items)):
            raise ValueError(f'goals.{group} 含重复知识主题')
        goals[group] = group_items
    if set(goals['reader']) == set(goals['teacher']):
        raise ValueError('goals.reader 与 goals.teacher 需要独立输出')
    material_plan, seen, diagnostics = [], set(), []
    files = {r['file_id']: r for r in context['files']}
    for item in draft['material_plan']:
        exact_keys(item, ('file_id', 'description', 'source_ranges'), 'material_plan')
        fid = item['file_id']
        if type(fid) is not str or fid in seen or fid not in catalog or fid not in files:
            raise ValueError('material_plan.file_id 重复、未知，或没有可核验的原件')
        if context.get('allowed_selection') is not None and fid not in context['allowed_selection']:
            raise ValueError('material_plan.file_id 不属于本轮已保存讲解的材料范围')
        seen.add(fid)
        original = files[fid]
        if not isinstance(item['source_ranges'], list):
            raise ValueError('material_plan.source_ranges 应是数组')
        ranges = [range_checked(row, catalog[fid]) for row in item['source_ranges']]
        if not ranges:
            diagnostics.append({'file_id': fid, 'reason': 'no_confirmed_locator'})
        if len({digest(row) for row in ranges}) != len(ranges):
            raise ValueError('material_plan.source_ranges 含重复位置')
        if mode == 'FINALE':
            chapter_ranges = context.get('allowed_ranges', {}).get(fid, [])
            if not ranges or any(r['type']=='whole_file' or not any(
                    x['type'] == r['type'] and x['start'] <= r['start'] <= r['end'] <= x['end']
                    for x in chapter_ranges) for r in ranges):
                raise ValueError('FINALE 选取的原件范围超出能核对的已保存讲解')
        row = {'file_id': fid, 'relative_path': original['relative_path'], 'status': 'active',
               'added_round': context['added_rounds'][fid], 'progress_before_pct': original['progress_pct'],
               'description': short(item['description'], 'material_plan.description', 220),
               'source_ranges': ranges, 'source_ids': []}
        material_plan.append(row)
    if not material_plan and mode != 'ANSWER':
        raise ValueError('material_plan 没有选择实际原件')
    if mode == 'ANSWER':
        fixed = context['fixed']
        if not material_plan and not any(fixed['context'].values()):
            raise ValueError('ANSWER 未选择原件，也没有可用于本题的真实上下文')
        return ({'question': deepcopy(fixed['question']), 'question_styles': inherited, 'goals': goals,
                 'material_plan': material_plan, 'context': deepcopy(fixed['context']),
                 'policy_refs': POLICY_REFS.copy(), 'custom_instruction': fixed['custom_instruction']}, diagnostics)
    fixed = context['fixed']
    semantic = {'question_styles': inherited, 'goals': goals, 'material_plan': material_plan, 'policy_refs': POLICY_REFS.copy()}
    return ({k: deepcopy((semantic if k in semantic else fixed)[k]) for k in CARD_KEYS}, diagnostics)


def link_sources(card, indexes, catalog):
    """Link registered IDs to chosen ranges without changing the chosen ranges."""
    card = deepcopy(card)
    reports = []
    for row in card['material_plan']:
        fid = row['file_id']
        indexed = indexes.get(fid, {})
        matches, coverage = [], set()
        positions = catalog[fid]['positions']
        source_rows = indexed.get('rows', []) if indexed.get('verified') else []
        spans = row['source_ranges']
        for src in source_rows:
            for span in spans:
                kind = span['type']
                if kind == 'whole_file':
                    located = src.get('position', {})
                elif kind == 'subtitle_time':
                    located = src.get('time', {})
                else:
                    located = src.get('position', {})
                if not located or (kind != 'whole_file' and located.get('type') != kind):
                    continue
                lower = located['start'] if kind == 'whole_file' else max(span['start'], located['start'])
                upper = located['end'] if kind == 'whole_file' else min(span['end'], located['end'])
                if lower <= upper:
                    matches.append(src['id'])
                    if located['type'] == catalog[fid]['type']:
                        covered = [p for p in positions if lower <= p <= upper]
                        coverage.update(covered)
                    elif kind == 'subtitle_time' and src.get('position', {}).get('type') == 'subtitle_sid':
                        m = src['position']
                        coverage.update(p for p in positions if m['start'] <= p <= m['end'])
        row['source_ids'] = list(dict.fromkeys(matches))
        expected = {p for p in positions if any(s['type'] == 'whole_file' or
                    (s['type'] == catalog[fid]['type'] and s['start'] <= p <= s['end']) for s in spans)}
        unresolved = sorted(expected - coverage)
        reports.append({'file_id': fid, 'source_count_before': 0, 'source_count_after': len(row['source_ids']),
                        'index_status': 'linked_partial' if unresolved and matches else
                        'linked' if matches else 'awaiting_verified_source_index',
                        'unlinked_positions': unresolved, 'reason': indexed.get('reason')})
    return card, reports


def table(rows, labels=('字段', '内容')):
    def cell(value):
        return str(value if value is not None else '—').replace('|', '\\|').replace('\n', '<br>')
    return ['| ' + ' | '.join(labels) + ' |', '|---|---|',
            *(f'| {cell(k)} | {cell(v)} |' for k, v in rows)]


def render(card, value):
    rules(value)
    answer = 'question' in card
    if not answer:
        from reader_chapter_sources import task_markdown
        return task_markdown(card, value, policy_names=RULE_TITLES)
    lines = ['# 问答写作任务卡', '', '## 用户原始问题', '', card['question']['text'],
             '', '## 上次提问的宏观风格', '']
    if card['question_styles']:
        lines += table([(q['pattern'], q['explanation_need']) for q in card['question_styles']],
                       ('提问风格', '解释强化方向'))
    for k, title in [('reader', '读者目标'), ('teacher', '教师目标')]:
        lines += ['', '## ' + title, '', *['- ' + x for x in card['goals'][k]]]
    lines += ['', '## 依据材料']
    for row in card['material_plan']:
        lines += ['', *table([(k, v) for k, v in row.items() if k != 'source_ids'])]
    lines += ['', '## 当前问题的上下文', '']
    for name, item in card['context'].items():
        if item:
            lines += ['### ' + name, '', item if isinstance(item, str) else json.dumps(item, ensure_ascii=False), '']
    for k, title in [('writing_preferences', '写作规范'), ('format_rules', '格式规范')]:
        lines += ['', '## ' + title]
        for r in value[k]:
            lines += ['', '### ' + RULE_TITLES[r['rule_id']], '', r['requirement'], '', '**Bad**', '',
                      r['bad'], '', '**Good**', '', r['good']]
    if card['custom_instruction']:
        lines += ['', '## 用户特殊写作要求', '', card['custom_instruction']]
    return '\n'.join(lines) + '\n'
