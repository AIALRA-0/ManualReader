"""先核对删除对象，再确认删除；确认只在短时间内对同一项目和调用者有效。"""
import hashlib,json,secrets,threading,time
from reader_questions import change_question

class DeletionReviews:
    def __init__(self,clock=time.monotonic,ttl=300):
        self.clock=clock;self.ttl=ttl;self.pending={};self.lock=threading.RLock()

    @staticmethod
    def snapshot(state,question_id,target):
        known={q['id']:q for r in state['rounds'] for q in r['questions']}
        question=known.get(question_id)
        if not question or question.get('deleted_at'):raise ValueError('问题不存在或已经删除，请重新读取当前项目')
        if target not in ('question','answer'):raise ValueError('请选择删除问题或删除回答')
        if target=='answer' and not question.get('answer'):raise ValueError('这条问题当前没有可删除的回答')
        branch={question_id}
        if target=='question':
            while True:
                more={q['id'] for q in known.values() if not q.get('deleted_at') and q.get('parent_question_id') in branch}
                if more<=branch:break
                branch|=more
        rows=[{k:known[qid].get(k) for k in ('id','text','version','parent_question_id','passage_id','sentence_id','answer','answer_parts')} for qid in sorted(branch)]
        fingerprint=hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        return question,sorted(branch),fingerprint

    def preview(self,state,material_id,actor,question_id,data):
        target=data.get('target','question')
        question,branch,fingerprint=self.snapshot(state,question_id,target)
        now=self.clock();identity='delete-'+secrets.token_urlsafe(24)
        with self.lock:
            self.pending={k:v for k,v in self.pending.items() if v['until']>now}
            if len(self.pending)>=256:self.pending.pop(next(iter(self.pending)))
            self.pending[identity]={'material_id':material_id,'actor':actor,'question_id':question_id,'target':target,'fingerprint':fingerprint,'until':now+self.ttl}
        return {'confirmation_id':identity,'expires_in_seconds':self.ttl,'target':target,
                'question':{k:question.get(k) for k in ('id','text','version','parent_question_id','passage_id','sentence_id')},
                'question_version':question.get('version',1),'affected_question_ids':branch,'question_count':len(branch),
                'preserved':['answer_history','notes','quizzes','exercise_attempts','read_passages'],
                'instruction':'先核对项目、问题原话和受影响的追问，再提交 deletion-confirm；原样带回 confirmation_id、question_version 和 expected_text，并明确 confirmed=true。不要自动确认，也不要把一次确认用于其他问题；内容变动后需要重新核对'}

    def confirm(self,state,material_id,actor,question_id,data,stamp,expected_target=None):
        if data.get('confirmed')is not True:raise ValueError('需要先核对删除预览，再明确二次确认')
        if not data.get('confirmation_id'):raise ValueError('请先调用这个问题的 deletion-preview，核对后通过 deletion-confirm 二次确认')
        with self.lock:
            review=self.pending.get(data.get('confirmation_id'))
            if not review or review['until']<=self.clock():raise ValueError('删除预览已过期或已经使用，请重新核对')
            if (review['material_id'],review['actor'],review['question_id'])!=(material_id,actor,question_id):raise ValueError('这次删除确认不属于当前项目、调用者或问题')
            if expected_target and review['target']!=expected_target:raise ValueError('确认的删除对象与当前操作不一致')
            question,branch,fingerprint=self.snapshot(state,question_id,review['target'])
            if fingerprint!=review['fingerprint']:
                self.pending.pop(data['confirmation_id'],None)
                raise ValueError('问题、回答或追问已经变化，请重新取得删除预览')
            if data.get('expected_version')!=question.get('version',1) or data.get('expected_text')!=question['text']:raise ValueError('请核对预览中的问题原话和当前版本，再确认删除')
            if review['target']=='question':
                result=change_question(state,question_id,{'action':'delete','expected_text':question['text'],'expected_version':question.get('version',1),'confirmed':True},stamp)
            else:
                from reader_work import change_answer
                result=change_answer(state,question_id,{'action':'delete','expected_answer':question['answer'],'confirmed':True},stamp)
            self.pending.pop(data['confirmation_id'],None)
        return {**result,'target':review['target'],'history_preserved':True}

REVIEWS=DeletionReviews()
