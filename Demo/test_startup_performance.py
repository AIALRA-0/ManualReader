"""只使用临时数据，检查缓存、文件读取和失效规则。"""
from pathlib import Path
import json,subprocess,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_performance import ReadCache,asset_digest,version_html

class StartupPerformance(unittest.TestCase):
    def test_cache_keeps_projects_and_versions_separate(self):
        cache=ReadCache(2);calls=[]
        def build(value):calls.append(value);return {'value':value}
        self.assertEqual(cache.get('a',1,lambda:build(1)),{'value':1})
        self.assertEqual(cache.get('a',1,lambda:build(99)),{'value':1})
        self.assertEqual(cache.get('a',2,lambda:build(2)),{'value':2})
        self.assertEqual(cache.get('b',2,lambda:build(3)),{'value':3})
        cache.get('c',1,lambda:build(4))
        self.assertEqual(len(cache.rows),2)
        self.assertEqual(calls,[1,2,3,4])

    def test_parallel_readers_share_one_projection(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading,time
        cache=ReadCache(2);calls=[];barrier=threading.Barrier(12)
        def read():
            barrier.wait()
            def build():calls.append(1);time.sleep(.03);return 'same'
            return cache.get('project',1,build)
        with ThreadPoolExecutor(max_workers=12)as pool:values=list(pool.map(lambda _:read(),range(12)))
        self.assertEqual(values,['same']*12);self.assertEqual(calls,[1]);self.assertFalse(cache.pending)

    def test_failed_projection_is_not_cached(self):
        cache=ReadCache(2)
        with self.assertRaises(ValueError):cache.get('a',1,lambda:(_ for _ in()).throw(ValueError()))
        self.assertEqual(cache.get('a',1,lambda:'ready'),'ready')

    def test_page_versions_follow_actual_bytes(self):
        with tempfile.TemporaryDirectory()as tmp:
            folder=Path(tmp);path=folder/'app.js';path.write_text('first')
            html='<script src="/app.js?v=old"></script><a href="/api/progress">API</a>'
            first=version_html(html,folder)
            self.assertIn('/app.js?v='+asset_digest(path),first)
            self.assertIn('href="/api/progress"',first)
            path.write_text('second and longer')
            self.assertNotEqual(first,version_html(html,folder))

    def test_http_assets_use_snapshot_and_browser_cache(self):
        code=r'''
import os,json,sys,threading,urllib.request,urllib.error,copy
from pathlib import Path
from unittest.mock import patch
root=Path(sys.argv[1]);folder=Path(sys.argv[2]);sys.path.insert(0,str(root))
m={'id':'test','title':'本地测试','passages':[{'id':'p','en':'Original','zh':'译文','sentences':[]}],'outline':[],'pages':{}}
(folder/'material.json').write_text(json.dumps(m));(folder/'pin.txt').write_text('123456')
os.environ.update(MANUAL_READER_MATERIAL=str(folder/'material.json'),MANUAL_READER_DB=str(folder/'state.sqlite3'),MANUAL_READER_PIN_FILE=str(folder/'pin.txt'),MANUAL_READER_LIBRARY=str(folder/'library'),MANUAL_READER_SETTINGS=str(folder/'settings.json'))
import reader_server as app
w=app.workspace();asset=w.files/'file-a'/'assets'/'page-0001.png';asset.parent.mkdir(parents=True);asset.write_bytes(b'image-test');original=asset.parent.parent/'original.html';original.write_text('<script src="/app.js?v=keep-original"></script>')
w.write({'revision':0,'files':[{'id':'file-a','stored_name':'original.html'}],'explanations':[],'presentation':{}})
w.read_snapshot()
server=app.ReaderHTTPServer(('127.0.0.1',0),app.ProductionHandler);threading.Thread(target=server.serve_forever,daemon=True).start()
base='http://127.0.0.1:'+str(server.server_port)
def get(path,extra=None):
 with urllib.request.urlopen(urllib.request.Request(base+path,headers={'X-Reader-Pin':'123456',**(extra or {})}))as r:return dict(r.headers),r.read()
with patch.object(app.Workspace,'read',side_effect=AssertionError('图片读取不应重新解析整份材料')):
 h,b=get('/content-files/file-a/asset/page-0001.png');assert b==b'image-test';assert 'max-age=300'in h['Cache-Control']
 assert 'dispatch;dur='in h['Server-Timing']
 h,b=get('/content-files/file-a/original');assert b==original.read_bytes()
# 列名称不应触发正文投影；旧数据只要求有名称和编号。
library=folder/'library';library.mkdir();(library/'library-index.json').write_text(json.dumps({'folders':[],'materials':[{'id':'test','name':'实际名称'}]}))
with patch.object(app,'material',side_effect=AssertionError('材料树不应整理正文')):
 h,b=get('/api/library/tree?view=names');assert json.loads(b)['materials'][0]['name']=='实际名称'
w.write({'revision':1,'files':[],'explanations':[],'presentation':{}})
before=app.core.read_state()
h,b=get('/app.js')
assert h['Cache-Control']=='no-store'
from reader_performance import asset_digest
h,b=get('/app.js?v='+asset_digest(app.STATIC/'app.js'));assert 'immutable'in h['Cache-Control']
assert app.core.read_state()==before
h,b=get('/api/progress');assert json.loads(b)['notes']==before['notes']
progress_tag=h['ETag'];assert 'private'in h['Cache-Control']
try:get('/api/progress',{'X-Reader-Pin':'wrong','If-None-Match':progress_tag});raise AssertionError('版本号不能代替授权')
except urllib.error.HTTPError as e:assert e.code in (401,403)

try:get('/api/progress',{'If-None-Match':progress_tag});raise AssertionError('未变化的进度应复用浏览器原记录')
except urllib.error.HTTPError as e:assert e.code==304

with patch.object(app,'coverage_payload',wraps=app.coverage_payload)as coverage:
 h,b=get('/api/coverage?summary=true');first=json.loads(b)
 h,b=get('/api/coverage?summary=true');assert json.loads(b)==first and coverage.call_count==1
 h,b=get('/api/coverage');full=json.loads(b);assert full['overall']==first['overall'] and full['revision']==first['revision'] and coverage.call_count==2
 app.core.mutate(lambda state:state['read_passages'].update({'p':True}))
 h,b=get('/api/coverage?summary=true');assert json.loads(b)['input_revisions']['reader_state']==app.core.read_state()['revision'] and coverage.call_count==3

from reader_practice_text import display_lesson
app.core.JSON_RESPONSES.rows.clear()
with patch('reader_practice_text.display_lesson',wraps=display_lesson)as prepare:
 h,b=get('/api/material');tag=h['ETag'];assert 'no-cache'in h['Cache-Control'];first_body=b
 h,b=get('/api/material');assert b==first_body and prepare.call_count==1
# 字节仍有效时，切换过其他项目也无需重新整理正文。
h,_=get('/api/progress');get('/api/coverage?summary=true')
with patch.object(app,'material',side_effect=AssertionError('命中字节或 ETag 不应重新投影材料')):
 h,b=get('/api/material');assert b==first_body
 get('/api/coverage?summary=true');get('/api/import-jobs')
 try:get('/api/material',{'If-None-Match':tag});raise AssertionError('原文未变化应返回 304')
 except urllib.error.HTTPError as e:assert e.code==304
 h,b=get('/api/progress')
 progress_tag=h['ETag']
 try:get('/api/progress',{'If-None-Match':progress_tag});raise AssertionError('进度未变化应返回 304')
 except urllib.error.HTTPError as e:assert e.code==304

with patch.object(app,'Runs')as audit:
 h,b=get('/api/material',{'X-Reader-Run':'run-'+'a'*20})
 assert audit.return_value.request.call_args.args[4]==len(json.dumps(json.loads(b),ensure_ascii=False).encode())

import time
h,b=get('/api/session',{'X-Reader-Proxy-Started':str(time.time()-.05)})
assert any(item.strip().startswith('proxy_wait;dur=')and float(item.split('=')[1])>=50 for item in h['Server-Timing'].split(','))

try:get('/api/material',{'If-None-Match':tag});raise AssertionError('应该复用浏览器已保存的原文')
except urllib.error.HTTPError as e:assert e.code==304
app.core.mutate(lambda state:state['notes'].update({'p':'笔记更新'}))
h,b=get('/api/progress',{'If-None-Match':progress_tag});assert h['ETag']!=progress_tag and json.loads(b)['notes']['p']=='笔记更新'
try:get('/api/material',{'If-None-Match':tag});raise AssertionError('笔记更新不应重传原文')
except urllib.error.HTTPError as e:assert e.code==304
app.core.mutate(lambda state:state['quizzes'].append({'id':'quiz','prompt':'新题干','options':['甲','乙']}))
h,b=get('/api/material',{'If-None-Match':tag});assert h['ETag']!=tag
m['passages'][0]['zh']='更新后的译文';(folder/'material.json').write_text(json.dumps(m))
h2,b=get('/api/material',{'If-None-Match':h['ETag']});assert h2['ETag']!=h['ETag'] and json.loads(b)['passages'][0]['zh']=='更新后的译文'
# 整理同份材料只做一次，而且不占住状态写入所用的全局锁。
from concurrent.futures import ThreadPoolExecutor
app.PROJECT_CACHE.rows.clear();entered=threading.Event();release=threading.Event();calls=[]
def prepare(original):
 calls.append(1);entered.set();assert release.wait(5);return {'id':'test'}
with patch.object(app.Workspace,'project',side_effect=prepare):
 with ThreadPoolExecutor(max_workers=4)as pool:
  pending=[pool.submit(app.material)for _ in range(4)]
  assert entered.wait(5)
  acquired=app.core.LOCK.acquire(timeout=.1)
  if acquired:app.core.LOCK.release()
  release.set();assert acquired,'正文整理不应锁住其他项目和状态读取'
  assert all(f.result()=={'id':'test'}for f in pending)and len(calls)==1
server.shutdown()
'''
        with tempfile.TemporaryDirectory()as tmp:
            result=subprocess.run([sys.executable,'-X','utf8','-c',code,str(Path(__file__).resolve().parents[1]),tmp],capture_output=True,text=True,encoding='utf8',timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
