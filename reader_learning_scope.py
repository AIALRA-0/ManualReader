"""保留原件，同时明确登记哪些单元不属于学习内容。"""
import copy

def excluded_sources(material,stored):
    if not stored.get('learning_exclusions'):return {},[]
    sources={p['id']:p for p in material.get('passages',[])if p.get('content_kind')!='explanation'}
    active={};outdated=[]
    for row in stored.get('learning_exclusions',[]):
        p=sources.get(row['passage_id'])
        if p and p.get('file_id','primary')==row.get('file_id','primary')and (p.get('en')or'')==row.get('original','') and (p.get('src')or'')==row.get('media',''):
            active[p['id']]=row
        else:outdated.append(row['passage_id'])
    return active,outdated

def update_exclusions(material,stored,data):
    ids=data.get('passage_ids');action=data.get('action','exclude')
    if action not in('exclude','restore')or not isinstance(ids,list)or not ids or any(not isinstance(i,str)for i in ids):raise ValueError('请指定要排除或恢复的原文编号')
    sources={p['id']:p for p in material.get('passages',[])if p.get('content_kind')!='explanation'}
    if any(i not in sources for i in ids):raise ValueError('排除范围只能使用本项目现有的原文编号')
    reason=data.get('reason','')
    if action=='exclude'and(not isinstance(reason,str)or not reason.strip()or len(reason)>500):raise ValueError('排除原文时需要写清理由')
    rows={r['passage_id']:copy.deepcopy(r)for r in stored.get('learning_exclusions',[])}
    for identity in ids:
        if action=='restore':rows.pop(identity,None)
        else:
            p=sources[identity];rows[identity]={'passage_id':identity,'file_id':p.get('file_id','primary'),'reason':reason.strip(),'original':p.get('en')or'','media':p.get('src')or''}
    stored['learning_exclusions']=list(rows.values())
    return stored['learning_exclusions']

def reading_material(material,stored):
    excluded,_=excluded_sources(material,stored)
    return {**material,'passages':[p for p in material['passages']if p['id']not in excluded]}if excluded else material

def chapterwise_outline_guard(stored,nodes,current_ids,can_advance,selected_id=None):
    roots_by_id={}
    added=[]
    for node in nodes:
        identity=node['id']
        parent=node.get('parent_id')
        roots_by_id[identity]=roots_by_id[parent] if parent else identity
        if identity not in current_ids:
            added.append(identity)
    if not added:
        return
    added_roots={roots_by_id[identity] for identity in added}
    if len(added_roots)!=1:
        raise ValueError('每轮只确定一个顶层章和它需要的子节，请等轮到其他章再添加')
    root=next(iter(added_roots))
    if root not in current_ids and stored.get('learning_outline') and not can_advance:
        raise ValueError('等用户勾选当前章节完成后，再登记下一顶层章')
    if root in current_ids and selected_id and root!=roots_by_id.get(selected_id):
        raise ValueError('本轮只能给当前顶层章添加子节，请先按阅读顺序进入目标章')
