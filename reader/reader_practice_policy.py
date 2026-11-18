"""任务、提示词和写回检查共用练习规则，不在读取旧题时改题。"""
import copy,math
VERSION='reader-practice-objective-v2'
DEFAULT={'version':VERSION,'preferred_type':'choice','choice_majority':True,'minimum_objective_ratio':0.75,'objective_types':['choice','true_false','numeric','ordering','code'],'maximum_supplements':1}
INSTRUCTION='你先决定每题要检验哪个判断、计算或操作，再换条件或数值出题，核对答案并逐项解释选项\n一组有多道题时，选择题过半，每组至少四分之三能客观判分\n读者需要亲自计算、排序或写代码时，你可用 numeric、ordering、code，只有确实要看读者怎样自行表达时才用少量短答或复述\n你用 placement 把题放在实际教过该能力的 part 后面'
def practice_policy():
    return copy.deepcopy(DEFAULT)
def validate_practice(quizzes,policy=None,*,current_writing=False):
    policy=practice_policy()if policy is None else policy
    if not isinstance(quizzes,list)or not quizzes:raise ValueError('这次写作没有练习，请保留正文并补上对应练习')
    if not all(isinstance(q,dict)for q in quizzes):raise ValueError('练习需要使用对象')
    objective=sum(q.get('type','choice')in policy['objective_types']for q in quizzes)
    required=math.ceil(len(quizzes)*policy['minimum_objective_ratio'])
    if objective<required:
        raise ValueError('practice_type_mix：这组'+str(len(quizzes))+'题只有'+str(objective)+'题可客观判分，至少需要'+str(required)+'题；保留正文、原编号和练习位置，只补齐题目及解析，不把全部练习改为自评题')
    if current_writing and policy.get('choice_majority') and len(quizzes)>1:
        choices=sum(q.get('type','choice')=='choice' for q in quizzes)
        if choices*2<=len(quizzes):
            raise ValueError('practice_type_mix：多题组的选择题需要过半，请保留正文和练习位置，只调整题型及解析')
    return {'version':policy['version'],'total':len(quizzes),'objective':objective,'required':required}
