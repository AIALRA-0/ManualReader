from pathlib import Path
import copy,sys,tempfile,unittest
from unittest.mock import patch,Mock
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from reader_coverage import coverage_payload
from reader_learning_scope import update_exclusions
from reader_features import settings,save_settings
from reader_performance import TimedRLock,begin_request,server_timing
class Changes(unittest.TestCase):
    def test_current_statistics_reuses_reading_percentage_even_at_half_percent(self):
        import reader_server as server
        m={'id':'demo','title':'统计标题','outline':[],'passages':[{'id':'p'+str(i),'file_id':'primary','en':'内容','zh':'内容','kind':'paragraph','sentences':[]}for i in range(200)]}
        state={'rounds':[],'terms':[],'quizzes':[],'notes':{},'read_passages':{'p0':True}};store=Mock();store.read_snapshot.return_value={}
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'material.json';path.write_text('{}')
            with patch.multiple(server,material=lambda identity=None:m,mid=lambda:'demo',stored_state=lambda identity:state,workspace=lambda identity=None:store,mpath=lambda identity=None:path,samples=lambda document,state:[],sample_stats=lambda records,reading:{'reading':reading},coverage_payload=lambda *args,**kwargs:{}):
                result=server.statistics_payload('current')
        from reader_features import reading_progress
        self.assertEqual(result['reading']['overall'],reading_progress(m,state)['overall']);self.assertEqual(result['reading']['overall']['percent'],0)
    def test_palette_defaults_and_saved_override_keep_existing_colors(self):
        with tempfile.TemporaryDirectory()as folder:
            p=Path(folder)/'settings.json';old=settings(p);self.assertIn('progress_reading',old['appearance']['dark'])
            colors=copy.deepcopy(old['appearance']);colors['dark']['progress_reading']='#abcdef';new=save_settings(p,{'appearance':colors})
            self.assertEqual(new['appearance']['dark']['progress_reading'],'#abcdef');self.assertEqual(new['appearance']['dark']['source_fill'],old['appearance']['dark']['source_fill'])
    def test_missing_ranges_use_same_translation_and_explanation_inputs(self):
        p={'id':'p','en':'A first sentence. A second sentence.','zh':'','sentences':[],'file_id':'f','section_id':'s','source_ids':['atom'],'kind':'paragraph'}
        m={'id':'demo','title':'完整文章名称','passages':[p],'pages':{},'outline':[],'reading_files':[{'id':'f','name':'article.md','passage_ids':['p'],'outline':[{'id':'s','passage_id':'p'}]}]};w={'files':[],'explanations':[]};state={'rounds':[],'read_passages':{}}
        saved=copy.deepcopy((m,w,state));r=coverage_payload(m,w,state)
        self.assertEqual(r['material_title'],'完整文章名称');self.assertEqual(r['overall']['translation']['done'],0)
        for key in('translation','explanation'):
            self.assertEqual(r['missing'][key][0]['passage_id'],'p');self.assertEqual(sum(x['missing_characters']for x in r['missing'][key]),r['overall'][key]['total']-r['overall'][key]['done'])
        self.assertEqual((m,w,state),saved)
        compact=coverage_payload(m,w,state,include_missing=False);self.assertEqual(compact['overall'],r['overall']);self.assertEqual(compact['revision'],r['revision']);self.assertNotIn('missing',compact)
        update_exclusions(m,w,{'passage_ids':['p'],'reason':'测试排除'})
        r=coverage_payload(m,w,state);self.assertEqual(r['missing']['translation'],[]);self.assertEqual(r['files'][0]['translation']['total'],0);self.assertGreater(r['original_overall']['translation']['total'],0)
    def test_instrumentation_lock_reenters_without_changing_behavior(self):
        begin_request();lock=TimedRLock()
        with lock:
            with lock:pass
        self.assertIn('lock;dur=',server_timing(__import__('time').perf_counter()))
if __name__=='__main__':unittest.main()
