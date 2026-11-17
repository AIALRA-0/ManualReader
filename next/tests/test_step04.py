from base64 import b64decode
from hashlib import sha256
from http.server import ThreadingHTTPServer
from io import BytesIO
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile
import json

import pytest

from test_next_service import new_fixture
from test_step03_matrix import image
from manualreader.client import API
from manualreader.step04_client import submit, wait, download
from manualreader.step04_server import client_archive, handler
from manualreader.store import Project
from manualreader.stages.step02.card import assemble as card_from, link_sources
from manualreader.stages.step03.engine import Engine
from manualreader.stages.step04 import PROMPT_HASHES, prompt
from manualreader.stages.step04.draft_assets import check_references
from manualreader.stages.step04.jobs import Jobs, inspect_output
from manualreader.stages.step04.sources import assemble, context_for, SourceUnavailable
from manualreader.stages.step04.writer_transport import MarkdownTransport
from manualreader.transport import PROFILE


DRAFT = '# 实际 Markdown\n\n完整的原始公式 \\(x+1\\)\n\n```mermaid\ngraph TD\nA --> B\n```\n'.encode()


def fixture(root, mode, numbers=((1,1),), *, revise=False):
    projection, paths, text = new_fixture(root)
    if mode=='FINALE':
        projection['saved_explanations']=[{'node_id':'prior','chapter':'已保存','version':1,
                                          'source_ids':['s1'],'explanation_id':'e','blocks':['原保存讲解']}]
        paths['project'].write_text(json.dumps(projection,ensure_ascii=False),encoding='utf8')
    project = Project(root,'p')
    rid = project.lifecycle({'action':'prepare','mode':mode})['round_id']
    project.lifecycle({'action':'select','round_id':rid,'file_ids':['f']})
    assert project.lifecycle({'action':'reconcile','round_id':rid})['status']=='ready'
    context = project.context({'round_id':rid,'explicit_finale':mode=='FINALE',
                               'question_id':'q2','revise_existing_answer':revise})
    catalog = {'f':project.original({'action':'catalog','round_id':rid,'file_id':'f'})}
    draft = {'question_styles':[], 'goals':{'reader':['目标'],'teacher':['知识点']},
             'material_plan':[{'file_id':'f','description':'实际文字',
                              'source_ranges':[{'type':'text_line','start':a,'end':b} for a,b in numbers]}]}
    card = card_from(context,catalog,draft)[0]
    indexes={'f':project.original({'action':'index','round_id':rid,'file_id':'f'})}
    card,_ = link_sources(card,indexes,catalog)
    return project,rid,card,text


@pytest.mark.parametrize('mode', tuple(PROMPT_HASHES))
def test_prompt_is_frozen(mode):
    assert sha256(prompt(mode)).hexdigest() == PROMPT_HASHES[mode]
    assert sha256(prompt('NORMAL')).hexdigest() == '3f9ae5292bd68d8e7f7d39dadd01c63ce3b3c902a9a65fee78a8d42bbe37a8db'


def test_scoped_regions_and_missing_original(tmp_path):
    project,rid,card,text = fixture(tmp_path,'NORMAL',((1,1),(3,3)))
    state,context = context_for(project,rid,'NORMAL',card,False,False)
    evaluator=Engine(tmp_path/'cache',parallel=False)
    try:
        result,docs,images=assemble(evaluator,project,rid,'NORMAL',card,state,context)
        assert result['task_card']==card
        assert docs[0]['original']==[{'n':1,'text':'first argument'},{'n':3,'text':'third argument'}]
        assert docs[0]['boundary_context']==[{'n':2,'text':'second argument'}]
        assert docs[0]['source_locations']['s1'][0]['start']==1
        assert not images
        assert assemble(evaluator,project,rid,'NORMAL',card,state,context)[1][0]['local_cache']==['disk']
        text.write_bytes(text.read_bytes().replace(b'first',b'other'))
        with pytest.raises(SourceUnavailable,match='M1'):
            assemble(evaluator,project,rid,'NORMAL',card,state,context)
    finally:
        evaluator.close()


def test_answer_has_real_question_and_no_old_answer(tmp_path):
    project,rid,card,_ = fixture(tmp_path,'ANSWER')
    context_for(project,rid,'ANSWER',card,False,False)
    assert card['question']['text']=='当前问题原话'
    assert '这条问题已有的回答' not in card['context']
    assert card['context']['父问题及相关回答'][0]['answer']=='父回答'
    card['context']['这条问题已有的回答']='必须默认排除的本题旧答案'
    with pytest.raises(ValueError,match='上下文已经变化'):
        context_for(project,rid,'ANSWER',card,False,False)
    context_for(project,rid,'ANSWER',card,False,True)


def test_finale_needs_consent_and_real_saved_text(tmp_path):
    project,rid,card,_ = fixture(tmp_path,'FINALE')
    with pytest.raises(ValueError,match='明确选择'):
        context_for(project,rid,'FINALE',card,False,False)
    _,ctx = context_for(project,rid,'FINALE',card,True,False)
    assert ctx['saved_explanations'][0]['blocks']==['原保存讲解']
    assert not ctx['fixture']


