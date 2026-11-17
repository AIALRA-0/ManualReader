import json
from hashlib import sha256
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from manualreader.client import API
from manualreader.server import handler, SDK_FILES
from manualreader.store import Project, build_context, EMPTY
from manualreader.stages.step02.card import assemble, render, policies
from manualreader.transport import Transport, MAX_REQUESTS, PROFILE, FUNCTION


def new_fixture(root):
    d=root/'projects'/'p'
    orig=d/'originals'/'sources'
    orig.mkdir(parents=True)
    txt=b'first argument\nsecond argument\nthird argument\n'
    (orig/'a.md').write_bytes(txt)
    h=sha256(txt).hexdigest()
    shape=lambda x,i:{'node_id':x,'parent_node_id':None,'number':str(i),'title':'实际章节 '+str(i)}
    directory=[shape('prior',1),shape('here',2)]
    directory[0]['title']='上一章'
    previous={'title':'上一章','bridges':['承接'], 'recaps':[], 'forecast':'预告'}
    ranges=[{'type':'text_line','start':1,'end':1}]
    questions={
        'q1':{'id':'q1','text':'上一级原话','answer':'父回答','selected_text':'父所选',
              'explicit_requirement':'用原题中的数值','version':1,'learning_node_id':'prior'},
        'q2':{'id':'q2','text':'当前问题原话','answer':'必须默认排除的本题旧答案','selected_text':'用户实际选中内容',
              'parent_question_id':'q1','version':2,'learning_node_id':'here','scope_hints':{'f':ranges}},
        'other':{'id':'other','text':'别的问题','version':1,'learning_node_id':'prior'}
    }
    f={'project_id':'p','name':'隔离测试','selection':'here','directory':directory,
       'files':{'f':{'file_id':'f','relative_path':'sources/a.md','sha256':h,'size_bytes':len(txt)}},
       'progress_input':{'completed_chapter_ids':['prior','here'],'total_formal_chapters':2,
                         'file_coverage':{'f':{'file_size':len(txt),'covered_bytes':len(txt)}}},
       'versions':{'material_schema':2,'workspace_revision':2,'records_revision':3,
                   'selection_revision':1,'file_manifest_revision':1},
       'previous':{'prior':previous},'questions':questions,
       'scope_hints':{'here':{'f':ranges}},
       'approved_styles':{'prior':[{'pattern':'读法','explanation_need':'解释来源','evidence_question_ids':['q1']}]},
       'recent_questions':{'prior':['other']},
       'saved_explanations':[],'fixture':None}
    paths={'project':d/'project.json','index':d/'source-index.json'}
    paths['index'].write_text(json.dumps({'f':{'sha256':h,'rows':[{'id':'s1','position':ranges[0]}]}}),encoding='utf8')
    paths['project'].write_text(json.dumps(f),encoding='utf8')
    return f,paths,orig/'a.md'


def material(root,p,paths,mode,**values):
    m1=Project(root,'p').lifecycle({'action':'prepare','mode':mode})['manifest']
    return build_context(p,mode,m1,paths['project'].parent,paths['index'],values)


def test_answer_changes_are_explicit_and_parent_requirements_survive(tmp_path):
    p,paths,_=new_fixture(tmp_path)
    q=material(tmp_path,p,paths,'ANSWER',question_id='q2')
    assert q['approved_styles']==q['unprocessed_questions']==[]
    fixed=q['fixed']
    assert fixed['question']['text']=='当前问题原话'
    assert '这条问题已有的回答' not in fixed['context']
    parent=fixed['context']['父问题及相关回答'][0]
    assert parent=={'question':'上一级原话','answer':'父回答','用户原始要求':'用原题中的数值'}
    revised=material(tmp_path,p,paths,'ANSWER',question_id='q2',revise_existing_answer=True)
    assert '本题旧答案' in revised['fixed']['context']['这条问题已有的回答']
    normal=material(tmp_path,p,paths,'NORMAL')
    assert normal['approved_styles'][0]['evidence_question_ids']==['q1']
    assert normal['unprocessed_questions'][0]['question_id']=='other'


