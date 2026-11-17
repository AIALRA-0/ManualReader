"""Frozen contracts for the final processing model requests."""

from ..step05.contracts import arr, enum, obj, TEXT

LIST = arr(TEXT)
NULLABLE = {'type':['string','null']}
INTEGER = {'type':'integer'}
COMPONENT_STATUS = enum(('known','confirmed','inferred','unknown','uncertain','unreadable'))
GROUP_STATUS = enum(('explained','partial','needs_review'))
MATH = obj(group_id=TEXT,resource_ids=LIST,kind=enum(('math',)),overview=TEXT,summary=TEXT,
           components=arr(obj(component_id=TEXT,raw=TEXT,meaning=TEXT,certainty=COMPONENT_STATUS)),
           composition=LIST,conditions=LIST,status=GROUP_STATUS)
VISUAL = obj(group_id=TEXT,resource_ids=LIST,kind=TEXT,overview=TEXT,summary=TEXT,
             components=arr(obj(component_id=TEXT,raw=TEXT,meaning=TEXT,
                                certainty=COMPONENT_STATUS,location_hint=TEXT)),
             relations=LIST,unreadable=LIST,status=GROUP_STATUS)
LABEL = obj(resource_id=TEXT,label_id=TEXT,original=TEXT,location_hint=TEXT,certainty=COMPONENT_STATUS)
TERM = obj(action=enum(('reuse_existing','new')),reuse_id=NULLABLE,name=TEXT,aliases=LIST,
           definition=TEXT,intuition=TEXT,chinese_name=TEXT,english_name=TEXT,abbreviation=TEXT,part_ids=LIST,block_ids=LIST,source_ids=LIST,
           status=enum(('candidate','reused','needs_review')))
GUIDE = obj(chapter_intro=TEXT,reading_flow=arr(obj(part_id=TEXT,title=TEXT,focus=TEXT)),
            part_recaps=arr(obj(part_id=TEXT,background=TEXT,question=TEXT,conclusion=TEXT,
                                causes=TEXT,evidence=TEXT,application=TEXT,synthesis=TEXT,next_transition=TEXT,status=TEXT)),
            chapter_outro=TEXT,uncertainties=LIST)
PLACEMENT = obj(kind=enum(('lesson','answer')),after_part_id=TEXT,question_id=NULLABLE)
QUIZ_CONTEXT = dict(placement=PLACEMENT,prompt=TEXT,explanation=TEXT,block_ids=LIST,source_ids=LIST)
QUESTION = {'anyOf':[
    obj(**QUIZ_CONTEXT,type=enum(('choice','true_false')),options=LIST,correct_index=INTEGER,option_explanations=LIST),
    obj(**QUIZ_CONTEXT,type=enum(('numeric',)),expected=TEXT,tolerance=TEXT,unit=TEXT,calculation=TEXT,
        unit_aliases=arr(obj(unit=TEXT,multiplier=TEXT))),
    obj(**QUIZ_CONTEXT,type=enum(('ordering',)),items=arr(obj(id=TEXT,text=TEXT)),before=arr(LIST),reference_order=LIST),
    obj(**QUIZ_CONTEXT,type=enum(('code',)),language=TEXT,runner_id=TEXT,starter_code=TEXT,reference_code=TEXT,variant_id=TEXT),
    obj(**QUIZ_CONTEXT,type=enum(('recall',)),reference_answer=TEXT),
]}
GLOSSARY_TERM = obj(action=enum(('reuse_existing','new','needs_review')),term_id=NULLABLE,name=TEXT,aliases=LIST,
                    professional_definition=TEXT,intuitive_explanation=TEXT,part_ids=LIST,block_ids=LIST,source_ids=LIST,reason=TEXT)
CHAPTER_GUIDE = obj(chapter_intro=TEXT,reading_flow=arr(obj(part_id=TEXT,title=TEXT,focus=TEXT)),
    part_recaps=arr(obj(part_id=TEXT,context=TEXT,problem=TEXT,claim=TEXT,reason=TEXT,evidence=TEXT,implication=TEXT)),
    chapter_synthesis=TEXT,previous_transition=TEXT,next_transition=TEXT,
    ending_kind=enum(('next','open','complete')),uncertainties=LIST)
SCHEMAS={
    'A1':obj(groups=arr(MATH),unresolved_resource_ids=LIST),
    'A2':obj(groups=arr(VISUAL),verified_labels=arr(LABEL),unresolved_resource_ids=LIST),
    'B1':obj(groups=arr(obj(translation_group_id=TEXT,segments=arr(obj(
        source_id=TEXT,translation_id=TEXT,text=TEXT,
        relation=enum(('translated','preserved_math','identity')),
        status=enum(('complete','uncertain')))))),unresolved_source_ids=LIST),
    'B2':obj(translations=arr(obj(resource_id=TEXT,label_id=TEXT,original=TEXT,
                                  translated=TEXT,status=enum(('complete','uncertain','unreadable')))),
             unresolved_label_ids=LIST),
    'C1':obj(decisions=arr(obj(candidate_id=TEXT,action=enum(('keep','list','separate_paragraphs')),
                                item_spans=arr(obj(start=INTEGER,end=INTEGER)),reason=TEXT))),
    'D1':obj(proposals=arr(obj(start_block_id=TEXT,end_block_id=TEXT,title=TEXT,
                                action=enum(('split_part','add_heading','normalize_heading')),reason=TEXT))),
    'E1':obj(assignment_groups=arr(obj(source_ids=LIST,block_ids=LIST)),unmapped_source_ids=LIST,
             unmapped_block_ids=LIST),
    'F12':obj(guidance=GUIDE,terms=arr(TERM)),
    'F1':CHAPTER_GUIDE,
    'SOURCE_GAP':obj(items=arr(obj(
        source_id=TEXT,block_id=TEXT,
        disposition=enum(('already_explained','add','reference_only','unresolved')),
        original_excerpt=TEXT,body_excerpt=TEXT,addition=TEXT,reason=TEXT))),
    'TERM_NAME':obj(updates=arr(obj(identity=TEXT,name=TEXT,reason=TEXT)),unresolved=LIST),
    'F2':obj(terms=arr(GLOSSARY_TERM),unresolved_terms=LIST,
             planned_term_reviews=arr(obj(goal_id=TEXT,term_names=LIST,reason=TEXT))),
    'F3':obj(questions=arr(QUESTION),unresolved=LIST),
}
