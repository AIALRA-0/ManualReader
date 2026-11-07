"""保存整组问题，按来源定位；编辑和删除保留原编号、回答与笔记。"""
import copy,re,uuid,hashlib
from reader_contract import normalize_question

def has_current_answer(question):
    """回答仍存在且对应当前问题版本时，才算已经回答。"""
    return bool(str(question.get('answer') or '').strip() and not question.get('deleted_at')
                and not question.get('answer_deleted_at') and not question.get('answer_outdated')
                and question.get('answered_question_version',question.get('version',1))==question.get('version',1))


def needs_answer(question):
    return not question.get('deleted_at') and not has_current_answer(question)


def question_pool_status(state,round_id=None):
    questions=[q for r in state['rounds'] if not round_id or r['id']==round_id for q in r['questions'] if not q.get('deleted_at')]
    return {'saved':len(questions),'pending':sum(needs_answer(q) for q in questions),'answered':sum(has_current_answer(q) for q in questions),'drafts':sum(bool(v.strip()) for values in state.get('drafts',{}).values() for v in values.values()),'scope':'project'}


def update_round_status(state,question_id):
    for row in state['rounds']:
        if not any(q['id']==question_id for q in row['questions']):continue
        active=[q for q in row['questions'] if not q.get('deleted_at')]
        row['status']='answered' if all(has_current_answer(q) for q in active) else 'partial' if any(has_current_answer(q) for q in active) else 'pending'


def question_changed(question,stamp):
    """修改问题后，旧回答留作对照，旧回答的删除标记不延续到新问题。"""
    question.pop('answer_deleted_at',None)
    if question.get('answer'):question['answer_outdated']=True
    else:
        question.pop('answer_outdated',None)
        if question.get('answer_parts'):
            question.setdefault('answer_parts_history',[]).append({'parts':copy.deepcopy(question['answer_parts']),'question_version':question.get('answered_question_version'),'saved_at':stamp(),'action':'question_edit'})
        for field in ('answer_parts','answer_body','display_answer','previous_answer','answer_revision','format_audit'):
            question.pop(field,None)


def answer_revision(question):
    if not has_current_answer(question):return None
    text=str(question['answer'])+'\0'+str(question.get('answered_question_version',question.get('version',1)))
    return hashlib.sha256(text.encode('utf8')).hexdigest()

def mark_answers_seen(state,rows,stamp):
    """登记展开时的问题或回答版本，不修改章节已读、笔记或练习。"""
    if not isinstance(rows,list) or not 1<=len(rows)<=200 or any(not isinstance(row,dict)for row in rows):raise ValueError('请提交本次展开的回答列表')
    known={q['id']:q for r in state['rounds']for q in r['questions']};seen=[];skipped=[]
    for row in rows:
        q=known.get(row.get('question_id'));revision=answer_revision(q)if q and not q.get('deleted_at')else None
        if not q or q.get('deleted_at'):skipped.append(row.get('question_id'));continue
        version=q.get('version',1)
        if revision:
            if row.get('answer_revision')!=revision:skipped.append(q['id']);continue
            expected={'answer_revision':revision,'question_version':version}
        else:
            if row.get('answer_revision')or row.get('question_version')!=version:skipped.append(q['id']);continue
            expected={'question_version':version}
        records=state.setdefault('answer_views',{});record=records.get(q['id'])
        if not record or any(record.get(k)!=v for k,v in expected.items()):records[q['id']]={**expected,'seen_at':stamp()}
        seen.append({'question_id':q['id'],**records[q['id']]})
    return {'seen':seen,'skipped':skipped}


