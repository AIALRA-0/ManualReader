from collections import Counter
from threading import Event
from time import sleep

from manualreader.stages.step05.anchors import anchors
from manualreader.stages.step05.contracts import additions, consolidate, digest, links
from manualreader.stages.step05.inference import PROMPTS, prompt, read, write
from manualreader.stages.step06.pipeline import JointJobs, shards
from manualreader.step56_server import downloads, render


def source(sid, *, file_id='f', page=1, text='first premise', role='body', units=()):
    return {'source_id':sid,'file_id':file_id,'position':{'type':'pdf_page','start':page,'end':page},
            'section_id':'s','kind':'paragraph','role':role,'text':text,'asset_refs':[],
            'registered_unit_ids':list(units),'refs':[],'time':None}


def setup(root, sample, rows, mode='NORMAL'):
    folder=root/'inputs'/sample
    draft='## 原稿\n\n这里保留一个完整公式。\n\n$$\nx+y=0\n\n  y=-x\n$$\n'.encode()
    card={'chapter':{'title':'一份草稿'},'context':{'selection':'用户所选内容'},'goals':{},
          'material_plan':[{'file_id':fid,'source_ids':[r['source_id'] for r in rows if r['file_id']==fid]}
                           for fid in dict.fromkeys(r['file_id'] for r in rows)]}
    write(folder/'task-card.json',card)
    write(folder/'source-index.json',{'sources':rows,'assets':{},'saved_chapters':[]})
    name='answer.md' if mode=='ANSWER' else 'chapter.md'
    (folder/name).write_bytes(draft)
    write(folder/'step04-run.json',{'fixture_based':False,'mode':mode,'output_file':name,
                                  'output_hash':digest(draft),'task_card_sha256':digest((folder/'task-card.json').read_bytes())})
    return folder,name,draft


def ready(jobs,sample):
    for _ in range(300):
        state=jobs.status(sample)
        if state['status'] not in ('queued','running'):
            return state
        sleep(.01)
    raise AssertionError('task did not finish')


def test_prompts_and_offsets_include_the_whole_formula():
    for stage,(_,_,expected) in PROMPTS.items():
        assert digest(prompt(stage))==expected
    chapter='标题\r\n\r\n$$\r\na=b\r\n\r\nc=d\r\n$$\r\n\r\n```md\r\nline\r\n\r\nlast\r\n```'
    rows=anchors(chapter)
    assert len(rows)==3
    assert all(chapter[r['start']:r['end']]==r['text'] and digest(r['text'])==r['content_sha256'] for r in rows)
    assert 'c=d' in rows[1]['text'] and 'last' in rows[2]['text']


def test_partition_keeps_registered_ids_across_multiple_files_and_ranges():
    rows=[source('a',page=4),source('b',page=4),source('c',page=11),
          source('d',file_id='g'), source('e',file_id='g',page=43)]
    partition=shards(rows,budget=390)
    assert [[r['source_id'] for r in group] for group in partition]==[['a','b'],['c'],['d','e']]


def test_links_keep_valid_pairs_and_reject_only_invalid_pairs():
    errors=[]
    rules=links([{'source_ids':['s','x','fabricated'],'body_anchor_ids':['a','b','invalid']}],
                {'s','x'},{'a','b'},errors)
    assert rules=={('s','a'),('s','b'),('x','a'),('x','b')}
    assert len(errors)==5


def test_verified_absorption_uses_sources_not_a_model_label():
    rows={r['source_id']:r for r in (
        source('parent',role='page_furniture',text='footer'),source('footer',role='page_furniture',text='footer'),
        source('core',role='body',text='core fact'),source('other',file_id='g',role='page_furniture',text='footer'))}
    candidates=[{'source_id':sid,'parent_source_id':'parent','evidence_kind':'verified_layout_duplicate'}
                for sid in ('footer','core','other')]
    report=consolidate(rows,[{'anchor_id':'a','text':'已讲内容'}],{('parent','a')},set(),[],candidates,'NORMAL')
    assert report['explanation_to_source']=={'a':['footer','parent']}
    assert report['substantively_linked_source_ids']==['parent']
    assert report['unresolved_source_ids']==['core','other']


def test_only_verified_asset_ids_can_register_and_duplicate_patches_remain():
    row={'anchor_id':'a','placement':'after','kind':'reuse_original','markdown':'![diagram](asset:asset-ok)',
         'source_ids':['s'],'asset_refs':['asset-ok']}
    invalid={**row,'markdown':'![fake](bad/figure.png)'}
    errors=[]
    left=additions([row,invalid],'06A',{'s'},{'a'},{'asset-ok':{}},errors)
    right=additions([row],'06B',{'s'},{'a'},{'asset-ok':{}},errors)
    assert len(errors)==1
    ledger=consolidate({'s':source('s')},[{'anchor_id':'a','text':'图说明'}],
                       set(),set(),left+right,[],'NORMAL')
    assert right[0]['duplicate_of']==left[0]['patch_id']
    assert len(ledger['source_to_explanation']['s'])==2


