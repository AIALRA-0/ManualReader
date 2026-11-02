"""按原件编号分配章节责任，保留旧目录、原文和用户进度。"""
import copy,hashlib,re
from collections import defaultdict

VERSION='reader-coverage-contract-v1'

def complete_outline(material,nodes):
    """保留已有章节；尚未分配的材料按原目录追加待讲章节，不声称已讲完。"""
    nodes=copy.deepcopy(nodes);sources={p['id']:p for p in material.get('passages',[]) if p.get('content_kind')!='explanation' and not p.get('historical')}
    order={p:i for i,p in enumerate(sources)};candidates=defaultdict(list);invalid=[]
    explanations={e['id']:e for e in material.get('explanations',[])}
    for i,node in enumerate(nodes):
        links=list(node.get('source_links',[]))
        links.extend(link for eid in node.get('explanation_ids',[]) for link in explanations.get(eid,{}).get('source_links',[]))
        for identity in list(node.get('owned_source_ids',[]))+[l.get('passage_id') for l in links]:
            if identity in sources:candidates[identity].append((node.get('number','').count('.'),i,node['id']))
            elif identity:invalid.append({'chapter_id':node['id'],'source_id':identity})
        node['owned_source_ids']=[]
    owners={pid:sorted(rows,key=lambda x:(-x[0],x[1]))[0][2] for pid,rows in candidates.items()}
    known={n['id']:n for n in nodes}
    for pid,owner in owners.items():known[owner]['owned_source_ids'].append(pid)
    missing=defaultdict(list)
    for pid,p in sources.items():
        if pid not in owners:missing[(p.get('file_id','primary'),p.get('section_id') or 'whole')].append(pid)
    files={f['id']:f for f in material.get('reading_files',[])}
    next_number=max([int(n.get('number','0')) for n in nodes if not n.get('parent_id') and str(n.get('number','')).isdigit()]+[-1])+1
    for (fid,sid),ids in missing.items():
        f=files.get(fid,{})
        section=next((s for s in f.get('outline',[]) if s.get('id')==sid),{})
        raw_title=section.get('title') or f.get('name') or material.get('title') or '原文'
        title=re.sub(r'^\d+(?:\.\d+)*\.?\s+','',raw_title).strip()
        identity='source-plan-'+hashlib.sha256((fid+'\0'+sid).encode()).hexdigest()[:16]
        while identity in known:identity+='x'
        node={'id':identity,'parent_id':None,'number':str(next_number),'title':title,'source_links':[{'file_id':fid,'passage_id':pid,'sentence_id':None} for pid in ids],
              'explanation_ids':[],'legacy_section_ids':[],'owned_source_ids':ids,'planning_origin':'original_outline','source_section_id':sid,'source_file_id':fid}
        nodes.append(node);known[identity]=node;next_number+=1
        for pid in ids:owners[pid]=identity
    previous=None
    for node in nodes:
        node['owned_source_ids'].sort(key=lambda x:order.get(x,len(order)))
        node['previous_node_id']=previous;previous=node['id']
        # 阅读先后不自动等于知识依赖，只有明确登记的依赖才列入。
        node['depends_on']=[i for i in node.get('depends_on',[]) if i in known and i!=node['id']]
    return nodes,{'version':VERSION,'total':len(sources),'assigned':len(owners),'percent':100 if sources else None,
        'source_owners':owners,'unassigned':sorted(set(sources)-set(owners)),'invalid_references':invalid,
        'shared_references':[{'source_id':pid,'chapter_ids':list(dict.fromkeys(r[2] for r in rows)),'responsible_chapter_id':owners[pid]} for pid,rows in candidates.items() if len({r[2] for r in rows})>1],
        'meaning':'比例表示每个原文单元已有负责章节；是否已经翻译、讲解和读完分别计算'}

def audit_components(material,state):
    """核对各部分的真实链接，内容是否易懂仍要通过实际阅读验收。"""
    passages={p['id']:p for p in material.get('passages',[])}
    all_questions={q['id']:q for r in state.get('rounds',[]) for q in r.get('questions',[])}
    questions={qid:q for qid,q in all_questions.items() if not q.get('deleted_at')}
    answers=[q for q in questions.values() if q.get('answer')]
    terms=state.get('terms',[]);quizzes=state.get('quizzes',[])
    term_ids={t['id'] for t in terms}
    return {'question_count':len(questions),'answer_count':len(answers),
        'unanswered_question_ids':[q['id'] for q in questions.values() if not q.get('answer')],
        'unstructured_answer_ids':[q['id'] for q in answers if not q.get('answer_parts')],
        'question_source_gaps':[q['id'] for q in questions.values() if q.get('passage_id') not in passages],
        'followup_parent_gaps':[q['id'] for q in questions.values() if q.get('parent_question_id') and q['parent_question_id'] not in questions],
        'term_count':len(terms),'term_explanation_gaps':[t['id'] for t in terms if not (t.get('professional_definition') or t.get('definition')) or not t.get('intuitive_explanation')],
        'question_term_gaps':[{'question_id':q['id'],'term_id':tid} for q in questions.values() for tid in q.get('term_ids',[]) if tid not in term_ids],
        'quiz_count':len(quizzes),'quiz_question_gaps':[z['id'] for z in quizzes if any(qid not in all_questions for qid in z.get('question_ids',[]))],
        'read_progress_policy':'只有用户手动勾选才能增加已读记录；子章节并入父章节时按稳定片段编号去重',
        'note_policy':'内容更新保留原问题、片段和笔记编号；旧句子失效时保留历史原句，不把笔记静默移到别处'}
