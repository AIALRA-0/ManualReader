"""把一次讲解绑定到已开始的章节，页面换章不会丢失稿件的归属。"""
import uuid

class LessonConflict(ValueError):
    def __init__(self,code,message):super().__init__(message);self.code=code

def task_for(state,node_id,actor,stamp):
    tasks=state.setdefault('lesson_tasks',{})
    existing=next((t for t in reversed(list(tasks.values()))if t['learning_node_id']==node_id and t['actor']==actor and t['status']=='prepared'),None)
    if existing:return existing
    task={'id':'lesson-task-'+uuid.uuid4().hex[:20],'learning_node_id':node_id,'actor':actor,'status':'prepared','created_at':stamp()}
    tasks[task['id']]=task
    return task

def resolve_task(state,node_id,actor,selected_id,task_id=None):
    tasks=state.get('lesson_tasks',{})
    if task_id:
        task=tasks.get(task_id)
        if not task or task['actor']!=actor or task['learning_node_id']!=node_id:
            raise LessonConflict('lesson_task_mismatch','讲解任务与当前连接或目标章节不一致，请核对已有任务编号；不需要重新配对')
        if task['status']!='prepared':raise LessonConflict('lesson_task_finished','这个任务已写回，请读取已有回执；修改讲解时准备新的任务')
        return task
    existing=next((t for t in reversed(list(tasks.values()))if t['learning_node_id']==node_id and t['actor']==actor and t['status']=='prepared'),None)
    if existing:return existing
    if node_id!=selected_id:
        raise LessonConflict('lesson_selection_changed','页面当前章节已改变，这不是授权过期；稿件保留在客户端。请在原章节取得讲解任务后提交已有稿件，不要重新生成或重新配对')
    return None

def selection_guard(state,data):
    expected=data.get('expected_selection_revision')
    if expected is not None and expected!=state.get('learning_selection_revision',0):
        raise LessonConflict('selection_changed','其他页面或 Agent 已切换章节，已忽略这次旧页面请求；请刷新后再选择')

def record_selection(state,node_id,stamp,actor,action='select'):
    previous=state.get('learning_node_id')
    if previous==node_id:return
    state['learning_selection_revision']=state.get('learning_selection_revision',0)+1
    events=state.setdefault('learning_selection_events',[])
    events.append({'from':previous,'to':node_id,'at':stamp(),'actor':actor,'action':action,'revision':state['learning_selection_revision']})
    state['learning_selection_events']=events[-40:]