def source_for(material,question):
    p=next((p for p in material['passages'] if p['id']==question.get('passage_id')), {})
    s=next((s for s in p.get('sentences',[]) if s['id']==question.get('sentence_id')), {})
    snapshot=question.get('target') or {}
    original=snapshot.get('original') or s.get('en') or p.get('aligned_original') or p.get('en') or ''
    translation=snapshot.get('translation') or s.get('zh') or p.get('zh') or ''
    file=next((f for f in material.get('reading_files',[]) if f['id']==p.get('file_id')), {})
    label=p.get('alt') or p.get('path') or file.get('name') or '素材 '+str(p.get('id') or question.get('passage_id'))
    return {'original':original,'translation':translation,'text':snapshot.get('text') or translation or original or label,
            'kind':p.get('content_kind','source'),'file_id':p.get('file_id'),
            'explanation_id':p.get('explanation_id'),'label':label,
            'file_name':file.get('name'),'section_id':p.get('section_id'),
            'precision':'sentence' if s else 'paragraph'}


def answer_body(answer,source):
    body=str(answer or '').strip()
    # 原句由接口和页面统一展示，正文中已有的首部引用只显示一次。
    match=re.match(r'^(?:#{1,6}\s*)?(?:\*\*)?原(?:句|文)(?:\*\*)?[：:](?:\*\*)?\s*',body)
    if match:
        rest=body[match.end():]
        for quote in [source.get('text'),source.get('original'),source.get('translation')]:
            if not quote:continue
            for variant in ['“'+quote+'”','「'+quote+'」','"'+quote+'"',quote]:
                if rest.startswith(variant):
                    tail=rest[len(variant):].lstrip(' \n\r')
                    translated=re.match(r'^(?:中文|译文|翻译)[：:](?:\*\*)?\s*([^\n]+)\n*',tail)
                    if translated:
                        normalize=lambda value:re.sub(r'\s+','',str(value or '')).strip('。.;；“”"')
                        if normalize(translated[1])==normalize(source.get('translation')):tail=tail[translated.end():].lstrip()
                    return re.sub(r'^(?:#{1,6}\s*)?(?:\*\*)?回答(?:\*\*)?[：:](?:\*\*)?\s*','',tail).strip()
        line=rest.find('\n')
        if line>=0:return re.sub(r'^(?:#{1,6}\s*)?(?:\*\*)?回答(?:\*\*)?[：:](?:\*\*)?\s*','',rest[line:].strip()).strip()
    return re.sub(r'^(?:#{1,6}\s*)?(?:\*\*)?回答(?:\*\*)?[：:](?:\*\*)?\s*','',body).strip()


def infer_learning_node(material,q):
    if q.get('learning_node_id'):return q['learning_node_id']
    if q.get('target',{}).get('learning_node_id'):return q['target']['learning_node_id']
    nodes=material.get('learning',{}).get('nodes',[]);p=next((p for p in material['passages'] if p['id']==q.get('passage_id')),{})
    owners=[n['id'] for n in nodes if p.get('explanation_id') in n.get('explanation_ids',[]) or q.get('passage_id') in n.get('passage_ids',[]) or p.get('file_id')=='primary' and p.get('section_id') in n.get('legacy_section_ids',[])]
    if len(owners)==1:return owners[0]
    # 旧问题只有原文编号时，按章节明确登记的原文归属显示，不改写用户记录。
    pid=q.get('passage_id');sid=q.get('sentence_id')
    direct=[n for n in nodes if any(l.get('passage_id')==pid and (not sid or not l.get('sentence_id') or l['sentence_id']==sid) for l in n.get('source_links',[]))]
    if len(direct)==1:return direct[0]['id']
    return None

def decorate_question(material,q):
    from reader_alignment import question_alignment
    result=copy.deepcopy(q);source=source_for(material,q);result['answer_source']=source
    result['answer_revision']=(q.get('answer_revision') or answer_revision(q)) if has_current_answer(q) else None
    result['needs_answer']=needs_answer(q)
    result['alignment']=question_alignment(material,q)
    result['learning_node_id']=infer_learning_node(material,q)
    if not has_current_answer(q):
        result['answer']=None
        for key in ('answer_parts','answer_body','display_answer','previous_answer'):result.pop(key,None)
        if q.get('answer') and not q.get('answer_deleted_at'):
            result['answer_outdated']=True;result['previous_answer']=q['answer'];result['answer_body']=answer_body(q['answer'],source)
        return result
    if q.get('answer'):result['answer_body']=answer_body(q['answer'],source);result['display_answer']='原文：\n\n'+source['text']+'\n\n回答：\n\n'+answer_body(q['answer'],source)
    return result


