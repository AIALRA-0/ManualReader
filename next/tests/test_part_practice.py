import asyncio
from copy import deepcopy
import pytest

from manualreader.steps.step_14_practice import complete_parts
from manualreader.stages.finish.provider import frozen_prompts, SOL
from reader_part_practice import coverage, install_missing, lesson_parts, relocate_existing


def question(pid, index=0):
    return {'type':'choice','prompt':f'{pid} 的材料支持哪项判断 {index}？',
            'options':['材料中的判断','缺少依据的判断'],'correct_index':0,
            'explanation':'根据对应正文的条件选择第一项',
            'option_explanations':['这项符合正文条件','这项遗漏了正文条件'],
            'placement':{'kind':'lesson','after_part_id':pid},'block_ids':[pid+'-b'],'source_ids':[]}


@pytest.mark.parametrize('mode',['NORMAL','INTRO','FINALE','ANSWER'])
def test_every_mode_recovers_only_missing_parts(mode):
    parts=[{'part_id':p,'title':p,'blocks':[{'block_id':p+'-b','markdown':'相应的完整正文 '+p,'source_ids':[]}]} for p in ['p1','p2','p3']]
    calls=[]
    async def ask(stage,payload):
        calls.append(deepcopy(payload))
        assert stage=='F3'
        assert all(p['blocks'][0]['markdown'].startswith('相应的完整正文') for p in payload['parts'])
        selected=payload['parts'][:1] if len(calls)==1 else payload['parts']
        return {'questions':[question(p['part_id']) for p in selected]}
    result=asyncio.run(complete_parts(parts,ask,mode=mode,title='章节'))
    assert result['coverage']=={'total':3,'covered':3,'percentage':'100.00'}
    assert [p['part_id'] for p in calls[1]['parts']]==['p2','p3']
    assert len(result['questions'])==3
    assert calls[0]['practice_mix']['minimum_choice']==2
    assert calls[0]['practice_mix']['minimum_objective']==3
    async def never(*args):
        pytest.fail('已有完整题目不应再次调用模型')
    replay=asyncio.run(complete_parts(parts,never,mode=mode,title='章节',existing=result['questions']))
    assert replay['questions']==result['questions']


def test_multiple_old_questions_and_answers_are_preserved():
    old=[{**question('a',i),'id':f'old-{i}','part_id':'a','selected':i} for i in range(2)]
    before=deepcopy(old)
    async def ask(stage,payload):
        assert [p['part_id'] for p in payload['parts']]==['b']
        return {'questions':[question('b')]}
    parts=[{'part_id':p,'blocks':[{'block_id':p+'-b','markdown':p,'source_ids':[]}]} for p in ['a','b']]
    result=asyncio.run(complete_parts(parts,ask,mode='INTRO',title='章',existing=old))
    assert old==before
    assert result['questions'][:2]==old
    assert result['coverage']['covered']==2


def test_invalid_response_does_not_discard_successful_questions():
    parts=[{'part_id':p,'blocks':[{'block_id':p+'-b','markdown':p,'source_ids':[]}]} for p in ['a','b']]
    calls=[]
    async def ask(stage,payload):
        calls.append(payload)
        if len(calls)==1:
            return {'questions':[question('a'),{**question('b'),'option_explanations':[]},question('outside')]}
        return {'questions':[question('b')]}
    result=asyncio.run(complete_parts(parts,ask,mode='ANSWER',title='章'))
    assert result['unresolved_part_ids']==[]
    assert [p['part_id'] for p in calls[1]['parts']]==['b']
    assert len(result['questions'])==2


def material(route=True):
    texts=[('# 正式章节标题','title'),('## 第一部分','a'),('相同的段落','a-1'),
           ('相同的段落','a-2'),('## 第二部分','b'),('第二部分的末段','b-1')]
    passages=[{'id':'e--'+bid,'content_kind':'explanation','explanation_id':'e','zh':text}
              for text,bid in texts]
    passages.append({'id':'e--closing','zh':'章节总结','chapter_ending_role':'synthesis','progress_exempt':True})
    explanation={'id':'e','title':'正式章节标题','generation_mode':'INTRO',
                 'passage_ids':[p['id'] for p in passages],
                 'narrative':{'flow':[{'part_id':'a','label':'第一部分'}, {'part_id':'b','label':'第二部分'}] if route else []}}
    return {'explanations':[explanation],'passages':passages},explanation


@pytest.mark.parametrize('route',[True,False])
def test_real_parts_keep_all_paragraphs_and_use_last_body_passage(route):
    m,e=material(route)
    parts=lesson_parts(m,e)
    assert len(parts)==2
    assert parts[0]['passage_ids']==['e--a','e--a-1','e--a-2']
    assert parts[0]['end_passage_id']=='e--a-2'
    assert parts[1]['end_passage_id']=='e--b-1'
    assert [b['markdown'] for b in parts[0]['blocks']].count('相同的段落')==2


def test_append_native_questions_is_idempotent_and_keeps_body_and_history():
    m,e=material()
    old={**question('a'),'id':'answered','selected':0,'explanation_ids':['e'],
         'placement':{'kind':'lesson','explanation_id':'e','after_part_id':'a-2','after_passage_id':'e--a-2'}}
    state={'rounds':[],'terms':[],'quizzes':[old],'notes':{'n':'笔记'},'read_passages':{'e--a-1':True}}
    before=deepcopy(state);body=deepcopy(m)
    additions=[{**question('b'),'part_id':'b'}]
    saved=install_missing(m,state,'e',additions,lambda:'now')
    assert len(saved['saved']['quizzes'])==1
    assert state['quizzes'][0]==before['quizzes'][0]
    assert state['notes']==before['notes'] and state['read_passages']==before['read_passages']
    assert m==body
    assert coverage(m,state)['percentage']=='100.00'
    assert state['quizzes'][-1]['placement']['after_passage_id']=='e--b-1'
    installed=deepcopy(state)
    assert install_missing(m,state,'e',additions,lambda:'later')['saved']['quizzes']==[]
    assert state==installed


def test_active_prompt_matches_all_four_modes_and_verified_profile():
    prompt=frozen_prompts()['F3'].decode('utf8')
    assert '四种模式都必须逐 Part' in prompt
    assert '末尾' in prompt
    assert SOL=={'model':'gpt-6.1-sol','effort':'high','service_tier':'default'} or SOL.get('model')=='gpt-6.1-sol' and SOL.get('effort')=='high'


def test_existing_part_question_moves_without_regeneration_or_answer_change():
    m,e=material()
    old={**question('a'),'id':'answered','selected':0,'explanation_ids':['e'],
         'placement':{'kind':'lesson','explanation_id':'e','after_part_id':'a','after_passage_id':'e--a'}}
    state={'quizzes':[old],'rounds':[]}
    before=deepcopy(old)
    assert coverage(m,state)['covered']==0
    changes=relocate_existing(m,state)
    assert len(changes)==1
    assert state['quizzes'][0]['placement']['after_passage_id']=='e--a-2'
    assert {k:v for k,v in old.items() if k!='placement'}=={k:v for k,v in before.items() if k!='placement'}
    assert coverage(m,state)['covered']==1
    assert relocate_existing(m,state)==[]
