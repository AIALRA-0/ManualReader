"""检查讲解任务和阅读位置分离，同时保留章节与连接权限。"""
import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_lesson_tasks import LessonConflict,task_for,resolve_task,selection_guard,record_selection

class LessonTasks(unittest.TestCase):
    def setUp(self):self.state={'learning_node_id':'chapter-13','notes':{'q':'保留笔记'},'read_passages':{'p':'manual'}}
    def task(self):return task_for(self.state,'chapter-13','agent-one',lambda:'time')
    def test_page_change_keeps_original_task(self):
        task=self.task();self.state['learning_node_id']='chapter-12'
        self.assertEqual(resolve_task(self.state,'chapter-13','agent-one','chapter-12',task['id']),task)
    def test_other_connection_and_chapter_are_rejected(self):
        task=self.task()
        for node,actor in [('chapter-14','agent-one'),('chapter-13','agent-two')]:
            with self.assertRaises(LessonConflict):resolve_task(self.state,node,actor,'chapter-12',task['id'])
    def test_legacy_client_resumes_only_prepared_task(self):
        task=self.task();self.assertEqual(resolve_task(self.state,'chapter-13','agent-one','chapter-12'),task)
        task['status']='saved'
        with self.assertRaises(LessonConflict):resolve_task(self.state,'chapter-13','agent-one','chapter-12')
    def test_saved_task_gets_new_identity_when_prepared_again(self):
        old=self.task();old['status']='saved';new=self.task();self.assertNotEqual(old['id'],new['id'])
    def test_stale_selection_does_not_change_state(self):
        self.state['learning_selection_revision']=2;before=copy.deepcopy(self.state)
        with self.assertRaises(LessonConflict):selection_guard(self.state,{'expected_selection_revision':1})
        self.assertEqual(self.state,before)
    def test_selection_audit_preserves_manual_records(self):
        record_selection(self.state,'chapter-12',lambda:'time','browser')
        self.assertEqual(self.state['learning_selection_events'][0]['from'],'chapter-13')
        self.assertEqual(self.state['learning_selection_revision'],1)
        self.assertEqual(self.state['notes'],{'q':'保留笔记'});self.assertEqual(self.state['read_passages'],{'p':'manual'})

if __name__=='__main__':unittest.main()
