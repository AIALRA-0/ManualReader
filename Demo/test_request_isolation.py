"""现有状态读取不等待长写入；不同项目的写入锁互不阻塞。"""
from pathlib import Path
import json,subprocess,sys,tempfile,threading,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_performance import ScopedRLock

class RequestIsolation(unittest.TestCase):
    def test_nested_phase_is_not_counted_twice(self):
        from unittest.mock import patch
        import reader_performance as perf
        perf.begin_request()
        with patch.object(perf.time,'perf_counter',side_effect=[0,1,2]):
            perf.timed_call('project',lambda:perf.timed_call('project',lambda:None))
        self.assertEqual(perf._request.phases['project'],2000)

    def test_other_project_does_not_wait_and_release_keeps_acquired_scope(self):
        context=threading.local();locks=ScopedRLock(lambda:getattr(context,'project','a'))
        locks.acquire();context.project='b';locks.acquire();locks.release()
        done=threading.Event()
        def other():
            context.project='b'
            with locks:done.set()
        thread=threading.Thread(target=other);thread.start()
        try:self.assertTrue(done.wait(1))
        finally:locks.release();thread.join(2)

    def test_same_project_still_serializes(self):
        locks=ScopedRLock(lambda:'same');done=threading.Event()
        def other():
            with locks:done.set()
        with locks:
            thread=threading.Thread(target=other);thread.start();self.assertFalse(done.wait(.05))
        self.assertTrue(done.wait(1));thread.join(1)

    def test_read_committed_snapshot_does_not_wait_for_writer(self):
        code=r'''
from pathlib import Path
import os,sys,json,threading
from concurrent.futures import ThreadPoolExecutor
root=Path(sys.argv[1]);folder=Path(sys.argv[2]);sys.path.insert(0,str(root))
(folder/'material.json').write_text(json.dumps({'id':'test','title':'test','passages':[{'id':'p','sentences':[]}],'outline':[]}))
(folder/'pin').write_text('123456');os.environ.update(MANUAL_READER_MATERIAL=str(folder/'material.json'),MANUAL_READER_DB=str(folder/'state.sqlite3'),MANUAL_READER_PIN_FILE=str(folder/'pin'))
import server
initial=server.read_state();entered=threading.Event();release=threading.Event()
def write():
 def change(state):
  state['notes']['p']='new';entered.set();assert release.wait(3)
 server.mutate(change)
thread=threading.Thread(target=write);thread.start();assert entered.wait(2)
with ThreadPoolExecutor(1)as pool:
 future=pool.submit(server.read_state)
 try:assert future.result(timeout=1)==initial
 finally:release.set()
thread.join(2);assert server.read_state()['notes']['p']=='new'
assert server.read_state()['read_passages']==initial['read_passages']
'''
        with tempfile.TemporaryDirectory()as folder:
            result=subprocess.run([sys.executable,'-c',code,str(Path(__file__).resolve().parents[1]),folder],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
