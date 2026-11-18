from reader_writer_profiles import writer_profile,writer_label,public_profiles
"""统一处理写回格式与问题标点，保留用户输入和来源原文。"""
import re
import random

def current_quizzes(quizzes):
    replaced={q.get('revises_quiz_id')for q in quizzes if q.get('revises_quiz_id')}
    return [q for q in quizzes if q['id']not in replaced and not q.get('deleted_at')
            and not (q.get('archived_duplicate_of') or q.get('practice_archived'))]

def normalize_quiz(quiz):
    """选择题保留原选项；自行作答题保存参考答案，用同一份记录登记用户核对的结果。"""
    if not isinstance(quiz,dict):raise ValueError('练习需要使用对象')
    value=dict(quiz);kind=value.get('type','choice')
    if kind in ('numeric','ordering','code'):return __import__('objective_grading').normalize(value)
    if kind in ('recall','short_answer','fill_blank','calculation','derivation','code','open'):
        reference=value.get('reference_answer')
        if not isinstance(reference,str)or not reference.strip():raise ValueError('自行作答题需要 reference_answer，供读者完成后核对')
        if len(reference)>30000:raise ValueError('参考答案超过30000字')
        value.update(type='recall',reference_answer=reference.strip(),options=['还没答对','答对了'],correct_index=1,option_explanations=['对照参考答案中的步骤，补上自己遗漏或写错的部分','已按参考答案核对自己的作答'])
    elif kind not in ('choice','true_false'):raise ValueError('练习 type 使用 choice、true_false 或 recall；recall 可以承载填空、计算、推导和代码练习')
    options=value.get('options');correct=value.get('correct_index')
    if not isinstance(value.get('prompt'),str)or not value['prompt'].strip()or not isinstance(value.get('explanation'),str)or not value['explanation'].strip():raise ValueError('练习需要题目和解析')
    if not isinstance(options,list)or not 2<=len(options)<=6 or any(not isinstance(o,str)or not o.strip()for o in options)or len(set(options))!=len(options):raise ValueError('选择题需要二至六个不同的选项')
    if type(correct)is not int or not 0<=correct<len(options):raise ValueError('正确选项编号不在范围内')
    return value

def quiz_fields(quiz):
    return {k:quiz[k]for k in ('type','reference_answer','placement','expected','calculation','tolerance','unit','unit_aliases','items','before','reference_order','objective_ids','source_ids','assessment','language','runner_id','starter_code','reference_code','variant_id')if k in quiz}

def same_exercise(left,right):
    """核对题干和实际判分所需字段；旧作答继续关联原来的题。"""
    keys=('prompt','options','correct_index','reference_answer','expected','tolerance','unit','unit_aliases',
          'items','before','language','runner_id','reference_code','variant_id')
    return left.get('type','choice')==right.get('type','choice') and all(left.get(key)==right.get(key) for key in keys)

def option_explanations(quiz):
    values=quiz.get('option_explanations')
    return [v.strip() for v in values] if isinstance(values,list) and len(values)==len(quiz.get('options',[])) and all(isinstance(v,str) and v.strip() for v in values) else []

def quiz_feedback(quiz,selected):
    if quiz.get('type')=='recall':return str(quiz.get('explanation',''))
    reasons=option_explanations(quiz)
    common=str(quiz.get('explanation',''))
    if selected!=quiz.get('correct_index') and reasons and isinstance(selected,int) and 0<=selected<len(reasons):
        reason='你选择的“'+quiz['options'][selected]+'”：'+reasons[selected]
        return reason+ ('\n\n'+common if reasons[selected] not in common else '')
    return common


def quiz_display_orders(quizzes):
    """按选项数均衡答案位置，只返回显示顺序，不修改题目或用户作答。"""
    counts={};orders={}
    for quiz in quizzes:
        options=quiz.get('options',[]);correct=quiz.get('correct_index');n=len(options)
        if not 2<=n<=6 or not isinstance(correct,int) or not 0<=correct<n:continue
        if quiz.get('type')=='recall':orders[quiz['id']]=list(range(n));continue
        used=counts.setdefault(n,[0]*n);rng=random.Random(quiz['id']);order=list(range(n))
        dependent=re.search(r'以上(?:[两三四五六\d]+项)?(?:都|均|皆|全)|上述(?:选项|各项)|前[两三四五六\d]+项|[A-F]\s*(?:[、和与或]\s*[A-F]|选项)|选项\s*[A-F]',quiz.get('prompt','')+'\n'+'\n'.join(options))
        if not dependent:
            target=rng.choice([i for i,c in enumerate(used) if c==min(used)])
            order.remove(correct);rng.shuffle(order);order.insert(target,correct)
        used[order.index(correct)]+=1;orders[quiz['id']]=order
    return orders

