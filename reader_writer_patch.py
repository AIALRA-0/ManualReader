"""只补一次脚本指出的缺项；正确正文留在客户端，不让模型重抄。"""
import copy,json
from reader_task_prompts import STRUCTURAL_REPAIR as INSTRUCTION

def schema(output):
    """用成稿的字段类型约束补丁，Markdown 作为普通 JSON 字符串传递。"""
    from reader_writer_schema import I,S,arr,obj
    types = [{'type':'null'},S,I,{'type':'boolean'},arr(S),arr(I)]
    def collect(field):
        if field.get('type') in ('object','array') and field not in types:
            types.append(field)
        for value in field.get('properties', {}).values():
            collect(value)
        for value in field.get('anyOf', []):
            collect(value)
        if 'items' in field:
            collect(field['items'])
    for field in output.get('properties', {}).values():
        collect(field)
    return obj(changes=arr(obj(path=S,value={'anyOf':types}),minItems=1))

def apply(original,patch):
    value=copy.deepcopy(original)
    if not isinstance(patch,dict)or not isinstance(patch.get('changes'),list)or not patch['changes']:raise ValueError('补齐结果没有字段变更')
    applied=set()
    for row in patch['changes']:
        path=row.get('path','');keys=[k.replace('~1','/').replace('~0','~')for k in path.split('/')[1:]]
        if not path.startswith('/')or not keys or keys[0]not in('plan','explanation','answers','terms','quizzes','evidence_gaps','generated_assets','reader_checks','visual_checks','coordinate_figures'):raise ValueError('补齐字段超出写作内容范围')
        if path in applied and keys[-1]!='-':
            raise ValueError('同一字段只能提交一次修改：'+path)
        applied.add(path)
        if path in('/explanation','/answers')or keys[-1]=='parts':raise ValueError('补齐不能重写整批正文')
        if 'value' in row:
            new=row['value']
        else:
            raw=row['value_json']
            try:new=json.loads(raw)
            except json.JSONDecodeError:
                if keys[-1]!='markdown':raise
                new=raw
        parent=value
        for key in keys[:-1]:
            if isinstance(parent,list):
                if not key.isascii() or not key.isdecimal():raise ValueError('补齐数组索引需要是非负整数')
                parent=parent[int(key)]
            else:parent=parent[key]
        key=keys[-1]
        if isinstance(parent,list):
            if key=='-':parent.append(new)
            else:
                if not key.isascii() or not key.isdecimal():raise ValueError('补齐数组索引需要是非负整数')
                index=int(key)
                if not 0<=index<len(parent):raise ValueError('补齐数组索引不存在')
                parent[index]=new
        else:parent[key]=new
    def ids(data):
        return {(None,p['id']) for p in data.get('explanation',{}).get('parts',[])}|{(a['question_id'],p['id'])for a in data.get('answers',[])for p in a.get('parts',[])}
    if not ids(original)<=ids(value):raise ValueError('补齐移除了已生成的内容编号')
    return value
