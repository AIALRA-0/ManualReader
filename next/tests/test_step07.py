"""Exercise local patch rules against Reader's actual Markdown scanner."""

from importlib.resources import files

import pytest

from manualreader.stages.step05.anchors import anchors
from manualreader.stages.step05.contracts import digest
from manualreader.stages.step07.fusion import fuse, invert, layer, local_edit
from manualreader.stages.step07.inference import POLICY_SHA, PROMPT_NAME, PROMPT_SHA, SCHEMA
from manualreader.stages.step07.resources import Media, scan_batch


def setup(body, additions=()):
    spans=anchors(body)
    source='registered-source-1'
    patches=[{'patch_id':f'p{i}','anchor_id':spans[p[2]]['anchor_id'],'placement':'after',
              'markdown':p[0],'origin':p[1],'asset_refs':[],'source_ids':[source]}
             for i,p in enumerate(additions)]
    targets=[spans[0]['anchor_id']]+['overlay:'+p['patch_id'] for p in patches]
    links=[{'source_id':source,'body_anchor_id':key,'relation_type':'navigation_only'} for key in targets]
    table={'source_to_explanation':{source:targets},'links':links,
           'source_navigation':{'linked':1,'total':1}}
    items=[{'target_ids':targets}]
    coverage={**{k:items for k in ('goal_items','question_items','question_raw_units')},
              **{k:{'total':1,'covered':1} for k in ('goal_coverage','question_item_coverage','question_raw_unit_coverage')}}
    return spans,patches,table,coverage


def decision(identifier='p0',action='keep_inline',quote='',side='',edits=()):
    return {'overlay_id':identifier,'action':action,'new_anchor_quote':quote,
            'insert_side':side,'text_revisions':list(edits),'reason':'test'}


def edit(old,new):
    return {'original_exact':old,'replacement':new}


def execute(tmp_path,body,patches,rows=(),edits=(),bridges=()):
    spans,overlays,mapping,coverage=setup(body,patches)
    material=Media(tmp_path,{}, {'assets':{},'sources':[]})
    reply={'supplement_decisions':list(rows),'c3_edits':list(edits),'transition_edits':list(bridges)}
    result=fuse('case',body,spans,overlays,mapping,coverage,reply,material)
    fused,changes,remap,*_=result
    assert invert(fused,changes['operations'])==body
    assert remap['mapping']==mapping
    assert remap['goal_question_coverage']==coverage
    return result


def test_prompt_is_frozen():
    actual=files('manualreader.stages.step07').joinpath(PROMPT_NAME).read_bytes()
    assert digest(actual)==PROMPT_SHA
    policy=files('manualreader.stages.step02').joinpath('reader_step02_policies.json').read_bytes()
    assert digest(policy)==POLICY_SHA
    assert SCHEMA['required']==['supplement_decisions','c3_edits','transition_edits']


def test_blank_overlay_retains_unicode_crlf_bytes(tmp_path):
    chapter='🚪正文甲\r\n\r\n第二段\r\n'
    fused,*_=execute(tmp_path,chapter,[])
    assert fused.encode()==chapter.encode()


def test_moves_to_unique_adjacent_quote_without_changing_resource_ids(tmp_path):
    chapter='## 当前主题\n\n甲说明背景\n\n乙给出结果 🔍'
    patches=[('用公式 \\(z=7\\) 补充','06B',1)]
    baseline=execute(tmp_path,chapter,patches,[decision()])
    accepted=execute(tmp_path,chapter,patches,[decision(action='move_inline',quote='乙给出结果',side='after')])
    assert accepted[0].index('乙给出结果') < accepted[0].index('用公式')
    assert accepted[3][0]['resource_id']==baseline[3][0]['resource_id']
    assert accepted[3][0]['start']!=baseline[3][0]['start']


def test_fallback_on_nonunique_move(tmp_path):
    chapter='## 第一节\n\n原处\n\n## 第二节\n\n远处出现\n\n远处出现'
    patches=[('必要的说明','06A',1)]
    for quote in ('远处出现','无效引句'):
        result=execute(tmp_path,chapter,patches,[decision(action='move_inline',quote=quote,side='after')])
        assert result[1]['overlays'][0]['rejections']
        assert result[1]['overlays'][0]['action']=='keep_inline'


def test_deferred_resource_keeps_link_and_required_06a_stays_inline(tmp_path):
    chapter='写出推导'
    patches=[('必要条件 \\(x>0\\)','06A',0),('拓展例子 \\(x=2\\)','06B',0)]
    result=execute(tmp_path,chapter,patches,[decision(action='defer'),decision('p1','defer')])
    assert '必要条件' in result[0] and '拓展例子' not in result[0]
    assert result[2]['anchors']['overlay:p1']['surface']=='deferred'
    assert result[4]['overlay:p1']['markdown']==patches[1][0]
    assert [r['surface'] for r in result[3]]==['body','deferred']


