"""只缩短传给写作者的来源编号；原文、代码和问题编号始终保留。"""
import re

_SINGLES={'source_id','passage_id','first_source_id','last_source_id','file_id'}
_LISTS={'source_ids','passage_ids','required_source_ids','candidate_original_ids','explained_source_ids','owned_source_ids','for_source_ids'}
_LITERAL_MARKDOWN = re.compile(r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`')
_SOURCE_LINK = re.compile(r'\]\((source:|/content-files/)([A-Za-z0-9_-]+)(?=[/?#\s)])')


def restore_links(markdown, aliases):
    def replace(match):
        prefix, identity = match.groups()
        return '](' + prefix + aliases.get(identity, identity)

    result = []
    cursor = 0
    for literal in _LITERAL_MARKDOWN.finditer(markdown):
        result.extend((_SOURCE_LINK.sub(replace, markdown[cursor:literal.start()]), literal[0]))
        cursor = literal.end()
    result.append(_SOURCE_LINK.sub(replace, markdown[cursor:]))
    return ''.join(result)

def _convert(value,mapping,key='',input_mode=False):
    if isinstance(value,str):
        if key in _SINGLES or key in _LISTS:return mapping.get(value,value)
        if not input_mode and key=='markdown':
            return restore_links(value,mapping)
        return value
    if isinstance(value,list):return[_convert(x,mapping,key,input_mode)for x in value]
    if isinstance(value,dict):
        row={k:_convert(v,mapping,k,input_mode)for k,v in value.items()}
        if input_mode and key in('sources','reference_blocks') and isinstance(value.get('id'),str):row['id']=mapping.get(value['id'],value['id'])
        return row
    return value

def compact_packet(packet):
    """返回无损任务和编号对照表；对照表留在客户端，不重复塞给模型。"""
    ids=list(dict.fromkeys(s['id']for key in('sources','reference_blocks')for s in packet.get(key,[])if isinstance(s,dict)and s.get('id')))
    mapping={identity:'src'+str(i+1)for i,identity in enumerate(ids)}
    files=list(dict.fromkeys(s['file_id']for key in('sources','reference_blocks')for s in packet.get(key,[])if isinstance(s,dict)and s.get('file_id')))
    mapping.update({identity:'doc'+str(i+1)for i,identity in enumerate(files)if identity not in mapping})
    packed=_convert(packet,mapping,input_mode=True)
    sources={row['id']:row for row in packed.get('sources',[])}
    for row in sources.values():
        prior=set()
        for field in ('original','translation','excerpt'):
            text=row.get(field)
            if text and text in prior:
                row.pop(field)
            elif text:
                prior.add(text)
    for row in packed.get('reference_blocks',[]):
        original=sources.get(row['id'],{})
        if row.get('text') and row['text'] in (original.get('original'),original.get('translation'),original.get('excerpt')):
            row.pop('text')
    # 同一术语的定义原先被两个兼容字段重复传入，只保留专业定义那一份。
    for term in packed.get('registered_terms',[]):
        if term.get('definition')==term.get('professional_definition'):term.pop('definition',None)
    packed['source_id_rule']='你在 source_id 只用 sources 或 reference_blocks 给出的 src 别名，客户端写回前会恢复原编号\n同一编号的原文只给一次，摘录或译文和原文完全相同的也只给一次\n外部网址和检索引用号用在各自的链接里'
    return packed,{short:full for full,short in mapping.items()}

def restore_output(value,aliases):
    """恢复来源字段和 Reader 本地链接中的编号，保留标题、代码和内容编号。"""
    return _convert(value,aliases)

def answer_repair_packet(packet, ids, analysis):
    """补答只使用仍未保存的问题及原文，不把已写回的回答带回模型。"""
    ids=set(ids)
    questions=[q for q in packet['questions'] if q['id'] in ids]
    source_ids={q['source_id'] for q in questions}
    parent_ids={q.get('parent_question_id') for q in questions}
    by_id={p['id']:p for p in packet.get('parent_answers',[])}
    pending=list(parent_ids)
    while pending:
        parent=by_id.get(pending.pop(),{}).get('parent_question_id')
        if parent and parent not in parent_ids:
            parent_ids.add(parent)
            pending.append(parent)
    return {**packet,'questions':questions,'sources':[s for s in packet['sources'] if s['id'] in source_ids],
            'reference_blocks':[s for s in packet.get('reference_blocks',[]) if source_ids.intersection(s['for_source_ids'])],
            'parent_answers':[p for p in by_id.values() if p['id'] in parent_ids],'reader_analysis':analysis}
