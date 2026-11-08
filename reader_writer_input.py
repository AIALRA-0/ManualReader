"""只缩短传给写作者的来源编号；原文、代码和问题编号始终保留。"""
import copy,re

_SINGLES={'source_id','passage_id','first_source_id','last_source_id','file_id'}
_LISTS={'source_ids','passage_ids','required_source_ids','candidate_original_ids','explained_source_ids','owned_source_ids','for_source_ids'}

def _convert(value,mapping,key='',input_mode=False):
    if isinstance(value,str):
        if key in _SINGLES or key in _LISTS:return mapping.get(value,value)
        if not input_mode and key=='markdown':
            return re.sub(r'\]\(source:([^\s)]+)\)',lambda m:'](source:'+mapping.get(m[1],m[1])+')',value)
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
    # 同一术语的定义原先被两个兼容字段重复传入，只保留专业定义那一份。
    for term in packed.get('registered_terms',[]):
        if term.get('definition')==term.get('professional_definition'):term.pop('definition',None)
    packed['source_id_rule']='sources与reference_blocks中的src编号是本次输入别名，直接引用它；客户端写回前恢复原编号。不要猜原文件编号，不把外部URL或检索引用号填入source_id。'
    return packed,{short:full for full,short in mapping.items()}

def restore_output(value,aliases):
    """只恢复来源字段与 source: 图片地址，不改标题、正文、代码和 part/unit 编号。"""
    return _convert(value,aliases)
