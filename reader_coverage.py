"""从现有原文、译文、明确的讲解范围和用户阅读记录计算覆盖情况。"""
from collections import Counter, defaultdict
import hashlib
import json
import re
import unicodedata


SCHEMA_VERSION = 1
_IGNORED_ROLES = {'page_furniture', 'metadata', 'whitespace'}
_IMAGES = {'image', 'visual', 'page_image', 'figure', 'scan'}
_FORMULAS = {'equation', 'formula', 'math'}
_DOCUMENT_EXTENSIONS = {'md', 'markdown', 'txt', 'rst', 'adoc', 'html', 'htm', 'docx', 'pdf'}
_PROTECTED = re.compile(
    r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|'
    r'\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|\$[^$\n]+\$|'
    r'https?://[^\s<>\])]+|\b[A-Za-z]:[\\/][^\s<>]+|'
    r'(?<!\w)(?:\.{1,2}/|/)[A-Za-z0-9_./\\-]+|'
    r'(?<!\w)(?:[A-Za-z0-9_.-]+/){2,}[A-Za-z0-9_.-]+|'
    r'(?<!\w)(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,10}\b')
_FORMULA_SPANS = re.compile(r'\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|\$[^$\n]+\$')


def _text(value):
    return value if isinstance(value, str) else ''


def _original(row):
    return next((_text(row.get(k)) for k in ('en', 'raw_text', 'aligned_original', 'zh')
                 if _text(row.get(k)).strip()), '')


def _positions(text):
    """保留原文的字符位置，方便把有效文字与明确的字符区间相交。"""
    protected = bytearray(len(text))
    for match in _PROTECTED.finditer(text):
        protected[match.start():match.end()] = b'\1' * (match.end() - match.start())
    return [i for i, char in enumerate(text) if not protected[i] and char.isalnum()]


def _language(text, declared=None):
    code = _text(declared).lower().replace('_', '-')
    if code in {'zh', 'chinese', 'zh-cn', 'zh-tw', 'zh-hans', 'zh-hant'}:
        return 'zh'
    if code in {'none', 'not-required', 'not_required', 'zxx'}:
        return 'not_required'
    if code.startswith('ja') or code == 'japanese':
        return 'ja'
    if code.startswith('ko') or code == 'korean':
        return 'ko'
    safe = ''.join(text[i] for i in _positions(text))
    if re.search(r'[\u3040-\u30ff\u31f0-\u31ff]', safe):
        return 'ja'
    if re.search(r'[\uac00-\ud7af\u1100-\u11ff]', safe):
        return 'ko'
    han = len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002ebef]', safe))
    other = sum(c.isalpha() and not ('\u3400' <= c <= '\u9fff') for c in safe)
    # 中文句子中的 API、GPU 等缩写不让整段变成待翻译的外文。
    without_acronyms = re.sub(r'\b[A-Z][A-Z0-9_-]{1,}\b', '', _PROTECTED.sub('', text))
    other_without_acronyms = sum(c.isalpha() and not ('\u3400' <= c <= '\u9fff')
                                  for c in without_acronyms)
    if han and (han >= other_without_acronyms or han / max(1, han + other) >= .35):
        return 'zh'
    if any(c.isalpha() for c in safe):
        return code or 'foreign'
    return 'not_required' if safe or text.strip() else 'unknown'


def is_chinese_text(value):
    """判断正文是否以中文表达；公式、路径、网址和英文缩写按原样保留。"""
    return _language(_text(value)) == 'zh'


def valid_preserved_token(row,allow_product_names=False):
    """只允许原样保留短人名或编号，不能借此把外文句子标成已译。"""
    if not isinstance(row,dict) or not isinstance(row.get('text'),str):return False
    token=row['text']
    if not token or token!=token.strip() or len(token)>64 or '\n' in token:return False
    if row.get('reason')=='person_name':
        return bool(re.fullmatch(r"[A-Z][A-Za-z'’.-]*(?: [A-Z][A-Za-z'’.-]*){1,5}",token))
    if row.get('reason')=='identifier':
        shape=bool(re.fullmatch(r'[A-Za-z0-9_./:+#-]+(?: [A-Za-z0-9_./:+#-]+){0,2}',token))
        return shape and bool(re.search(r'[0-9_]',token) or re.fullmatch(r'[A-Z]{2,12}',token))
    if allow_product_names and row.get('reason')=='product_name':
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 &'()+./-]*",token)) and len(token.split())<=8
    return False


