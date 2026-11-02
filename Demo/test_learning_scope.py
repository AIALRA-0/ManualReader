import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_learning_scope import excluded_sources,update_exclusions,reading_material,chapterwise_outline_guard
from reader_responsibility import complete_outline
from reader_learning import learning_payload,validate_outline
from reader_features import reading_progress
from reader_coverage import coverage_payload

class LearningScope(unittest.TestCase):
    def setUp(self):
        self.m={'id':'demo','kind':'project','title':'测试网页','pages':{},'outline':[],
                'passages':[{'id':'body','en':'正文内容','zh':'正文内容','file_id':'file','section_id':'body','kind':'paragraph','source_ids':['one'],'sentences':[]},
                            {'id':'footer','en':'登录并订阅','zh':'登录并订阅','file_id':'file','section_id':'footer','kind':'paragraph','source_ids':['two'],'sentences':[]},
                            {'id':'logo','en':'','zh':'','src':'logo.png','file_id':'file','section_id':'footer','kind':'image','source_ids':['three'],'sentences':[]}],
                'reading_files':[{'id':'file','name':'test.html','passage_ids':['body','footer','logo'],'outline':[{'id':'body','title':'正文','passage_id':'body'},{'id':'footer','title':'网页入口','passage_id':'footer'}]}]}
        self.nodes=[{'id':'main','parent_id':None,'title':'正文','number':'0','source_links':[{'passage_id':'body','file_id':'file'}],'explanation_ids':[]}]
        self.w={'revision':1,'learning_outline':self.nodes,'files':[],'explanations':[]}
    def exclude(self):update_exclusions(self.m,self.w,{'passage_ids':['footer','logo'],'reason':'网页登录与标志'})
    def test_exclusion_stops_automatic_recreation_and_keeps_originals(self):
        before=copy.deepcopy(self.m);original,_=complete_outline(self.m,self.nodes);self.assertEqual(len(original),2)
        self.exclude();active,_=excluded_sources(self.m,self.w)
        nodes,coverage=complete_outline(self.m,original,active)
        self.assertEqual([n['id']for n in nodes],['main']);self.assertEqual(coverage['excluded_count'],2);self.assertEqual(coverage['total'],1);self.assertEqual(coverage['percent'],100)
        self.assertEqual(before,self.m)
    def test_refresh_and_same_source_reimport_keep_exclusion(self):
        self.exclude();state={'rounds':[],'read_passages':{}}
        for m in (self.m,copy.deepcopy(self.m)):
            plan=learning_payload(m,self.w,state);self.assertEqual([n['id']for n in plan['nodes']],['main'])
        checked=copy.deepcopy(self.w);checked['revision']+=1
        self.assertEqual([n['id']for n in learning_payload(self.m,checked,state)['nodes']],['main'])
    def test_changed_source_is_not_silently_excluded(self):
        self.exclude();changed=copy.deepcopy(self.m);changed['passages'][1]['en']='这里已经变为正文概念'
        active,outdated=excluded_sources(changed,self.w)
        self.assertNotIn('footer',active);self.assertIn('footer',outdated)
    def test_restore_reuses_original_automatic_chapter_identity(self):
        before,_=complete_outline(self.m,self.nodes);identity=before[1]['id'];self.exclude()
        update_exclusions(self.m,self.w,{'passage_ids':['footer','logo'],'action':'restore'})
        nodes,_=complete_outline(self.m,self.nodes,excluded_sources(self.m,self.w)[0]);self.assertEqual(nodes[1]['id'],identity)
    def test_excluded_is_not_counted_as_read(self):
        self.exclude();state={'read_passages':{'footer':True,'logo':True},'notes':{'q':'保留笔记'}};saved=copy.deepcopy(state)
        progress=reading_progress(reading_material(self.m,self.w),state)['overall']
        self.assertEqual(progress['done'],0);self.assertEqual(progress['total'],1);self.assertEqual(state,saved)
    def test_coverage_separates_learning_and_original_material(self):
        self.exclude();r=coverage_payload(self.m,self.w,{'rounds':[],'read_passages':{'footer':True,'logo':True}})
        self.assertEqual(r['learning_exclusions']['count'],2);self.assertEqual(r['overall']['manual_reading']['done'],0)
        self.assertIsNotNone(r['original_overall'])
    def test_one_major_chapter_accepts_multiple_subsections(self):
        nodes=[{'id':'root','parent_id':None},{'id':'child-one','parent_id':'root'},{'id':'child-two','parent_id':'root'}]
        chapterwise_outline_guard({},nodes,set(),False)
        with self.assertRaises(ValueError):chapterwise_outline_guard({},[{'id':'a','parent_id':None},{'id':'b','parent_id':None}],set(),False)
    def test_next_major_chapter_requires_manual_completion(self):
        next_nodes=[{'id':'main','parent_id':None},{'id':'next','parent_id':None}]
        with self.assertRaises(ValueError):chapterwise_outline_guard(self.w,next_nodes,{'main'},False)
        chapterwise_outline_guard(self.w,next_nodes,{'main'},True)
        chapterwise_outline_guard(self.w,next_nodes,{'main','next'},False)
    def test_unplanned_material_is_reported_without_exposing_every_chapter(self):
        nodes,plan=complete_outline(self.m,self.nodes,defer_unassigned=True)
        self.assertEqual([n['id']for n in nodes],['main']);self.assertEqual(set(plan['deferred']),{'footer','logo'});self.assertLess(plan['percent'],100)

if __name__=='__main__':unittest.main()