def test_writer_wire_has_exact_prompt_and_real_pixels(tmp_path):
    raw=b'{"input":"data"}'.decode()
    png=image()
    h=sha256(png).hexdigest()
    wire=MarkdownTransport(tmp_path,raw,prompt('NORMAL').decode(),{h:png})
    body={'model':PROFILE['model'],'reasoning':{'effort':'high'},'service_tier':'default',
          'input':[{'role':'user','content':[{'type':'input_text','text':raw}]}],
          'text':{'format':{'type':'json_schema'}},'tools':[{'type':'function'}]}
    request,images=wire.prepare(body)
    assert images==[h]
    assert b64decode(request['input'][0]['content'][1]['image_url'].split(',')[1])==png
    assert request['instructions'].encode()==prompt('NORMAL')
    assert request['tool_choice']=='none' and not request['tools'] and 'text' not in request
    wire.receipts=[{'status':429},{}]
    wire.prepare(body)
    for code in (200,502,None):
        wire.receipts=[{'status':code},{}]
        with pytest.raises(ValueError,match='first output'):
            wire.prepare(body)


def test_partial_without_style_gate_and_local_image_validation(tmp_path):
    receipt={'response':{'status':'completed','model':PROFILE['model'],'service_tier':'default'}}
    assert inspect_output(b'x',receipt)=='complete'
    assert inspect_output(b'',receipt)=='incomplete'
    receipt['response']['status']='incomplete'
    assert inspect_output(DRAFT,receipt)=='incomplete'
    diagram=image()
    h=sha256(diagram).hexdigest()
    rel='assets/'+h+'.png'
    (tmp_path/'assets').mkdir()
    (tmp_path/rel).write_bytes(diagram)
    refs=check_references(tmp_path,f'![图]({rel})\n![外部](https://example.invalid/p.png)'.encode(),[],None,None,None)
    assert [(x['path'],x['available']) for x in refs]==[(rel,True),('https://example.invalid/p.png',False)]


def test_isolated_service_idempotent_download_and_unauthorized(tmp_path):
    project,rid,card,_ = fixture(tmp_path,'NORMAL')
    engine=Engine(tmp_path/'cache',parallel=False)
    calls=[]
    def writer(folder, system, raw, images, cli):
        calls.append(system)
        assert json.loads(raw)['task_card']==card
        return DRAFT,{'response':{'status':'completed','model':PROFILE['model'],'service_tier':'default'},
                      'provider_responses':[{'status':200}]}
    jobs=Jobs(tmp_path/'runs',engine,4,writer=writer,cli='unused')
    key='one long local isolated access token'
    http=ThreadingHTTPServer(('127.0.0.1',0),handler(tmp_path,key,engine,jobs))
    base=f'http://127.0.0.1:{http.server_port}'
    wire=(json.dumps(card,ensure_ascii=False)+'\n').encode('utf8')
    Thread(target=http.serve_forever,daemon=True).start()
    try:
        api=API(base,key,project.identity)
        start=submit(api,wire,rid,'NORMAL')
        end=wait(api,start,.03)
        assert end['status']=='complete' and end['calls']==1
        assert download(api,end,tmp_path/'download.md')==DRAFT
        assert (jobs.folder(end['task_id'])/'task-card.json').read_bytes()==wire
        assert submit(api,wire,rid,'NORMAL',resume=True)['task_id']==end['task_id']
        assert calls==[prompt('NORMAL')]
        assert b'first argument' not in urlopen(base+'/step04/'+end['task_id']).read()
        with pytest.raises(HTTPError) as err:
            urlopen(Request(base+'/v1/step04',data=b'{}'))
        assert err.value.code==403
        with pytest.raises(ValueError,match='编号无效'):
            jobs.folder('../README')
        with ZipFile(BytesIO(client_archive()[0])) as archive:
            for name, record in json.loads(archive.read('client-hashes.json')).items():
                assert sha256(archive.read(name)).hexdigest()==record['sha256']
    finally:
        http.shutdown()
        http.server_close()
        jobs.pool.shutdown()
        engine.close()


def test_technical_failure_can_resume_only_before_provider_dispatch(tmp_path):
    project,rid,card,_ = fixture(tmp_path,'NORMAL')
    engine=Engine(tmp_path/'cache',parallel=False)
    calls=[]
    def writer(folder, system, raw, images, cli):
        calls.append(folder)
        if len(calls)==1:
            return b'',{'response':{},'provider_responses':[],'cli_error':'simulated pre-dispatch failure'}
        return DRAFT,{'response':{'status':'completed','model':PROFILE['model']},
                      'provider_responses':[{'status':200}]}
    jobs=Jobs(tmp_path/'runs',engine,2,writer=writer,cli='unused')
    raw=json.dumps(card,ensure_ascii=False).encode()
    from time import sleep
    def terminal(job):
        for _ in range(200):
            view=jobs.status(job['task_id'])
            if view.get('output_hash') or view['status']=='transport_failure':
                return view
            sleep(.01)
        pytest.fail('Writer did not publish a final state')
    try:
        first=terminal(jobs.submit(project,rid,'NORMAL',raw))
        assert first['status']=='transport_failure' and first['request_attempts']==0
        assert jobs.submit(project,rid,'NORMAL',raw)['status']=='transport_failure'
        final=terminal(jobs.submit(project,rid,'NORMAL',raw,resume=True))
        assert final['status']=='complete' and final['resumed_before_provider_dispatch']
        assert len(calls)==2
        assert json.loads((calls[0]/'attempt-before-dispatch/run.json').read_bytes())['calls']==0
        assert jobs.submit(project,rid,'NORMAL',raw,resume=True)['status']=='complete'
        assert len(calls)==2
    finally:
        jobs.pool.shutdown()
        engine.close()
