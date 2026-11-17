"""Repair explicit splits and clipped source blocks using the original plan and file text."""

from copy import deepcopy
from pathlib import PurePosixPath
import re


# A colon can introduce a shared qualifier. Paired punctuation can contain examples or formulas.
PROTECTED = re.compile(r'[：:（）()\[\]【】《》“”「」『』\x60$\\/]')
RELATION = re.compile(r'关系|比较|对比|对照|差异|转换|对应|结合|相互|共同|协同|取舍|权衡|匹配|耦合|交互|并存|区别|与.*之间')
SHORT_TOPIC = re.compile(r'[。？！!?，,；;：:=]|(?:^|\s)[①②③一二三四][、.]|^(?:先|再|例如|比如)|因为|所以|从而|导致|分别')
TOPIC_END = re.compile(r'(?:算法|机制|方法|技术|运算|乘法|计算|指令|函数|模型|定理|证明|变换|流程|协议|结构|约束|判据|条件|频率|容量|带宽|延迟|准确率|代价|门限|周期|吞吐量|速率|布局|配置|参数|方向)\Z')


def split_goal(goal):
    if PROTECTED.search(goal) or RELATION.search(goal) or re.search(r'等(?:$|[\u3400-\u9fff])|之类',goal):
        return [goal]
    parts = [part.strip() for part in re.split(r'[;；、]', goal)]
    if len(parts) == 1:
        # Only split a joined pair if each phrase already names its own complete topic.
        pair = [part.strip() for part in re.split(r'与|以及|和',goal)]
        if len(pair) == 2 and all(len(part) >= 4 and TOPIC_END.search(part) for part in pair):
            parts = pair
    if len(parts) < 2 or any(len(part) < 2 or SHORT_TOPIC.search(part) for part in parts):
        return [goal]
    if any('的' in part for part in parts) and not all('的' in part for part in parts):
        return [goal]
    if any(part.startswith(('及','和','与','或','以及')) for part in parts):
        return [goal]
    # Different phrases joined with an inline conjunction may share an omitted subject or qualifier.
    if any(re.search(r'[与和及或]', part) for part in parts):
        return [goal]
    return parts


def repair_goals(goals):
    edits = 0
    result = {}
    for role, items in goals.items():
        present = set(items)
        repaired = []
        for goal in items:
            parts = split_goal(goal)
            if len(parts) > 1 and len(parts) == len(set(parts)) and not present.intersection(parts):
                present.update(parts)
                repaired.extend(parts)
                edits += 1
            else:
                repaired.append(goal)
        result[role] = repaired
    if set(result['reader']) == set(result['teacher']):
        return goals, 0
    return result, edits


FENCE = re.compile(r'^ {0,3}(\x60{3,}|~{3,})([^\r\n]*)$')
MAX_EXTENSION = 8


def markdown_blocks(rows):
    """Return the exact spans of complete standalone fenced code and display math."""
    opened = None
    for n, row in sorted(rows.items()):
        line = row['text'].rstrip()
        fence = FENCE.fullmatch(line)
        delimiter = line.strip()
        if opened:
            start, marker, count = opened
            if marker in ('$', r'\]'):
                closed = delimiter == ('$$' if marker == '$' else marker)
            else:
                closed = (fence and fence[1][0] == marker and len(fence[1]) >= count
                          and not fence[2].strip())
            if closed:
                yield start, n
                opened = None
        elif fence and not (fence[1][0] == '\x60' and '\x60' in fence[2]):
            opened = n, fence[1][0], len(fence[1])
        elif delimiter in ('$$', r'\['):
            opened = (n, '$' if delimiter == '$$' else r'\]', 0)


def repaired_ranges(row, context, units):
    original = row['source_ranges']
    file_id = row['file_id']
    if (PurePosixPath(row['relative_path']).suffix.lower() not in ('.md','.markdown')
            or not original or any(span['type'] != 'text_line' for span in original)
            or file_id not in units):
        return original
    rows = units[file_id]
    blocks = list(markdown_blocks(rows))
    suggested = deepcopy(original)
    allowed = (context.get('allowed_ranges') or {}).get(file_id, [])
    for index, span in enumerate(original):
        start, end = span['start'], span['end']
        # Only extend an edge when a real delimiter proves the selected block was cut.
        first = next((a for a,b in blocks if a < start <= b and start - a <= MAX_EXTENSION), start)
        last = next((b for a,b in blocks if a <= end < b and b - end <= MAX_EXTENSION), end)
        if (first,last) == (start,end):
            continue
        if (any(n not in rows for n in range(first, start)) or any(n not in rows for n in range(end+1, last+1))
                or any(first <= other['end'] and other['start'] <= last
                       for i,other in enumerate(suggested) if i != index)):
            continue
        if context['mode'] == 'FINALE' and not any(
                scope['type'] == 'text_line' and scope['start'] <= first <= last <= scope['end']
                for scope in allowed):
            continue
        suggested[index] = {**span, 'start':first, 'end':last}
    return suggested


def repair_card(card, context, units):
    """Leave every other field intact and return just the counts of accepted edits."""
    result = deepcopy(card)
    result['goals'], topics = repair_goals(card['goals'])
    spans = 0
    for row in result['material_plan']:
        changed = repaired_ranges(row, context, units)
        spans += sum(a != b for a,b in zip(row['source_ranges'],changed))
        row['source_ranges'] = changed
    return result, {'goals':topics, 'source_ranges':spans}