def test_omit_requires_actual_equivalent_and_keeps_alias(tmp_path):
    chapter='保留的条件 \\(x>0\\)'
    patches=[(chapter,'06B',0),('不同条件 \\(x\\geq0\\)','06A',0)]
    fused,changes,remap,resources,*_=execute(tmp_path,chapter,patches,
        [decision(action='omit_duplicate',quote=chapter),decision('p1','omit_duplicate',quote=chapter)])
    assert fused.count(chapter)==1
    assert changes['overlays'][0]['action']=='omit_duplicate'
    assert changes['overlays'][1]['action']=='keep_inline'
    assert remap['anchors']['overlay:p0']['alias_of']
    assert len(resources)==3


@pytest.mark.parametrize('old,new,reason',[
    ('公式 \\(a=2\\)','公式 \\(a=3\\)','edit_changes_protected_formula_inline'),
    ('重复句','改重复句','quote_not_unique'),
])
def test_rejects_unsafe_edit(old,new,reason):
    body='出现 14 次，可能出现。公式 \\(a=2\\)，范围就是这些。\n\n重复句，重复句。'
    scan,=scan_batch([body])
    with pytest.raises(ValueError,match=reason):
        local_edit(body,edit(old,new),scan,[])


def test_edit_sentence_and_restore_with_parallel_insertions(tmp_path):
    body='同事把结果发给老师，然后她在课上展示结果。\n\n这里提供背景。'
    row={**edit('然后她在课上展示结果。','老师随后在课上展示结果。'),'source_ids':[],'reason':'test'}
    fused,changes,*_=execute(tmp_path,body,[('相关说明','06B',0)],[decision()],[row])
    assert '老师随后在课上展示结果。' in fused
    assert len(changes['operations'])==2


def test_multiple_model_decisions_only_fall_back_locally(tmp_path):
    body='主句\n\n接着读'
    result=execute(tmp_path,body,[('第一个补充','06B',0),('第二个补充','06B',1)],
                   [decision(),decision(action='defer'),decision('p1','defer')])
    assert [x['action'] for x in result[1]['overlays']]==['keep_inline','defer']
    assert result[1]['overlays'][0]['rejections']==['duplicate_model_decision']


def test_evidence_media_may_be_separate_from_embedded_image(tmp_path):
    raw=b'actual evidence'
    sha=digest(raw)
    (tmp_path/f'{sha}.png').write_bytes(raw)
    media=Media(tmp_path,{}, {'assets':{'asset1':{'sha256':sha}},'sources':[]})
    assert media.patch_assets_readable({'asset_refs':['asset1']},[])
    assert not media.patch_assets_readable({'asset_refs':['absent']},[])
    assert not media.patch_assets_readable({'asset_refs':[]},[{'kind':'image','status':'ok','media_path':'../f.png'}])


def test_markdown_code_table_diagram_and_math_are_distinct():
    text='|变量|关系|\n|---|---|\n|\\(x\\)|最大值|\n\n'+chr(96)*3+'python\nprint("$not_math")\n'+chr(96)*3
    text+='\n\n'+chr(96)*3+'mermaid\nflowchart TD\n A --> B\n'+chr(96)*3
    scan,=scan_batch([text])
    assert {x['kind'] for x in scan['resources']}=={'table','formula_inline','mermaid','code'}


def test_restore_rejects_wrong_version():
    body,ops=layer('开门', [{'start':1,'end':2,'old':'门','new':'窗'}])
    assert invert(body,ops)=='开门'
    with pytest.raises(ValueError):
        invert('关门',ops)



def test_unchanged_inline_formula_can_move_with_its_prose(tmp_path):
    body='接着她用 \\(x=7\\) 写出同一个结果。'
    proposal={**edit(body,'老师随后用 \\(x=7\\) 写出同一个结果。'),'source_ids':[],'reason':'test'}
    fused,_,_,resources,*_=execute(tmp_path,body,[('背景补充','06B',0)],[decision()],[proposal])
    assert fused.startswith('老师随后用')
    assert fused[resources[0]['start']:resources[0]['end']]=='\\(x=7\\)'
    assert resources[0]['original_start']!=resources[0]['start']



def bridge(overlay,side,quote,new,old=None):
    return {'overlay_id':overlay,'side':side,'operation':'replace' if old is not None else 'insert',
            'anchor_quote':quote,'original_exact':old,'replacement':new,'reason':'test'}


def c3(old,new):
    return {**edit(old,new),'source_ids':[],'issue_type':'test','reason':'test'}


def test_whole_document_edit_without_overlays_allows_format_and_wording_changes(tmp_path):
    old='它一共重复 12 次，称为「setup」。'
    body=old+'\n\n保留后面的技术段落。'
    new='控制器执行 12 次初始化\n\n这里使用中文名称'
    fused,edits,*_=execute(tmp_path,body,[],edits=[c3(old,new)])
    assert fused.startswith(new) and edits['operations'][0]['kind']=='c3_edit'


