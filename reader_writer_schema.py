"""约束写回字段及真实编号，保留既有的选择、计算、排序和编程题型。"""
import json

S={'type':'string'};I={'type':'integer'}
ID={'type':'string','pattern':'^[A-Za-z0-9_-]{1,70}$'}
def obj(**fields):
    # 每个字段独立，避免为编号加枚举时连带限制正文字符串。
    return json.loads(json.dumps({'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}))
def arr(item,**limits):return {'type':'array','items':item,**limits}
def enum(values):return {'type':'string','enum':list(dict.fromkeys(values))}if values else S

def new_term_text_fields():
    from reader_term_quality import MINIMUMS
    descriptions = {
        'professional_definition': '一个自然段，独立讲清是什么、为什么需要和怎样起作用。',
        'intuitive_explanation': '一个自然段，走完具体场景，并说明对象、动作和结果如何对应实际机制。',
    }
    return {key: {**S, 'description': descriptions[key], 'minLength': minimum}
            for key, minimum in MINIMUMS.items()}


def term_schema(known_terms, questions):
    common = dict(name=S, question_ids=questions, case_exceptions=arr(S), english_case_exception={'type':'boolean'},
                  aliases=arr({**S,'description':'正文或分析中实际用到且确实表示同一概念的短名称'}))
    new = obj(**common, **new_term_text_fields(), term_id={'type':'null'},
              action={'type':'null'}, expected_version={'type':'null'})
    if not known_terms:
        return new
    reuse = obj(**{**common,'name':{'type':'null','description':'按登记编号复用，脚本读取正式名称'}},
                term_id=enum(known_terms), action={'type':'null'}, expected_version={'type':'null'},
                professional_definition=enum(['']), intuitive_explanation=enum(['']))
    return {'anyOf': [new, reuse]}


def output_schema(packet):
    role=packet.get('mode','answer');sources=[s['id']for key in('sources','reference_blocks')for s in packet.get(key,[])];qids=[q['id']for q in packet.get('questions',[])]
    source=enum(sources);questions=arr(enum(qids))if qids else arr(S,maxItems=0)
    dependency={**S,'description':'只填此前unit的id；不在编号后追加说明；无前置时为空数组'}
    answer = '内部核对所用的结论，在章节正文推导到对应位置时再写出' if role=='lesson' else '直接回答用户的实际问题'
    plan=obj(target=S,direct_answer={**S,'description':answer},units=arr(obj(id=ID,say=S,needs=arr(dependency),source_refs=arr(obj(source_id=source,quote=S))),minItems=1),order=arr(ID,minItems=1),stop_when=S)
    if role=='lesson'and packet.get('source_groups'):
        fields={g['id']:obj(unit_id=S,part_id=S,quote=S)for g in packet['source_groups']}
        plan['properties']['coverage']=obj(**fields);plan['required'].append('coverage')
    placement=obj(kind=enum([role]),question_id=enum(qids)if role=='answer'else{'type':'null'},after_part_id=S)
    base={'placement':placement,'prompt':S,'explanation':S}
    choice=obj(**base,type=enum(['choice','true_false']),options=arr(S,minItems=2,maxItems=6),correct_index=I,option_explanations=arr(S,minItems=2,maxItems=6))
    numeric=obj(**base,type=enum(['numeric']),expected={**S,'description':'仅数字、小数或四则运算式'},calculation={**S,'description':'用数字和+-*/()写可执行算式，中文推导写explanation'},tolerance=S,unit=S,unit_aliases=arr(obj(unit=S,multiplier=S)))
    ordering=obj(**base,type=enum(['ordering']),items=arr(obj(id=S,text=S),minItems=2),before=arr(arr(S,minItems=2,maxItems=2)),reference_order=arr(S,minItems=2))
    code=obj(**base,type=enum(['code']),language=S,runner_id=S,starter_code=S,reference_code=S,variant_id=S)
    recall=obj(**base,type=enum(['recall']),reference_answer=S)
    known_terms=[t['id']for t in packet.get('registered_terms',[])if t.get('id')]
    term_questions={**questions, 'minItems':1} if role=='answer' and qids else questions
    term=term_schema(known_terms, term_questions)
    fields={'writing_version':enum([packet.get('writing_version')or __import__('reader_prompts').WRITING_VERSION]),'plan':plan,'terms':arr(term),
            'quizzes':arr({'anyOf':[choice,numeric,ordering,code,recall]},minItems=1),'evidence_gaps':arr(S),
            'generated_assets':arr(obj(path=S,alt=S,question_ids={**questions,'minItems':1,'maxItems':1}
                                       if role=='answer' else questions,after_part_id=ID))}
    if packet.get('reader_analysis'):
        from reader_analysis import required_checks, writer_check_ids
        analysis=packet['reader_analysis']
        checks=required_checks(analysis)
        points=writer_check_ids(analysis)
        reasoning=[key for key in points if checks[key][1].get('kind')=='reasoning']
        other=[key for key in points if key not in reasoning]
        forms=[]
        if other:forms.append(obj(addresses=enum(other),part_id=ID,quote=S))
        if reasoning:forms.append(obj(addresses=enum(reasoning),part_id=ID,premise_quote=S,mechanism_quote=S,result_quote=S))
        fields['reader_checks']=arr({'anyOf':forms}) if forms else arr(S,maxItems=0)
        visual_ids=[v['id'] for item in analysis['items'] for v in item.get('visuals',[])]
        fields['visual_checks']=arr(obj(visual_id=enum(visual_ids),part_id=ID)) if visual_ids else arr(S,maxItems=0)
        xy=arr({'type':'number'},minItems=2,maxItems=2)
        point=obj(label=S,at=xy,style=enum(['base','new','guide']))
        line=obj(points=arr(xy,minItems=2),label=S,style=enum(['base','new','remove','guide']))
        panel=obj(points=arr(point,minItems=1),lines=arr(line))
        fields['coordinate_figures']=arr(obj(id=ID,panels=arr(panel,minItems=1,maxItems=12)))
    if role=='lesson':
        part=obj(id=ID,unit_ids=arr(ID,minItems=1),source_ids=arr(source),source_spans=arr(obj(first_source_id=source,last_source_id=source)),heading=S,premise=S,question=S,markdown=S)
        from reader_connected import START_TITLES, FIRST_START_TITLE, END_TITLES
        start=obj(role=enum(['previous','current']),heading=enum([*START_TITLES.values(),FIRST_START_TITLE]),markdown=S)
        following=obj(role=enum(['next']),heading=enum([END_TITLES['next']]),markdown=S)
        recap=obj(role=enum(['recap']),heading=enum([END_TITLES['recap']]),bullets=arr(obj(part_id=S,context=S,problem=S,claim=S,reason=S,evidence=S,implication=S)))
        framing=obj(start=arr(start,minItems=1,maxItems=2),map_heading=enum(['阅读流程']),end=arr({'anyOf':[recap,following]},minItems=1,maxItems=2))
        facts=packet.get('required_facts',[])
        evidence=obj(facts=arr(obj(ids=arr(enum([f['id']for f in facts])),part_id=S,quote=S))if facts else arr(S,maxItems=0),prerequisites=arr(obj(term=S,part_id=S,quote=S)),transitions=arr(obj(part_id=S,previous_part_id=S,quote=S,continuation=S)))
        fields['explanation']=obj(id=S,title=S,framing=framing,parts=arr(part,minItems=1),evidence=evidence)
    else:
        part=obj(id=ID,unit_ids=arr(ID,minItems=1),question_span=S,markdown=S)
        fields['answers']=arr(obj(question_id=enum(qids),question_version=I,parts=arr(part,minItems=1)),minItems=len(qids),maxItems=len(qids))
    return obj(**fields)
