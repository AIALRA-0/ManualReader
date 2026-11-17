from copy import deepcopy

import pytest

from manualreader.stages.step02.card import link_sources
from manualreader.stages.step02.repair import repair_card, split_goal


@pytest.mark.parametrize('topic,parts',[
    ('整数乘法与向量计算',['整数乘法','向量计算']),
    ('误差传播模型；采样窗口长度',['误差传播模型','采样窗口长度']),
    ('区域人口、用水总量、供水价格',['区域人口','用水总量','供水价格']),
    ('温度传感器与控制参数的对应关系',None),
    ('高速通道、低延迟接口的对比',None),
    ('处理器的宽发射、深乱序微架构',None),
    ('阳台、露台等赠送空间',None),
    ('给定输入时的吞吐量（同步、异步）',None),
    (r'函数值与误差上界：\(a=b\)',None),
])
def test_split_only_self_contained_topics(topic,parts):
    assert split_goal(topic)==(parts or [topic])


def original(text,kind='text_line',suffix='.md'):
    rows={i+1:{'n':i+1,'text':line} for i,line in enumerate(text)}
    span={'type':kind,'start':3,'end':3}
    card={'goals':{'reader':['范围与结果的关系'],'teacher':['公式结构']},
          'material_plan':[{'file_id':'f','relative_path':'sources/doc'+suffix,
                            'source_ranges':[span],'source_ids':['kept']}],
          'chapter':{'node_id':'n'},'policy_refs':{'fixed':True},'question_styles':[]}
    catalog={'f':{'type':kind,'positions':list(rows)}}
    index={'f':{'verified':True,'rows':[
        {'id':'kept','position':span},
        {'id':'added','position':{'type':kind,'start':4,'end':4}},
        {'id':'outside','position':{'type':kind,'start':5,'end':5}}]}}
    return card,{'f':rows},catalog,index


@pytest.mark.parametrize('delimiters',[
    ('~~~python','~~~'),
    ('$$','$$'),
    (r'\[',r'\]'),
])
def test_exact_block_extension_retains_previous_source_and_adds_only_new_source(delimiters):
    card,units,catalog,index=original(['heading',delimiters[0],'a = b',delimiters[1],'next section'])
    untouched=deepcopy(card)
    repaired,stats=repair_card(card,{'mode':'NORMAL'},units)
    final,_=link_sources(repaired,index,catalog)
    assert card==untouched
    assert final['material_plan'][0]['source_ids']==['kept','added']
    assert final['material_plan'][0]['source_ranges']==[{'type':'text_line','start':2,'end':4}]
    assert {k:v for k,v in final.items() if k!='material_plan'}=={k:v for k,v in card.items() if k!='material_plan'}
    assert stats=={'goals':0,'source_ranges':1}
    assert repair_card(final,{'mode':'NORMAL'},units)==(final,{'goals':0,'source_ranges':0})


@pytest.mark.parametrize('mode',('NORMAL','INTRO','FINALE','ANSWER'))
def test_ambiguous_page_keeps_bound_source_and_draft(mode):
    card,units,catalog,index=original(['preview','prior','proof may continue','next heading','section'],
                                      kind='pdf_page',suffix='.pdf')
    repaired,stats=repair_card(card,{'mode':mode},units)
    linked,_=link_sources(repaired,index,catalog)
    assert repaired==linked==card
    assert stats=={'goals':0,'source_ranges':0}


def test_finale_does_not_extend_beyond_recorded_scope():
    card,units,_,_=original(['heading','$$','a = b','$$','next section'])
    context={'mode':'FINALE','allowed_ranges':{'f':[{'type':'text_line','start':3,'end':3}]}}
    repaired,stats=repair_card(card,context,units)
    assert repaired==card and stats['source_ranges']==0


def test_unclosed_fence_and_shared_goal_phrase_stay_intact():
    card,units,_,_=original(['heading','~~~','code','also code','more'])
    card['goals']['reader']=['模型的边界条件、假设和结论之间的关系']
    assert repair_card(card,{'mode':'NORMAL'},units)==(card,{'goals':0,'source_ranges':0})
