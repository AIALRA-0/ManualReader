"""检查连接及时释放，写入复用只在原始记录完全一致且尚未使用时生效。"""
from pathlib import Path
import sqlite3,tempfile,unittest,subprocess,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_database import DatabaseConnections,ReadSnapshot

class DatabaseLifecycle(unittest.TestCase):
    def test_request_closes_and_pool_anchor_stays_usable(self):
        with tempfile.TemporaryDirectory()as tmp:
            pool=DatabaseConnections(2);path=Path(tmp)/'state.sqlite3'
            try:
                with pool.connect(path)as conn:
                    conn.execute('CREATE TABLE example(value INTEGER)')
                    conn.execute('INSERT INTO example VALUES(3)')
                with self.assertRaises(sqlite3.ProgrammingError):conn.execute('SELECT 1')
                with pool.connect(path)as other:self.assertEqual(other.execute('SELECT value FROM example').fetchone(),(3,))
                self.assertEqual(len(pool.anchors),1)
                self.assertEqual(next(iter(pool.anchors.values()))[1].execute('SELECT value FROM example').fetchone(),(3,))
            finally:pool.close()

    def test_exception_rolls_back_and_closes_request(self):
        with tempfile.TemporaryDirectory()as tmp:
            pool=DatabaseConnections(2);path=Path(tmp)/'state.sqlite3'
            try:
                with pool.connect(path)as conn:conn.execute('CREATE TABLE example(value INTEGER)')
                with self.assertRaises(ValueError):
                    with pool.connect(path)as conn:
                        conn.execute('INSERT INTO example VALUES(7)');raise ValueError('取消这次事务')
                with self.assertRaises(sqlite3.ProgrammingError):conn.execute('SELECT 1')
                with pool.connect(path)as other:self.assertEqual(other.execute('SELECT count(*) FROM example').fetchone(),(0,))
            finally:pool.close()

    def test_snapshot_is_consumed_even_when_record_does_not_match(self):
        snapshot=ReadSnapshot({'value':1},'before')
        self.assertIsNone(snapshot.take('after'));self.assertIsNone(snapshot.take('before'))

    def test_real_transactions_reuse_match_preserve_newer_data_and_rollback(self):
        code=r'''
from pathlib import Path
import os,json,sys
root=Path(sys.argv[1]);tmp=Path(sys.argv[2]);sys.path.insert(0,str(root))
m={'id':'test','title':'合成项目','passages':[{'id':'p','en':'Source','zh':'原句','sentences':[]}],'outline':[]}
(tmp/'material.json').write_text(json.dumps(m));(tmp/'pin').write_text('123456')
os.environ.update(MANUAL_READER_MATERIAL=str(tmp/'material.json'),MANUAL_READER_DB=str(tmp/'state.sqlite3'),MANUAL_READER_PIN_FILE=str(tmp/'pin'))
import server as core
core.read_state()
snapshot=core.read_state(with_snapshot=True);saved=snapshot.state
def change(state):
 assert state is saved
 state['notes']['own']='保留';return state['revision']
previous=core.mutate(change,snapshot=snapshot)
assert snapshot.state is None and snapshot.serialized is None
assert core.read_state()['revision']==previous+1
old=core.read_state(with_snapshot=True);old_state=old.state
core.mutate(lambda state:state['notes'].update({'newer':'较新内容'}))
def after_other_writer(state):
 assert state is not old_state
 state['notes']['second']='第二次'
core.mutate(after_other_writer,snapshot=old)
assert core.read_state()['notes']=={'own':'保留','newer':'较新内容','second':'第二次'}
bad=core.read_state(with_snapshot=True);before=core.read_state()
def fails(state):state['notes']['bad']='不应保存';raise ValueError('取消')
try:core.mutate(fails,snapshot=bad);raise AssertionError('应该回滚')
except ValueError:pass
assert core.read_state()==before and bad.state is None
core.mutate(lambda state:state['notes'].update({'last':'最终内容'}),snapshot=bad)
after=core.read_state();assert 'bad'not in after['notes']
assert after['read_passages']==before['read_passages']and after['rounds']==before['rounds']and after['quizzes']==before['quizzes']
print('TRANSACTIONS_OK')
'''
        with tempfile.TemporaryDirectory()as tmp:
            r=subprocess.run([sys.executable,'-X','utf8','-c',code,str(Path(__file__).resolve().parents[1]),tmp],capture_output=True,text=True,encoding='utf8',timeout=30)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)

    def test_http_selection_rechecks_version_inside_transaction(self):
        code=r'''
from pathlib import Path
import os,json,sys,threading,urllib.request,urllib.error
from unittest.mock import patch
root=Path(sys.argv[1]);tmp=Path(sys.argv[2]);sys.path.insert(0,str(root))
m={'id':'test','title':'合成项目','kind':'project','pages':{},'passages':[{'id':'p','en':'Source','zh':'原句','sentences':[],'refs':[]}],'outline':[]}
(tmp/'material.json').write_text(json.dumps(m));(tmp/'pin').write_text('123456')
os.environ.update(MANUAL_READER_MATERIAL=str(tmp/'material.json'),MANUAL_READER_DB=str(tmp/'state.sqlite3'),MANUAL_READER_LIBRARY=str(tmp/'library'),MANUAL_READER_SETTINGS=str(tmp/'settings.json'),MANUAL_READER_PIN_FILE=str(tmp/'pin'))
import reader_server as app
app.workspace().write({'revision':0,'files':[],'explanations':[],'presentation':{},'learning_outline':[{'id':'a','title':'甲','source_links':[{'passage_id':'p'}]},{'id':'b','title':'乙','source_links':[{'passage_id':'p'}]}]})
app.core.mutate(lambda state:state.update(learning_node_id='a',learning_selection_revision=0,notes={'p':'保留'},read_passages={'p':True}))
original=app.learning_payload;changed=False
def plan(*args):
 global changed
 result=original(*args)
 if not changed:
  changed=True
  app.core.mutate(lambda state:state.update(learning_selection_revision=1,learning_node_id='a'))
 return result
server=app.ReaderHTTPServer(('127.0.0.1',0),app.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
base='http://127.0.0.1:'+str(server.server_port)
request=urllib.request.Request(base+'/api/learning/select',headers={'X-Reader-Pin':'123456','Content-Type':'application/json','Origin':base},data=json.dumps({'node_id':'b','expected_selection_revision':0}).encode())
with patch.object(app,'learning_payload',plan):
 try:urllib.request.urlopen(request);raise AssertionError('旧版本应该被拒绝')
 except urllib.error.HTTPError as e:assert e.code==409
state=app.core.read_state()
assert state['learning_node_id']=='a'and state['learning_selection_revision']==1
assert state['notes']=={'p':'保留'}and state['read_passages']=={'p':True}
server.shutdown()
'''
        with tempfile.TemporaryDirectory()as tmp:
            result=subprocess.run([sys.executable,'-X','utf8','-c',code,str(Path(__file__).resolve().parents[1]),tmp],capture_output=True,text=True,encoding='utf8',timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
