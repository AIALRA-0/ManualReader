"""Prepare the shared chapter and answer analysis and check its handoff to the writer."""
import re
import unicodedata

from reader_writer_schema import ID, S, arr, enum, obj


VERSION = 'reader-analysis-v3'
TASK_STEP = re.compile(r'(?im)^[ \t]*(?:[-*+][ \t]+)?(\((?:[a-z]|[1-9][0-9]?)\))(?=[ \t]|[\u3400-\u9fff])')
TECHNICAL_NAME = re.compile(r'[^\W\d_]', re.UNICODE)
OPENING_CHECKS = {'chapter-orientation': 'opening', 'chapter-first-action': 'first_action'}
LIST_ITEM = re.compile(r'^[ ]{0,3}(?:[-*+]|\d+[.)])[ \t]+')
BOLD = re.compile(r'(?<!\*)\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)')
INLINE_MATH = re.compile(r'\\\(([^\n]*?)\\\)|(?<![\\$])\$(?!\$)([^$\n]+?)(?<!\\)\$(?!\$)')
TERM_NAME = re.compile(r'^(.+?)[（(]([^）)]+)[）)]$')
MIXED_NAME = re.compile(r'([A-Za-z][A-Za-z0-9 -]+?)\s*([\u3400-\u9fff]+)')
NAMED_EXPRESSION = re.compile(r'^([\u3400-\u9fff][\u3400-\u9fff ]+)[（(](.+)[）)]$')
NAME_LIST = re.compile(r'\s*(?:和|与|及|、|\band\b)\s*',re.I)
CODE_EXCERPT = re.compile(r'(?:逐字|逐段|原样).{0,20}(?:摘录|引用|展示).{0,8}(?:源码|源代码|原始代码)|从.{0,30}(?:测试代码|源码|源代码|代码文件).{0,16}摘录')
DRAW_CODE = re.compile(r'配图|截图|画出|画一张|用图|时序图|流程图')


def _norm(value):
    return unicodedata.normalize('NFKC', str(value)).replace('`','').casefold().strip()


def _quotes(value):
    from reader_connected import prose_punctuation
    plain = BOLD.sub(r'\1', value)
    plain = INLINE_MATH.sub(lambda match:match[1] or match[2] or '',prose_punctuation(plain))
    return re.sub(r'\s+','', _norm(plain))


def _find_excerpt(markdown, text):
    body=_quotes(markdown)
    quote=_quotes(text)
    if quote and quote in body:
        return quote
    # 摘句可能只带了加粗词的一端。正文确实有加粗时，可以核对显示出来的文字。
    if text.count('**')==1 and '`' not in text and BOLD.search(markdown):
        visible=quote.replace('**','',1)
        if visible and visible in body:
            return visible
    return ''


def _term_names(anchor, terms):
    from reader_term_quality import term_labels
    original = _quotes(anchor)
    described = NAMED_EXPRESSION.fullmatch(anchor.strip())
    labels = {original}
    if described:
        labels.update(_quotes(text) for text in described.groups())
    mixed = MIXED_NAME.fullmatch(anchor.strip())
    matches = set()
    for term in terms:
        name = TERM_NAME.fullmatch(term.get('name') or '')
        if not name:
            continue
        zh, en = _quotes(name[1]), _quotes(name[2])
        bilingual_match = mixed and zh.endswith(_quotes(mixed[2])) and name[2].lower().startswith(mixed[1].strip().lower()+' ')
        declared = {_quotes(label) for label in term_labels(term)}
        if original in declared or bilingual_match:
            matches.add(tuple(sorted(declared | {zh,en})))
    return labels | set(next(iter(matches))) if len(matches)==1 else labels


def _contains_term(text, anchors, raw_anchor):
    if any(anchor in text for anchor in anchors):
        return True
    names=NAME_LIST.split(raw_anchor.strip())
    # 作者名单和并列名称可在原文写出各自全名，仍需同段出现全部名字。
    return len(names)>1 and all(len(_quotes(name))>=2 and _quotes(name) in text for name in names)


