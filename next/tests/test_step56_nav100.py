"""STEP-06 final NAVIGATION contract. No semantic reviewer, no new inference."""
from manualreader.stages.step06.navigation import complete_normal_navigation
from manualreader.stages.step06.coverage import analyze


def source(sid, file='a', section='s', page=1):
    return {'source_id':sid,'file_id':file,'section_id':section,
            'position':{'type':'pdf_page','start':page,'end':page},
            'kind':'paragraph','role':'body','text':'unmatched real content'}


def test_normal_navigation_100_percent_without_falsifying_content_coverage():
    sources={x['source_id']:x for x in [source('a'),source('b',page=2),source('c',file='b')]}
    original={'scope':'NORMAL','source_to_explanation':{'a':['body-a']},
              'substantively_linked_source_ids':['a'],'explanation_to_source':{'body-a':['a']},
              'links':[{'source_id':'a','body_anchor_id':'body-a'}],
              'absorption':[], 'unresolved_source_ids':['b','c']}
    anchors=[{'anchor_id':'head','text':'# H'},
             {'anchor_id':'body-a','text':'Real paragraph.'},
             {'anchor_id':'body-b','text':'Other paragraph.'}]
    result=complete_normal_navigation(original,sources,anchors)
    assert result['source_navigation']['coverage_pct']==100.0
    assert len(result['source_to_explanation'])==3
    assert result['unresolved_source_ids']==[]
    assert result['substantively_linked_source_ids']==['a']
    assert set(result['navigation_only_source_ids'])=={'b','c'}
    assert all(x['substantive_coverage'] is False for x in result['navigation_only_links'])
    assert result['source_to_explanation']['b']==['body-a']
    assert result['source_to_explanation']['c'] in (['body-a'],['body-b'])
    assert result==complete_normal_navigation(result,sources,anchors)
    assert original['unresolved_source_ids']==['b','c']


def test_other_modes_navigation_to_real_anchor_without_semantic_claim():
    for mode in ('INTRO','FINALE','ANSWER'):
        result={'scope':mode,'source_to_explanation':{},'unresolved_source_ids':['a']}
        done=complete_normal_navigation(result,{'a':source('a')},[{'anchor_id':'x','text':'existing content'}])
        assert done['source_navigation']['coverage_pct']==100.0
        assert done['source_to_explanation']['a']==['x']
        assert done['navigation_only_links'][0]['substantive_coverage'] is False
        assert done['navigation_only_links'][0]['navigation_scope']==mode


def test_answer_feedback_fragment_attaches_to_previous_answered_subquestion():
    card={'question':{'text':'这个是什么？我还是不好想象？有什么作用？'},'goals':{'reader':[],'teacher':[]}}
    aids=[{'anchor_id':'a','text':'answer'},{'anchor_id':'b','text':'other'}]
    reply={'goal_links':[],'question_links':[
        {'question_quote':'这个是什么？','body_anchor_ids':['a'],'addition_indices':[]},
        {'question_quote':'有什么作用？','body_anchor_ids':['b'],'addition_indices':[]}]}
    result=analyze(card,'ANSWER',aids,reply,{})
    assert result['question_item_coverage']['covered']==2
    assert result['question_raw_unit_coverage']['coverage_pct']==100.0
    raw=result['question_raw_units']
    assert raw[1]['relation_type']=='absorbed_to_previous_answered_question'
    assert raw[1]['target_ids']==['a']


def test_never_fake_answered_subquestion_if_no_answer_exists():
    card={'question':{'text':'这是什么？这个好懂吗？'},'goals':{'reader':[],'teacher':[]}}
    result=analyze(card,'ANSWER',[{'anchor_id':'a','text':'answer'}],
                   {'question_links':[],'goal_links':[]},{})
    assert result['question_item_coverage']['coverage_pct']==0
    assert result['question_raw_unit_coverage']['coverage_pct']==0
    assert not result['all_question_items_linked']


