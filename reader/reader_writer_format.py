"""修正本轮新生成的内部编号；用户编号、来源编号和正文保持原样。"""
import copy,hashlib,re

ID=re.compile(r'[A-Za-z0-9_-]{1,70}\Z')

def normalize(value):
    data=copy.deepcopy(value);changes=[]
    if not isinstance(data,dict):return data,changes
    plan=data.get('plan')or{};units=plan.get('units')or[]
    unit_ids={u.get('id')for u in units if isinstance(u,dict)and isinstance(u.get('id'),str)}
    for unit in units:
        if not isinstance(unit,dict)or not isinstance(unit.get('needs'),list):continue
        for i,dependency in enumerate(unit['needs']):
            if not isinstance(dependency,str)or dependency in unit_ids:continue
            match=re.fullmatch(r'([A-Za-z0-9_-]+)\s*[：:]\s*\S.*',dependency)
            if match and match[1]in unit_ids:
                unit['needs'][i]=match[1];changes.append({'field':'plan.needs','before':dependency,'after':match[1]})
    owners=[data.get('explanation')or{}]+[a for a in data.get('answers',[])if isinstance(a,dict)]
    parts=[p for owner in owners for p in owner.get('parts',[])if isinstance(p,dict)]
    occupied={p.get('id')for p in parts if isinstance(p.get('id'),str)};mapping={}
    for part in parts:
        old=part.get('id')
        if not isinstance(old,str)or ID.fullmatch(old):continue
        if old not in mapping:
            new=re.sub(r'[^A-Za-z0-9_-]+','-',old).strip('-')[:55]or'part'
            if new in occupied:new+='-'+hashlib.sha256(old.encode()).hexdigest()[:10]
            mapping[old]=new;occupied.add(new)
            changes.append({'field':'part.id','before':old,'after':new})
        part['id']=mapping[old]
    # 只更新合同中的内部引用；不对正文做字符串替换。
    def walk(node):
        if isinstance(node,dict):
            for key,item in node.items():
                if key in('part_id','after_part_id','previous_part_id')and isinstance(item,str)and item in mapping:node[key]=mapping[item]
                elif isinstance(item,(dict,list)):walk(item)
        elif isinstance(node,list):
            for item in node:walk(item)
    walk(data)
    return data,changes
