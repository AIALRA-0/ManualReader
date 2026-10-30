"""在隔离资料上检查旧记录兼容、名称完整性和错题本状态。"""
import copy,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from reader_practice import change,record_answer,payload
from reader_term_names import analyze_name,display_state,english_title
class ReaderTests(unittest.TestCase):
 def setUp(self):
  self.state={'quizzes':[{'id':'q1','prompt':'问题','options':['甲','乙'],'correct_index':1,'selected':None}],'notes':[{'text':'保留笔记'}],'read':['original']};self.stamp=lambda:'test-time'
 def test_image_generation_is_enabled_in_real_executor(self):
  from reader_execute import command
  args=command('codex',Path('.'),Path('result.json'),[]);self.assertIn('image_generation',args);self.assertIn('service_tier="default"',args)
 def test_review_keeps_original_answer_and_note(self):
  record_answer(self.state,'q1',0,self.stamp);s=change(self.state,{'action':'start'},self.stamp)
  result=change(self.state,{'action':'answer','quiz_id':'q1','selected':1,'session_id':s['id']},self.stamp)
  self.assertTrue(result['correct']);self.assertEqual(payload(self.state)['count'],0);self.assertEqual(self.state['quizzes'][0]['selected'],0);self.assertEqual(self.state['notes'],[{'text':'保留笔记'}]);self.assertEqual(self.state['read'],['original'])
  self.assertEqual(change(self.state,{'action':'start'},self.stamp)['items'],[])
 def test_repeat_review_write_is_idempotent(self):
  record_answer(self.state,'q1',0,self.stamp);s=change(self.state,{'action':'start'},self.stamp);data={'action':'answer','quiz_id':'q1','selected':1,'session_id':s['id']};change(self.state,data,self.stamp);change(self.state,data,self.stamp);self.assertEqual(len(self.state['practice_book']['attempts']),2)
 def test_edit_only_changes_notebook_copy(self):
  original=copy.deepcopy(self.state['quizzes']);change(self.state,{'action':'edit','quiz_id':'q1','quiz':{'prompt':'改过的题','options':['丙','丁'],'correct_index':0}},self.stamp);self.assertEqual(self.state['quizzes'],original);self.assertEqual(payload(self.state)['items'][0]['prompt'],'改过的题')
 def test_delete_requires_confirmation(self):
  with self.assertRaises(ValueError):change(self.state,{'action':'remove','quiz_id':'q1'},self.stamp)
 def test_old_incorrect_selection_is_imported_without_mutation(self):
  self.state['quizzes'][0]['selected']=0;before=copy.deepcopy(self.state);self.assertEqual(payload(self.state)['count'],1);self.assertEqual(before,self.state)
 def test_invalid_quiz_rejected(self):
  for options,index in [(['重复','重复'],0),(['甲','乙'],9),(['甲','乙'],True)]:
   with self.assertRaises(ValueError):change(self.state,{'action':'create','quiz':{'prompt':'问题','options':options,'correct_index':index}},self.stamp)
 def test_legacy_english_names_visible_without_database_write(self):
  state={'terms':[{'id':'g1','name':'GORDIAN 布局方法（GORDIAN Placement）'},{'id':'c1','name':'CPU 宽度（CPU Width）'}],'rounds':[]};before=copy.deepcopy(state);shown,report=display_state(state);self.assertEqual(state,before);self.assertEqual(len(shown['terms']),2);self.assertFalse(report['pending']);self.assertTrue(all('（' in t['name'] for t in shown['terms']))
 def test_incomplete_names_are_not_published_as_complete(self):
  for value,issue in [('CPU 宽度','missing_english_name'),('Memory','missing_chinese_name'),('内存（内存）','missing_english_name'),('Memory（Memory）','missing_chinese_name')]:self.assertIn(issue,analyze_name(value)['issues'])
 def test_reversed_name_and_duplicate_chinese_are_normalized(self):
  self.assertEqual(analyze_name('Quadratic Programming（二次规划）')['name'],'二次规划（Quadratic Programming）');self.assertEqual(analyze_name('二次规划（二次规划, Quadratic Programming）')['name'],'二次规划（Quadratic Programming）')
 def test_title_case_with_explicit_exceptions(self):
  self.assertEqual(english_title('rectangle dissection'),'Rectangle Dissection');self.assertEqual(english_title('macOS and eBPF'),'macOS And eBPF');self.assertEqual(english_title('rectangle dissection',preserve=True),'rectangle dissection')
 def test_display_dedup_preserves_original_entries(self):
  state={'terms':[{'id':'a','name':'矩形剖分（rectangle dissection）','question_ids':['q1']},{'id':'b','name':'矩形剖分（Rectangle Dissection）','question_ids':['q2']}],'rounds':[]};before=copy.deepcopy(state);shown,report=display_state(state);self.assertEqual(state,before);self.assertEqual(len(shown['terms']),1);self.assertEqual(shown['terms'][0]['question_ids'],['q1','q2'])
if __name__=='__main__':unittest.main(verbosity=2)
