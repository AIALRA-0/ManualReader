"""接收没有独立原件的章节讲解，按统一目录编号生成可阅读的材料。"""
import copy
import re
from source_parser import Document
from reader_blocks import markdown_chunks


def lecture_material(data, identity, previous=None):
    title = data.get('title') or (previous or {}).get('title')
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        raise ValueError('请填写最多 300 字的材料名称')
    incoming = data.get('sections')
    if not isinstance(incoming, list) or not incoming or len(incoming) > 1000:
        raise ValueError('请提交 1 至 1000 个章节')
    incoming_ids=[s.get('id') for s in incoming if isinstance(s,dict)]
    if len(incoming_ids)!=len(set(incoming_ids)):
        raise ValueError('同一次提交的章节编号重复')
    old = copy.deepcopy((previous or {}).get('authored_sections', []))
    if old and {s['id'] for s in old}.issubset({s.get('id') for s in incoming if isinstance(s,dict)}):
        old = []
    for node in incoming:
        if not isinstance(node, dict):
            raise ValueError('每个章节需要是对象')
        positions = {s['id']: i for i, s in enumerate(old)}
        if node.get('id') in positions:
            old[positions[node['id']]] = node
        else:
            parent = node.get('parent_id')
            if parent in positions:
                at=positions[parent]+1
                while at<len(old):
                    ancestor=old[at].get('parent_id')
                    while ancestor and ancestor!=parent:
                        ancestor=old[positions[ancestor]].get('parent_id') if ancestor in positions else None
                    if ancestor!=parent:break
                    at+=1
                old.insert(at,node)
            else:old.append(node)
    ids, names, paths, cleaned, sibling, active = set(), set(), {}, [], {}, []
    doc = Document(identity, title.strip(), 'agent-authored', 'authored')
    for node in old:
        sid, parent = node.get('id'), node.get('parent_id') or None
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,70}', sid) or sid in ids:
            raise ValueError('章节编号重复或格式无效')
        if parent and parent not in paths:
            raise ValueError('请先提交父章节，再提交子章节；父章节必须存在')
        if parent and parent not in active:
            raise ValueError('目录需要按父章节及其全部子章节的顺序提交，不能穿插到其他章节之后')
        active = active[:active.index(parent)+1]+[sid] if parent else [sid]
        index = sibling.get(parent, 0) + (1 if parent else 0)
        sibling[parent] = sibling.get(parent, 0) + 1
        path = paths[parent] + [index] if parent else [index]
        number = '.'.join(map(str, path))
        level = len(path)
        if 'level' in node and node['level'] != level:
            raise ValueError(f'{sid} 的层级与父章节不一致，应为 {level}')
        if 'number' in node and str(node['number']).rstrip('.') != number:
            raise ValueError(f'{sid} 的目录编号应为 {number}')
        heading = node.get('title')
        if not isinstance(heading, str) or not heading.strip() or len(heading) > 300 or '\n' in heading:
            raise ValueError('章节标题需要是一行内的文字，最多 300 字')
        if heading != heading.lstrip():
            raise ValueError('标题不要用空格表示层级，请填写 parent_id')
        prefix = re.match(r'^(\d+(?:\.\d+)*)\.?\s+(.+)$', heading)
        if prefix:
            if prefix[1] != number:
                raise ValueError(f'{sid} 的标题编号应为 {number}')
            heading = prefix[2]
        key = (parent, re.sub(r'\s+', '', heading).casefold())
        if key in names:
            raise ValueError('同一父章节下的标题重复')
        content = node.get('markdown', '')
        if not isinstance(content, str) or len(content) > 200000:
            raise ValueError('章节正文需要是最多 200000 字的 Markdown')
        # 子标题必须登记到目录中，避免正文出现目录里找不到的另一套层级
        outside_fence = re.sub(r'```[\s\S]*?```|~~~[\s\S]*?~~~', '', content)
        if re.search(r'^\s{0,3}#{1,6}\s+', outside_fence, re.M):
            raise ValueError('正文中的子标题请作为子章节提交到 sections 中')
        ids.add(sid); names.add(key); paths[sid] = path
        normalized = {'id': sid, 'parent_id': parent, 'level': level, 'number': number,
                      'title': heading.strip(), 'markdown': content}
        cleaned.append(normalized)
        heading_unit = doc.add(number + '. ' + heading.strip(), 'heading', level)
        heading_unit['id'] = sid + '-heading'; heading_unit['zh'] = heading_unit['en']; heading_unit['en'] = ''
        heading_unit['translatable'] = False
        heading_unit['source_ids'] = [heading_unit['id']]
        heading_unit['sentences'] = [{'id':sid+'-heading-s1','en':'','zh':heading_unit['zh'],'refs':[],'precision':'authored'}]
        row = doc.data['outline'][-1]; generated = row['id']
        row.update(id=sid, passage_id=heading_unit['id'], section_id=sid, parent_id=parent)
        heading_unit['section_id'] = sid; heading_unit['outline_id'] = sid
        doc.current = sid; doc.stack[-1] = (level, sid)
        # 普通文字按空行分段，完整公式和代码内部的空行不拆开。
        blocks = markdown_chunks(content)
        for i, block in enumerate(blocks, 1):
            if not block.strip():
                continue
            pid = sid + '-p' + str(i)
            p = doc.add(block.strip(), 'paragraph')
            p.update(id=pid, en='', zh=block.strip(), outline_id=sid, section_id=sid,
                     translatable=False, source_ids=[pid], refs=[], precision='authored')
            p['sentences'] = [{'id': pid + '-s1', 'en': '', 'zh': block.strip(), 'refs': [], 'precision': 'authored'}]
    doc.data['inventory'] = [{'id': aid, 'passage_id': p['id'], 'kind': p['kind']}
                             for p in doc.data['passages'] for aid in p.get('source_ids', [])]
    doc.data.update(reader_mode='authored', authored_sections=cleaned, original_file='',
                    translation_status='not_required', source='agent-authored')
    return doc.finish()
