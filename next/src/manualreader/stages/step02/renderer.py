from pathlib import Path

RECAP_KEYS = ('context', 'problem', 'claim', 'reason', 'evidence', 'implication')

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
