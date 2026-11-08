"""只补一次脚本指出的缺项；正确正文留在客户端，不让模型重抄。"""
import copy,json,re
SCHEMA={'type':'object','properties':{'changes':{'type':'array','items':{'type':'object','properties':{'path':{'type':'string'},'value_json':{'type':'string'}},'required':['path','value_json'],'additionalProperties':False},'minItems':1}},'required':['changes'],'additionalProperties':False}
INSTRUCTION='这是本次唯一一次集中补齐。原稿中正确的内容和编号保持，只补脚本明确指出的缺项。返回changes，每项path采用JSON Pointer，value_json是要写入该字段的JSON编码字符串；已有数组项目按索引修字段，新增项目用/-追加，不删除、重排或重抄整篇。不能替换根对象、整个explanation、answers或parts数组。补正文时也补对应的plan单元、来源组证据和练习placement；正文仍连贯讲清，不把检查过程写给读者。'
def apply(original,patch):
    value=copy.deepcopy(original)
    if not isinstance(patch,dict)or not isinstance(patch.get('changes'),list)or not patch['changes']:raise ValueError('补齐结果没有字段变更')
    for row in patch['changes']:
        path=row.get('path','');keys=[k.replace('~1','/').replace('~0','~')for k in path.split('/')[1:]]
        if not path.startswith('/')or not keys or keys[0]not in('plan','explanation','answers','terms','quizzes','evidence_gaps','generated_assets'):raise ValueError('补齐字段超出写作内容范围')
        if path in('/explanation','/answers')or keys[-1]=='parts':raise ValueError('补齐不能重写整批正文')
        new=json.loads(row['value_json']);parent=value
        for key in keys[:-1]:
            if isinstance(parent,list):parent=parent[int(key)]
            else:parent=parent[key]
        key=keys[-1]
        if isinstance(parent,list):
            if key=='-':parent.append(new)
            else:
                index=int(key)
                if not 0<=index<len(parent):raise ValueError('补齐数组索引不存在')
                parent[index]=new
        else:parent[key]=new
    def ids(data):
        return {p['id']for p in data.get('explanation',{}).get('parts',[])}|{p['id']for a in data.get('answers',[])for p in a.get('parts',[])}
    if not ids(original)<=ids(value):raise ValueError('补齐移除了已生成的内容编号')
    return value
