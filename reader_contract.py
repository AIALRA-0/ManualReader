"""统一处理写回格式与问题标点，保留用户输入和来源原文。"""
import re
import random

def option_explanations(quiz):
    values=quiz.get('option_explanations')
    return [v.strip() for v in values] if isinstance(values,list) and len(values)==len(quiz.get('options',[])) and all(isinstance(v,str) and v.strip() for v in values) else []

def quiz_feedback(quiz,selected):
    reasons=option_explanations(quiz)
    common=str(quiz.get('explanation',''))
    if selected!=quiz.get('correct_index') and reasons and isinstance(selected,int) and 0<=selected<len(reasons):
        reason='你选择的“'+quiz['options'][selected]+'”：'+reasons[selected]
        return reason+ ('\n\n'+common if reasons[selected] not in common else '')
    return common

QUIZ_WRITING_RULE='选择题默认四个选项，围绕本次原句或回答检验一个具体判断、应用或推理；干扰项来自读者可能混淆的条件、对象、步骤、因果或计算，不使用明显荒谬、答非所问的陪衬。各选项采用同一语法结构和相近的信息量，正确项不固定最详细、最长，错误项也要给出具体且看似合理的判断；选项长度明显悬殊时，在本次写作中自行调整，不填充无意义文字。正确选项位置由脚本在显示时均衡，不必自行安排ABCD；选项和解析引用选项内容，不依赖A、B等字母，不写“以上皆是”或“前两项”等依赖排列的答案。干扰项不能只是正确陈述的真实子集、等价改写或也符合题干的另一种情况；特别检查哪些、哪组、至少等问法是否允许多个选项成立。先自行确认只有一个正确选项，再输出选项、correct_index和解释。必须同时返回 option_explanations，与原始 options 按下标一一对应；每个错误选项具体说出错在什么对象、条件、步骤或因果，以及怎样判断才对，不能只复述正确答案。不另起审稿或循环修复'

def quiz_display_orders(quizzes):
    """按选项数均衡答案位置，只返回显示顺序，不修改题目或用户作答。"""
    counts={};orders={}
    for quiz in quizzes:
        options=quiz.get('options',[]);correct=quiz.get('correct_index');n=len(options)
        if not 2<=n<=6 or not isinstance(correct,int) or not 0<=correct<n:continue
        used=counts.setdefault(n,[0]*n);rng=random.Random(quiz['id']);order=list(range(n))
        dependent=re.search(r'以上(?:[两三四五六\d]+项)?(?:都|均|皆|全)|上述(?:选项|各项)|前[两三四五六\d]+项|[A-F]\s*(?:[、和与或]\s*[A-F]|选项)|选项\s*[A-F]',quiz.get('prompt','')+'\n'+'\n'.join(options))
        if not dependent:
            target=rng.choice([i for i,c in enumerate(used) if c==min(used)])
            order.remove(correct);rng.shuffle(order);order.insert(target,correct)
        used[order.index(correct)]+=1;orders[quiz['id']]=order
    return orders

def quiz_quality_flags(quiz):
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


def writing_guide():
    from reader_prompts import TEACHING_METHOD, NAMING_RULE, WRITING_VERSION
    return {'version':WRITING_VERSION,'model':'gpt-6.1-terra','effort':'high','instructions':[TEACHING_METHOD,NAMING_RULE,
      '先用普通话正面回答当前问题，再围绕答案解释具体对象、原因和必要前置',
      '问答的“原文：”与“回答：”由接口各占一段显示，正文按真实子问明确作答，再补齐依据；讲解原句只存于来源映射，正文围绕本节内容连贯讲清，不重复抄写原文',
      '本题结论先用普通话说清，所用对象、符号、指代、操作和必要术语就地解释',
      '普通解释用相互承接的完整句子组成自然段，话题或推理阶段改变时再换段；计算、公式和代码保留分步排版。新写段末省略中文句号，段内保留正常标点；原文引用、代码、公式、网址和用户原话保持原样',
      '每个术语必须具有中文全称和英文全称，缩写可选；术语采用缩写 中文全称（英文全称）或中文全称（英文全称），沿用已登记名称和大小写例外，不把普通词汇机械拆成术语',
      '术语沿用专业定义与直观解释两段：定义独立说明这是什么、怎样工作，影响结论的必要条件就地说明，不依赖读者记得当前问答；直观解释以“可以想象……”等自然衔接，沿用正文已经讲清的具体对象，把对象的动作和专业含义对应起来，不只列名词或把定义换词重复一遍',
      '先把回答讲清，再从同一次写作的正文提取术语两段，不另起审稿或术语写作轮次；已有术语优先只引用 term_id，复用其名称和含义；确需改善旧解释时明确提交 action=revise、expected_version 与两段新解释，保留同一 ID、历史和用户笔记',
      '公式使用明确 LaTeX 标记；回答、术语和练习在同一次写作中完成，关联原问题编号，由脚本逐项保存',QUIZ_WRITING_RULE,
      '复杂问题解释充分，简单问题简洁；不预先生成预测问题，不添加审稿代理或循环修复'],
      'batching':'默认 Terra high；一次读取 /api/work，原句去重后最多四组并行。材料、规则和已有术语作为共同前缀；只传相关的父问题回答，不带整段 Codex 聊天历史。写作者输出结果，脚本负责保存、格式整理和用量统计，不让模型反复读文件或确认保存结果'}
