"""HTTP 回归只使用合成项目，检查精简连接与术语读写不触及笔记或进度。"""
from pathlib import Path
import json,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class BootstrapApiTests(unittest.TestCase):
    def test_scoped_terms_and_bootstrap(self):
        code=r'''
from pathlib import Path
import os,sys,json,threading,urllib.request,urllib.error,copy
root=Path(sys.argv[1]);folder=Path(sys.argv[2]);sys.path.insert(0,str(root))
from Demo.test_term_quality import DEFINITION,INTUITION
material={'id':'synthetic','title':'合成项目','passages':[{'id':'p1','en':'Source','zh':'原句','section_id':'one','sentences':[]},{'id':'p2','en':'Next','zh':'下一段','section_id':'two','sentences':[]}],'outline':[{'id':'one','title':'第一段','passage_id':'p1'},{'id':'two','title':'第二段','passage_id':'p2'}]}
(folder/'material.json').write_text(json.dumps(material),'utf8');(folder/'pin.txt').write_text('123456','utf8')
os.environ.update(MANUAL_READER_MATERIAL=str(folder/'material.json'),MANUAL_READER_DB=str(folder/'state.sqlite3'),MANUAL_READER_PIN_FILE=str(folder/'pin.txt'),MANUAL_READER_LIBRARY=str(folder/'library'),MANUAL_READER_SETTINGS=str(folder/'settings.json'))
import reader_server as app
server=app.ReaderHTTPServer(('127.0.0.1',0),app.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
state=app.core.initial();state.update(terms=[{'id':'t1','name':'缓存（Cache）','version':1,'professional_definition':DEFINITION,'intuitive_explanation':'短解释','question_ids':['q1']}],notes={'q1':'保留'},read_passages={'p1':True})
def replace(current):current.clear();current.update(copy.deepcopy(state))
app.core.mutate(replace);before=app.core.read_state();base='http://127.0.0.1:'+str(server.server_port)
def request(path,data=None):
 req=urllib.request.Request(base+path,headers={'X-Reader-Pin':'123456','X-Reader-Material':'synthetic','Content-Type':'application/json','Origin':base},data=None if data is None else json.dumps(data).encode())
 try:
  with urllib.request.urlopen(req)as r:return r.status,json.load(r)
 except urllib.error.HTTPError as e:return e.code,json.load(e)
status,terms=request('/api/terms?needs_revision=1');assert status==200 and terms['material_id']=='synthetic'and terms['terms'][0]['id']=='t1'
status,bootstrap=request('/api/guide?view=bootstrap');assert status==200,bootstrap
assert bootstrap['view']=='bootstrap'and bootstrap['project']['id']=='synthetic'
assert bootstrap['project']['current_chapter'] is None
assert 'writing'not in bootstrap and 'notes'not in bootstrap and 'reader_research_transport.py'in json.dumps(bootstrap)
status,full=request('/api/guide');assert status==200 and len(json.dumps(bootstrap))<len(json.dumps(full))
assert full['writing']['analysis']['order']==['analysis','writing']
assert 'reader_analysis.py' in bootstrap['client_files']
from reader_task_prompts import OUTLINE_PLANNING
assert OUTLINE_PLANNING in bootstrap['initial_action'] and OUTLINE_PLANNING in full['learning_scope']['planning']
assert 'connection_template' not in bootstrap and 'reader_task_prompts.py' in bootstrap['client_files']
status,preview=request('/api/learning');assert status==200 and len(preview['nodes'])==2 and not preview['scope_active']
status,_=request('/api/work?mode=lesson');assert status==400
status,_=request('/api/learning/outline',{'nodes':preview['nodes']});assert status==400
first=preview['nodes'][0]
status,plan=request('/api/learning/outline',{'nodes':[first,{'id':'detail','parent_id':first['id'],'title':'检验一步'}]})
assert status==200 and [n['number']for n in plan['nodes']]==['0','0.1'] and plan['coverage_plan']['deferred']==['p2'],plan
assert app.core.read_state()==before
status,_=request('/api/terms',{'repair_only':True,'updates':[{'term_id':'t1','expected_version':1,'professional_definition':DEFINITION,'intuitive_explanation':INTUITION}]});assert status==400
assert app.core.read_state()==before
status,_=request('/api/terms',{'repair_only':True,'updates':[{'term_id':'t1','expected_version':1,'intuitive_explanation':INTUITION}]});assert status==200
after=app.core.read_state()
assert after['notes']==before['notes']and after['read_passages']==before['read_passages']
assert after['terms'][0]['professional_definition']==DEFINITION
status,remaining=request('/api/terms?needs_revision=1');assert status==200 and not remaining['terms']
status,task=request('/api/work?mode=lesson');assert status==200 and task['learning_node']['id']==first['id'] and task['required_source_ids']==['p1'],task
assert app.core.read_state()['read_passages']==before['read_passages']
server.shutdown();print('API_OK')
'''
        with tempfile.TemporaryDirectory()as folder:
            result=subprocess.run([sys.executable,'-X','utf8','-c',code,str(ROOT),folder],capture_output=True,text=True,encoding='utf8',timeout=60)
        self.assertEqual(result.returncode,0,result.stderr+'\n'+result.stdout)
        self.assertIn('API_OK',result.stdout)
if __name__=='__main__':unittest.main()
