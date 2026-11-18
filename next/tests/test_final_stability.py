import asyncio
from copy import deepcopy
import http.client
import json
from unittest.mock import patch

from manualreader.artifacts import write_json as write
from manualreader.steps.step_13_terminology import repair_context_references
from manualreader.stages.finish.content import translate,translation_processing
from manualreader.stages.finish.pipeline import attachment_gaps,wait_translation
from reader_part_practice import answer_coverage
from reader_practice_upgrade import associate_terms
from reader_writer_transport import Gate

DEFINITION='缓存保存最近或经常需要的数据，让处理器先在访问较快的位置查找。找到时可以直接使用；没有找到时仍需从下一层取得数据，并按管理策略决定是否留下副本。容量、访问延迟和替换方式会共同影响命中机会，缓存本身不会消除所有等待。'
INTUITION='把常用工具放在桌边的小盒子里，可以少走几次路；小盒子放不下的工具仍要到储物柜拿。桌边找到工具相当于缓存命中，去储物柜取工具相当于未命中。盒子大小决定能放多少东西，整理规则决定留下哪些东西；频繁换一批从未用过的工具时，即使有小盒子也仍然需要来回取用。'


def source():
    return {'source_files':{'f':'version-1'},'sources':[
        {'source_id':sid,'file_id':'f','kind':'paragraph','text':text,'position':{'start':i,'end':i},'asset_refs':[]}
        for i,(sid,text) in enumerate([('s1','A cache stores data.'),('s2','A miss fetches data from the next level.')])],
        'assets':{},'snapshot_sha256':'snapshot'}


def test_translation_resume_requests_only_missing_and_keeps_preserved_decision(tmp_path):
    async def initial(stage,payload):
        return {'groups':[{'translation_group_id':'g','segments':[{'source_id':'s1','translation_id':'t1',
                   'text':'缓存保存数据','relation':'translated','status':'complete'}]}],'unresolved_source_ids':['s2']}
    first=asyncio.run(translate(tmp_path,'sample',source(),initial))
    calls=[]
    async def supplement(stage,payload):
        calls.append(payload)
        assert [u['source_id'] for u in payload['units']]==['s2']
        return {'groups':[{'translation_group_id':'retained','segments':[{'source_id':'s2','translation_id':'s2',
                   'text':'由原生装配保留原文','relation':'identity','status':'uncertain'}]}],'unresolved_source_ids':[]}
    result=asyncio.run(translate(tmp_path,'sample',source(),supplement,existing=first))
    assert len(calls)==1
    assert result['groups'][0]['segments'][0]['text']=='缓存保存数据'
    assert translation_processing(source(),result['links'])['processed']==2
    async def never(*args):
        raise AssertionError('成功译文和明确保留的原文不应重新调用模型')
    replay=asyncio.run(translate(tmp_path,'sample',source(),never,existing=result))
    assert translation_processing(source(),replay['links'])['processed']==2


def test_old_translation_version_is_not_reused(tmp_path):
    existing={'groups':[{'segments':[{'source_id':'s1','source_version':'outdated',
                    'original_sha256':'old','text':'旧版本译文','status':'complete'}]}]}
    asked=[]
    async def request(stage,payload):
        asked.extend(u['source_id'] for u in payload['units'])
        return {'groups':[],'unresolved_source_ids':['s1','s2']}
    asyncio.run(translate(tmp_path,'sample',source(),request,existing=existing))
    assert set(asked)=={'s1','s2'}


def test_completed_label_does_not_hide_missing_part_questions(tmp_path):
    write(tmp_path/'part-blocks.json',{'parts':[{'part_id':'a','block_ids':['a']},{'part_id':'b','block_ids':['b']}],
                                     'blocks':[],'resources':[]})
    write(tmp_path/'quiz-candidates.json',[{'part_id':'a','prompt':'已存在的题目'}])
    assert attachment_gaps(tmp_path)=={'practice':['b']}
    write(tmp_path/'quiz-candidates.json',[{'part_id':p,'prompt':'对应 '+p} for p in ['a','b']])
    assert attachment_gaps(tmp_path)=={}


