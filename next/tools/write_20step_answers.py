"""Put two new ANSWER outputs through the native isolated result writer."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys

from manualreader.stages.finish.preview import setup
from manualreader.artifacts import read_json as read, write_json as write


def actual_question(corpus,project,identity):
    record=next(row for row in read(corpus/'import-report.json').values() if row['project_id']==project)
    db,expected=next((Path(p),h) for p,h in record['preserved'].items() if Path(p).name=='state.sqlite3')
    if sha256(db.read_bytes()).hexdigest()!=expected:
        raise ValueError('原问题登记的数据库不是已核对版本')
    with sqlite3.connect(db.as_uri()+'?mode=ro&immutable=1',uri=True) as conn:
        state=json.loads(conn.execute('SELECT value FROM reader_state WHERE id=1').fetchone()[0])
    return next(q for row in state['rounds'] for q in row['questions'] if q['id']==identity)


def one(backend,root,corpus,sample,rec):
    pid='next-final-'+sample
    backend.CONTEXT.material_id=pid
    case=actual_question(corpus,rec['project_id'],rec['question_id'])
    input_card=read(root/'inputs'/sample/'task-card.json')
    if input_card['question']['id']!=case['id'] or input_card['question']['version']!=case['version']:
        raise ValueError('问题身份或版本与本轮任务不一致')
    active=backend.material(pid)
    anchor=next((p for p in active['passages'] if p['id']==case['passage_id']),None)
    if not anchor:
        target=case.get('target',{})
        excerpt=target.get('text') or target.get('original') or target.get('translation')
        exp_id,sep,block_id=case['passage_id'].partition('--')
        if not (excerpt and sep and exp_id and block_id):
            raise ValueError('原问题的实际节选没有可定位的原锚点')
        work=backend.workspace(pid)
        if any(row['id']==exp_id for row in work.read()['explanations']):
            raise ValueError('原问题锚点的同名章节已存在')
        work.explain({'id':exp_id,'title':'原问题选中的段落','writing_version':'external-author-v1',
                      'blocks':[{'id':block_id,'markdown':excerpt,'progress_exempt':True}]},active)
        active=backend.material(pid)
        anchor=next(p for p in active['passages'] if p['id']==case['passage_id'])
    identity=case['id']
    contents=root/'jobs'/sample/'body.final.md'
    answer=contents.read_text('utf8').strip()
    if not answer:
        raise ValueError('本轮没有有效的回答')
    def apply(state):
        row=next((r for r in state['rounds'] if any(q['id']==identity for q in r['questions'])),None)
        if row is None:
            pid=anchor['id']
            question={k:case[k] for k in ('id','original_text','text','version','target','parent_question_id') if k in case}
            question.update(passage_id=pid,sentence_id=None,answer=None,term_ids=[])
            row={'id':'r-next-'+identity,'passage_id':pid,'status':'pending','questions':[question],
                 'created_at':backend.core.stamp()}
            state['rounds'].append(row)
        record=backend.core.save_result(state,row['id'],{'answers':[{'question_id':identity,
               'question_version':case['version'],'text':answer,'term_ids':[]}],
               'terms':[],'quizzes':[],'writing_version':'external-author-v1'},actor='isolated-next-cold-answer',finish=False)
        result=next(q for q in record['questions'] if q['id']==identity)
        return {'sample':sample,'q_id':identity,'source_anchor':anchor['id'],
                'question_version':result['answered_question_version'],'generated_answer_sha256':sha256(contents.read_bytes()).hexdigest(),
                'native_answer_saved':bool(result.get('answer'))}
    return backend.core.mutate(apply)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('run',type=Path)
    p.add_argument('legacy',type=Path)
    p.add_argument('corpus',type=Path)
    a=p.parse_args()
    setup(a.run/'reader-isolated',a.legacy,0)
    sys.path.insert(0,str(a.legacy.resolve()))
    import reader_server
    c=read(a.run/'whiteboard-inputs.json')
    receipts={}
    for sample in c['deep_browser_samples']:
        if c['samples'][sample]['mode']=='ANSWER':
            try:
                receipts[sample]=one(reader_server,a.run,a.corpus,sample,c['samples'][sample])
            except (ValueError,OSError,KeyError,StopIteration,TypeError) as error:
                receipts[sample]={'error':str(error)}
    write(a.run/'native-answer-20step.json',receipts)


if __name__=='__main__':
    main()
