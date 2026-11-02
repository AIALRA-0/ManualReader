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

def chapterwise_outline_guard(stored,nodes,current_ids,can_advance):
    roots=[n for n in nodes if not n.get('parent_id')and n['id']not in current_ids]
    if len(roots)>1:raise ValueError('一次只规划一个新的首层大章节；这个大章下可以一起规划多个子节，不要一次展开整个项目')
    if roots and stored.get('learning_outline')and not can_advance:raise ValueError('当前章节还未由用户勾选完成，请先保留当前大章，不新增下一大章')