def test_translation_readiness_is_per_chapter_without_batch_barrier(tmp_path):
    write(tmp_path/'translation-queue.json',{'state':'running','at':2})
    job=tmp_path/'jobs'/'a';write(job/'translation-initial.json',{'value':'old'})
    write(job/'translation-ready.json',{'batch_at':1})
    async def scenario():
        waiting=asyncio.create_task(wait_translation(tmp_path,job))
        await asyncio.sleep(.02)
        assert not waiting.done()
        write(job/'translation-initial.json',{'value':'new'})
        write(job/'translation-ready.json',{'batch_at':2})
        # 整批仍在运行；这一篇可以立即继续。
        return await asyncio.wait_for(waiting,2)
    assert asyncio.run(scenario())=={'value':'new'}


def test_term_repair_preserves_qualified_field_and_recovers_only_missing_items():
    rows=[{'name':name,'status':'candidate','professional_definition':DEFINITION,
           'intuitive_explanation':'太短','aliases':[],'source_ids':[],'block_ids':[name],'part_ids':['part']}
          for name in ['缓存（Cache）','副本（Copy）']]
    before=deepcopy(rows);calls=[]
    async def request(stage,payload):
        calls.append(deepcopy(payload))
        selected=payload['terms_to_repair'][:1] if len(calls)==1 else payload['terms_to_repair']
        assert all(t['write_fields']==['intuitive_explanation'] for t in selected)
        return {'terms':[{'name':t['name'],'professional_definition':'不应采用的替代定义',
                          'intuitive_explanation':INTUITION} for t in selected]}
    result=asyncio.run(repair_context_references(rows,request))
    assert len(calls)==2 and len(calls[1]['terms_to_repair'])==1
    assert all(r['professional_definition']==DEFINITION and r['intuitive_explanation']==INTUITION for r in result)
    assert rows==before


def test_answer_part_coverage_preserves_repeated_parts_and_term_links():
    question={'id':'q','text':'缓存怎样工作','answer':'缓存正文','version':1,
              'answer_parts':[{'id':pid,'markdown':'缓存保存副本'} for pid in ['a','b']],'term_ids':[]}
    term={'id':'t','name':'缓存（Cache）','definition':DEFINITION,'question_ids':[]}
    quizzes=[{'id':pid,'prompt':'对应 '+pid,'placement':{'kind':'answer','question_id':'q','after_part_id':pid}} for pid in ['a','b']]
    state={'rounds':[{'id':'r','questions':[question]}],'terms':[term],'quizzes':quizzes}
    before=deepcopy(state)
    report=answer_coverage({'passages':[]},state)
    assert report['total']==report['covered']==2
    assert report['terms']['linked']==0
    associate_terms(state,question_ids={'q'})
    assert question['term_ids']==['t'] and term['question_ids']==['q']
    assert answer_coverage({'passages':[]},state)['terms']['percentage']=='100.00'
    assert state['quizzes']==before['quizzes']
    assert question['answer_parts']==before['rounds'][0]['questions'][0]['answer_parts']


def test_pre_dispatch_error_is_recorded_without_calling_upstream(tmp_path):
    gate=Gate({'model':'gpt-6.1-sol','effort':'high'},'expected','system',tmp_path,False,False).start()
    try:
        with patch('reader_writer_transport.requests.post') as upstream:
            client=http.client.HTTPConnection('127.0.0.1',gate.port,timeout=3)
            body={'model':'gpt-6.1-sol','reasoning':{'effort':'high'},'input':[]}
            client.request('POST','/responses',json.dumps(body).encode(),{'Content-Type':'application/json'})
            response=client.getresponse();assert response.status==502;response.read();client.close()
            upstream.assert_not_called()
        assert len(gate.receipts)==1
        assert gate.receipts[0]['error_type']=='ValueError'
        assert json.loads((tmp_path/'transport.json').read_text('utf8'))[0]['phase']=='transport_error'
    finally:gate.stop()