@pytest.mark.parametrize('mode',('INTRO','NORMAL'))
def test_chapter_selection_and_previous_identity_are_authoritative(tmp_path,mode):
    p,paths,_=new_fixture(tmp_path)
    q=material(tmp_path,p,paths,mode)
    assert q['fixed']['previous']==(EMPTY if mode=='INTRO' else p['previous']['prior'])
    assert q['fixed']['chapter']['node_id']==(None if mode=='INTRO' else 'here')


def test_finale_does_not_start_on_progress_or_attach_unverified_content(tmp_path):
    p,paths,_=new_fixture(tmp_path)
    with pytest.raises(ValueError,match='明确选择'):
        material(tmp_path,p,paths,'FINALE')
    with pytest.raises(ValueError,match='no_saved_chapter'):
        material(tmp_path,p,paths,'FINALE',explicit_finale=True)
    p['saved_explanations']=[{'node_id':'prior','chapter':'已保存','version':1,'source_ids':['bad'],
                             'explanation_id':'e','blocks':['已存在原始讲解']}]
    with pytest.raises(ValueError,match='no_saved_chapter'):
        material(tmp_path,p,paths,'FINALE',explicit_finale=True)
    p['saved_explanations'][0]['source_ids']=['s1']
    c=material(tmp_path,p,paths,'FINALE',explicit_finale=True)
    assert c['fixed']['chapter']['node_id'] is None
    assert c['saved_explanations'][0]['blocks']==['已存在原始讲解']
    assert c['allowed_ranges']['f']==[{'type':'text_line','start':1,'end':1}]


def test_finale_and_answer_markdown_use_the_confirmed_contract(tmp_path):
    p,paths,_=new_fixture(tmp_path)
    p['saved_explanations']=[{'node_id':'prior','chapter':'已保存','version':1,'source_ids':['s1'],
                             'explanation_id':'e','blocks':['实际讲解']}]
    final=material(tmp_path,p,paths,'FINALE',explicit_finale=True)
    obj={'question_styles':[],'goals':{'reader':['对象'],'teacher':['实用术语']},
         'material_plan':[{'file_id':'f','description':'原件内容','source_ranges':[{'type':'text_line','start':1,'end':1}]}]}
    catalog={'f':{'file_id':'f','type':'text_line','positions':[1,2,3]}}
    result=render(assemble(final,catalog,obj)[0],policies())
    assert result.startswith('# 隔离测试 · 阶段性收束写作任务卡') and '第 None 节' not in result
    answer=material(tmp_path,p,paths,'ANSWER',question_id='q2')
    obj['material_plan']=[]
    result=render(assemble(answer,{},obj)[0],policies())
    for section in ('用户原始问题','上次的提问告诉我们，哪些地方值得讲细一点','本题宏观目标',
                    '依据的材料','当前问题的上下文','写作规范','格式规范'):
        assert '## '+section in result
    assert '## 版本' not in result
    assert result.count('Bad：')==result.count('Good：')==17


def test_same_size_edit_blocks_stale_plan_and_reuse_of_old_index(tmp_path):
    p,paths,orig=new_fixture(tmp_path)
    project=Project(tmp_path,'p')
    m1=project.lifecycle({'action':'prepare','mode':'NORMAL'})
    rid=m1['round_id']
    original=lambda action,**x:project.original({'action':action,'round_id':rid,'file_id':'f',**x})
    assert original('index')['verified']
    assert project.lifecycle({'action':'select','round_id':rid,'file_ids':['f']})['status']=='selected'
    old=orig.read_bytes()
    orig.write_bytes(old.replace(b'first',b'other'))
    with pytest.raises(ValueError,match='M1'):
        original('read',start=1,end=1)
    assert project.lifecycle({'action':'reconcile','round_id':rid})['status']=='selected_material_changed'
    new=project.lifecycle({'action':'prepare','mode':'NORMAL'})
    rid=new['round_id']
    assert original('index')['reason']=='parsed_original_version_differs'