def attach_source(material,round_,rows):
    for q,row in zip(round_['questions'],rows):
        q.setdefault('original_text',row.get('original_text',row.get('text','')))
        if row.get('learning_node_id'):q.setdefault('learning_node_id',row['learning_node_id'])
        source=source_for(material,q)
        q.setdefault('target',{'kind':source['kind'],'file_id':source['file_id'],
            'explanation_id':source['explanation_id'],'passage_id':q['passage_id'],
            'sentence_id':q.get('sentence_id'),'original':source['original'],
            'translation':source['translation'],'label':source['label']})


def create_batch(state,data,material,create_round):
    request=data.get('request_id');rows=data.get('questions')
    if not isinstance(request,str) or not request or len(request)>80:raise ValueError('请提供最多 80 字的提交编号')
    if not isinstance(rows,list) or not rows or len(rows)>500 or any(not isinstance(q,dict) for q in rows):raise ValueError('一组需要包含 1 至 500 个问题')
    grouped={}
    registered={q['id']:q for r in state['rounds'] for q in r['questions']}
    for row in rows:
        if row.get('parent_question_id') and (row['parent_question_id'] not in registered or registered[row['parent_question_id']].get('deleted_at')):raise ValueError('原问题已删除或不存在，请刷新问题列表')
        pid=row.get('passage_id')
        if not isinstance(pid,str):raise ValueError('每个问题都需要保留所属段落编号')
        passage=next((p for p in material['passages'] if p['id']==pid),None)
        if not passage:raise ValueError('问题的来源不属于当前材料')
        for key,expected in [('material_id',material['id']),('file_id',passage.get('file_id')),('explanation_id',passage.get('explanation_id'))]:
            if row.get(key) is not None and row[key]!=expected:raise ValueError('问题提交的 '+key+' 与实际来源不一致')
        item={**row,'learning_node_id':row.get('learning_node_id') or data.get('learning_node_id') or state.get('learning_node_id'),'original_text':row.get('original_text',row.get('text','')),'text':normalize_question(row.get('text',''))}
        grouped.setdefault(pid,[]).append(item)
    rounds=[]
    for pid,members in grouped.items():
        for offset in range(0,len(members),100):
            chunk=members[offset:offset+100]
            r=create_round(state,{'passage_id':pid,'request_id':request+'-'+str(len(rounds)),'questions':chunk})
            r.setdefault('batch_id',request);attach_source(material,r,chunk);rounds.append(r)
    return {'batch_id':request,'rounds':rounds,'questions':[q for r in rounds for q in r['questions']]}


