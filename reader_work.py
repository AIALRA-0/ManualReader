from reader_writer_profiles import writer_profile,writer_label,public_profiles
"""把阅读任务整理成去重后的写作输入，页面坐标继续留在阅读器中。"""
from reader_contract import writing_guide
from reader_quality import QA_PART_CONTRACT
from reader_prompts import WRITING_VERSION
from reader_questions import source_for,infer_learning_node,has_current_answer,needs_answer,update_round_status,question_pool_status
from reader_learning import learning_payload
from reader_term_names import display_state
from reader_media import source_media,annotate_questions

VISUAL_SOURCE_KINDS = {'image','visual','page_image','table','figure','formula','equation'}


def related_terms(terms,questions,sources):
    """沿用登记表，只传本轮问题或原文实际涉及的术语。"""
    from reader_term_quality import mentioned_terms
    selected_ids={q['id'] for q in questions}
    body='\n'.join([*(q['text'] for q in questions),*(str(s.get(key) or '') for s in sources
                    for key in ('original','en','translation','zh','text','excerpt','alt'))])
    mentioned={row['id'] for row in mentioned_terms(body,terms)}
    return [term for term in terms if term['id'] in mentioned or selected_ids.intersection(term.get('question_ids',[]))]


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
    references=[p for p in packet.get('reference_blocks',[]) if source_ids.intersection(p['for_source_ids'])]
    terms=related_terms(packet['registered_terms'],selected,[*sources,*references,
                        *({'text':p.get('answer') or p.get('answer_excerpt','')} for p in parents)])
    result={**{k:packet[k] for k in ('schema_version','material_id','title','writer','instructions','reference_url','result_contract')},'sources':sources,'parent_answers':parents,'questions':selected,'registered_terms':terms}
    for key in ('practice_policy','framework_version','writing_version','revision_mode','reader_feedback_latest','confirmed_passage','confirmed_understanding'):
        if key in packet:result[key]=packet[key]
    if 'reference_blocks' in packet:result['reference_blocks']=references
    else:result['reference_original']=packet['reference_original']
    return result


def compact_packet(material,state,question_ids=None,include_answered=False):
    """只直接输入问题附近的完整段落；其余原件继续可读取，不截短问题或答案。"""
    wanted = None if question_ids is None else set(question_ids)
    packet=work_packet(material,state,include_answered=include_answered,question_ids=wanted,compact=True)
    packet['question_pool']=question_pool_status(state);packet['revision_mode']=include_answered is True
    if wanted is not None and wanted-{q['id'] for q in packet['questions']}:
        raise ValueError('待处理问题已经改变，请重新读取当前问题池')
    passages=material['passages']
    by_id={p['id']:p for p in passages}
    positions={p['id']:i for i,p in enumerate(passages)}
    blocks={}
    from collections import defaultdict
    neighborhoods=defaultdict(list)
    neighborhood_positions={}
    def scope(p):
        return p.get('file_id'),p.get('explanation_id'),p.get('section_id')
    for passage in passages:
        if not passage.get('duplicate_of') and passage.get('role')!='page_furniture':
            rows=neighborhoods[scope(passage)]
            neighborhood_positions[passage['id']]=len(rows)
            rows.append(passage)
    def related_visual(original):
        for p in passages[positions[original['id']]+1:positions[original['id']]+2]:
            if (p.get('file_id'),p.get('section_id'))==(original.get('file_id'),original.get('section_id')) and p.get('kind') in VISUAL_SOURCE_KINDS and p.get('src'):
                return p
    for source in packet['sources']:
        source.pop('question_time_source',None)
        pid=source['passage_id'];own=by_id[pid]
        neighbors=neighborhoods[scope(own)];index=neighborhood_positions.get(pid)
        needed=[own] if index is None else neighbors[max(0,index-1):index+2]
        for link in source.get('source_links',[]):
            original=by_id.get(link['passage_id'])
            if original is None:continue
            needed.append(original)
            if image:=related_visual(original):needed.append(image)
        for p in needed:
            block=blocks.setdefault(p['id'],{'id':p['id'],'file_id':p.get('file_id'),'explanation_id':p.get('explanation_id'),'text':p.get('aligned_original') or p.get('en') or p.get('zh',''),'for_source_ids':[]})
            if p.get('kind') in VISUAL_SOURCE_KINDS and p.get('src'):
                block.update(kind=p['kind'],src=p['src'],alt=p.get('alt',''))
            if source['id'] not in block['for_source_ids']:block['for_source_ids'].append(source['id'])
    packet['reference_blocks']=list(blocks.values());packet['input_mode']='source_neighborhood'
    grouped={}
    for q in packet['questions']:grouped.setdefault(q['source_id'],[]).append(q['id'])
    groups=[[],[]]
    for ids in sorted(grouped.values(),key=len,reverse=True):min(groups,key=len).extend(ids)
    packet['groups']=[g for g in groups if g]
    packet['instructions'].extend([
        '你可以在本组任务包里读到提问的原句、附近段落和已登记的来源关系',
        '客户端先分析怎样讲，再把材料和分析交给写作者，收到正文后负责提交',
    ])
    return annotate_questions(packet)