def analysis_packet(packet):
    """Supply the scope and short source previews. The full scoped originals remain with the writer."""
    lesson = packet.get('mode') == 'lesson'
    node = packet.get('learning_node') or {}
    questions = [{key: row[key] for key in ('id', 'text', 'source_id', 'parent_question_id', 'question_version')
                  if key in row} for row in packet.get('questions', [])]
    known = {}
    budget = 18000
    steps = []
    sources = packet.get('sources', [])
    source_ids = {row['id'] for row in sources}
    for row in [*sources, *packet.get('reference_blocks', [])]:
        identity = row['id']
        if identity in known:
            continue
        original = row.get('original') or row.get('excerpt') or row.get('text') or row.get('en') or row.get('zh') or ''
        # 题意以原件为准，避免译文里的断句或措辞先入为主。
        preview = (original or row.get('translation') or '') if lesson else row.get('translation') or original
        if lesson and identity in source_ids and row.get('kind') not in ('code','table','explanation'):
            steps.extend(TASK_STEP.findall(_norm(original)))
        chars = min(budget, 2400)
        excerpt = preview[:chars]
        budget -= len(excerpt)
        media=row.get('media_items') or ([row['media']] if row.get('media') else [])
        labels=[m['alt'] for m in media if m.get('alt')]
        if row.get('src') and row.get('alt'):labels.insert(0,row['alt'])
        known[identity] = {'id':identity, 'kind':row.get('kind') or row.get('source_kind'), 'preview':excerpt,
                           'preview_complete':len(excerpt)==len(preview), 'image_labels':labels[:3],
                           'has_image':bool(row.get('src') or any(m.get('src') for m in media))}
    evidence = []
    for key in ('reader_feedback_latest','confirmed_understanding','confirmed_passage'):
        if packet.get(key):
            evidence.append({'id':key,'value':packet[key]})
    for row in packet.get('recent_reader_questions', []):
        if row.get('question_id'):
            evidence.append({'id':row['question_id'],'value':row.get('text','')})
    parents = [{'id':p['id'],'question':p.get('question') or p.get('text'),
                'answer_excerpt':str(p.get('answer_excerpt') or p.get('answer') or '')[:1200]}
               for p in packet.get('parent_answers', []) if p.get('id')]
    term_names=[{'name':term['name'],'aliases':term.get('aliases',[])}
                for term in packet.get('registered_terms',[]) if term.get('name')]
    return {'mode':'lesson' if lesson else 'answer', 'chapter':{k:node[k] for k in ('id','title','status') if k in node},
            'task_ids':[node['id']] if lesson else [q['id'] for q in questions],
            'questions':questions, 'sources':list(known.values()), 'source_groups':packet.get('source_groups', []),
            'source_task_steps':list(dict.fromkeys(steps)), 'reading_context':packet.get('reading_context'),
            'reader_evidence':evidence, 'parent_answers':parents, 'registered_term_names':term_names}


def analysis_schema(packet):
    kinds = ['task_step','first_use','reasoning'] if packet['source_task_steps'] else ['first_use','reasoning']
    gaps = obj(id=ID,kind=enum(kinds),anchor=S,question=S,handling=S)
    extra = obj(id=ID,point=S,basis=S,handling=enum(['inline','defer']))
    sources = [row['id'] for row in packet['sources']]
    evidence = [row['id'] for row in packet['reader_evidence']]
    fields = dict(task_id=enum(packet['task_ids']),reader_evidence_ids=arr(enum(evidence)) if evidence else arr(S,maxItems=0),
                  reader_start=S,task_focus=S,prerequisites=arr(S,maxItems=5),gaps=arr(gaps,maxItems=max(6,len(packet['source_task_steps'])+3)),
                  source_ids=arr(enum(sources)) if sources else arr(S,maxItems=0),
                  author_approach=S,expected_takeaway=S,verification=arr(S,maxItems=4),helpful_extras=arr(extra,maxItems=2),
                  visuals=arr(obj(id=ID,kind=enum(['coordinates','original','diagram','illustration','table']),show=S)),
                  essential_terms=arr(S))
    if packet['mode']=='lesson':
        fields['opening'] = obj(orientation=S, first_action=S)
    item = obj(**fields)
    count = len(packet['task_ids'])
    return obj(mode=enum([packet['mode']]),chapter_title=S if packet['mode']=='lesson' else {'type':'null'},
               items=arr(item,minItems=count,maxItems=count))