def translation_segment_ready(segment,allow_product_names=False):
    """人名和课号已逐字登记时，只判断剩余的应译文字是否为中文。"""
    text=_text(segment.get('text'))
    preserved=segment.get('preserved_tokens',[])
    if not isinstance(preserved,list) or len(preserved)>30 or any(not valid_preserved_token(row,allow_product_names) or row['text'] not in text for row in preserved):return False
    if is_chinese_text(text):return True
    for row in preserved:text=text.replace(row['text'],'')
    return bool(preserved) and (_language(text) in {'zh','not_required'} or not text.strip())


def visual_label_ready(label):
    """图中产品名可原样保留，其余标签仍需要真实的中文翻译。"""
    if label.get('preserve'):
        return bool(label.get('original')) and label.get('original')==label.get('translation') and label.get('preserve_reason') in ('symbol','product_name','unit','numeric','identifier')
    preserved=label.get('preserved_tokens',[])
    if not isinstance(preserved,list) or any(not isinstance(row,dict) or not isinstance(row.get('text'),str) or row['text'] not in label.get('original','') for row in preserved):return False
    return translation_segment_ready({'text':label.get('translation',''),'preserved_tokens':preserved},allow_product_names=True)


def translation_ready(source, translation=None, source_language=None):
    """判断原文能否以中文阅读，外文原件需要在对应位置保存中文译文。"""
    if isinstance(source, dict):
        row = source
        if row.get('kind') in _IMAGES and row.get('visual_language_verified'):
            return bool(row.get('visual_translation_complete'))
        if row.get('translation_segments') and all(translation_segment_ready(s) for s in row['translation_segments']):return True
        if _code(row) or row.get('kind') in _FORMULAS:
            return True
        original = _original(row)
        source_language = source_language or row.get('source_language') or row.get('language')
        if row.get('kind') in _IMAGES:
            original = _text(row.get('ocr_text'))
            source_language = row.get('source_language') or row.get('detected_language')
        translation = _text(row.get('zh')) if translation is None else translation
    else:
        original = _text(source)
    language = _language(original, source_language)
    return language in {'zh', 'not_required'} or (language != 'unknown' and is_chinese_text(translation))


def _code(row):
    if row.get('kind') != 'code':
        return False
    path = _text(row.get('path')).lower()
    extension = path.rsplit('.', 1)[-1] if '.' in path else _text(row.get('language')).lower()
    return extension not in _DOCUMENT_EXTENSIONS


def _eligible(row):
    return (row.get('content_kind') != 'explanation' and not row.get('historical')
            and not row.get('duplicate_of') and row.get('role') not in _IGNORED_ROLES)


def _merge(ranges):
    result = []
    for start, end in sorted(set(ranges)):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _intersect(left, right):
    result = []
    for a, b in left:
        for c, d in right:
            if max(a, c) < min(b, d):
                result.append((max(a, c), min(b, d)))
    return _merge(result)


def _weight(positions, ranges):
    return sum(any(a <= i < b for a, b in ranges) for i in positions)


def _metric(done, total, unit, *, empty='not_required', label=None):
    status = empty if not total else 'complete' if done >= total else 'partial' if done else 'pending'
    return {'done': done, 'total': total,
            'percent': round(done / total * 100, 2) if total else None,
            'status': status, 'unit': unit,
            'label': label or ('未有材料' if not total and empty == 'empty'
                               else '当前没有需要统计的内容' if not total else f'{done}/{total}')}


