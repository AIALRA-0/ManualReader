"""把阅读任务整理成去重后的写作输入，页面坐标继续留在阅读器中。"""
from reader_contract import writing_guide
from reader_questions import source_for,infer_learning_node
from reader_learning import learning_payload
from reader_term_names import display_state
from reader_media import source_media,annotate_questions


def group_packet(packet,ids):
    """本组只读自己的问题、相关原句、父问题和可复用术语，不复制整段聊天。"""
    selected=[q for q in packet['questions'] if q['id'] in ids];source_ids={q['source_id'] for q in selected}
    sources=[s for s in packet['sources'] if s['id'] in source_ids]
    parent_ids={q['parent_question_id'] for q in selected if q.get('parent_question_id')}
    ancestors={p['id']:p for p in packet['parent_answers']}
    while True:
        more=parent_ids|{ancestors[p].get('parent_question_id') for p in parent_ids if p in ancestors and ancestors[p].get('parent_question_id')}
        if more==parent_ids:break
        parent_ids=more
    parents=[p for p in packet['parent_answers'] if p['id'] in parent_ids]
    prose='\n'.join(q['text'] for q in selected)+'\n'+'\n'.join(s.get('original','')+'\n'+s.get('translation','') for s in sources)
    import re
    selected_ids={q['id'] for q in selected};body=prose.casefold()
    def relevant(t):
        name=t['name'];prefix=name.split('（')[0].strip();english=re.search(r'（([^）]+)）',name)
        names=[name,prefix,*t.get('aliases',[])]
        if re.match(r'^[A-Za-z][A-Za-z0-9-]* ',prefix):names.extend(prefix.split(' ',1))
        if english:names.append(english[1])
        return bool(selected_ids.intersection(t.get('question_ids',[]))) or any(alias and alias.casefold() in body for alias in names)
    terms=[t for t in packet['registered_terms'] if relevant(t)]
    result={**{k:packet[k] for k in ('schema_version','material_id','title','writer','instructions','reference_url','result_contract')},'sources':sources,'parent_answers':parents,'questions':selected,'registered_terms':terms}
    if 'reference_blocks' in packet:result['reference_blocks']=[p for p in packet['reference_blocks'] if source_ids.intersection(p['for_source_ids'])]
    else:result['reference_original']=packet['reference_original']
    return result


def compact_packet(material,state,question_ids=None,include_answered=False):
    """只直接输入问题附近的完整段落；其余原件继续可读取，不截短问题或答案。"""
    packet=work_packet(material,state,include_answered=include_answered)
    if question_ids is not None:
        wanted=set(question_ids);packet['questions']=[q for q in packet['questions'] if q['id'] in wanted]
        if wanted-{q['id'] for q in packet['questions']}:raise ValueError('待处理问题已经改变，请重新读取当前问题池')
    source_ids={q['source_id'] for q in packet['questions']};packet['sources']=[s for s in packet['sources'] if s['id'] in source_ids]
    by_id={p['id']:p for p in material['passages']};blocks={}
    for source in packet['sources']:
        source.pop('question_time_source',None)
        pid=source['passage_id'];own=by_id[pid];neighbors=[p for p in material['passages'] if p.get('file_id')==own.get('file_id') and p.get('explanation_id')==own.get('explanation_id') and p.get('section_id')==own.get('section_id') and not p.get('duplicate_of') and p.get('role')!='page_furniture']
        index=next(i for i,p in enumerate(neighbors) if p['id']==pid);needed=neighbors[max(0,index-1):index+2]
        needed += [by_id[l['passage_id']] for l in source.get('source_links',[]) if l['passage_id'] in by_id]
        for p in needed:
            block=blocks.setdefault(p['id'],{'id':p['id'],'file_id':p.get('file_id'),'explanation_id':p.get('explanation_id'),'text':p.get('aligned_original') or p.get('en') or p.get('zh',''),'for_source_ids':[]})
            if source['id'] not in block['for_source_ids']:block['for_source_ids'].append(source['id'])
    packet.pop('reference_original',None);packet.pop('dispatch',None);packet.pop('reading_context',None);packet['reference_blocks']=list(blocks.values());packet['input_mode']='source_neighborhood'
    grouped={}
    for q in packet['questions']:grouped.setdefault(q['source_id'],[]).append(q['id'])
    groups=[[],[]]
    for ids in sorted(grouped.values(),key=len,reverse=True):min(groups,key=len).extend(ids)
    packet['groups']=[g for g in groups if g]
    packet['instructions']=[s for s in packet['instructions'] if '已给定的完整原文' not in s and '写作者不读写文件' not in s]
    packet['instructions'] += ['当前输入包含本组原句和附近完整段落，不等于整篇原文；必要前置、专业细节或推导需要其他材料时，从本地 reference.json 按原编号或关键词读取；不要靠缺少上下文的猜测回答',
      '只把确实需要的外部检索放在本组内进行，主对话不必先集中检索再派发；不削减回答、术语或练习来换速度',
      '问题、原句和规则已写入这一个输入文件，读取一次即可；将结果直接写入指定 result JSON 文件，最终只返回文件路径和条数，主对话用脚本写回，禁止重新抄写整份结果',
      '每个回答使用“原文：”和“回答：”两个首部，两个首部各独占一段，空行后再写原句或回答；正文没有额外编号或小标题时不自行添加']
    return annotate_questions(packet)


