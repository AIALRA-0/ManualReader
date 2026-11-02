"""用合成记录验证删答、改题、二次确认和回答阅读状态，不访问网站或模型。"""
import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_questions import needs_answer,has_current_answer,question_pool_status,change_question,answer_revision,mark_answers_seen
from reader_question_actions import DeletionReviews

class QuestionLifecycle(unittest.TestCase):
 def setUp(self):
  self.q={'id':'demo-q','text':'缓存命中省掉哪一步？','version':1,'passage_id':'demo-source','sentence_id':'demo-sentence','answer':'省掉重新访问原数据','answered_question_version':1}
  self.child={'id':'demo-child','text':'为什么仍要返回结果？','version':1,'passage_id':'demo-source','parent_question_id':'demo-q','answer':None}
  self.state={'rounds':[{'id':'demo-round','questions':[self.q,self.child]}],'notes':{'demo-q':'这是我的理解'},'read_passages':{'demo-source':'manual'},'quizzes':[{'id':'demo-quiz','selected':1}],'drafts':{}}
  self.stamp=lambda:'synthetic-time';self.reviews=DeletionReviews()
 def preview(self,target='answer'):
  return self.reviews.preview(self.state,'demo-project','demo-agent','demo-q',{'target':target})
 def confirm(self,p,actor='demo-agent',project='demo-project'):
  return self.reviews.confirm(self.state,project,actor,'demo-q',{'confirmation_id':p['confirmation_id'],'confirmed':True,'expected_version':p['question_version'],'expected_text':p['question']['text']},self.stamp)
 def test_delete_edit_returns_current_version_to_pool(self):
  p=self.preview();self.confirm(p);self.assertTrue(needs_answer(self.q))
  change_question(self.state,'demo-q',{'action':'edit','expected_text':self.q['text'],'expected_version':1,'text':'缓存命中时具体省去哪次读取？'},self.stamp)
  self.assertEqual(self.q['version'],2);self.assertEqual(question_pool_status(self.state)['pending'],2)
  self.assertEqual(self.q['id'],'demo-q');self.assertEqual(self.child['parent_question_id'],'demo-q');self.assertTrue(self.q['answer_history'])
 def test_old_fragments_do_not_count_as_answer(self):
  self.q.update(answer=None,version=5,answered_question_version=3,answer_deleted_at='old',answer_parts=[{'markdown':'旧片段'}],answered_at='old')
  self.assertTrue(needs_answer(self.q));self.assertFalse(has_current_answer(self.q));self.assertIsNone(answer_revision(self.q))
 def test_preview_does_not_change_records(self):
  before=copy.deepcopy(self.state);p=self.preview('question');self.assertEqual(p['question_count'],2);self.assertEqual(self.state,before)
 def test_confirmation_is_scoped_and_one_use(self):
  p=self.preview()
  for project,actor in [('other-project','demo-agent'),('demo-project','other-agent')]:
   with self.assertRaises(ValueError):self.confirm(p,actor,project)
  self.confirm(p)
  with self.assertRaises(ValueError):self.confirm(p)
 def test_branch_changes_require_new_preview(self):
  p=self.preview('question');self.child['text']='改过的追问？'
  with self.assertRaises(ValueError):self.confirm(p)
  self.assertNotIn('deleted_at',self.q)
 def test_expired_preview_cannot_delete(self):
  clock=[0];self.reviews=DeletionReviews(clock=lambda:clock[0]);p=self.preview();clock[0]=301
  with self.assertRaises(ValueError):self.confirm(p)
 def test_deleting_branch_preserves_notes_progress_and_quizzes(self):
  before={k:copy.deepcopy(self.state[k])for k in ('notes','read_passages','quizzes')};result=self.confirm(self.preview('question'))
  self.assertEqual(set(result['deleted_ids']),{'demo-q','demo-child'})
  for k in before:self.assertEqual(self.state[k],before[k])
  self.assertEqual(self.q['answer'],'省掉重新访问原数据')
 def test_new_answer_revision_requires_another_open(self):
  rev=answer_revision(self.q);mark_answers_seen(self.state,[{'question_id':'demo-q','answer_revision':rev}],self.stamp)
  self.assertEqual(self.state['answer_views']['demo-q']['answer_revision'],rev)
  self.q['answer']='命中时省掉重新读取原数据，程序仍须返回缓存副本'
  self.assertNotEqual(answer_revision(self.q),rev)
  self.assertEqual(mark_answers_seen(self.state,[{'question_id':'demo-q','answer_revision':rev}],self.stamp)['seen'],[])
  new=answer_revision(self.q);mark_answers_seen(self.state,[{'question_id':'demo-q','answer_revision':new}],self.stamp)
  self.assertEqual(self.state['answer_views']['demo-q']['answer_revision'],new);self.assertEqual(self.state['read_passages'],{'demo-source':'manual'})

if __name__=='__main__':unittest.main(verbosity=2)
