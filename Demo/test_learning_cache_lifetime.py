"""目录缓存不能把已经离开材料缓存的整份正文继续留在内存中。"""
import gc,json,sys,unittest,weakref
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import reader_learning as learning
from reader_workspace import MaterialSnapshot

class LearningCacheLifetime(unittest.TestCase):
    def tearDown(self):learning._PLAN_CACHE.clear()

    def test_cache_does_not_retain_material(self):
        material=MaterialSnapshot(id='test',passages=[],alignment={'version':1})
        reference=weakref.ref(material)
        with patch.object(learning,'_static_learning',return_value=([],{}))as build:
            first=learning.learning_payload(material,{'revision':1},{})
            second=learning.learning_payload(material,{'revision':1},{})
            self.assertEqual(first,second);self.assertEqual(build.call_count,1)
            self.assertEqual(json.loads(json.dumps(material)),dict(material))
        build.reset_mock();del material;gc.collect()
        self.assertIsNone(reference())

    def test_plain_dictionary_remains_supported_without_retention(self):
        material={'id':'test','passages':[],'alignment':{'version':1}}
        with patch.object(learning,'_static_learning',return_value=([],{})):
            learning.learning_payload(material,{'revision':1},{})
        self.assertFalse(learning._PLAN_CACHE)

    def test_project_snapshot_reuses_plan_before_alignment_is_attached(self):
        material=MaterialSnapshot(id='test',passages=[])
        with patch.object(learning,'_static_learning',return_value=([],{}))as build:
            learning.learning_payload(material,{'revision':1},{})
            material['alignment']={'version':1}
            learning.learning_payload(material,{'revision':1},{})
        self.assertEqual(build.call_count,1)

    def test_http_encoding_keeps_reading_values(self):
        from reader_performance import json_bytes
        value={'title':'中文与公式 \\frac{1}{2}','notes':['😀',None,True],1:2,'coordinates':[.2,1e-8]}
        self.assertEqual(json.loads(json_bytes(value)),json.loads(json.dumps(value)))
        with patch('reader_performance._fast_json',None):
            self.assertEqual(json.loads(json_bytes(value)),json.loads(json.dumps(value)))

    def test_parallel_readers_build_one_static_plan(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading,time
        material=MaterialSnapshot(id='test',passages=[],alignment={'version':1});barrier=threading.Barrier(8);calls=[]
        def build(*args):calls.append(1);time.sleep(.03);return [],{}
        def read(_):barrier.wait();return learning.learning_payload(material,{'revision':1},{})
        with patch.object(learning,'_static_learning',side_effect=build):
            with ThreadPoolExecutor(8)as pool:results=list(pool.map(read,range(8)))
        self.assertEqual(calls,[1]);self.assertTrue(all(r==results[0]for r in results));self.assertFalse(learning._PLAN_PENDING)

    def test_progress_updates_do_not_consume_cached_plan(self):
        material=MaterialSnapshot(id='test',passages=[],alignment={'version':1})
        node={'id':'n','status':'ready','reading_passage_ids':['p'],'_subtree_reading_ids':['p'],'own_reading_progress':{},'reading_progress':{}}
        with patch.object(learning,'_static_learning',return_value=([node],{}))as build:
            first=learning.learning_payload(material,{'revision':1},{'read_passages':{'p':True}})
            second=learning.learning_payload(material,{'revision':1},{})
        self.assertEqual(build.call_count,1);self.assertEqual(first['nodes'][0]['reading_progress']['percent'],100)
        self.assertEqual(second['nodes'][0]['reading_progress']['percent'],0);self.assertIn('_subtree_reading_ids',node)

if __name__=='__main__':unittest.main()