def quiz_quality_flags(quiz):
    if quiz.get('type')=='recall':return []
    options=quiz.get('options',[]);correct=quiz.get('correct_index');flags=[]
    if not options or not isinstance(correct,int) or not 0<=correct<len(options):return flags
    lengths=[len(re.sub(r'\s+','',o)) for o in options];others=sorted(lengths[:correct]+lengths[correct+1:]);middle=others[len(others)//2]
    if lengths[correct]>max(20,middle*1.6):flags.append('correct_option_much_longer')
    return flags

def revise_quiz_options(state,identity,data,stamp):
    quiz=next((q for q in state.get('quizzes',[]) if q['id']==identity),None)
    if not quiz:raise ValueError('没有找到这道练习')
    if quiz.get('selected') is not None:raise ValueError('这道练习已有作答，保留原选项和作答记录')
    if data.get('expected_options')!=quiz['options']:raise ValueError('练习选项已经更新，请重新读取')
    options=data.get('options');correct=data.get('correct_index')
    if not isinstance(options,list) or not 2<=len(options)<=6 or any(not isinstance(o,str) or not o.strip() for o in options) or isinstance(correct,bool) or not isinstance(correct,int) or not 0<=correct<len(options):raise ValueError('请提交完整选项和正确选项编号')
    if len(set(options))!=len(options):raise ValueError('同一道题的选项不能重复')
    if options[correct]!=quiz['options'][quiz['correct_index']]:raise ValueError('调整干扰项时必须保留原正确选项的内容')
    quiz.setdefault('option_history',[]).append({'options':quiz['options'],'correct_index':quiz['correct_index'],'saved_at':stamp()})
    quiz.update(options=options,correct_index=correct,option_revision=quiz.get('option_revision',1)+1)
    return {'id':identity,'option_revision':quiz['option_revision'],'selected':quiz.get('selected')}

def canonical_term(value,case_exceptions=()):
    from reader_term_names import analyze_name
    return analyze_name(value,case_exceptions)['name']

PROTECTED=re.compile(r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\$\$[\s\S]*?\$\$|(?<![\\$])\$(?!\$)[^$\n]+(?<!\\)\$|https?://[^\s<>]+|[“「『][\s\S]*?[”」』]')

def prose_display(value):
    raw=str(value or '');parts=[];at=0
    def clean(text):
        return text.replace('。','；')
    for match in PROTECTED.finditer(raw):
        parts.extend([clean(raw[at:match.start()]),match.group()]);at=match.end()
    parts.append(clean(raw[at:]));return ''.join(parts)

def normalize_question(value):
    raw=str(value or '');text=re.sub(r'^\s*(?:[-*•]|\d+[.)、])\s+','',raw).strip()
    text=re.sub(r'[ \t]+',' ',text)
    if text and not re.search(r'[?？!！:：]$',text):
        if re.search(r'什么|为何|为什么|为啥|怎么|怎样|如何|是否|哪|几|多少|区别|关系|意思|吗\s*[。.]?$',text) or not re.match(r'^(?:请|解释|说明|举例|总结|列出|对比)',text):
            text=re.sub(r'[。.]$','',text)+'？'
    return text

def reader_context_fields(data):
    """只接收本轮真实读者反馈和明确确认的内容，不据旧回答推断用户已经掌握。"""
    result={};feedback=data.get('reader_feedback_latest')
    if feedback is not None:
        if isinstance(feedback,str):feedback={'text':feedback,'source':'用户在本轮对话中的直接反馈'}
        if not isinstance(feedback,dict) or not isinstance(feedback.get('text'),str) or not feedback['text'].strip() or len(feedback['text'])>8000:
            raise ValueError('最新读者反馈需要提供不超过8000字的用户原话')
        result['reader_feedback_latest']={}
        for key,limit in [('text',8000),('source',200),('interpretation',2000)]:
            if key in feedback:
                if not isinstance(feedback[key],str) or len(feedback[key])>limit:raise ValueError('读者反馈的说明格式或长度不正确')
                result['reader_feedback_latest'][key]=feedback[key]
    for key,limit in [('confirmed_passage',10000),('confirmed_understanding',4000)]:
        if key in data:
            if not isinstance(data[key],str) or len(data[key])>limit:raise ValueError('用户已确认内容的格式或长度不正确')
            result[key]=data[key]
    return result


def writing_guide(role="answer"):
    from reader_prompts import WRITER_INSTRUCTIONS,WRITING_VERSION,FILE_LINK_RULE
    from reader_generation import VERSION as FRAMEWORK_VERSION,PLAN_CONTRACT,PLACEMENT_CONTRACT
    from reader_analysis import VERSION as ANALYSIS_VERSION
    from reader_task_prompts import READING_ANALYSIS
    return {'version':WRITING_VERSION,'framework_version':FRAMEWORK_VERSION,**writer_profile(role),'writer_profiles':public_profiles(),
      'practice_policy':__import__('reader_practice_policy').practice_policy(),'plan':PLAN_CONTRACT,'practice_placement':PLACEMENT_CONTRACT,'practice_forms':{'choice':{'type':'choice','prompt':'具体题目','options':['四个同类且合理的选项'],'correct_index':0,'explanation':'解题过程','option_explanations':['逐项解释']},'recall':{'type':'recall','prompt':'请自行写出、计算、推导或操作的题目','reference_answer':'完整参考答案，可含公式、代码和图片','explanation':'核对步骤与关键判断'}},
      'file_links':FILE_LINK_RULE,'instructions':[WRITER_INSTRUCTIONS],
      'analysis':{'version':ANALYSIS_VERSION,'modes':['lesson','answer'],'system':READING_ANALYSIS,
                  'order':['analysis','writing'],'evidence':'reader_checks 将卡点编号对应到实际正文摘句'},
      'batching':'每组先分析再写，讲解和问答共用分析器。普通服务最多两组并行。客户端只传本组相关材料和父回答，分别记录分析与正文的耗时、token 和缓存。'}