def work_packet(material, state, round_id=None, include_answered=False, *, question_ids=None, compact=False):
    state,_=display_state(state)
    rounds = [r for r in state['rounds'] if not round_id or r['id'] == round_id]
    if round_id and not rounds:
        raise ValueError('没有找到这一轮问题')
    all_questions = {q['id']: q for r in state['rounds'] for q in r['questions']}
    paragraphs = {p['id']: p for p in material['passages']}
    files={row['id']:row for row in material.get('reading_files',[])}
    questions, sources, ancestors = [], {}, {}
    for row in rounds:
        for q in row['questions']:
            if question_ids is not None and q['id'] not in question_ids:
                continue
            if q.get('deleted_at'):continue
            if not needs_answer(q) and not include_answered:
                continue
            p = paragraphs[q.get('passage_id') or row['passage_id']]
            s = next((s for s in p['sentences'] if s['id'] == q.get('sentence_id')), None)
            sid = q.get('target',{}).get('source_segment_id') or q.get('sentence_id') or p['id']
            source=source_for(material,q,passage=p,files=files)
            sources[sid] = {'id': sid, 'original':source['original'] or source['text'],
                            'excerpt':source['text'],'passage_id':p['id'],'sentence_id':q.get('sentence_id'),
                            'candidate_original_ids':([] if p.get('content_kind')=='explanation' else q.get('target',{}).get('source_refs') or [q.get('sentence_id') or p['id']]),
                            'translation':source['translation'],
                            'content_kind':p.get('content_kind','source'),'file_id':p.get('file_id'),
                            'explanation_id':p.get('explanation_id'),'source_links':p.get('source_links',[]),
                            'question_time_source':q.get('target')}
            media_items=source_media(material,p,by_id=paragraphs)
            if q.get('target',{}).get('source_refs'):
                collected=[item for identity in q['target']['source_refs'] if identity in paragraphs
                           for item in source_media(material,paragraphs[identity],by_id=paragraphs)]
                unique={item.get('src'):item for item in collected};media_items=list(unique.values())
            sources[sid]['source_kind']=p.get('kind')
            if media_items:sources[sid]['media_items']=media_items
            if media_items:
                sources[sid]['media']=media_items[0]
            elif p.get('kind') in ('image','visual','page_image'):
                sources[sid]['media']={'src':p.get('src'),'alt':p.get('alt',''),
                    'refs':(s or p).get('refs',[]),'instruction':'你回答前先看原图，再根据看到的内容判断'}
            questions.append({'id': q['id'], 'round_id': row['id'], 'source_id': sid,
                              'learning_node_id':infer_learning_node(material,q),
                              'text': q['text'], 'question_version':q.get('version',1),'expected_answer_revision':__import__('reader_questions').answer_revision(q),'parent_question_id': q.get('parent_question_id')})
            parent = q.get('parent_question_id')
            while parent and parent not in ancestors:
                old = all_questions.get(parent)
                if not old:
                    break
                ancestors[parent] = {'id': parent,'parent_question_id':old.get('parent_question_id'), 'question': old.get('answered_question_text') if old.get('answer_outdated') else old['text'], 'answer': old.get('answer', '') if not old.get('answer_deleted_at') else '', 'outdated':not has_current_answer(old)}
                parent = old.get('parent_question_id')
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
    packet={'schema_version': 1, 'framework_version':__import__('reader_generation').VERSION,'writing_version':WRITING_VERSION, 'material_id': material['id'], 'title': material['title'],
            'writer':writer_profile('answer'),
            'instructions': writing_guide()['instructions'] + [
                '你引用已提供的 reference_url 或检索实际返回的地址，脚本按 parts 生成页面正文，你只需返回各 part',
            ],
            'reference_url':reference_url, 'sources': list(sources.values()),
            'registered_terms': [{**{k: t[k] for k in ('id', 'name', 'aliases','question_ids','definition', 'professional_definition', 'intuitive_explanation', 'needs_intuitive_explanation') if k in t},'version':t.get('version',1)} for t in state['terms']],
            'parent_answers': list(ancestors.values()), 'questions': questions,
            'groups': [g for g in groups if g],
            'result_contract': {'framework_version':__import__('reader_generation').VERSION,'plan':__import__('reader_generation').PLAN_CONTRACT,'answers': [{'question_id': '原问题编号','question_version':'本次问题的 question_version，必须原样返回','parts':[QA_PART_CONTRACT],'explained_source_ids':['实际解释到的原句或对象编号']}],
                                'terms': [{'term_id': '已有术语编号，复用时填写', 'name': '新术语的规范名称',
                                           'professional_definition': '独立说明概念是什么、怎样起作用和本题必需条件', 'intuitive_explanation': '沿用已采用的生活对象或具体例子，自然承接并解释同一个含义', 'question_ids': ['原问题编号']}],
                                'quizzes': [{'placement':__import__('reader_generation').PLACEMENT_CONTRACT['answer'], 'prompt': '题目', 'options': ['选项一', '选项二', '选项三', '选项四'],
                                            'correct_index': 0, 'explanation': '解析', 'option_explanations':['按原始选项下标，逐项具体解释对错原因']}]}}
    packet['practice_policy']=__import__('reader_practice_policy').practice_policy()
    packet['instructions'].append('你先读 sources.excerpt，确认用户对哪段文字提问，显示片段的 id 只用来定位\n你在 explained_source_ids 只填 candidate_original_ids 或 source_links 中实际讲过的真实原文编号，问题只涉及讲解且没有解释原文时留空')
    if compact:
        return packet
    # 旧的 /api/work 仍会返回完整参考原文，新建回答任务只需要问题附近的片段。
    needed_files={row.get('file_id') for row in sources.values() if row.get('file_id')}
    linked_ids={link['passage_id'] for row in sources.values() for link in row.get('source_links',[])}
    needed_explanations={row.get('explanation_id') for row in sources.values() if row.get('explanation_id')}
    packet['reference_original'] = '\n\n'.join(
        p.get('aligned_original') or p.get('en') or p.get('zh', '') for p in material['passages']
        if not p.get('duplicate_of') and p.get('role') != 'page_furniture'
        and (not material.get('reading_files') or p.get('file_id') in needed_files
             or p['id'] in linked_ids or p.get('explanation_id') in needed_explanations))
    packet['reading_context'] = learning_payload(
        material, {'learning_outline':material.get('learning',{}).get('nodes')
                   if material.get('learning',{}).get('scope_active') else None}, state)['context']
    packet['dispatch']={'mode':'reader_local_pipeline','max_parallel':2,**writer_profile('answer'),'groups':[{'id':'writer-'+str(i+1),'question_ids':ids} for i,ids in enumerate(packet['groups'])],'input_rule':'用当前客户端的 daily 命令处理已保存的问题。每组先分析怎样讲，再把分析结果和本组相关原文交给写作者。','writeback':'客户端收到一组结果后逐题写回，并分别记录分析与写作用量。'}
    return annotate_questions(packet)


