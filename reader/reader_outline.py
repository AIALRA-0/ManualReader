"""按原编号修改学习目录；删除待讲单元时保留已经写下的讲解。"""
import copy

NODE_FIELDS=('id','parent_id','title','source_links','explanation_ids','legacy_section_ids','source_label','depends_on','title_provisional')
OUTLINE_RULES={
    'hierarchy':'X. 是一个可独立学习的主题章，不是包住整个项目的容器；X.X 与更深层级是本章内部的步骤或子问题。不要为了只新增一个顶层章，把其他独立章降成这个章的子节',
    'creation':'可以先在本轮暂排全书顺序。每次只登记一个 X. 顶层章和实际需要的子节，正文只写当前单元。后续主题轮到时再按材料与进度确定',
    'editing':'保留稳定 id；未讲单元可以新增、改名、移动和删除。已讲单元可以改名、移动和调整来源，但不能删除，也不能移除已登记的讲解归属；不重写正文、提问、笔记或已读记录',
    'numbering':'接口按 parent_id 和同级顺序生成编号，顶层从 0 开始，子节从 1 开始；新增、删除、移动后按实际位置计算显示编号，稳定 id 不变；不要把编号当作 id',
    'progress':'新建下一顶层章与切到下一学习单元仍遵守用户手动完成的规则；编辑待讲目录不会自动切章或标记已读',
}

def has_teaching(node, material):
    explanations=set(node.get('explanation_ids',[]));legacy=set(node.get('legacy_section_ids',[]))
    for p in material.get('passages',[]):
        belongs=p.get('explanation_id') in explanations or (p.get('file_id','primary')=='primary' and p.get('section_id') in legacy)
        if belongs and not p.get('heading') and ((p.get('zh')or p.get('en')or'').strip() or p.get('src')):return True
    return node.get('status')=='ready'

def protect_taught_nodes(before, after, material):
    current={n['id']:n for n in after}
    for old in before:
        if not has_teaching(old,material):continue
        new=current.get(old['id'])
        if not new:raise ValueError('章节 '+old['id']+' 已有讲解，只能调整或重命名，不能删除')
        for field in ('explanation_ids','legacy_section_ids'):
            if not set(old.get(field,[])).issubset(new.get(field,[])):
                raise ValueError('章节 '+old['id']+' 已有讲解，不能移除讲解归属；正文与原编号需要保留')

def edit_nodes(before, data):
    actions=data.get('actions')
    if not isinstance(actions,list)or not 1<=len(actions)<=100 or any(not isinstance(a,dict)for a in actions):raise ValueError('请提交 1 到 100 项目录修改')
    rows={n['id']:{k:copy.deepcopy(n[k])for k in NODE_FIELDS if k in n}for n in before}
    children={None:[]}
    for n in before:children.setdefault(n.get('parent_id')or None,[]).append(n['id']);children.setdefault(n['id'],[])
    def subtree(identity):
        result=[identity]
        for child in children.get(identity,[]):result.extend(subtree(child))
        return result
    def place(identity,parent,action):
        if parent is not None and parent not in rows:raise ValueError('父章节不存在，请先添加父章节')
        if parent in subtree(identity):raise ValueError('不能把章节移动到自己或自己的子节下面')
        if action.get('before_id')and action.get('after_id'):raise ValueError('一次只指定 before_id 或 after_id')
        siblings=children.setdefault(parent,[])
        anchor=action.get('before_id')or action.get('after_id')
        if anchor and (anchor==identity or anchor not in siblings):raise ValueError('用于定位顺序的章节必须是目标父章节下的另一个同级单元')
        for ids in children.values():
            if identity in ids:ids.remove(identity)
        index=siblings.index(anchor)+(1 if action.get('after_id')else 0)if anchor else len(siblings)
        siblings.insert(index,identity);rows[identity]['parent_id']=parent
    for action in actions:
        kind=action.get('action');identity=action.get('id')
        if kind=='add':
            node=action.get('node')
            if not isinstance(node,dict)or not node.get('id'):raise ValueError('新增章节需要 node 和稳定 id')
            identity=node['id']
            if identity in rows:raise ValueError('章节编号已经存在；修改请使用 update')
            if set(node)-set(NODE_FIELDS):raise ValueError('目录只接受章节字段，讲解正文请使用讲解接口')
            rows[identity]=copy.deepcopy(node);children[identity]=[];place(identity,node.get('parent_id')or None,action)
        elif kind in ('update','move','delete'):
            if identity not in rows:raise ValueError('没有找到要修改的章节 '+str(identity))
            if kind=='update':
                changes=action.get('changes')
                if not isinstance(changes,dict)or not changes or set(changes)-set(NODE_FIELDS[2:]):raise ValueError('update 只修改标题、来源、讲解归属和前置关系；移动请使用 move，id 保持不变')
                if 'title' in changes and 'title_provisional' not in changes:
                    rows[identity].pop('title_provisional',None)
                rows[identity].update(copy.deepcopy(changes))
            elif kind=='move':place(identity,action.get('parent_id')or None,action)
            else:
                if children.get(identity)and action.get('cascade')is not True:raise ValueError('该章节还有子节；删除整组待讲单元时需要 cascade=true')
                ids=set(subtree(identity))
                for siblings in children.values():siblings[:]=[i for i in siblings if i not in ids]
                for i in ids:rows.pop(i);children.pop(i,None)
        else:raise ValueError('目录操作只支持 add、update、move 和 delete')
    ordered=[]
    def visit(parent):
        for identity in children.get(parent,[]):ordered.append(rows[identity]);visit(identity)
    visit(None)
    if len(ordered)!=len(rows):raise ValueError('目录存在无法归属的章节')
    return ordered

def changes_between(before, after):
    old={n['id']:n for n in before};new={n['id']:n for n in after}
    return {'added':[n['id']for n in after if n['id']not in old], 'removed':[n['id']for n in before if n['id']not in new],
            'updated':[n['id']for n in after if n['id']in old and any(n.get(k)!=old[n['id']].get(k)for k in NODE_FIELDS+('number',))]}