class _Index:
    def __init__(self, material):
        self.sources = {}
        self.sentences = defaultdict(list)
        self.atoms = defaultdict(list)
        self.duplicate_ids = set()
        for row in material.get('passages', []):
            if not _eligible(row) or not isinstance(row.get('id'), str):
                continue
            pid = row['id']
            if pid in self.sources:
                self.duplicate_ids.add(pid)
            self.sources[pid] = row
            for sentence in row.get('sentences', []):
                if not sentence.get('historical') and isinstance(sentence.get('id'), str):
                    self.sentences[sentence['id']].append((pid, sentence))
            for atom in row.get('source_ids', []):
                if isinstance(atom, str):
                    self.atoms[atom].append(pid)

    def reference(self, row):
        if not isinstance(row, dict):
            raise ValueError('原文范围需要使用对象')
        pid = row.get('passage_id') or row.get('source_id')
        sid = row.get('sentence_id')
        if not pid and sid in self.sentences and len(self.sentences[sid]) == 1:
            pid = self.sentences[sid][0][0]
        source = self.sources.get(pid)
        if not source or pid in self.duplicate_ids:
            raise ValueError('原文范围引用了不存在或重复的段落编号：' + str(pid))
        fid = source.get('file_id', 'primary')
        if row.get('file_id') is not None and row['file_id'] != fid:
            raise ValueError('原文范围的文件编号与段落不对应：' + str(pid))
        original = _original(source)
        base = original
        offset = 0
        if sid is not None:
            matches = [(p, s) for p, s in self.sentences.get(sid, []) if p == pid]
            if len(matches) != 1:
                raise ValueError('原文范围的句子编号不属于该段落：' + str(sid))
            base = _original(matches[0][1])
            offset = self.sentence_offset(source, matches[0][1])
        has_start = row.get('start') is not None
        has_end = row.get('end') is not None
        if has_start != has_end:
            raise ValueError('字符区间需要同时提供 start 和 end')
        start, end = (row['start'], row['end']) if has_start else (0, len(base))
        if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int)
                or not isinstance(end, int) or start < 0 or end > len(base) or start >= end):
            if not (not has_start and not base):
                raise ValueError('字符区间超出了对应原文，或起止位置无效：' + str(pid))
        return {'file_id': fid, 'passage_id': pid, 'sentence_id': sid,
                'start': start, 'end': end, '_offset': offset,
                '_full': sid is None and start == 0 and end == len(original)}

    @staticmethod
    def sentence_offset(source, target):
        original = _original(source)
        cursor = 0
        for sentence in source.get('sentences', []):
            if sentence.get('historical'):
                continue
            value = _original(sentence)
            start = original.find(value, cursor) if value else -1
            if start < 0 and value:
                # 老资料的句子常会合并换行；仍然只采用能逐字核对的位置。
                pattern = r'\s*'.join(re.escape(x) for x in value.split())
                match = re.search(pattern, original[cursor:]) if pattern else None
                start = cursor + match.start() if match else -1
            if sentence is target:
                return start if start >= 0 and original[start:start + len(value)] == value else None
            if start >= 0:
                cursor = start + len(value)
        return None

    def identity(self, identity):
        if not isinstance(identity, str):
            raise ValueError('已讲解的原文编号需要使用字符串')
        if identity in self.sources:
            return self.reference({'passage_id': identity})
        if identity in self.sentences and len(self.sentences[identity]) == 1:
            pid, _ = self.sentences[identity][0]
            return self.reference({'passage_id': pid, 'sentence_id': identity})
        owners = list(dict.fromkeys(self.atoms.get(identity, [])))
        if len(owners) == 1:
            row = self.reference({'passage_id': owners[0]})
            if len(self.sources[owners[0]].get('source_ids', [])) > 1:
                row['_unsegmented'] = True
            return row
        raise ValueError('已讲解的原文编号不存在，或同时属于多个段落：' + identity)


def validate_source_mapping(material, value):
    """校验与正文一起提交的来源映射；调用方负责把返回值与正文一起保存。"""
    if not isinstance(value, dict):
        raise ValueError('正文与来源映射需要使用同一个对象提交')
    index = _Index(material)
    result = {}
    if 'explained_source_ids' in value:
        ids = value['explained_source_ids']
        if not isinstance(ids, list):
            raise ValueError('explained_source_ids 需要使用原文编号列表')
        for identity in ids:
            index.identity(identity)
        result['explained_source_ids'] = list(dict.fromkeys(ids))
    if 'source_ranges' in value:
        ranges = value['source_ranges']
        if not isinstance(ranges, list):
            raise ValueError('source_ranges 需要使用原文范围列表')
        clean = []
        for row in ranges:
            normalized = index.reference(row)
            item = {k: normalized[k] for k in ('file_id', 'passage_id', 'sentence_id')}
            if row.get('start') is not None:
                item.update(start=normalized['start'], end=normalized['end'])
            clean.append(item)
        result['source_ranges'] = clean
    return result


