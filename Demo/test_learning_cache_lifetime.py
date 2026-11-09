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

    def test_http_encoding_keeps_reading_values(self):
        from reader_performance import json_bytes
        value={'title':'中文与公式 \\frac{1}{2}','notes':['😀',None,True],1:2,'coordinates':[.2,1e-8]}
        self.assertEqual(json.loads(json_bytes(value)),json.loads(json.dumps(value)))
        with patch('reader_performance._fast_json',None):
            self.assertEqual(json.loads(json_bytes(value)),json.loads(json.dumps(value)))

if __name__=='__main__':unittest.main()