def work_packet(material, state, round_id=None, include_answered=False):
    state,_=display_state(state)
    rounds = [r for r in state['rounds'] if not round_id or r['id'] == round_id]
    if round_id and not rounds:
        raise ValueError('没有找到这一轮问题')
    all_questions = {q['id']: q for r in state['rounds'] for q in r['questions']}
    paragraphs = {p['id']: p for p in material['passages']}
    questions, sources, ancestors = [], {}, {}
    for row in rounds:
        for q in row['questions']:
            if q.get('deleted_at'):continue
            if (q.get('answer') and not q.get('answer_outdated') or q.get('answer_deleted_at')) and not include_answered:
                continue
            p = paragraphs[q.get('passage_id') or row['passage_id']]
            s = next((s for s in p['sentences'] if s['id'] == q.get('sentence_id')), None)
            sid = q.get('target',{}).get('source_segment_id') or q.get('sentence_id') or p['id']
            source=source_for(material,q)
            sources[sid] = {'id': sid, 'original':source['original'] or source['text'],
                            'excerpt':source['text'],'passage_id':p['id'],'sentence_id':q.get('sentence_id'),
                            'candidate_original_ids':([] if p.get('content_kind')=='explanation' else [q.get('sentence_id') or p['id']]),
                            'translation':source['translation'],
                            'content_kind':p.get('content_kind','source'),'file_id':p.get('file_id'),
                            'explanation_id':p.get('explanation_id'),'source_links':p.get('source_links',[]),
                            'question_time_source':q.get('target')}
            media_items=source_media(material,p)
            sources[sid]['source_kind']=p.get('kind')
            if media_items:sources[sid]['media_items']=media_items
            if media_items:
                sources[sid]['media']=media_items[0]
            elif p.get('kind') in ('image','visual','page_image'):
                sources[sid]['media']={'src':p.get('src'),'alt':p.get('alt',''),
                    'refs':(s or p).get('refs',[]),'instruction':'回答前查看这项原图，不能只根据图名猜测'}
            questions.append({'id': q['id'], 'round_id': row['id'], 'source_id': sid,
                              'learning_node_id':infer_learning_node(material,q),
                              'text': q['text'], 'question_version':q.get('version',1),'parent_question_id': q.get('parent_question_id')})
            parent = q.get('parent_question_id')
            while parent and parent not in ancestors:
                old = all_questions.get(parent)
                if not old:
                    break
                ancestors[parent] = {'id': parent,'parent_question_id':old.get('parent_question_id'), 'question': old.get('answered_question_text') if old.get('answer_outdated') else old['text'], 'answer': old.get('answer', ''),'outdated':bool(old.get('answer_outdated'))}
                parent = old.get('parent_question_id')
    # 完整原文只登记一次，坐标和页面文字层不传给写作者；定位仍由稳定编号完成
    needed_files={row.get('file_id') for row in sources.values() if row.get('file_id')}
    linked_ids={link['passage_id'] for row in sources.values() for link in row.get('source_links',[])}
    needed_explanations={row.get('explanation_id') for row in sources.values() if row.get('explanation_id')}
    reference = '\n\n'.join(p.get('aligned_original') or p.get('en') or p.get('zh', '')
                            for p in material['passages'] if not p.get('duplicate_of') and p.get('role') != 'page_furniture'
                            and (not material.get('reading_files') or p.get('file_id') in needed_files or p['id'] in linked_ids or p.get('explanation_id') in needed_explanations))
    import re
    arxiv=re.search(r'arXiv\s*:\s*([0-9]{4}\.[0-9]{4,5}(?:v[0-9]+)?)','\n'.join(p.get('en','') for p in material['passages'][:6]),re.I)
    reference_url='https://arxiv.org/abs/'+arxiv[1] if arxiv else material.get('source','') if str(material.get('source','')).startswith('http') else None
    grouped = {}
    for q in questions:
        grouped.setdefault(q['source_id'], []).append(q['id'])
    groups = [[], []]
    for ids in sorted(grouped.values(), key=len, reverse=True):
        min(groups, key=len).extend(ids)
    if len(questions)<=3:
        groups=[[q['id'] for q in questions]]
    packet={'schema_version': 1, 'material_id': material['id'], 'title': material['title'],
            'writer': {'model': 'gpt-6.1-terra', 'effort': 'high', 'service_tier':'default', 'max_parallel': 2},
            'instructions': writing_guide()['instructions'] + [
                '回答、术语和每题的选择题在同一次写作中完成；先写完整内容，再由脚本保存和检查',
                '只输出结果 JSON，不重复打印材料、规划说明或处理总结；写作者不读写文件，也不操作阅读器',
                '引用链接使用已提供的 reference_url 或实际检索返回的地址，不猜测论文编号',
                '已给定的完整原文和已有术语可直接使用；只有回答确实需要外部事实时才检索，并保留出处',
                '每条回答以原文：开头，引用本题 sources 中的原句或图片名称，然后再回答；页面还会按稳定编号自动补齐这一首部，所有问题必须保留原文位置'],
            'reading_context':learning_payload(material,{'learning_outline':material.get('learning',{}).get('nodes') if material.get('learning',{}).get('scope_active') else None},state)['context'], 'reference_original': reference, 'reference_url':reference_url, 'sources': list(sources.values()),
            'registered_terms': [{**{k: t[k] for k in ('id', 'name', 'aliases','question_ids','definition', 'professional_definition', 'intuitive_explanation', 'needs_intuitive_explanation') if k in t},'version':t.get('version',1)} for t in state['terms']],
            'parent_answers': list(ancestors.values()), 'questions': questions,
            'groups': [g for g in groups if g],
            'result_contract': {'answers': [{'question_id': '原问题编号','question_version':'本次问题的 question_version，必须原样返回','text': '原句和回答','explained_source_ids':['实际解释到的原句或对象编号']}],
                                'terms': [{'term_id': '已有术语编号，复用时填写', 'name': '新术语的规范名称',
                                           'professional_definition': '准确说明概念、必要条件和边界', 'intuitive_explanation': '沿用已采用的生活对象或具体例子，自然承接并解释同一个含义', 'question_ids': ['原问题编号']}],
                                'quizzes': [{'question_ids': ['原问题编号'], 'prompt': '题目', 'options': ['选项一', '选项二', '选项三', '选项四'],
                                            'correct_index': 0, 'explanation': '解析'}]}}
    packet['instructions'].append('先读 sources.excerpt，它是用户实际提问的文字；id 可能是显示片段编号，不能把它冒充原文覆盖编号。explained_source_ids 只能填实际解释过的 candidate_original_ids 或 source_links 中真实原文编号；问题针对讲解且未进一步解释原文时留空，不把背景引用全算已讲解')
    packet['dispatch']={'mode':'manual_codex_subagents','max_parallel':2,'model':'gpt-6.1-terra','effort':'high','groups':[{'id':'writer-'+str(i+1),'question_ids':ids} for i,ids in enumerate(packet['groups'])],'input_rule':'共同原文只在本任务包保存一次；用 reader_bridge.py prepare 提取各组直接写作输入。只传本组原句、父问题和相关术语，保留共同原文，不重复带整段聊天','writeback':'主对话收到一组结果后逐题写回；网页不启动模型，不等待其他组完成才保存'}
    return annotate_questions(packet)


