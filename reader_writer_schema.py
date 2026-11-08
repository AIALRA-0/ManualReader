"""约束写回字段及真实编号，保留既有的选择、计算、排序和编程题型。"""
import json

S={'type':'string'};I={'type':'integer'}
ID={'type':'string','pattern':'^[A-Za-z0-9_-]{1,70}$'}
def obj(**fields):
    # 每个字段独立，避免为编号加枚举时连带限制正文字符串。
    return json.loads(json.dumps({'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}))
def arr(item,**limits):return {'type':'array','items':item,**limits}
def enum(values):return {'type':'string','enum':list(dict.fromkeys(values))}if values else S

def output_schema(packet):
    role=packet.get('mode','answer');sources=[s['id']for key in('sources','reference_blocks')for s in packet.get(key,[])];qids=[q['id']for q in packet.get('questions',[])]
    source=enum(sources);questions=arr(enum(qids))if qids else arr(S,maxItems=0)
    dependency={**S,'description':'只填此前unit的id；不在编号后追加说明；无前置时为空数组'}
    plan=obj(target=S,direct_answer=S,units=arr(obj(id=ID,say=S,needs=arr(dependency),source_refs=arr(obj(source_id=source,quote=S))),minItems=1),order=arr(ID,minItems=1),stop_when=S)
    if role=='lesson'and packet.get('source_groups'):
        fields={g['id']:obj(unit_id=S,part_id=S,quote=S)for g in packet['source_groups']}
        plan['properties']['coverage']=obj(**fields);plan['required'].append('coverage')
    placement=obj(kind=enum([role]),question_id=enum(qids)if role=='answer'else{'type':'null'},after_part_id=S)
    base={'placement':placement,'question_ids':questions,'prompt':S,'explanation':S}
    choice=obj(**base,type=enum(['choice','true_false']),options=arr(S,minItems=2,maxItems=6),correct_index=I,option_explanations=arr(S,minItems=2,maxItems=6))
    numeric=obj(**base,type=enum(['numeric']),expected={**S,'description':'仅数字、小数或四则运算式'},calculation={**S,'description':'用数字和+-*/()写可执行算式，中文推导写explanation'},tolerance=S,unit=S,unit_aliases=arr(obj(unit=S,multiplier=S)))
    ordering=obj(**base,type=enum(['ordering']),items=arr(obj(id=S,text=S),minItems=2),before=arr(arr(S,minItems=2,maxItems=2)),reference_order=arr(S,minItems=2))
    code=obj(**base,type=enum(['code']),language=S,runner_id=S,starter_code=S,reference_code=S,variant_id=S)
    recall=obj(**base,type=enum(['recall']),reference_answer=S)
    known_terms=[t['id']for t in packet.get('registered_terms',[])if t.get('id')]
    term=obj(term_id={'anyOf':[enum(known_terms),{'type':'null'}]}if known_terms else{'type':'null'},name=S,professional_definition=S,intuitive_explanation=S,question_ids=questions,
             action={'anyOf':[enum(['revise']),{'type':'null'}]},expected_version={'anyOf':[I,{'type':'null'}]},case_exceptions=arr(S),english_case_exception={'type':'boolean'})
    fields={'writing_version':enum([packet.get('writing_version')or __import__('reader_prompts').WRITING_VERSION]),'plan':plan,'terms':arr(term),
            'quizzes':arr({'anyOf':[choice,numeric,ordering,code,recall]},minItems=1),'evidence_gaps':arr(S),
            'generated_assets':arr(obj(path=S,alt=S,question_ids=questions,after_part_id={'anyOf':[S,{'type':'null'}]}))}
    if role=='lesson':
        part=obj(id=ID,unit_ids=arr(ID,minItems=1),source_ids=arr(source),source_spans=arr(obj(first_source_id=source,last_source_id=source)),heading=S,premise=S,question=S,markdown=S)
        lead=obj(role=enum(['previous','current','next']),heading=S,markdown=S)
        recap=obj(role=enum(['recap']),heading=S,bullets=arr(obj(part_id=S,context=S,problem=S,claim=S,reason=S,evidence=S,implication=S)))
        framing=obj(start=arr(lead,minItems=1,maxItems=2),map_heading=S,end=arr({'anyOf':[recap,lead]},minItems=1,maxItems=2))
        facts=packet.get('required_facts',[])
        evidence=obj(facts=arr(obj(ids=arr(enum([f['id']for f in facts])),part_id=S,quote=S))if facts else arr(S,maxItems=0),prerequisites=arr(obj(term=S,part_id=S,quote=S)),transitions=arr(obj(part_id=S,previous_part_id=S,quote=S)))
        fields['explanation']=obj(id=S,title=S,framing=framing,parts=arr(part,minItems=1),evidence=evidence)
    else:
        part=obj(id=ID,unit_ids=arr(ID,minItems=1),question_span=S,markdown=S)
        fields['answers']=arr(obj(question_id=enum(qids),question_version=I,parts=arr(part,minItems=1)),minItems=len(qids),maxItems=len(qids))
    return obj(**fields)
