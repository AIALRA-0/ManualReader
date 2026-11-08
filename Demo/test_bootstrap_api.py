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
material={'id':'synthetic','title':'合成项目','passages':[{'id':'p1','en':'Source','zh':'原句','sentences':[]}],'outline':[]}
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
assert 'writing'not in bootstrap and 'notes'not in bootstrap and 'reader_research_transport.py'in json.dumps(bootstrap)
status,full=request('/api/guide');assert status==200 and len(json.dumps(bootstrap))<len(json.dumps(full))
assert app.core.read_state()==before
status,_=request('/api/terms',{'repair_only':True,'updates':[{'term_id':'t1','expected_version':1,'professional_definition':DEFINITION,'intuitive_explanation':INTUITION}]});assert status==400
assert app.core.read_state()==before
status,_=request('/api/terms',{'repair_only':True,'updates':[{'term_id':'t1','expected_version':1,'intuitive_explanation':INTUITION}]});assert status==200
after=app.core.read_state()
assert after['notes']==before['notes']and after['read_passages']==before['read_passages']
assert after['terms'][0]['professional_definition']==DEFINITION
status,remaining=request('/api/terms?needs_revision=1');assert status==200 and not remaining['terms']
server.shutdown();print('API_OK')
'''
        with tempfile.TemporaryDirectory()as folder:
            result=subprocess.run([sys.executable,'-X','utf8','-c',code,str(ROOT),folder],capture_output=True,text=True,encoding='utf8',timeout=60)
        self.assertEqual(result.returncode,0,result.stderr+'\n'+result.stdout)
        self.assertIn('API_OK',result.stdout)
if __name__=='__main__':unittest.main()