def change_answer(state, question_id, data, stamp):
    q = next((q for r in state['rounds'] for q in r['questions'] if q['id'] == question_id), None)
    if not q:
        raise ValueError('没有找到这个问题')
    action = data.get('action')
    if action not in ('edit', 'delete'):
        raise ValueError('请指定编辑或删除回答')
    if not q.get('answer'):
        raise ValueError('这个问题还没有回答')
    if data.get('expected_answer') != q['answer']:
        raise ValueError('回答已经更新，请刷新后再修改')
    value = data.get('text') if action == 'edit' else None
    if action == 'edit' and (not isinstance(value, str) or not value.strip() or len(value) > 30000):
        raise ValueError('回答需要填写 1 至 30000 字')
    if action == 'delete' and data.get('confirmed') is not True:
        raise ValueError('删除前需要再次确认')
    q.setdefault('answer_history', []).append({'text': q['answer'], 'action': action, 'saved_at': stamp()})
    q['answer'] = value.strip() if value else None
    q.pop('explained_source_ids',None);q.pop('source_ranges',None)
    q['answer_updated_at'] = stamp()
    if action=='delete':q['answer_deleted_at']=stamp()
    else:q.pop('answer_deleted_at',None)
    for r in state['rounds']:
        if q in r['questions']:
            r['status'] = 'answered' if all(x.get('answer') for x in r['questions']) else 'partial'
    return {'question_id': question_id, 'action': action, 'saved': True}