def test_gap_can_finish_before_independent_depth_and_preserves_original(tmp_path):
    src,name,draft=setup(tmp_path,'sample',[source('original'),source('gap',page=7)])
    calls=Counter()
    depth_started=Event()
    gap_finished=Event()
    def infer(folder,stage,packet,images,cli):
        calls[stage]+=1
        aid=packet['temporary_anchors'][1]['anchor_id']
        if stage=='06B':
            assert 'initial_links' not in packet
            depth_started.set()
            assert gap_finished.wait(5)
            reply={'additions':[]}
        elif stage=='05':
            assert depth_started.wait(5)
            reply={'links':[{'source_ids':['original'],'body_anchor_ids':[aid]}]}
        else:
            assert {row['source_id'] for row in packet['sources']}=={'gap'}
            reply={'additional_links':[], 'absorb_candidates':[],
                   'additions':[{'anchor_id':aid,'placement':'after','kind':'formula',
                                 'markdown':'$x+y=0$','source_ids':['gap'],'asset_refs':[]}]}
            gap_finished.set()
        return {'state':'complete','reply':reply}
    jobs=JointJobs(tmp_path,3,infer=infer,cli='fake')
    try:
        jobs.submit('sample')
        report=ready(jobs,'sample')
        assert report['status']=='complete'
        assert calls=={'05':1,'06A':1,'06B':1}
        assert (src/name).read_bytes()==draft
        assert jobs.submit('sample',resume=True)['status']=='complete'
        result=jobs.folder('sample')
        assert read(result/'step06-merged-mapping.json')['unresolved_source_ids']==[]
        title,html=render(src,result,report,'/step56/sample')
        assert title and '$x+y=0$' in html and '查看原稿位置' in html
        from io import BytesIO
        from zipfile import ZipFile
        with ZipFile(BytesIO(downloads(result,src))) as archive:
            assert archive.read(name)==draft
    finally:
        jobs.close()


def test_answer_without_sources_dispatches_only_06b(tmp_path):
    setup(tmp_path,'answer',[],'ANSWER')
    calls=[]
    def infer(folder,stage,packet,images,cli):
        calls.append(stage)
        assert packet['question_context']=={'selection':'用户所选内容'}
        return {'state':'complete','reply':{'additions':[]}}
    jobs=JointJobs(tmp_path,2,infer=infer,cli='fake')
    try:
        jobs.submit('answer')
        state=ready(jobs,'answer')
        assert state['status']=='complete' and calls==['06B']
        result=read(jobs.folder('answer')/'step06-merged-mapping.json')
        assert 'question_context' in result['explanation_dispositions'].values()
    finally:
        jobs.close()


def test_resume_keeps_provider_dispatched_outputs(tmp_path):
    from manualreader.stages.step05.inference import invoke
    saved=tmp_path/'sent'
    packet={'state':'complete','reply':{'links':[]}}
    write(saved/'outcome.json',packet)
    write(saved/'dispatch.json',{'stage':'05'})
    assert invoke(saved,'05',{}, {}, None)==packet
    pending=tmp_path/'pending'
    write(pending/'dispatch.json',{'stage':'05'})
    assert invoke(pending,'05',{}, {},None)['state']=='interrupted_after_dispatch'


def test_cancel_stops_waiting_calls_and_resume_reuses_completed_response(tmp_path):
    setup(tmp_path,'sample',[source('s')])
    running=Event()
    release=Event()
    sent=[]

    def infer(folder,stage,packet,images,cli):
        history=folder/'outcome.json'
        if history.exists():
            return read(history)
        sent.append(stage)
        if stage=='06B':
            running.set()
            assert release.wait(5)
            reply={'additions':[]}
        else:
            reply={'links':[{'source_ids':['s'],'body_anchor_ids':[packet['temporary_anchors'][1]['anchor_id']]}]}
        result={'state':'complete','reply':reply}
        write(history,result)
        return result

    jobs=JointJobs(tmp_path,1,infer=infer,cli='fake')
    try:
        jobs.submit('sample')
        assert running.wait(5)
        jobs.cancel('sample')
        release.set()
        assert ready(jobs,'sample')['status']=='cancelled'
        assert sent==['06B']
        for _ in range(50):
            if 'sample' not in jobs.cancelled:
                break
            sleep(.01)
        jobs.submit('sample',resume=True)
        assert ready(jobs,'sample')['status']=='complete'
        assert sent==['06B','05']
    finally:
        release.set()
        jobs.close()