def validate_analysis(packet, result):
    if result['mode']!=packet['mode'] or sorted(row['task_id'] for row in result['items'])!=sorted(packet['task_ids']):
        raise ValueError('分析结果需要逐一对应当前章节或本组问题编号')
    sources = {row['id'] for row in packet['sources']}
    evidence = {row['id'] for row in packet['reader_evidence']}
    task_steps = set(packet['source_task_steps'])
    seen = set(OPENING_CHECKS)
    from reader_media import DRAW_REQUEST, LOG_VISUAL, NO_DRAW_REQUEST
    questions={q['id']:q['text'] for q in packet.get('questions',[])}
    for row in result['items']:
        if not set(row['source_ids'])<=sources or not set(row['reader_evidence_ids'])<=evidence:
            raise ValueError('分析引用了本次任务未提供的原文或读者记录')
        if packet['mode']=='lesson' and 'reader_feedback_latest' in evidence and 'reader_feedback_latest' not in row['reader_evidence_ids']:
            raise ValueError('分析需要考虑这次用户直接说的目标或背景')
        if not all(row[k].strip() for k in ('reader_start','task_focus','author_approach','expected_takeaway')):
            raise ValueError('分析需要交代读者起点、当前重点、讲法和读完会得到什么')
        if packet['mode']=='lesson':
            opening = row.get('opening', {})
            if not all(opening.get(key,'').strip() for key in ('orientation','first_action')):
                raise ValueError('先说清读者在动手前要理解什么，再选一个可以跟着做的起步动作')
        identified = []
        for gap in row['gaps']:
            if gap['kind']=='task_step':
                anchor = _norm(gap['anchor'])
                prefix = TASK_STEP.match(anchor+' ')
                label = prefix[1] if prefix else anchor
                if label in task_steps:
                    gap['anchor'] = label
                else:
                    # 有时模型把普通问题误标成题面小问，按原来的解释动作检查。
                    gap['kind'] = 'reasoning'
            if gap['kind']!='reasoning' and not gap['anchor'].strip():
                raise ValueError('题面编号和首次解释的术语需要有可在正文中找到的原词')
            if gap['kind']=='task_step':
                identified.append(_norm(gap['anchor']))
        if identified!=packet['source_task_steps']:
            raise ValueError('分析需要按原件顺序逐项登记本章题面上的编号')
        for item in row['gaps']+row['helpful_extras']:
            if not item['id'] or item['id'] in seen:
                index=len(seen)
                while f'planned-check-{index}' in seen:
                    index+=1
                item['id']=f'planned-check-{index}'
            seen.add(item['id'])
        if 'essential_terms' in row:
            row['essential_terms']=list(dict.fromkeys(name.strip() for name in row['essential_terms']
                                                       if isinstance(name,str) and TECHNICAL_NAME.search(name)))
    for row in result['items']:
        has_original=any(source.get('has_image') for source in packet['sources']
                         if source['id'] in row['source_ids'])
        wants_original='原图' in questions.get(row['task_id'],'')
        written_sources=[]
        visuals=[]
        for index, visual in enumerate(row.get('visuals', []), 1):
            if not visual['show'].strip():
                raise ValueError('每幅规划图需要说明读者要看到的关系')
            if CODE_EXCERPT.search(visual['show']) and not DRAW_CODE.search(visual['show']):
                written_sources.append(visual['show'])
                continue
            identity = visual['id']
            if not identity or identity in seen:
                identity = f'visual-{row["task_id"][:45]}-{index}'
                while identity in seen:
                    identity += '-1'
            visual['id'] = identity
            # 写作者可以画出同一坐标里的变化，而不必把空间位置换成流程节点。
            if visual['kind']=='original' and not has_original and not wants_original and not LOG_VISUAL.search(visual['show']):
                visual['kind']='diagram'
            if visual['kind']=='diagram' and re.search(r'同一.{0,6}坐标|坐标尺度|坐标轴|网格|实际位置|空间位置',visual['show']):
                visual['kind']='coordinates'
            seen.add(visual['id'])
            visuals.append(visual)
        if written_sources:
            row['visuals']=visuals
            row['author_approach']+='\n'+'\n'.join(
                show+'。先用已经提供的原文和真实文件，拿不到原件时说明文件位置，并用材料已有事实解释这一部分'
                for show in written_sources)
    for row in result['items']:
        question=questions.get(row['task_id'],'')
        if not row.get('visuals') and DRAW_REQUEST.search(question) and not NO_DRAW_REQUEST.search(question):
            identity='requested-visual-'+str(len(seen))
            while identity in seen:identity+='-1'
            row['visuals']=[{'id':identity,'kind':'original' if '原图' in question else 'diagram','show':question}]
            seen.add(identity)
    return result