def test_duplicate_file_selections_and_wrong_epoch_are_rejected(tmp_path):
    p,paths,_=new_fixture(tmp_path)
    p=Project(tmp_path,'p')
    m1=p.lifecycle({'action':'prepare','mode':'NORMAL'})
    with pytest.raises(ValueError,match='文件编号重复'):
        p.lifecycle({'action':'select','round_id':m1['round_id'],'file_ids':['f','f']})
    with pytest.raises(ValueError,match='没有该轮次'):
        p.locate_round('other')


def test_transport_deduplicates_actual_text_and_only_allows_one_model_read(tmp_path):
    model=tmp_path/'model';model.mkdir()
    t=Transport(model,'body','frozen',
        {'f':{'type':'text_line','rows':{1:{'n':1,'text':'cached'},2:{'n':2,'text':'new original text'}},'images':{}}},
        {'f':[1]},[])
    raw={'model':PROFILE['model'],'reasoning':{'effort':'high'},'service_tier':'default','input':[
            {'role':'user','content':[{'type':'input_text','text':'body'}]},
            {'type':'function_call','name':FUNCTION,'call_id':'c','arguments':json.dumps({'requests':[
                {'file_id':'f','start':1,'end':2}]})},
            {'type':'function_call_output','call_id':'c','output':'unsupported call'}],
        'tools':[{'name':'arbitrary'}]}
    packet,_=t.prepare(raw)
    assert packet['tools']==[] and packet['tool_choice']=='none'
    reply=json.loads(packet['input'][-1]['output'])
    assert reply['already_sent_count']==1 and [r['n'] for r in reply['rows']]==[2]
    t.prepare(raw)
    assert len((tmp_path/'original_model_read_trace.jsonl').read_text('utf8').splitlines())==1


def test_installed_http_has_own_client_and_protects_routes(tmp_path):
    p,paths,_=new_fixture(tmp_path)
    key='isolated-test-token-only'
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(tmp_path,key))
    Thread(target=server.serve_forever,daemon=True).start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'
        assert json.load(urlopen(base+'/health',timeout=5))['client_files']==len(SDK_FILES)
        request=Request(base+'/v1/step01',data=b'{}')
        with pytest.raises(HTTPError) as auth:
            urlopen(request,timeout=5)
        assert auth.value.code==403
        a=API(base,key,'p')
        r=a.call('step01','prepare',mode='ANSWER')
        with pytest.raises(RuntimeError,match='有效文件清单'):
            a.call('step02','read',round_id=r['round_id'],file_id='outside',start=1,end=1)
        q=a.call('step02','context',round_id=r['round_id'],question_id='q2')
        assert q['fixed']['question']['version']==2
    finally:
        server.shutdown()
        server.server_close()


def test_provider_429_is_forwarded_and_same_run_has_a_hard_request_bound(tmp_path, monkeypatch):
    class Reply:
        status_code=429
        headers={'Content-Type':'application/json','Retry-After':'1'}
        def iter_lines(self, **_):
            yield b'{"error":{"message":"simulated upstream throttling"}}'
        def close(self):pass
    from manualreader import transport
    posted=[]
    def substitute(*args, **kwargs):
        posted.append((args, kwargs['json']['tool_choice']))
        return Reply()
    monkeypatch.setattr(transport.requests,'post',substitute)
    model=tmp_path/'model';model.mkdir()
    t=Transport(model,'body','frozen',{}, {}, []).start()
    address=f'http://127.0.0.1:{t.server.server_port}/responses'
    body={'model':PROFILE['model'],'reasoning':{'effort':'high'},'input':[
        {'role':'user','content':[{'type':'input_text','text':'body'}]}]}
    try:
        for i in range(MAX_REQUESTS+1):
            request=Request(address,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
            with pytest.raises(HTTPError) as error:
                urlopen(request,timeout=5)
            assert error.value.code==(429 if i<MAX_REQUESTS else 409)
            if i<MAX_REQUESTS:assert error.value.headers.get('Retry-After')=='1'
        assert len(posted)==MAX_REQUESTS
        assert all(row['status']==429 for row in t.receipts)
    finally:t.stop()
