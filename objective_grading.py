"""客观练习使用确定的数值与先后约束评分：按数值和先后约束核对，不调用模型。"""
import ast,copy,json,math,re,unicodedata
from fractions import Fraction

def arithmetic(text):
    if not isinstance(text,str)or not text.strip()or len(text)>180:raise ValueError('请填写一个数值或简单分数')
    text=unicodedata.normalize('NFKC',text).strip().replace('−','-').replace('×','*').replace('÷','/')
    try:tree=ast.parse(text,mode='eval')
    except SyntaxError as error:raise ValueError('请填写数值或四则运算式')from error
    def read(n):
        if isinstance(n,ast.Constant)and type(n.value)in(int,float):return Fraction(str(n.value))
        if isinstance(n,ast.UnaryOp)and isinstance(n.op,(ast.UAdd,ast.USub)):
            v=read(n.operand);return v if isinstance(n.op,ast.UAdd)else-v
        if isinstance(n,ast.BinOp)and isinstance(n.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            a,b=read(n.left),read(n.right)
            if isinstance(n.op,ast.Add):return a+b
            if isinstance(n.op,ast.Sub):return a-b
            if isinstance(n.op,ast.Mult):return a*b
            if b==0:raise ValueError('分母不能为零')
            return a/b
        if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id in('min','max')and not n.keywords and 1<=len(n.args)<=6:
            values=[read(v)for v in n.args];return min(values)if n.func.id=='min'else max(values)
        raise ValueError('这里只接受数字、小数、分数和四则运算')
    try:return read(tree.body)
    except(SyntaxError,ZeroDivisionError,OverflowError,TypeError)as e:raise ValueError('请检查数值或分数格式')from e

def calculation(text,tolerance=Fraction(0)):
    """核对常见的中文算式写法；等号每边都计算，不只取最后答案。"""
    if not isinstance(text,str):raise ValueError('计算过程需要算式')
    value=unicodedata.normalize('NFKC',text).strip().replace('−','-').replace('×','*').replace('÷','/')
    if ':'in value:
        label,value=value.split(':',1)
        if not label or not re.fullmatch(r'[\u3400-\u9fffA-Za-z\s]+',label):raise ValueError('计算说明和算式没有分开')
    values=[arithmetic(v.strip())for v in value.split('=')]
    if not values or any(abs(v-values[0])>tolerance for v in values):raise ValueError('计算过程的等号两边不相等')
    return values[0]

def number(text,quiz):
    if not isinstance(text,str):raise ValueError('请填写数值')
    value=unicodedata.normalize('NFKC',text).strip().replace('−','-').replace('×','*').replace('÷','/')
    if len(value)>180 or not value:raise ValueError('请先填写答案')
    aliases=quiz.get('unit_aliases',{});unit=quiz.get('unit','')
    if unit and unit not in aliases:aliases={**aliases,unit:'1'}
    multiplier=Fraction(1)
    for name in sorted(aliases,key=len,reverse=True):
        if name and value.endswith(name):value=value[:-len(name)].strip();multiplier=arithmetic(str(aliases[name]));break
    if value.endswith('%'):value=value[:-1].strip();multiplier/=100
    try:return arithmetic(value)*multiplier
    except(SyntaxError,ValueError)as e:raise ValueError('请按题目要求填写数值，单位使用 '+(unit or '题目给定形式'))from e

def normalize(quiz):
    q=copy.deepcopy(quiz);kind=q.get('type')
    if kind=='code':
        if q.get('runner_id')!='lru-arbiter-v1' or not q.get('starter_code') or not q.get('reference_code'):raise ValueError('代码题缺少固定运行器或代码')
        q.update(options=['需要修改','正确'],correct_index=1,option_explanations=['查看编译或测试指出的修改位置','本题测试通过']);return q
    if kind not in('numeric','ordering'):return q
    if not str(q.get('prompt','')).strip()or not str(q.get('explanation','')).strip():raise ValueError('客观题需要题目和完整解析')
    if kind=='numeric':
        expected=arithmetic(str(q.get('expected','')));tol=arithmetic(str(q.get('tolerance','0')))
        if tol<0:raise ValueError('允许误差不能为负数')
        if q.get('calculation') is not None and abs(calculation(q['calculation'],tol)-expected)>tol:raise ValueError('题目预设答案与重新计算的结果不同')
        if not isinstance(q.get('unit_aliases',{}),dict):raise ValueError('单位换算需要对象')
        for k,v in q.get('unit_aliases',{}).items():
            if not k or arithmetic(str(v))<=0:raise ValueError('单位换算倍数需要为正数')
    else:
        items=q.get('items',[]);ids=[x.get('id')for x in items]
        if len(ids)<2 or len(ids)!=len(set(ids))or any(not x.get('text')or not x.get('id')for x in items):raise ValueError('排序题需要至少两个编号不重复的动作')
        edges=q.get('before',[])
        if not edges or any(not isinstance(p,list)or len(p)!=2 or p[0]not in ids or p[1]not in ids or p[0]==p[1]for p in edges):raise ValueError('排序约束需要明确且有效的前后动作')
        order=q.get('reference_order',[])
        if sorted(order)!=sorted(ids)or any(order.index(a)>=order.index(b)for a,b in edges):raise ValueError('参考顺序没有满足题目的先后关系')
    # 继续兼容 Reader 已有的题目统计；这些选项不显示，也不接受客户端自评。
    q.update(options=['需要修改','正确'],correct_index=1,option_explanations=['按客观规则核对后需要修改','按客观规则核对正确'])
    return q

def grade(quiz,response):
    kind=quiz['type']
    if kind=='code':
        result=__import__('reader_code_runner').run(response)
        if result.get('status')=='environment_error':raise ValueError(result['correction'])
        return result
    if kind=='numeric':
        value=number(response,quiz);expected=arithmetic(str(quiz['expected']));tolerance=arithmetic(str(quiz.get('tolerance','0')))
        correct=abs(value-expected)<=tolerance
        correction=''if correct else'本题应得到 '+str(quiz['expected'])+(' '+quiz.get('unit','')if quiz.get('unit')else'')+'；请对照下面的计算过程修改'
        return {'correct':correct,'normalized_response':str(value),'expected':str(expected),'correction':correction,'grading_method':'deterministic_numeric'}
    if kind=='ordering':
        if isinstance(response,str):
            try:response=json.loads(response)
            except ValueError:response=[s.strip()for s in response.split(',')]
        ids=[x['id']for x in quiz['items']]
        if not isinstance(response,list)or len(response)!=len(ids)or any(not isinstance(x,str)for x in response)or sorted(response)!=sorted(ids):raise ValueError('请将每个动作各排列一次，不能遗漏或重复')
        bad=[(a,b)for a,b in quiz['before']if response.index(a)>=response.index(b)];names={x['id']:x['text']for x in quiz['items']}
        return {'correct':not bad,'normalized_response':response,'violations':[{'before':a,'after':b}for a,b in bad],'correction':'；'.join('先“'+names[a]+'”，再“'+names[b]+'”'for a,b in bad),'grading_method':'deterministic_ordering'}
    raise ValueError('这类练习仍由现有 Reader 评分')

def evidence(state,objectives):
    quizzes={q['id']:q for q in state.get('quizzes',[])};attempts=state.get('practice_book',{}).get('attempts',[]);rows=[]
    for goal in objectives:
        details=[]
        for qid in goal.get('quiz_ids',[]):
            hist=[a for a in attempts if a.get('quiz_id')==qid];q=quizzes.get(qid,{})
            if not hist:label='尚未作答'
            elif hist[-1].get('correct')is True:label='首次作答正确'if len(hist)==1 else'再次作答正确'
            else:label='本次需要修改'
            details.append({'quiz_id':qid,'status':label,'attempts':len(hist),'last_correct':hist[-1].get('correct')if hist else None})
        rows.append({**goal,'evidence':details})
    return rows