def test_windows_text_pipe_forwards_mixed_line_endings_as_one_current_task(tmp_path):
    from reader_execute import invoke_writer
    from Demo.test_writer_http import Response
    class Process:
        def __init__(self,args,**kwargs):
            self.args=args;self.stdout=[];self.sent='';self.stdin=self
        def write(self,text):
            self.sent+=text.replace('\n','\r\n')
        def close(self):pass
        def wait(self):
            from urllib.parse import urlsplit
            endpoint=json.loads(next(arg.split('=',1)[1] for arg in self.args if arg.startswith('openai_base_url=')))
            host=urlsplit(endpoint)
            body={'model':'gpt-6.1-sol','reasoning':{'effort':'high'},
                  'input':[{'role':'user','content':[{'type':'input_text','text':self.sent}]}]}
            client=http.client.HTTPConnection(host.hostname,host.port,timeout=3)
            client.request('POST','/responses',json.dumps(body).encode(),{'Content-Type':'application/json'})
            reply=client.getresponse();assert reply.status==200;reply.read();client.close()
            (tmp_path/'final.txt').write_text('完整回答','utf8')
            return 0
    with patch('reader_execute.subprocess.Popen',Process),patch('reader_writer_transport.requests.post',return_value=Response()) as upstream:
        answer,usage,_=invoke_writer(tmp_path,'当前任务\r\n第二行\n第三行',[],'synthetic',
                                     allow_image_generation=False,research=False)
    assert answer=='完整回答' and usage['usage_complete']
    assert upstream.call_count==1
    sent=upstream.call_args.kwargs['json']
    assert sent['input'][0]['content'][0]['text']=='当前任务\n第二行\n第三行'


def test_source_translation_starts_before_writer_and_persists_its_own_receipts(tmp_path):
    import threading
    from manualreader import connected
    entered=threading.Event();release=threading.Event()
    write(tmp_path/'planner/step01-start.json',{'manifest':{'files':[{'file_id':'f','content_sha256':'hash'}]}})
    (tmp_path/'media').mkdir()
    packet={'sources':[{'id':'s','file_id':'f','original':'An actual source paragraph.','kind':'paragraph'}],
            'file_signatures':{'f':'hash'}}
    scope={'material_plan':[{'file_id':'f','source_ids':['s']}]}
    async def translate(*args):
        entered.set()
        assert await asyncio.to_thread(release.wait,3)
        return {'groups':[],'links':[{'source_id':'s'}],'counts':{'complete':1}}
    with patch.object(connected,'Provider'),patch.object(connected,'translate',side_effect=translate),patch.object(connected,'persist') as persist:
        future=connected.start_translation(tmp_path,packet,scope,{'existing_terms':[]})
        if not entered.wait(3):
            future.result(timeout=3)
            raise AssertionError('提前翻译没有进入任务')
        assert not future.done() and not (tmp_path/'chapter.md').exists()
        release.set()
        assert future.result(timeout=3)['counts']['complete']==1
        assert persist.call_count==1
    assert json.loads((tmp_path/'jobs/chapter/translation-initial.json').read_text('utf8'))['links'][0]['source_id']=='s'


def test_connected_finish_consumes_early_translation_without_second_request(tmp_path):
    from concurrent.futures import Future
    from manualreader import connected
    future=Future();future.set_result({'groups':[],'links':[{'source_id':'s'}],'counts':{'complete':1}})
    async def finish(*args,**kwargs):
        assert kwargs['refresh_translation'] is True
        assert (await kwargs['translation_task'])['counts']['complete']==1
        return {'failures':{},'ready':1}
    card={'project':{'id':'project'}}
    with patch.object(connected,'prepare',return_value=('chapter',{})),patch.object(connected,'upstream'),\
            patch.object(connected,'Provider'),patch.object(connected,'persist'),\
            patch.object(connected,'finish_batch',side_effect=finish),patch.object(connected,'translate') as translate,\
            patch('manualreader.stages.finish.review.next_calls',return_value={}),\
            patch('manualreader.stages.finish.review.next_run',return_value={}):
        connected.run(tmp_path,{},card,{'mode':'NORMAL'},early_translation=future)
    translate.assert_not_called()