def required_checks(analysis):
    points = {item['id']:(row['task_id'],item) for row in analysis['items']
              for item in row['gaps'] + [extra for extra in row['helpful_extras'] if extra['handling']=='inline']}
    if analysis['mode']=='lesson' and analysis['items'][0].get('opening'):
        task_id = analysis['items'][0]['task_id']
        points.update({identity:(task_id, {'kind':kind}) for identity,kind in OPENING_CHECKS.items()})
    return points


def writer_check_ids(analysis):
    checks = required_checks(analysis)
    if analysis['mode']=='lesson' and analysis['items'][0].get('opening'):
        return [identity for identity, (_, item) in checks.items() if item.get('kind')!='task_step']
    return list(checks)


def _opening_checks(explanation, item, parts):
    """核对题意出现在章首，动手的第一步出现在正文首段。"""
    start = explanation.get('framing', {}).get('start', [])
    if not start or not parts:
        raise ValueError('讲解需要有章首和正文')
    if any(part['id'] in ('opening','opening-question') for part in parts):
        raise ValueError('opening 和 opening-question 是固定的章首位置，请为正文使用不同编号')
    current = next((row.get('markdown','') for row in start if row.get('role')=='current'), '')
    if not current:
        raise ValueError('章首缺少「这一节要弄明白什么」')
    questions = [(gap['id'],_quotes(gap['anchor'])) for gap in item['gaps'] if gap['kind']=='task_step']
    if len(questions)>1:
        items = []
        for line in current.splitlines():
            marker = LIST_ITEM.match(line)
            if marker:
                items.append(line[marker.end():])
            elif items and line.startswith('  '):
                items[-1] += '\n'+line
            elif line.strip():
                items.append('')
        next_index = 0
        for _, label in questions:
            candidates = [(index,text) for index,text in enumerate(items)
                          if _quotes(text).startswith(label)]
            if len(candidates)!=1 or candidates[0][0]<next_index:
                raise ValueError(f'章首需要按题面顺序单独列出 {label}，每小问用一个 Markdown 列表项')
            index, text = candidates[0]
            if not _quotes(text)[len(label):].strip('：:。；;？！?!'):
                raise ValueError(f'章首的 {label} 还需要说清读者要做什么、交什么')
            next_index = index+1
    elif questions and questions[0][1] not in _quotes(current):
        raise ValueError(f'章首还需要交代题面小问 {questions[0][1]} 要做什么')
    return {
        'opening': ('opening', start[0].get('markdown','')),
        'first_action': (parts[0]['id'], re.split(r'\n[ \t]*\n', parts[0]['markdown'].strip(), maxsplit=1)[0]),
        'checked_questions': {identity for identity, _ in questions},
    }


def _term_next_to_quote(markdown, quote, anchors, *, adjacent_prose=False, raw_anchor=''):
    blocks = re.split(r'\n[ \t]*\n', markdown.replace('\r\n','\n'))
    normalized = list(map(_quotes,blocks))
    for index, paragraph in enumerate(normalized):
        if quote not in paragraph:
            continue
        if _contains_term(paragraph,anchors,raw_anchor):
            return True
        # 表中的规则有时在下一段才解释。只接受紧邻且有 Markdown 表格分隔行的块。
        if index and re.search(r'(?m)^\s*\|\s*:?-{3,}', blocks[index-1]):
            if _contains_term(normalized[index-1],anchors,raw_anchor):
                return True
        if adjacent_prose:
            # 同一部分可以先给定义再演示，也可以先演示再给名称。
            for nearby in (index-1,index+1):
                if (0<=nearby<len(blocks) and not re.match(r'^\s*(?:[#>|`~]|[-*+]\s)', blocks[nearby])
                        and _contains_term(normalized[nearby],anchors,raw_anchor)):
                    return True
    return False


def validate_writing_evidence(value, analysis):
    """Each planned point must have an actual passage from the same chapter or answer."""
    problems = writing_evidence_issues(value, analysis)
    if problems:
        raise ValueError('\n'.join(problems))


