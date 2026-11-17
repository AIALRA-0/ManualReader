"""General STEP-06 acceptance tests: no sample-ID special cases or extra agent audit."""
from manualreader.stages.step05.contracts import consolidate, absorption_is_verified, SCHEMAS
from manualreader.stages.step06.coverage import analyze
from manualreader.stages.step05.inference import prompt


def row(sid,role='content',text='abc',file='f',page=1,kind='paragraph',bbox=None,atoms=None):
    return {'source_id':sid,'file_id':file,'position':{'type':'pdf_page','start':page,'end':page},
            'section_id':'ss','kind':kind,'role':role,'text':text,'registered_unit_ids':atoms or [sid],
            'asset_refs':[],'refs':[{'page_id':f'f:page:{page}','bbox':bbox}] if bbox else []}


def test_normal_only_absorption_verified_page_furniture():
    s={r['source_id']:r for r in [row('p','content','main'),row('c','page_furniture','page heading'),row('x','content','key fact')]}
    sugg=[{'source_id':'c','parent_source_id':'p','evidence_kind':'verified_source_group_member'},
          {'source_id':'x','parent_source_id':'p','evidence_kind':'verified_source_group_member'}]
    for mode in ('INTRO','FINALE','ANSWER'):
        out=consolidate(s,[{'anchor_id':'a','text':'draft'}],{('p','a')},set(),[],sugg,mode)
        assert out['absorption']==[] and out['unresolved_source_ids']==['c','x']
    out=consolidate(s,[{'anchor_id':'a','text':'draft'}],{('p','a')},set(),[],sugg,'NORMAL')
    assert [x['source_id'] for x in out['absorption']]==['c']
    assert out['unresolved_source_ids']==['x']


def test_formula_and_caption_evidence_without_fake_adjacent_links():
    p=row('p',text='x + y = 5',kind='formula',atoms=['atom1'])
    c=row('c',text='x + y',kind='formula',atoms=['atom1'])
    assert absorption_is_verified(c,p,'verified_parent_formula')
    assert not absorption_is_verified(row('other',text='a'),p,'verified_parent_formula')
    image=row('image',kind='image',text='plot',bbox=[5,5,100,100])
    caption=row('caption',kind='paragraph',text='Fig 1',bbox=[80,90,120,113])
    assert absorption_is_verified(caption,image,'verified_caption_member')
    far=row('far',kind='paragraph',text='Fig 1',bbox=[600,600,700,700])
    assert not absorption_is_verified(far,image,'verified_caption_member')
    assert not absorption_is_verified(row('cross',file='other'),p,'verified_source_group_member')


def test_all_goals_and_real_question_spans_counted_using_actual_targets():
    card={'question':{'text':'啥叫解空间？啥叫搜索？为啥模拟退火？'},
          'goals':{'reader':['解空间','搜索'],'teacher':['模拟退火原理']}}
    a=[{'anchor_id':'a','text':'answer'}]
    reply={'additions':[{'anchor_id':'a','placement':'after','kind':'text','markdown':'detail',
                         'source_ids':[],'asset_refs':[]}],
           'goal_links':[{'role':'reader','goal_index':0,'body_anchor_ids':['a'],'addition_indices':[]},
                         {'role':'reader','goal_index':1,'body_anchor_ids':[],'addition_indices':[0]},
                         {'role':'teacher','goal_index':0,'body_anchor_ids':[],'addition_indices':[0]}],
           'question_links':[{'question_quote':'啥叫解空间？','body_anchor_ids':['a'],'addition_indices':[]},
                             {'question_quote':'啥叫搜索？','body_anchor_ids':[],'addition_indices':[0]},
                             {'question_quote':'为啥模拟退火？','body_anchor_ids':[],'addition_indices':[0]}]}
    actual=analyze(card,'ANSWER',a,reply,{0:'06B-abc'})
    assert actual['goal_coverage']['covered']==3
    assert actual['all_goals_linked']
    assert actual['question_item_coverage']['covered']==3
    assert actual['all_question_items_linked']
    assert actual['question_raw_unit_coverage']['covered']==3


def test_empty_question_links_not_falsely_counted_as_100():
    card={'question':{'text':'这个是什么？有什么用？'},'goals':{'reader':['含义'],'teacher':['性质']}}
    result=analyze(card,'ANSWER',[{'anchor_id':'a'}],{'goal_links':[], 'question_links':[]},{})
    assert result['goal_coverage']['covered']==0
    assert result['question_item_coverage']['coverage_pct']==0.0
    assert not result['all_question_items_linked']


def test_06b_schema_has_same_call_both_relationship_lists():
    p=prompt('06B')
    assert b'question_links' in p and b'goal_links' in p
    assert set(SCHEMAS['06B']['required'])=={'additions','goal_links','question_links'}


def test_cached_replay_reuses_05_and_06a_bytes_and_only_runs_06b(tmp_path):
    from test_step56 import setup, ready, source
    from manualreader.stages.step05.anchors import anchors
    from manualreader.stages.step05.inference import read, write
    from manualreader.stages.step06.pipeline import JointJobs
    from manualreader.step56_server import render

    folder, _, draft=setup(tmp_path,'test',[source('s')])
    target=anchors(draft.decode())[1]['anchor_id']
    card=read(folder/'task-card.json')
    card['goals']={'reader':['一项知识目标']}
    write(folder/'task-card.json',card)
    record=read(folder/'step04-run.json')
    from manualreader.stages.step05.contracts import digest
    record['task_card_sha256']=digest((folder/'task-card.json').read_bytes())
    write(folder/'step04-run.json',record)
    replay=folder/'cached-step05-06a'
    write(replay/'step05-mapping.json',{'links':[{'source_id':'s','body_anchor_id':target}]})
    write(replay/'step05-unmapped.json',{'source_ids':[]})
    write(replay/'step06a-additions.json',{'links':[],'additions':[],'absorb_candidates':[]})
    write(replay/'receipts-index.json',{'05-00':{'response_id':'old'}})
    calls=[]

    def infer(folder,stage,packet,images,cli):
        calls.append(stage)
        result={'additions':[], 'question_links':[], 'goal_links':[
            {'role':'reader','goal_index':0,'body_anchor_ids':[target],'addition_indices':[]}]}
        return {'state':'complete','reply':result}

    jobs=JointJobs(tmp_path,1,infer=infer,cli='fake')
    try:
        jobs.submit('test')
        state=ready(jobs,'test')
        assert state['status']=='complete' and calls==['06B']
        job=jobs.folder('test')
        assert all((job/name).read_bytes()==(replay/name).read_bytes() for name in
                   ('step05-mapping.json','step05-unmapped.json','step06a-additions.json'))
        assert read(job/'run-metrics.json')['reused_calls']=={'05-00':{'response_id':'old'}}
        _, html=render(folder,job,state,'/step56/test')
        assert f'href="#{target}">位置 1</a>' in html
    finally:
        jobs.close()