def change_question(state,identity,data,stamp):
    questions={q['id']:q for r in state['rounds'] for q in r['questions']};q=questions.get(identity)
    if not q:raise ValueError('没有找到这个问题')
    action=data.get('action')
    if action not in ('edit','delete'):raise ValueError('请选择编辑或删除问题')
    if data.get('expected_text')!=q['text']:raise ValueError('问题已更新，请刷新后再修改')
    if action=='edit':
        if q.get('deleted_at'):raise ValueError('这个问题已删除，历史记录继续保留')
        value=normalize_question(data.get('text',''))
        if not value or len(value)>5000:raise ValueError('问题需要填写 1 至 5000 字')
        q.setdefault('question_history',[]).append({'text':q['text'],'saved_at':stamp(),'action':'edit'})
        if data.get('expected_version') is not None and data['expected_version']!=q.get('version',1):raise ValueError('问题版本已更新，请读取最新内容')
        q['text']=value;q['original_text']=data['text'];q['question_updated_at']=stamp();q['version']=q.get('version',1)+1
        question_changed(q,stamp);update_round_status(state,identity)
        return {'question_id':identity,'text':value,'original_text':q['original_text'],'version':q['version'],'answer_preserved':bool(q.get('answer'))}
    if data.get('confirmed') is not True:raise ValueError('删除问题前需要再次确认')
    if q.get('deleted_at'):raise ValueError('这个问题已经删除，历史记录继续保留')
    branch={identity}
    while True:
        more={item['id'] for item in questions.values() if not item.get('deleted_at') and item.get('parent_question_id') in branch}
        if more<=branch:break
        branch|=more
    at=stamp()
    for qid in branch:
        item=questions[qid];item.setdefault('deleted_at',at)
        item.setdefault('question_history',[]).append({'text':item['text'],'saved_at':at,'action':'delete'})
        state.get('drafts',{}).get(item['passage_id'],{}).pop('followup:'+qid,None)
        update_round_status(state,qid)
    return {'question_id':identity,'deleted_ids':sorted(branch),'history_preserved':True}


def save_drafts(state,data,locate):
    rows=data.get('drafts')
    if not isinstance(rows,list) or len(rows)>500 or any(not isinstance(row,dict) for row in rows):raise ValueError('草稿需要按所属段落提交列表')
    questions={q['id']:q for r in state['rounds'] for q in r['questions']}
    for row in rows:
        pid=row.get('passage_id');locate(pid);values=row.get('values')
        if not isinstance(values,dict):raise ValueError('每段草稿需要按片段编号保存')
        for key,value in values.items():
            if not isinstance(key,str) or not isinstance(value,str) or len(value)>15000:raise ValueError('草稿格式或长度不正确')
            if key.startswith('followup:'):
                parent=questions.get(key.removeprefix('followup:'))
                if not parent or parent['passage_id']!=pid or parent.get('deleted_at'):raise ValueError('追问草稿不属于这个段落，或原问题已删除')
            elif key=='paragraph':locate(pid)
            else:locate(pid,key)
    for row in rows:state['drafts'][row['passage_id']]=row['values']
    return {'saved':True,'passage_ids':[row['passage_id'] for row in rows]}