def test_normal_no_body_anchor_does_not_forge_navigation():
    sources={'a':source('a')}
    result=complete_normal_navigation({'scope':'NORMAL','source_to_explanation':{},
                 'unresolved_source_ids':['a'],'substantively_linked_source_ids':[]},sources,[])
    assert result['source_navigation']['unresolved']==1


def test_existing_overlay_links_must_survive_navigation_closure():
    rows={'a':source('a'), 'b':source('b', page=2)}
    merged={'scope':'NORMAL','source_to_explanation':{'a':['overlay:patch-z']},
            'substantively_linked_source_ids':['a'],'unresolved_source_ids':['b']}
    result=complete_normal_navigation(merged,rows,[{'anchor_id':'text-anchor','text':'intro'}],
                                      [{'patch_id':'patch-z','markdown':'genuine expansion'}])
    assert result['source_to_explanation']['a']==['overlay:patch-z']
    assert result['source_navigation']['coverage_pct']==100.0
    assert result['navigation_only_source_ids']==['b']


def test_all_modes_preserve_valid_existing_overlay_links():
    for mode in ('NORMAL','INTRO','FINALE','ANSWER'):
        rows={'a':source('a'),'b':source('b')}
        original={'scope':mode,'source_to_explanation':{'a':['overlay:p-123']},'unresolved_source_ids':['b']}
        done=complete_normal_navigation(original,rows,[{'anchor_id':'body-one','text':'actual paragraph'}],
                                         [{'patch_id':'p-123','markdown':'actual patch'}])
        assert done['source_navigation']['coverage_pct']==100.0
        assert done['source_to_explanation']['a']==['overlay:p-123']
        assert done['navigation_only_links'][0]['source_id']=='b'
        assert done['navigation_only_links'][0]['substantive_coverage'] is False


def test_navigation_provenance_keeps_prior_absorption_and_real_subtitle_positions():
    rows={sid:{**source(sid),'position':{'type':'subtitle_sid','start':position,'end':position}}
          for sid,position in [('distant',500),('near',120),('unmapped',125),('absorbed',122)]}
    original={
        'scope':'NORMAL',
        'source_to_explanation':{'distant':['a'],'near':['b'],'absorbed':['b']},
        'substantively_linked_source_ids':['distant','near'],
        'absorption':[{'source_id':'absorbed','parent_source_id':'near','evidence_kind':'verified_source_group_member'}],
    }
    anchors=[{'anchor_id':'a','text':'Full paragraph A'},{'anchor_id':'b','text':'Full paragraph B'}]
    result=complete_normal_navigation(original,rows,anchors)
    assert result['source_to_explanation']['unmapped']==['b']
    assert result['source_navigation']['direct_semantic_count']==2
    assert result['source_navigation']['navigation_only_count']==2
    evidence={link['source_id']:link for link in result['links']}
    assert evidence['absorbed']['relation_type']=='navigation_only'
    assert evidence['near']['relation_type']=='direct_semantic'
    assert evidence['unmapped']['source_position']==rows['unmapped']['position']
    assert evidence['unmapped']['parent_position']==rows['absorbed']['position']


def test_invalid_targets_are_unresolved_when_no_real_anchor_exists():
    original={'scope':'INTRO','source_to_explanation':{'s':['invalid']}}
    result=complete_normal_navigation(original,{'s':source('s')},[])
    assert result['source_to_explanation']=={}
    assert result['unresolved_source_ids']==['s']


def test_raw_fragments_preserve_precise_offsets_and_mark_navigation_only():
    question='什么是力？  我还有些困惑。'
    result=analyze({'question':{'text':question}},'ANSWER',[{'anchor_id':'a','text':'answer'}],
                   {'question_links':[{'question_quote':'什么是力？','body_anchor_ids':['a']}]},{})
    first,feedback=result['question_raw_units']
    assert question[feedback['start']:feedback['end']]==feedback['text']=='我还有些困惑。'
    assert feedback['navigation_only'] and not feedback['substantive_coverage']
    assert not first['navigation_only']