def _has_body(row):
    return (any(_text(row.get(k)).strip() for k in ('markdown', 'text', 'answer', 'zh', 'en'))
            or any(_has_body(x) for x in row.get('blocks', []) if isinstance(x, dict))
            or any(_has_body(x) for x in row.get('passages', []) if isinstance(x, dict)
                   and not x.get('heading') and not x.get('historical')))


def _mapping_rows(index, value, issues, owner):
    result = []
    for key, resolver in (('explained_source_ids', index.identity), ('source_ranges', index.reference)):
        rows = value.get(key, [])
        if not isinstance(rows, list):
            issues.append({'code': 'invalid_mapping', 'owner_id': owner,
                           'message': key + ' 没有按列表保存，这部分不计入覆盖'})
            continue
        for row in rows:
            try:
                resolved = resolver(row)
            except (ValueError, TypeError) as error:
                issues.append({'code': 'invalid_mapping', 'owner_id': owner, 'message': str(error)})
                continue
            if resolved.get('_unsegmented') or resolved['_offset'] is None:
                issues.append({'code': 'unsegmented_mapping', 'owner_id': owner,
                               'passage_id': resolved['passage_id'],
                               'message': '已关联原文，但现有记录没有能逐字核对的范围，暂不计算文字覆盖'})
                continue
            result.append(resolved)
    return result


def _add_scope(scope, row):
    pid = row['passage_id']
    if row['_offset'] is not None:
        scope[pid].append((row['_offset'] + row['start'], row['_offset'] + row['end']))


def _files(material, workspace, index):
    rows = [dict(row) for row in material.get('reading_files', [])]
    known = {row['id'] for row in rows}
    for row in workspace.get('files', []):
        if row.get('id') not in known:
            rows.append(dict(row)); known.add(row.get('id'))
    fids = {p.get('file_id', 'primary') for p in index.sources.values()}
    for fid in sorted(fids - known):
        rows.append({'id': fid, 'name': material.get('original_name') or material.get('title', fid),
                     'kind': material.get('kind'), 'outline': material.get('outline', []),
                     'passage_ids': [p['id'] for p in index.sources.values()
                                     if p.get('file_id', 'primary') == fid]})
    return rows


def _claims(material, workspace, state, index, issues):
    covered = defaultdict(list)
    full = set()
    coarse = []
    explanations = workspace.get('explanations', material.get('explanations', []))
    candidates = []
    seen = set()
    for row in explanations:
        candidates.append((row, row.get('id', 'explanation')))
        for child in row.get('blocks', []) + row.get('passages', []):
            candidates.append((child, child.get('id', row.get('id', 'explanation'))))
    candidates.extend((p, p.get('id', 'explanation')) for p in material.get('passages', [])
                      if p.get('content_kind') == 'explanation' and not p.get('historical'))
    for batch in state.get('rounds', []):
        for question in batch.get('questions', []):
            if (question.get('deleted_at') or question.get('answer_deleted_at')
                    or question.get('answer_outdated') or not question.get('answer')):
                continue
            answer = question['answer']
            candidate = answer if isinstance(answer, dict) else {**question, 'text': _text(answer)}
            candidates.append((candidate, question.get('id', 'answer')))
    for row, owner in candidates:
        fingerprint = (owner, json.dumps({k: row.get(k) for k in ('explained_source_ids', 'source_ranges')},
                                        sort_keys=True, ensure_ascii=False))
        if fingerprint in seen or not _has_body(row):
            continue
        seen.add(fingerprint)
        mappings = _mapping_rows(index, row, issues, owner)
        for mapping in mappings:
            _add_scope(covered, mapping)
            if mapping['_full']:
                full.add(mapping['passage_id'])
        if not mappings:
            links = row.get('source_links', [])
            if not links and row.get('answer') and row.get('passage_id'):
                links = [{'passage_id': row['passage_id'], 'sentence_id': row.get('sentence_id')}]
            targets = set()
            for link in links if isinstance(links, list) else []:
                try:
                    targets.add(index.reference(link)['passage_id'])
                except (ValueError, TypeError):
                    pass
            if targets:
                coarse.append({'owner_id': owner, 'passage_ids': sorted(targets)})
    return {pid: _merge(ranges) for pid, ranges in covered.items()}, full, coarse


