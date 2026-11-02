"""记录错题与复习轮次；错过的题复习答对后仍保留，只有用户主动移出才隐藏。"""
import copy,uuid

def quizzes(state):
    book=state.get('practice_book',{})
    return {q['id']:q for q in [*state.get('quizzes',[]),*book.get('custom',[])]}

def payload(state):
    book=state.get('practice_book',{});members=book.get('members',{});items=[]
    attempts=book.get('attempts',[])
    for identity,q in quizzes(state).items():
        member=members.get(identity,{});history=[a for a in attempts if a.get('quiz_id')==identity]
        wrong=sum(a.get('correct') is False for a in history)
        ever_wrong=bool(member.get('ever_wrong') or wrong or q.get('selected') is not None and q.get('selected')!=q.get('correct_index'))
        active=member.get('active',ever_wrong)
        # 旧版本会在复习答对时自动移出，新规则恢复这类记录；用户明确移出的题仍保持移出。
        if not active and ever_wrong and history and history[-1].get('correct') and 'manual_removed' not in member:active=True
        if member.get('manual_removed'):active=False
        if not active:continue
        item=copy.deepcopy(q);item.update(member.get('overrides',{}));item['selected']=None
        item.update(review_attempts=sum(a.get('review') is True for a in history),wrong_count=wrong or int(ever_wrong),last_correct=history[-1].get('correct') if history else None)
        items.append(item)
    return {'items':items,'quiz_ids':[q['id'] for q in items],'count':len(items),'unmastered':sum(q.get('last_correct') is not True for q in items),'rounds':len(book.get('sessions',[]))}

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
    correct=selected==current['correct_index'];result={'correct':correct,'explanation':__import__('reader_contract').quiz_feedback(current,selected),'removed_from_book':False,'quiz_id':identity}
    if not correct:member.update(ever_wrong=True,active=True,manual_removed=False)
    elif member.get('ever_wrong') and not member.get('manual_removed'):member['active']=True
    member['updated_at']=stamp();book.setdefault('attempts',[]).append({'quiz_id':identity,'selected':selected,'correct':correct,'review':review,'session_id':session_id,'at':stamp()})
    if review:session['answered'][identity]=result
    else:q['selected']=selected
    result['in_mistake_book']=identity in payload(state)['quiz_ids']
    return result

def change(state,data,stamp):
    book=state.setdefault('practice_book',{});members=book.setdefault('members',{});action=data.get('action');identity=data.get('quiz_id')
    if action=='start':
        available=payload(state)['items'];wanted=data.get('quiz_ids')
        if wanted is not None and (not isinstance(wanted,list) or any(i not in {q['id'] for q in available} for i in wanted)):raise ValueError('只能练习当前错题本中的题目')
        items=[q for q in available if wanted is None or q['id'] in wanted];ids=[q['id'] for q in items]
        session={'id':'review-'+uuid.uuid4().hex[:16],'quiz_ids':ids,'answered':{},'started_at':stamp()};book.setdefault('sessions',[]).append(session)
        return {**session,'items':items}
    if action=='answer':return record_answer(state,identity,data.get('selected'),stamp,True,data.get('session_id'))
    if action in ('create','edit'):
        source=data.get('quiz',{});prompt=source.get('prompt');options=source.get('options');correct=source.get('correct_index')
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>4000:raise ValueError('请填写题目，最多4000字')
        if not isinstance(options,list) or not 2<=len(options)<=6 or any(not isinstance(o,str) or not o.strip() or len(o)>2000 for o in options) or len(set(options))!=len(options):raise ValueError('请填写2至6个不同的选项')
        if isinstance(correct,bool) or not isinstance(correct,int) or not 0<=correct<len(options):raise ValueError('请指定正确选项')
        update={'prompt':prompt.strip(),'options':options,'correct_index':correct,'explanation':str(source.get('explanation',''))[:12000],'option_explanations':__import__('reader_contract').option_explanations(source)}
        if action=='create':
            identity='practice-'+uuid.uuid4().hex[:16];book.setdefault('custom',[]).append({'id':identity,**update,'selected':None,'question_ids':[]})
        elif identity not in quizzes(state):raise ValueError('没有找到这道题')
        else:members.setdefault(identity,{})['overrides']=update
        members.setdefault(identity,{}).update(active=True,manual_removed=False,updated_at=stamp())
    elif action in ('add','remove'):
        if identity not in quizzes(state):raise ValueError('没有找到这道题')
        if action=='remove' and data.get('confirmed') is not True:raise ValueError('移出错题本需要确认')
        members.setdefault(identity,{}).update(active=action=='add',manual_removed=action=='remove',updated_at=stamp())
    else:raise ValueError('没有这个错题本操作')
    return payload(state)
