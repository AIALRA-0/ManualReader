"""任务、提示词和写回检查共用练习规则，不在读取旧题时改题。"""
import copy,math
VERSION='reader-practice-objective-v1'
DEFAULT={'version':VERSION,'preferred_type':'choice','minimum_objective_ratio':0.75,'objective_types':['choice','true_false','numeric','ordering','code'],'maximum_supplements':1}
INSTRUCTION='练习默认用选择题；需要计算、排序或实际代码执行时，也可使用已有确定判分的 numeric、ordering、code。每组至少四分之三的练习必须能客观判分，不能把整组改成由用户自行核对的 recall、填空或简答。必要的自主表达题作为少量补充，题型不按领域机械分配。题目和解析仍在同一次写作中生成，保留原 placement，不用脚本编造干扰项。'
def practice_policy():
    return copy.deepcopy(DEFAULT)
def validate_practice(quizzes,policy=None):
    policy=practice_policy()if policy is None else policy
    if not isinstance(quizzes,list)or not quizzes:raise ValueError('这次写作没有练习，请保留正文并补上对应练习')
    if not all(isinstance(q,dict)for q in quizzes):raise ValueError('练习需要使用对象')
    objective=sum(q.get('type','choice')in policy['objective_types']for q in quizzes)
    required=math.ceil(len(quizzes)*policy['minimum_objective_ratio'])
    if objective<required:
        raise ValueError('practice_type_mix：这组'+str(len(quizzes))+'题只有'+str(objective)+'题可客观判分，至少需要'+str(required)+'题；保留正文、原编号和练习位置，只补齐题目及解析，不把全部练习改为自评题')
    return {'version':policy['version'],'total':len(quizzes),'objective':objective,'required':required}
