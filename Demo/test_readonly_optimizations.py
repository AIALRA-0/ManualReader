import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_workspace import Workspace
from reader_term_names import display_state

class ReadonlyOptimizations(unittest.TestCase):
    def test_asset_lookup_reuses_file_names_and_honors_updates(self):
        with tempfile.TemporaryDirectory() as folder:
            one=Workspace(Path(folder)/'one')
            two=Workspace(Path(folder)/'two')
            for store,name in ((one,'first.pdf'),(two,'second.pdf')):
                store.write({'revision':0,'files':[{'id':'file','stored_name':name,'document':{'passages':[1,2]}}]})
                store.read()
            with patch.object(one,'read_snapshot',side_effect=AssertionError('已经读取过工作区，不应再次解析正文')):
                self.assertEqual(one.registered_file_name('file'),'first.pdf')
                self.assertIsNone(one.registered_file_name('unknown'))
            self.assertEqual(two.registered_file_name('file'),'second.pdf')
            one.write({'revision':0,'files':[]})
            self.assertIsNone(one.registered_file_name('file'))
            self.assertEqual(two.registered_file_name('file'),'second.pdf')

    def test_snapshot_reuses_only_unchanged_file(self):
        with tempfile.TemporaryDirectory()as folder:
            store=Workspace(folder);value={'revision':0,'files':[],'explanations':[],'presentation':{}}
            store.write(value);first=store.read_snapshot()
            with patch.object(store,'read',side_effect=AssertionError('不应重复读取未变化的文件')):self.assertIs(first,store.read_snapshot())
            updated=store.read();updated['presentation']={'layout':'three'};store.write(updated)
            self.assertEqual(store.read_snapshot()['presentation'],{'layout':'three'})
            independent=store.read();independent['presentation']['layout']='single'
            self.assertEqual(store.read_snapshot()['presentation']['layout'],'three')
    def test_fast_display_matches_full_copy_and_keeps_input(self):
        state={'rounds':[{'id':'r','questions':[{'id':'q','term_ids':['b'],'answer':'已有完整回答','answer_parts':[{'markdown':'保留所有逻辑'}]}]}],
               'terms':[{'id':'a','name':'缓存（Cache）','definition':'保存副本','question_ids':['q']},{'id':'b','name':'缓存（cache）','definition':'同一概念','question_ids':[]}],
               'quizzes':[{'id':'z','options':['甲','乙']}],'notes':{'q':'用户笔记'},'read_passages':{'source':'time'}}
        saved=copy.deepcopy(state)
        full=display_state(state);fast=display_state(state,copy_all=False)
        self.assertEqual(full,fast);self.assertEqual(saved,state)
        fast[0]['quizzes'][0]['display_order']=[1,0]
        self.assertEqual(saved,state)
    def test_learning_does_not_modify_cached_workspace(self):
        from reader_learning import learning_payload
        material={'id':'demo','kind':'project','passages':[],'outline':[],'pages':{},'reading_files':[]}
        workspace={'revision':1,'files':[],'explanations':[],'learning_outline':[{'id':'first','title':'第一章','parent_id':None,'source_links':[],'explanation_ids':[]}]}
        before=copy.deepcopy(workspace)
        learning_payload(material,workspace,{'rounds':[],'read_passages':{}})
        self.assertEqual(workspace,before)

if __name__=='__main__':unittest.main()
