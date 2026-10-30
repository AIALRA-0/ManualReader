"""记录错题与复习轮次；复习答对后只移出错题本，原题和原作答继续保留。"""
import copy,uuid

def quizzes(state):
    book=state.get('practice_book',{})
    return {q['id']:q for q in [*state.get('quizzes',[]),*book.get('custom',[])]}

def payload(state):
    book=state.get('practice_book',{});members=book.get('members',{});items=[]
    for identity,q in quizzes(state).items():
        member=members.get(identity,{})
        active=member.get('active',q.get('selected') is not None and q.get('selected')!=q.get('correct_index'))
        if not active:continue
        item=copy.deepcopy(q);item.update(member.get('overrides',{}));item['selected']=None
        item['review_attempts']=sum(a.get('quiz_id')==identity for a in book.get('attempts',[]));items.append(item)
    return {'items':items,'count':len(items),'rounds':len(book.get('sessions',[]))}

def record_answer(state,identity,selected,stamp,review=False,session_id=None):
    available=quizzes(state);q=available.get(identity)
    if not q:raise ValueError('没有找到这道练习')
    book=state.setdefault('practice_book',{});members=book.setdefault('members',{});member=members.setdefault(identity,{})
    current={**q,**member.get('overrides',{})} if review else q
    if isinstance(selected,bool) or not isinstance(selected,int) or not 0<=selected<len(current['options']):raise ValueError('请先选择一个选项')
    if review:
        session=next((s for s in book.get('sessions',[]) if s['id']==session_id),None)
        if not session or identity not in session['quiz_ids']:raise ValueError('这道题不属于当前复习轮次')
        if identity in session['answered']:return session['answered'][identity]
    correct=selected==current['correct_index'];result={'correct':correct,'explanation':current.get('explanation',''),'removed_from_book':correct,'quiz_id':identity}
    member.update(active=not correct,updated_at=stamp());book.setdefault('attempts',[]).append({'quiz_id':identity,'selected':selected,'correct':correct,'review':review,'session_id':session_id,'at':stamp()})
    if review:session['answered'][identity]=result
    else:q['selected']=selected
    return result

def change(state,data,stamp):
    book=state.setdefault('practice_book',{});members=book.setdefault('members',{});action=data.get('action');identity=data.get('quiz_id')
    if action=='start':
        ids=[q['id'] for q in payload(state)['items']]
        session={'id':'review-'+uuid.uuid4().hex[:16],'quiz_ids':ids,'answered':{},'started_at':stamp()};book.setdefault('sessions',[]).append(session)
        return {**session,'items':payload(state)['items']}
    if action=='answer':return record_answer(state,identity,data.get('selected'),stamp,True,data.get('session_id'))
    if action in ('create','edit'):
        source=data.get('quiz',{});prompt=source.get('prompt');options=source.get('options');correct=source.get('correct_index')
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>4000:raise ValueError('请填写题目，最多4000字')
        if not isinstance(options,list) or not 2<=len(options)<=6 or any(not isinstance(o,str) or not o.strip() or len(o)>2000 for o in options) or len(set(options))!=len(options):raise ValueError('请填写2至6个不同的选项')
        if isinstance(correct,bool) or not isinstance(correct,int) or not 0<=correct<len(options):raise ValueError('请指定正确选项')
        update={'prompt':prompt.strip(),'options':options,'correct_index':correct,'explanation':str(source.get('explanation',''))[:12000]}
        if action=='create':
            identity='practice-'+uuid.uuid4().hex[:16];book.setdefault('custom',[]).append({'id':identity,**update,'selected':None,'question_ids':[]})
        elif identity not in quizzes(state):raise ValueError('没有找到这道题')
        else:members.setdefault(identity,{})['overrides']=update
        members.setdefault(identity,{}).update(active=True,updated_at=stamp())
    elif action in ('add','remove'):
        if identity not in quizzes(state):raise ValueError('没有找到这道题')
        if action=='remove' and data.get('confirmed') is not True:raise ValueError('移出错题本需要确认')
        members.setdefault(identity,{}).update(active=action=='add',updated_at=stamp())
    else:raise ValueError('没有这个错题本操作')
    return payload(state)
