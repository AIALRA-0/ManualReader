"""验证正文关键动作和目录标题都实际落到了读者能读到的位置。"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_analysis import validate_writing_evidence
from reader_lesson_checks import validate as validate_lesson
from reader_outline import edit_nodes
from reader_prompts import WRITING_VERSION
from reader_workspace import Workspace


def test_removing_the_actual_operation_rejects_the_draft():
    analysis={'mode':'answer','items':[{'task_id':'q','gaps':[{'id':'step','kind':'reasoning'}],'helpful_extras':[]}]}
    premise='槽位零当前存着七。'
    mechanism='读取逻辑固定从槽位零取数。'
    result='两次读取都拿到七。'
    body={'id':'p','markdown':premise+mechanism+result}
    proof={'addresses':'step','part_id':'p','premise_quote':premise,'mechanism_quote':mechanism,'result_quote':result}
    output={'writing_version':WRITING_VERSION,'answers':[{'question_id':'q','parts':[body]}],'reader_checks':[proof]}
    validate_writing_evidence(output,analysis)
    body['markdown']=premise+result
    with pytest.raises(ValueError,match='mechanism_quote'):
        validate_writing_evidence(output,analysis)


def test_transition_must_also_appear_in_the_next_part_opening():
    previous='我们先算出输出电压是六伏。'
    current='用刚算出的六伏作基准，再接上负载。'
    latter={'id':'second','markdown':current+'\n\n我们现在量同一对点。'}
    draft={'parts':[{'id':'first','markdown':previous},latter],
           'evidence':{'transitions':[{'part_id':'second','previous_part_id':'first','quote':previous,'continuation':current}]}}
    validate_lesson(draft,[],require_transitions=True,require_continuation=True)
    latter['markdown']='先介绍电源的历史。\n\n'+current
    with pytest.raises(ValueError,match='下一部分开头'):
        validate_lesson(draft,[],require_transitions=True,require_continuation=True)


@pytest.mark.parametrize('renamed',[False,True])
def test_chapter_title_is_saved_with_explanation_and_a_manual_name_takes_precedence(tmp_path,renamed):
    store=Workspace(tmp_path)
    material={'id':'fixture','title':'测试','kind':'markdown','passages':[],'outline':[],'pages':{}}
    nodes=[{'id':'first','title':'第 5 题','title_provisional':True,'number':'0','source_links':[]}]
    if renamed:
        nodes=edit_nodes(nodes,{'actions':[{'action':'update','id':'first','changes':{'title':'我自己的标题'}}]})
    value=store.read()
    value['learning_outline']=nodes
    store.write(value)
    generated='两条路线怎样共用一段线'
    store.explain({'id':'lesson','title':generated,'learning_node_id':'first','blocks':[{'id':'p','markdown':'两条路线先走同一段，再分开。'}]},
                  material,finalize_chapter_title=True)
    saved=Workspace(tmp_path).read()
    chapter,=saved['learning_outline']
    assert chapter['id']=='first' and not chapter.get('title_provisional')
    assert chapter['title']==saved['explanations'][0]['title']==('我自己的标题' if renamed else generated)
