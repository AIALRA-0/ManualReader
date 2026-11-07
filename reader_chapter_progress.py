"""用问题的章节归属和追问关系统计进度，不改用户手动勾选的阅读记录。"""
from reader_questions import has_current_answer,answer_revision,infer_learning_node

def chapter_progress(material,nodes,state):
    by_node={n['id']:n for n in nodes};questions={q['id']:q for r in state.get('rounds',[]) for q in r['questions']};owners={}
    def ancestors(identity):
        result=set()
        while identity in by_node and identity not in result:
            result.add(identity);identity=by_node[identity].get('parent_id')
        return result
    def owner(q,trail=()):
        if q['id'] in owners:return owners[q['id']]
        if q['id'] in trail:return None
        identity=q.get('learning_node_id') or q.get('target',{}).get('learning_node_id')
        if identity not in by_node:
            parent=questions.get(q.get('parent_question_id'))
            identity=owner(parent,(*trail,q['id'])) if parent else None
        if identity not in by_node:identity=infer_learning_node({**material,'learning':{'nodes':nodes}},q)
        owners[q['id']]=identity if identity in by_node else None
        return owners[q['id']]
    memberships={qid:ancestors(owner(q)) for qid,q in questions.items() if not q.get('deleted_at')}
    qrows={identity:[] for identity in by_node};zrows={identity:[] for identity in by_node}
    for qid,ids in memberships.items():
        for identity in ids:qrows[identity].append(questions[qid])
    replaced={q.get('revises_quiz_id') for q in state.get('quizzes',[]) if q.get('revises_quiz_id')}
    for quiz in state.get('quizzes',[]):
        if quiz['id'] in replaced:continue
        ids=set().union(*(memberships.get(qid,set()) for qid in quiz.get('question_ids',[])))
        for identity in quiz.get('learning_node_ids',[]):ids.update(ancestors(identity))
        if not ids:
            pid=quiz.get('passage_id') or next((r.get('passage_id') for r in state.get('rounds',[]) if r['id']==quiz.get('round_id')),None)
            ids={n['id'] for n in nodes if pid and pid in n.get('subtree_passage_ids',n.get('passage_ids',[]))}
        for identity in ids:zrows[identity].append(quiz)
    def measure(done,total):return {'done':done,'total':total,'percent':round(done/total*100) if total else 0}
    views=state.get('answer_views',{})
    for node in nodes:
        rows=qrows[node['id']];ready=[q for q in rows if has_current_answer(q)]
        node['question_progress']={**measure(len(ready),len(rows)),'read':sum(views.get(q['id'],{}).get('answer_revision')==answer_revision(q) for q in ready),'pending':len(rows)-len(ready)}
        quizzes=zrows[node['id']];node['practice_progress']=measure(sum(q.get('selected') is not None for q in quizzes),len(quizzes))
    return nodes
