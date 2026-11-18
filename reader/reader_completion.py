"""只有用户点击完成按钮才记录完成；覆盖统计和 Agent 指令不代替勾选。"""
import re
from reader_learning import learning_payload

def readable(p):
    return not p.get('progress_exempt') and not p.get('heading') and not p.get('duplicate_of') and p.get('role') not in ('page_furniture','metadata','whitespace') and p.get('display_role') not in ('figure_label','figure_caption','decoration') and not re.fullmatch(r'\s*#{1,6}\s+[^\n]+\s*',p.get('zh') or p.get('en') or '')

def completion_scope(material,stored,state,view=None,plan=None):
    plan=learning_payload(material,stored,state) if plan is None else plan;node=next((n for n in plan['nodes'] if n['id']==plan['selected_id']),None)
    view=view or {};layout=view.get('content_layout',{});kinds={k for column in layout.get('columns',[['explanation'] if node and node.get('reading_passage_ids') else ['translation']]) for k in column}
    pid=state.get('focus',{}).get('passage_id');focus=next((p for p in material['passages'] if p['id']==pid),{})
    file_id=layout.get('file_id') or focus.get('file_id','primary');ids=set()
    if 'explanation' in kinds:
        if node:ids.update(node.get('reading_passage_ids',[]))
        else:
            e=next((e for e in material.get('explanations',[]) if e['id']==layout.get('explanation_id')),None)
            if e:ids.update(e.get('passage_ids',[]))
    if kinds.intersection(('original','translation')):
        sources=[p for p in material['passages'] if p.get('file_id','primary')==file_id and p.get('content_kind')!='explanation' and not p.get('explanation_id')]
        if view.get('scope')=='all':ids.update(p['id'] for p in sources)
        elif node and not layout.get('browse_original'):ids.update(l['passage_id'] for l in node.get('effective_source_links',[]) if l.get('file_id','primary')==file_id)
        else:
            page=view.get('page_id');section=focus.get('section_id')
            ids.update(p['id'] for p in sources if any(r.get('page_id')==page for r in p.get('refs',[])) if page)
            if not page:ids.update(p['id'] for p in sources if p.get('section_id')==section)
    known={p['id']:p for p in material['passages']};ids={pid for pid in ids if pid in known and readable(known[pid])}
    return plan['selected_id'],sorted(ids)

def completion_payload(material,stored,state,plan=None):
    plan=learning_payload(material,stored,state) if plan is None else plan
    node=plan['selected_id'];record=state.get('manual_completions',{}).get(node or '',{})
    expected=completion_scope(material,stored,state,record.get('view',{}),plan=plan)[1] if record else []
    known={p['id']:p for p in material['passages']};old_ids=set(record.get('passage_ids',[]));changed=bool(record) and (bool(set(expected)-old_ids) or any(pid not in known or known[pid].get('content_version',0)!=version for pid,version in record.get('versions',{}).items()))
    completed=record.get('completed') is True and not changed
    return {'node_id':node,'completed':completed,'can_advance':completed,'content_changed':changed,'passage_ids':record.get('passage_ids',[]),'recorded_at':record.get('at')}

def record_completion(material,stored,state,data,stamp):
    node=learning_payload(material,stored,state)['selected_id']
    if data.get('learning_node_id') not in (None,node):raise ValueError('完成按钮不属于当前学习章节')
    if not node:return {'node_id':None,'completed':False,'can_advance':False,'content_changed':False}
    ids=list(dict.fromkeys(data['passage_ids']));known={p['id']:p for p in material['passages']}
    state.setdefault('manual_completions',{})[node]={'completed':data['read'],'passage_ids':ids,'versions':{pid:known[pid].get('content_version',0) for pid in ids},'view':data.get('view') or {},'at':stamp(),'origin':'manual_ui'}
    return completion_payload(material,stored,state)