def writing_evidence_issues(value, analysis):
    required = required_checks(analysis)
    lesson = analysis['mode']=='lesson'
    if lesson:
        task = analysis['items'][0]['task_id']
        explanation = value.get('explanation') or {}
        owners = {task:explanation.get('parts', [])}
        item = analysis['items'][0]
        opening = _opening_checks(explanation,item,owners[task]) if item.get('opening') else {}
    else:
        owners = {a['question_id']:a.get('parts') or a.get('answer_parts',[]) for a in value.get('answers',[])}
        opening = {}
    parts = {(task,part['id']):part['markdown'] for task, rows in owners.items() for part in rows}
    checked_questions = opening.get('checked_questions',set())
    verified = set(checked_questions)
    problems = []
    reported = set()
    for row in value.get('reader_checks',[]):
        identity = row['addresses']
        if identity not in required:
            problems.append('写作证据引用了本轮分析里没有安排的卡点：'+identity)
            continue
        if identity in checked_questions:
            continue
        task, item = required[identity]
        part_id = row['part_id']
        kind = item.get('kind')
        markdown = parts.get((task,part_id),'')
        if opening and kind in ('opening','first_action'):
            expected_part, markdown = opening[kind]
            if part_id!=expected_part:
                problems.append(f'{identity} 需要在 {expected_part} 摘录实际文字')
                reported.add(identity)
                continue
        from reader_prompts import WRITING_VERSION
        if kind=='reasoning' and value.get('writing_version')==WRITING_VERSION:
            spans=[]
            for key in ('premise_quote','mechanism_quote','result_quote'):
                span=_find_excerpt(markdown,row.get(key,''))
                if len(span)<4 or span in spans:
                    problems.append(f'{identity} 在对应正文分别摘录条件、实际动作和结果：{key}')
                    reported.add(identity)
                    break
                spans.append(span)
            if identity not in reported:
                verified.add(identity)
            continue
        quote = _find_excerpt(markdown,row['quote'])
        if len(quote)<8:
            problems.append('写作证据应从同一问题或章节的正文摘录：'+identity)
            reported.add(identity)
            continue
        if kind in ('task_step','first_use'):
            anchors = _term_names(item['anchor'],value.get('terms',[])) if kind=='first_use' else {_quotes(item['anchor'])}
            if not _contains_term(quote,anchors,item['anchor']):
                linked = kind=='first_use' and _term_next_to_quote(markdown,quote,anchors,
                          adjacent_prose=bool(opening or value.get('writing_version')==WRITING_VERSION),
                          raw_anchor=item['anchor'])
                if not linked:
                    problems.append('正文摘录需要对应同段术语原词，或包含本小问题号：'+identity)
                    reported.add(identity)
                    continue
        verified.add(identity)
    if missing := required.keys() - verified - reported:
        problems.append('正文尚未给出这些分析卡点的证据：'+','.join(sorted(missing)))
    return problems


def reading_plan_markdown(task, result):
    """用分析器实际给出的判断生成供读者查看的计划。"""
    title = result.get('chapter_title') or '这组问题准备怎么答'
    questions = {q['id']:q['text'] for q in task['questions']}
    evidence = {row['id']:row['value'] for row in task['reader_evidence']}
    lines = [f'# {title}']
    for row in result['items']:
        if row['task_id'] in questions:
            lines.extend(('', f'## {questions[row["task_id"]]}'))
        lines.extend(('', '### 读者从哪里开始', '', row['reader_start']))
        for identity in row['reader_evidence_ids']:
            value = evidence[identity]
            text = value.get('text') if isinstance(value, dict) else value
            if isinstance(text,list):
                text = '\n'.join(item for item in text if isinstance(item,str))
            if isinstance(text,str) and text:
                lines.extend(('', *('> '+line for line in text.splitlines())))
        lines.extend(('', '### 这次要讲清什么', '', row['task_focus'], ''))
        lines.extend('- '+text for text in row['prerequisites'])
        if row['gaps']:
            lines.extend(('', '### 哪些地方需要讲开', ''))
        for gap in row['gaps']:
            lines.append(f'- {gap["question"]}')
            lines.extend('  '+line for line in gap['handling'].splitlines())
        lines.extend(('', '### 正文准备怎么讲', ''))
        if 'opening' in row:
            lines.extend((row['opening']['orientation'], row['opening']['first_action'], ''))
        lines.append(row['author_approach'])
        for visual in row.get('visuals', []):
            lines.append('- 配图 · '+visual['show'])
        for extra in row['helpful_extras']:
            label = '就地补一句' if extra['handling']=='inline' else '留到用到时再讲'
            lines.extend(('', f'- **{label}**　{extra["point"]}', f'  {extra["basis"]}'))
        lines.extend(('', '### 怎样确认讲清了', '', row['expected_takeaway'], ''))
        lines.extend('- '+text for text in row['verification'])
    return '\n'.join(lines).rstrip()+'\n'