def sync_questions(state,data,material,create_round,stamp,allow_removals=True):
    pid=data.get('passage_id');sid=data.get('sentence_id');parent=data.get('parent_question_id');client=data.get('client_id');version=data.get('version');lines=data.get('lines')
    passage=next((p for p in material['passages'] if p['id']==pid),None)
    if not passage or sid and not any(s['id']==sid for s in passage['sentences']):raise ValueError('提问的位置不属于当前项目')
    source_refs=data.get('source_refs',[])
    known={p['id']:p for p in material['passages']}
    if not isinstance(source_refs,list) or len(source_refs)>500 or any(not isinstance(i,str) or i not in known for i in source_refs):raise ValueError('提问引用的展示组不属于当前项目')
    if source_refs and pid not in source_refs:raise ValueError('展示组需要包含当前提问的位置')
    if not isinstance(client,str) or not client or len(client)>300 or isinstance(version,bool) or not isinstance(version,int) or version<1:raise ValueError('请提供输入块编号和递增版本')
    if not isinstance(lines,list) or len(lines)>200 or any(not isinstance(x,dict) for x in lines):raise ValueError('问题需要按行提交')
    ids=[x.get('client_line_id') for x in lines]
    if any(not isinstance(x,str) or not x or len(x)>100 for x in ids) or len(set(ids))!=len(ids):raise ValueError('输入行编号不能缺失或重复')
    allq={q['id']:q for r in state['rounds'] for q in r['questions']}
    if parent and (parent not in allq or allq[parent].get('deleted_at') or allq[parent]['passage_id']!=pid):raise ValueError('追问不属于这个段落')
    node_id=data.get('learning_node_id') or state.get('learning_node_id');nodes=material.get('learning',{}).get('nodes',[])
    if node_id and node_id not in {n['id'] for n in nodes}:raise ValueError('提问不属于已登记的学习章节')
    old=state.setdefault('question_inputs',{}).get(client)
    if old and (old['passage_id']!=pid or old.get('parent_question_id')!=parent or old.get('learning_node_id')!=node_id):raise ValueError('输入块编号已经用于另一处内容或学习章节')
    current=[q for q in allq.values() if q.get('client_id')==client and not q.get('deleted_at')]
    if old and version<=old['version']:return {'client_id':client,'version':old['version'],'questions':current,'saved':True,'stale':version<old['version']}
    prepared=[]
    for row in lines:
        raw=row.get('text')
        if not isinstance(raw,str) or len(raw)>5000:raise ValueError('每条问题最多 5000 字')
        text=normalize_question(raw)
        if text:prepared.append((row['client_line_id'],raw,text))
    existing={q.get('client_line_id'):q for q in allq.values() if q.get('client_id')==client and (not q.get('deleted_at') or (q.get('question_history') or [{}])[-1].get('action')=='autosave_remove')};active=set();now=stamp()
    for lid,raw,text in prepared:
        q=existing.get(lid)
        if q is None:
            row={'text':text,'original_text':raw,'sentence_id':sid,'parent_question_id':parent}
            round_=create_round(state,{'passage_id':pid,'request_id':'sync-'+uuid.uuid4().hex,'questions':[row]});attach_source(material,round_,[row]);q=round_['questions'][0]
            q.update(client_id=client,client_line_id=lid,version=1,round_id=round_['id'],learning_node_id=(allq[parent].get('learning_node_id') or infer_learning_node(material,allq[parent])) if parent else node_id)
        elif q['text']!=text:
            q.setdefault('question_history',[]).append({'text':q['text'],'saved_at':now,'action':'autosave'})
            q.update(text=text,original_text=raw,version=q.get('version',1)+1,question_updated_at=now)
            question_changed(q,stamp)
        q.pop('deleted_at',None);q['original_text']=raw;q['input_version']=version;q['sync_version']=version;active.add(q['id'])
        excerpt=data.get('source_excerpt')
        if isinstance(excerpt,str) and excerpt.strip():
            q['target']['text']=excerpt.strip();q['target']['source_segment_id']=data.get('source_segment_id')
            if re.search('[\u3400-\u9fff]',excerpt):q['target']['translation']=excerpt.strip()
            else:q['target']['original']=excerpt.strip()
        if source_refs:
            q['target']['source_refs']=list(dict.fromkeys(source_refs))
            q['target']['original']='\n'.join(known[i].get('en','') for i in source_refs if known[i].get('en'))
            q['target']['translation']='\n'.join(known[i].get('zh','') for i in source_refs if known[i].get('zh'))
    for q in current:
        if q['id'] not in active and not q.get('answer') and not state.get('notes',{}).get(q['id']) and not any(child.get('parent_question_id')==q['id'] and not child.get('deleted_at') for child in allq.values()):
            if not allow_removals:raise ValueError('Agent 删除问题需要先取得删除预览并二次确认，不能通过清空输入块绕过')
            q['deleted_at']=now;q.setdefault('question_history',[]).append({'text':q['text'],'saved_at':now,'action':'autosave_remove'})
    state['question_inputs'][client]={'version':version,'passage_id':pid,'sentence_id':sid,'parent_question_id':parent,'learning_node_id':node_id,'updated_at':now,'line_ids':[x[0] for x in prepared]}
    for q in [q for r in state['rounds'] for q in r['questions'] if q.get('client_id')==client]:update_round_status(state,q['id'])
    questions=[q for r in state['rounds'] for q in r['questions'] if q.get('client_id')==client and not q.get('deleted_at')]
    return {'client_id':client,'version':version,'questions':questions,'saved':True}