def coverage_payload(material, workspace_state, reader_state, *, include_missing=True):
    """返回可直接展示的统计；不会更改材料、来源编号、笔记或阅读记录。"""
    workspace = workspace_state or {}
    state = reader_state or {}
    index = _Index(material)
    issues = []
    files = _files(material, workspace, index)
    explained, full_explanations, coarse = _claims(material, workspace, state, index, issues)
    inventory = []
    outline_ok = set()
    positions = {}
    translated = defaultdict(list)
    languages = {}
    objects = {}
    object_rows = []
    code_keys = {}
    inventory_keys = set()
    for file in files:
        fid = file['id']
        document = file.get('document') or (material if fid == 'primary' else {})
        stored = next((f for f in workspace.get('files', []) if f.get('id') == fid), {})
        document = stored.get('document') or document
        members = [p for p in index.sources.values() if p.get('file_id', 'primary') == fid]
        rows = document.get('inventory')
        if rows is None:
            rows = [{'id': atom, 'passage_id': p['id'], 'kind': p.get('kind')}
                    for p in members for atom in p.get('source_ids', [])]
        if document is material:
            rows = [row for row in rows if row.get('passage_id') not in index.sources
                    or index.sources[row['passage_id']].get('file_id', 'primary') == fid]
        owned = Counter(atom for p in members for atom in p.get('source_ids', []))
        declared = Counter(row.get('id') for row in rows)
        for row in rows:
            if row.get('role') in _IGNORED_ROLES:
                continue
            key = (fid, row.get('id'), row.get('passage_id'))
            if key in inventory_keys:
                continue
            inventory_keys.add(key)
            pid = row.get('passage_id')
            valid = (pid in index.sources and index.sources[pid].get('file_id', 'primary') == fid
                     and row.get('id') in index.sources[pid].get('source_ids', [])
                     and owned[row.get('id')] == 1 and declared[row.get('id')] == 1
                     and pid not in index.duplicate_ids)
            inventory.append({'file_id': fid, 'passage_id': pid, 'done': valid})
            if not valid:
                issues.append({'code': 'source_registration', 'file_id': fid, 'passage_id': pid,
                               'message': '来源编号尚未唯一地对应到原文片段：' + str(row.get('id'))})
        outline = file.get('outline', document.get('outline', []))
        valid_sections = set()
        for row in outline:
            target = index.sources.get(row.get('passage_id'))
            if target and target.get('file_id', 'primary') == fid:
                valid_sections.add(row.get('section_id') or row.get('id'))
            else:
                issues.append({'code': 'outline_target', 'file_id': fid,
                               'message': '原文目录尚未对应到有效片段：' + str(row.get('id'))})
        for p in members:
            pid = p['id']
            if not p.get('source_ids') and not p.get('display_generated'):
                inventory.append({'file_id': fid, 'passage_id': pid, 'done': False})
                issues.append({'code': 'source_registration', 'file_id': fid, 'passage_id': pid,
                               'message': '原文片段还没有登记来源编号'})
            if p.get('section_id') in valid_sections:
                outline_ok.add(pid)
            original = _original(p)
            kind = p.get('kind')
            if _code(p):
                code_keys[pid] = (fid, p.get('module_id') or p.get('path') or fid)
            elif kind in _IMAGES:
                objects[pid] = 'image'
            elif kind in _FORMULAS:
                objects[pid] = 'formula'
            else:
                positions[pid] = _positions(original)
                if kind == 'table':
                    objects[pid] = 'table'
            if pid in objects:
                object_rows.append((pid, objects[pid], 0, len(original)))
            if pid in positions:
                code_spans = [m.span() for m in re.finditer(r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`', original)]
                for formula in _FORMULA_SPANS.finditer(original):
                    if not any(a <= formula.start() < b for a, b in code_spans):
                        object_rows.append((pid, 'formula', formula.start(), formula.end()))
            declaration = p.get('source_language') or p.get('language') or document.get('language')
            if kind in _IMAGES:
                original = _text(p.get('ocr_text'))
                declaration = p.get('source_language') or p.get('detected_language')
            languages[pid] = _language(original, declaration)
            if kind in _IMAGES and p.get('visual_translation_complete'):
                languages[pid] = 'not_required' if p.get('visual_language_status')=='no_text' else 'foreign'
            if pid not in positions:
                continue
            if languages[pid] in {'zh', 'not_required'}:
                translated[pid].append((0, len(_original(p))))
                continue
            if translation_ready(p):
                translated[pid].append((0, len(_original(p))))
            else:
                for sentence in p.get('sentences', []):
                    if sentence.get('historical'):
                        continue
                    if is_chinese_text(sentence.get('zh')):
                        offset = index.sentence_offset(p, sentence)
                        if offset is not None:
                            translated[pid].append((offset, offset + len(_original(sentence))))

    all_scope = {pid: [(0, len(_original(p)))] for pid, p in index.sources.items()}
    read_records = state.get('read_passages', {})

    def summary(scope, manual_ids=None, file_ids=None,include_excluded=False):
        if not include_excluded:scope={pid:ranges for pid,ranges in scope.items()if pid not in excluded}
        ids = set(scope)
        chars = sum(_weight(positions.get(pid, []), ranges) for pid, ranges in scope.items())
        translated_chars = sum(_weight(positions.get(pid, []), _intersect(ranges, translated.get(pid, [])))
                               for pid, ranges in scope.items())
        explained_chars = sum(_weight(positions.get(pid, []), _intersect(ranges, explained.get(pid, [])))
                              for pid, ranges in scope.items())
        registration = [row for row in inventory if row['passage_id'] in ids]
        # 全部文件范围也要显示尚未对应到任何段落的原始来源。
        scope_files = set(file_ids) if file_ids is not None else {
            index.sources[pid].get('file_id', 'primary') for pid in ids}
        if ids == set(index.sources):
            registration = inventory
        else:
            registration.extend(row for row in inventory if row['passage_id'] not in index.sources
                                and row['file_id'] in scope_files)
        result = {
            'source_registration': _metric(sum(r['done'] for r in registration), len(registration),
                                           'source_units', empty='empty'),
            'outline': _metric(len(ids & outline_ok), len(ids), 'source_passages', empty='empty'),
        }
        for name, done in (('translation', translated_chars), ('explanation', explained_chars)):
            metric = _metric(done, chars, 'effective_characters',
                             empty='empty' if not ids else 'not_required',
                             label='未有材料' if not ids else None)
            metric['text'] = {k: v for k, v in metric.items()}
            metric['objects'] = {}
            for kind in ('image', 'table', 'formula'):
                candidates = [(pid, a, b) for pid, k, a, b in object_rows if k == kind and pid in ids
                              and (a == b or _intersect([(a, b)], scope[pid]))]
                if name == 'translation':
                    needed = candidates if kind != 'formula' else []
                    complete = [(pid, a, b) for pid, a, b in needed if languages[pid] in {'zh', 'not_required'}
                                or (languages[pid] != 'unknown'
                                    and _language(_text(index.sources[pid].get('zh'))) == 'zh')]
                    if kind == 'image':
                        complete = [(pid,a,b) for pid,a,b in needed if translation_ready(index.sources[pid])]
                    if kind == 'table':
                        complete = [(pid, a, b) for pid, a, b in needed
                                    if _weight(positions.get(pid, []), translated.get(pid, []))
                                    == len(positions.get(pid, [])) and languages[pid] != 'unknown']
                else:
                    needed = candidates
                    complete = [(pid, a, b) for pid, a, b in needed
                                if pid in full_explanations or (a < b and any(
                                    start <= a and end >= b for start, end in explained.get(pid, [])))]
                object_metric = _metric(len(complete), len(needed), 'objects')
                object_metric['not_required_total'] = len(candidates) - len(needed)
                metric['objects'][kind] = object_metric
            selected_code = {key for pid, key in code_keys.items() if pid in ids}
            complete_code = {key for key in selected_code if all(pid in full_explanations
                            for pid, own in code_keys.items() if own == key and pid in ids)}
            metric['code'] = (_metric(0, 0, 'files_or_modules', label='代码按原样保留') if name == 'translation'
                              else _metric(len(complete_code), len(selected_code), 'files_or_modules'))
            metric['code']['not_required_total'] = len(selected_code) if name == 'translation' else 0
            metric['pending_language'] = sum(languages[pid] == 'unknown' for pid in ids
                                             if pid not in code_keys and objects.get(pid) != 'formula')
            metric['coarse_associations'] = sum(bool(ids.intersection(row['passage_ids'])) for row in coarse) if name == 'explanation' else 0
            if not chars and ids:
                required = [v for v in metric['objects'].values() if v['total']]
                if metric['code']['total']:
                    required.append(metric['code'])
                if required:
                    metric['status'] = ('complete' if all(v['status'] == 'complete' for v in required)
                                        else 'partial' if any(v['done'] for v in required) else 'pending')
                    metric['label'] = '请分别查看图片、表格、公式和代码的数量'
                elif name == 'translation':
                    metric['label'] = '当前内容按原样保留，不需要翻译文字'
            result[name] = metric
        manual = (set(manual_ids) if manual_ids is not None else ids)-(set()if include_excluded else set(excluded))
        passages = {p['id']: p for p in material.get('passages', []) if isinstance(p.get('id'), str)}
        manual = {pid for pid in manual if pid in passages and not passages[pid].get('heading')
                  and not passages[pid].get('historical') and not passages[pid].get('duplicate_of')
                  and passages[pid].get('role') not in _IGNORED_ROLES
                  and (_original(passages[pid]).strip() or passages[pid].get('kind') in _IMAGES)}
        result['manual_reading'] = _metric(sum(bool(read_records.get(pid)) for pid in manual), len(manual),
                                           'passages', empty='empty')
        return result

    all_manual = {p['id'] for p in material.get('passages', []) if isinstance(p.get('id'), str)}
    from reader_learning_scope import excluded_sources
    excluded,outdated=excluded_sources(material,workspace);learning_scope={pid:ranges for pid,ranges in all_scope.items()if pid not in excluded}
    result = {'schema_version': SCHEMA_VERSION, 'material_id': material.get('id'),'material_title':material.get('title',''),
              'overall': summary(learning_scope, all_manual-set(excluded)), 'original_overall':summary(all_scope, all_manual,include_excluded=True)if excluded else None,'learning_exclusions':{'count':len(excluded),'source_ids':sorted(excluded),'outdated_source_ids':outdated,'rule':'已排除内容保留原件与旧记录，不计入学习范围，也不算已经讲解或读完'}, 'files': [], 'chapters': [], 'issues': issues,
              'rules': [
                  '翻译和讲解的文字百分比按原文有效字符数计算，图片、表格、公式和代码另列数量',
                  '中文原件可以直接阅读；外文只有在对应范围保存了中文译文时才计入翻译覆盖',
                  '公式、代码、路径和网址按原样保留；扫描页和图片的语言尚未识别时显示待识别',
                  '讲解和回答需要随正文保存明确的来源编号或字符区间；整篇参考关系显示为已关联，尚未细分',
                  '已读和完成由用户手动记录；生成译文、讲解或回答不会改变用户的阅读状态',
              ]}
    for file in files:
        scope = {pid: ranges for pid, ranges in all_scope.items()
                 if index.sources[pid].get('file_id', 'primary') == file['id']}
        result['files'].append({'file_id': file['id'], 'name': file.get('name') or file.get('title') or file['id'],
                                'kind': file.get('kind'), **summary(scope, file_ids=[file['id']])})
    nodes = workspace.get('learning_outline', material.get('learning', {}).get('nodes', []))
    if not nodes and material.get('reader_mode') == 'authored':
        nodes = [{**n, 'legacy_section_ids': [n['id']]} for n in material.get('authored_sections', [])]
    explanations = {e['id']: e for e in workspace.get('explanations', material.get('explanations', []))}
    scopes = {}
    manual_scopes = {}
    by_id = {n['id']: n for n in nodes}

    def own_scope(node, visiting=None):
        identity = node['id']
        if identity in scopes:
            return scopes[identity]
        visiting = (visiting or set()) | {identity}
        scope = defaultdict(list)
        links = list(node.get('source_links', []))
        for eid in node.get('explanation_ids', []):
            links.extend(explanations.get(eid, {}).get('source_links', []))
        for link in links:
            try:
                _add_scope(scope, index.reference(link))
            except (ValueError, TypeError) as error:
                issues.append({'code': 'chapter_source', 'learning_node_id': identity, 'message': str(error)})
        parent = by_id.get(node.get('parent_id'))
        if not scope and parent and parent['id'] not in visiting:
            scope.update({pid: list(ranges) for pid, ranges in own_scope(parent, visiting).items()})
        scopes[identity] = {pid: _merge(ranges) for pid, ranges in scope.items()}
        return scopes[identity]

    for node in nodes:
        own_scope(node)
        manual_scopes[node['id']] = {p['id'] for p in material.get('passages', [])
                                    if p.get('explanation_id') in node.get('explanation_ids', [])
                                    or p.get('section_id') in node.get('legacy_section_ids', [])}
    for node in nodes:
        descendants = {node['id']}
        while True:
            expanded = descendants | {n['id'] for n in nodes if n.get('parent_id') in descendants}
            if expanded == descendants:
                break
            descendants = expanded
        scope = defaultdict(list)
        manual = set()
        for identity in descendants:
            for pid, ranges in scopes[identity].items():
                scope[pid].extend(ranges)
            manual.update(manual_scopes[identity])
        scope = {pid: _merge(ranges) for pid, ranges in scope.items()}
        result['chapters'].append({'learning_node_id': node['id'], 'title': node.get('title', ''),
                                   'number': node.get('number', ''), 'parent_id': node.get('parent_id'),
                                   'descendant_count': len(descendants) - 1,
                                   **summary(scope, manual),
                                   'own': summary(scopes[node['id']], manual_scopes[node['id']])})
    if include_missing:
        # 缺项直接使用上面计算百分比时的字符区间和对象规则，不另猜测覆盖情况。
        missing={'translation':[],'explanation':[]}
        file_names={f['id']:f.get('name')or f['id']for f in files}
        for pid,ranges in learning_scope.items():
            p=index.sources[pid];original=_original(p);chars=positions.get(pid,[])
            for name,claims in (('translation',translated),('explanation',explained)):
                total=_weight(chars,ranges);done=_weight(chars,_intersect(ranges,claims.get(pid,[])));objects_missing=[]
                for object_pid,kind,start,end in object_rows:
                    if object_pid!=pid:continue
                    if name=='translation':
                        complete=kind=='formula'or (translation_ready(p)if kind=='image'else _weight(chars,translated.get(pid,[]))==len(chars)and languages[pid]!='unknown')
                    else:complete=pid in full_explanations or start<end and any(a<=start and b>=end for a,b in explained.get(pid,[]))
                    if not complete:objects_missing.append(kind)
                if name=='explanation'and pid in code_keys and pid not in full_explanations:objects_missing.append('code')
                if done==total and not objects_missing:continue
                spans=[];cursor=0
                for a,b in _merge(claims.get(pid,[]))+[(len(original),len(original))]:
                    if a>cursor and _weight(chars,[(cursor,a)]):spans.append({'start':cursor,'end':a,'text':original[cursor:a]})
                    cursor=max(cursor,b)
                chapters=[n for n in nodes if pid in scopes.get(n['id'],{})]
                chapter=max(chapters,key=lambda n:str(n.get('number','')).count('.'))if chapters else {}
                missing[name].append({'passage_id':pid,'file_id':p.get('file_id','primary'),'file_name':file_names.get(p.get('file_id','primary'),'原文'),'learning_node_id':chapter.get('id'),'chapter':(' '.join([str(chapter.get('number','')),chapter.get('title','')])).strip(),'excerpt':(spans[0]['text']if spans else original or p.get('alt')or p.get('caption')or p.get('kind',''))[:260],'missing_characters':total-done,'missing_objects':dict(Counter(objects_missing)),'ranges':[{'start':v['start'],'end':v['end']}for v in spans],'page_ids':list(dict.fromkeys(r.get('page_id')for r in p.get('refs',[])if r.get('page_id')))})
        result['missing']=missing
    # 指纹包含统计实际消费的输入；即使手改文件没有递增 revision，也会变化。
    fingerprint = json.dumps({'schema_version': SCHEMA_VERSION, 'material': material,
                              'workspace': workspace, 'reader_state': state},
                             ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    result['revision'] = hashlib.sha256(fingerprint.encode('utf8')).hexdigest()[:24]
    result['input_revisions'] = {'workspace': workspace.get('revision', 0), 'reader_state': state.get('revision', 0)}
    return result
