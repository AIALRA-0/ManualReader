from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Lock
from unittest.mock import patch

import pytest

from reader_bridge import Bridge
from reader_contract import current_quizzes
from reader_generation import practice_issues,practice_placements
from reader_practice_upgrade import reconcile
from reader_writer_patch import apply
from reader_writer_transport import Gate


def exercise(identity, qid='q1', part='p1', selection=None):
    return {'id':identity,'question_ids':[qid],'prompt':'依次写入 5 和 9，先读哪个值？',
            'options':['5','9'],'correct_index':0,'selected':selection,
            'placement':{'kind':'answer','question_id':'q1','after_part_id':part}}


def test_one_pass_points_to_multiple_practice_failures():
    wrong=exercise('z1')
    wrong['question_ids'].append('q2')
    repeated=exercise('z2',part='p2')
    report=practice_issues([wrong,repeated],{('q1','p1'),('q1','p2'),('q2','p3')})
    assert len(report)==3
    assert 'question_ids' in report[0] and 'prompt' in report[1] and 'q2/p3' in report[2]


def test_migration_keeps_both_old_attempts_and_chooses_actual_owner():
    questions=[{'id':qid,'passage_id':'source','text':'先读谁','answer':'从队首取值',
                'answer_parts':[{'id':'p1','markdown':'读出队列最早存入的值'}]}
               for qid in ('q1','q2')]
    correct=exercise('z1',selection=0)
    duplicate=exercise('z2',qid='q2',selection=1)
    state={'rounds':[{'id':'r','questions':questions}],'quizzes':[duplicate,correct],
           'practice_book':{'attempts':[{'quiz_id':'z2','correct':False},{'quiz_id':'z1','correct':True}]},
           'notes':{'q1':'继续保留'},'read_passages':{'source':True}}
    before=deepcopy(state)
    material={'passages':[],'explanations':[]}
    owners,chosen=reconcile(state,material)
    assert set(owners)=={('answer','q1','p1'),('answer','q2','p1')}
    assert [quiz['id'] for quiz in chosen.values()]==['z1']
    assert duplicate['archived_duplicate_of']=='z1'
    assert current_quizzes(state['quizzes'])==[correct]
    for key in ('rounds','practice_book','notes','read_passages'):
        assert state[key]==before[key]
    assert [quiz['selected'] for quiz in state['quizzes']]==[1,0]
    first=deepcopy(state)
    reconcile(state,material)
    assert state==first


def test_legacy_overlay_does_not_reassign_explicit_exercises():
    material={'passages':[{'id':'e--p1','content_kind':'explanation'},
                          {'id':'e--p2','content_kind':'explanation'}],
              'explanations':[{'id':'e','passage_ids':['e--p1','e--p2'],
                               'practice_links':[{'quiz_id':'z','after_passage_id':'e--p2'}]}]}
    quiz={'id':'z','placement':{'kind':'lesson','explanation_id':'e','after_part_id':'p1','after_passage_id':'e--p1'}}
    assert practice_placements({'quizzes':[quiz]},material)['lesson']=={'e--p1':['z']}


def test_markdown_patch_preserves_newlines_and_sibling_parts():
    source={'answers':[{'question_id':'q1','parts':[{'id':'p1','markdown':'旧正文'}]},
                       {'question_id':'q2','parts':[{'id':'p1','markdown':'独立正文'}]}]}
    markdown='用下面的式子核对\n\n$$\na=2+3\n$$\n\n| 输入 | 结果 |\n| --- | --- |\n| 2+3 | 5 |'
    updated=apply(source,{'changes':[{'path':'/answers/0/parts/0/markdown','value':markdown}]})
    assert updated['answers'][0]['parts'][0]['markdown']==markdown
    assert updated['answers'][1]==source['answers'][1]
    with pytest.raises(ValueError):
        apply(source,{'changes':[{'path':'/answers/-1/parts/0/markdown','value':markdown}]})


def test_parallel_image_download_and_locked_telemetry(tmp_path):
    bridge=object.__new__(Bridge)
    bridge._image_lock=Lock()
    bridge._source_images={}
    calls=[]
    def download():
        calls.append(1)
        return '.png',b'png'
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert list(pool.map(lambda _:bridge.source_image('https://example.org/figure.png',download),range(60)))==[('.png',b'png')]*60
    assert calls==[1]
    gate=Gate({},'','',tmp_path)
    gate.receipts.append({'phase':'streaming'})
    with patch.object(Path,'replace',side_effect=PermissionError):
        gate.persist()
    gate.persist()
    assert (tmp_path/'transport.json').is_file()