def test_long_multi_paragraph_edit_retains_reachable_anchors(tmp_path):
    first='启动时先检查环境。'*60
    second='第二步读取真实参数。'
    body=first+'\n\n'+second
    updated='配置检查完成后再读取真实参数'
    fused,_,remap,*_=execute(tmp_path,body,[],edits=[c3(body,updated)])
    assert fused==updated
    for a in remap['anchors'].values():
        assert fused[a['start']:a['end']]==updated


def test_adding_pre_and_post_bridge_to_kept_overlay(tmp_path):
    body='读完背景\n\n现在进入后文'
    rows=[bridge('p0','before','读完背景','说明新增例子的目的'),
          bridge('p0','after','现在进入后文','例子说明怎样回到原问题')]
    fused,edits,*_=execute(tmp_path,body,[('这段是补充','06B',0)],[decision()],bridges=rows)
    texts=['读完背景','说明新增例子的目的','这段是补充','例子说明怎样回到原问题','现在进入后文']
    assert [fused.index(x) for x in texts]==sorted(fused.index(x) for x in texts)
    assert len(edits['transitions'])==2
    for item in edits['transitions']:
        pos=item['target']
        assert fused[pos['start']:pos['end']]==item['proposal']['replacement']


def test_moved_overlay_both_bridges_follow_final_location(tmp_path):
    body='旧位置\n\n第一段\n\n中间一段\n\n还需要更多背景\n\n本节结论'
    rows=[bridge('p0','before','旧位置','桥接到例子'),
          bridge('p0','after','本节结论','讲完例子后回到结论','本节结论')]
    moved=decision(action='move_inline',quote='本节结论',side='before')
    fused,edits,*_=execute(tmp_path,body,[('新放置的补充','06B',0)],[moved],bridges=rows)
    texts=['还需要更多背景','桥接到例子','新放置的补充','讲完例子后回到结论']
    assert fused.index('还需要更多背景')<fused.index(texts[1])<fused.index(texts[2])<fused.index(texts[3])
    assert len(edits['transitions'])==2


def test_between_two_overlays_uses_resolved_order(tmp_path):
    body='原句位置\n\n在这个位置结束'
    rows=[bridge('p0','between','第二个补充内容','让读者顺着两组例子走')]
    patches=[('第一个补充内容','06A',0),('第二个补充内容','06B',0)]
    fused,edits,*_=execute(tmp_path,body,patches,[decision(),decision('p1')],bridges=rows)
    assert fused.index(patches[0][0])<fused.index(rows[0]['replacement'])<fused.index(patches[1][0])
    assert edits['transitions'][0]['proposal']['side']=='between'


def test_editor_can_replace_label_but_rejects_new_link_target_and_bad_fence(tmp_path):
    link='参见 [原文](https://example.com/a)'
    body=link+'\n\n第二段已经有唯一标签\n\n第三段'
    updated='查阅 [正式文档](https://example.com/a)'
    fused,edits,*_=execute(tmp_path,body,[],edits=[
        c3(link,updated),c3('第二段已经有唯一标签','放入 [标签](https://example.com/b)'),
        c3('第三段',chr(96)*3+'text\n缺少结束围栏')])
    assert updated in fused and '第二段已经有唯一标签' in fused and '第三段' in fused
    assert {x['reason'] for x in edits['rejections']}=={
        'invalid_new_markdown_resource:code','markdown_link_targets_or_link_count_changed'}


def test_stale_or_conflicting_transition_only_rejects_that_proposal(tmp_path):
    body='旧位置\n\n要移动到这里\n\n保留我'
    rows=[bridge('p0','after','旧位置','这句会脱离原来语境','旧位置'),
          bridge('p0','before','保留我','先解释目的')]
    moved=decision(action='move_inline',quote='保留我',side='before')
    fused,edits,*_=execute(tmp_path,body,[('新放置的补充','06B',0)],[moved],bridges=rows)
    assert '旧位置' in fused and '先解释目的' in fused
    assert len(edits['transitions'])==1
    assert edits['rejections'][0]['reason']=='bridge_replacement_not_at_final_side'


def test_outlying_text_edits_do_not_need_nearby_overlay(tmp_path):
    paragraphs=['首段有补充']+[f'中间有 {i} 段铺垫' for i in range(9)]+['末段移动到这里']
    body='\n\n'.join(paragraphs)
    moved=decision(action='move_inline',quote=paragraphs[-1],side='after')
    fused,edits,*_=execute(tmp_path,body,[('补充文字','06B',0)],[moved],
        edits=[c3(paragraphs[2],'这一节解释这些例子共有的条件')])
    assert fused.index('末段移动到这里')<fused.index('补充文字')
    assert any(op['kind']=='c3_edit' for op in edits['operations'])