def change_answer(state, question_id, data, stamp, actor="reader"):
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
    from reader_questions import answer_revision
    prior_revision=answer_revision(q)
    if data.get('expected_answer_revision') is not None and data['expected_answer_revision']!=prior_revision:raise ValueError('回答已有新版本，请重新读取后修改')
    if data.get('expected_question_version') is not None and data['expected_question_version']!=q.get('version',1):raise ValueError('问题已更新，请重新读取后修改')
    value = data.get('text') if action == 'edit' else None
    if action == 'edit' and (not isinstance(value, str) or not value.strip() or len(value) > 30000):
        raise ValueError('回答需要填写 1 至 30000 字')
    if action == 'delete' and data.get('confirmed') is not True:
        raise ValueError('删除前需要再次确认')
    import copy
    q.setdefault('answer_history', []).append({'text': q['answer'], 'action': action, 'saved_at': stamp(),'answer_revision':prior_revision,'writer_source':actor,'question_version':q.get('answered_question_version',q.get('version',1)),**({'parts':copy.deepcopy(q['answer_parts'])} if q.get('answer_parts') else {})})
    q['answer'] = value.strip() if value else None
    for key in ('answer_parts','answer_body','display_answer','previous_answer','answer_revision','format_audit'):q.pop(key,None)
    q.pop('explained_source_ids',None);q.pop('source_ranges',None)
    q['answer_updated_at'] = stamp()
    if action=='delete':q['answer_deleted_at']=stamp()
    else:
        q.pop('answer_deleted_at',None);q.pop('answer_outdated',None)
        q['answered_question_version']=q.get('version',1);q['answered_question_text']=q['text']
    q.setdefault('answer_changes',[]).append({'at':stamp(),'before_revision':prior_revision,'after_revision':answer_revision(q),'writer_source':actor,'action':action})
    update_round_status(state,question_id)
    return {'question_id': question_id, 'action': action, 'saved': True}
