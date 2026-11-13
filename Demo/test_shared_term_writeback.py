"""Run a grouped answer writeback against an isolated Reader instance."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class SharedTermWritebackTests(unittest.TestCase):
    def test_two_rounds_share_one_term_revision(self):
        app = Path(__file__).resolve().parents[1]
        code = r'''
from pathlib import Path
import json, os, sys, threading
from urllib.request import Request, urlopen
sys.path.insert(0, sys.argv[1])
folder = Path(sys.argv[2])
from Demo.test_generation_assets import answer_with_terms
from Demo.test_term_quality import DEFINITION, INTUITION
from reader_generation import VERSION
from reader_prompts import WRITING_VERSION
from reader_runs import Runs

material = {'id':'synthetic','title':'隔离材料','passages':[
    {'id':'p1','en':'Read the oldest entry.','zh':'读出最早存入的值。','section_id':'one','sentences':[]}
], 'outline':[{'id':'one','title':'队首','passage_id':'p1'}]}
(folder/'material.json').write_text(json.dumps(material), 'utf8')
(folder/'pin.txt').write_text('123456', 'utf8')
os.environ.update(MANUAL_READER_MATERIAL=str(folder/'material.json'), MANUAL_READER_DB=str(folder/'state.sqlite3'),
                  MANUAL_READER_LIBRARY=str(folder/'library'), MANUAL_READER_SETTINGS=str(folder/'settings.json'),
                  MANUAL_READER_PIN_FILE=str(folder/'pin.txt'))
os.environ.pop('MANUAL_READER_PUBLIC_ORIGIN',None)
import reader_server as app
def question(identity):
    return {'id':identity,'text':'先读哪个值','version':1,'passage_id':'p1','term_ids':[]}
questions=[question('q1'),question('q2')]
term={'id':'t1','name':'缓存（Cache）','version':1,'question_ids':[],
      'definition':DEFINITION,'professional_definition':DEFINITION,'intuitive_explanation':INTUITION}
notes={'q1':'保留笔记'}
read={'p1':True}
def seed(state):
    state.update(terms=[term],notes=notes,read_passages=read,rounds=[
        {'id':'r'+q['id'],'passage_id':'p1','questions':[q],'status':'pending'} for q in questions])
app.core.mutate(seed)
run=Runs(folder).create('synthetic',{'questions':[{**q,'question_version':1} for q in questions],
                                    'sources':[],'groups':[['q1','q2']],
                                    'writer':{},'writing_version':WRITING_VERSION,'registered_terms':[term],
                                    'framework_version':VERSION})
proposal={'term_id':'t1','name':term['name'],'action':'revise','expected_version':1,
          'professional_definition':DEFINITION,'intuitive_explanation':INTUITION+' 下次需要时仍从同一个位置取出。',
          'question_ids':['q1','q2']}
payload=answer_with_terms(proposal)
payload['framework_version']=VERSION
payload['answers'].append({'question_id':'q2','question_version':1,'parts':[
    {'id':'p2','unit_ids':['u1'],'question_span':'先读哪个值','markdown':'队首保存了最早进入队列的值。'}]})
httpd=app.ReaderHTTPServer(('127.0.0.1',0),app.Handler)
threading.Thread(target=httpd.serve_forever,daemon=True).start()
base='http://127.0.0.1:'+str(httpd.server_port)
try:
    req=Request(base+'/api/runs/'+run['id']+'/groups/1/result', data=json.dumps(payload).encode(),
                headers={'Content-Type':'application/json','Origin':base,'X-Reader-Pin':'123456','X-Reader-Material':'synthetic'})
    with urlopen(req,timeout=10) as response:
        receipt=json.load(response)
    assert receipt['saved']==['q1'] and receipt['missing_question_ids']==['q2'],receipt
    first=app.core.read_state()
    assert len(first['quizzes'])==1 and first['quizzes'][0]['question_ids']==['q1']
    first_id=first['quizzes'][0]['id']
    payload['quizzes'].append({**payload['quizzes'][0],
                               'prompt':'依次放入 3 和 8，队首指向谁？',
                               'question_ids':['q2'],'placement':{'kind':'answer','question_id':'q2','after_part_id':'p2'}})
    req=Request(req.full_url,data=json.dumps(payload).encode(),
                headers={'Content-Type':'application/json','Origin':base,'X-Reader-Pin':'123456','X-Reader-Material':'synthetic'})
    with urlopen(req,timeout=10) as response:
        receipt=json.load(response)
    assert receipt['saved']==['q1','q2'] and not receipt['issues'],receipt
    assert receipt['run']['groups'][0]['first_saved_at'] < receipt['run']['groups'][0]['saved_at']
    with urlopen(req,timeout=10) as response:
        assert json.load(response)['saved']==['q1','q2']
    state=app.core.read_state()
    assert len(state['quizzes'])==2 and state['quizzes'][0]['id']==first_id
    saved=state['terms'][0]
    assert saved['id']=='t1' and saved['version']==2 and len(saved['definition_history'])==1
    assert saved['question_ids']==['q1','q2'] and all(r['questions'][0]['term_ids']==['t1'] for r in state['rounds'])
    assert state['notes']==notes and state['read_passages']==read
finally:
    httpd.shutdown()
    httpd.server_close()
'''
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code, str(app), folder],
                                    capture_output=True, text=True, encoding='utf8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
