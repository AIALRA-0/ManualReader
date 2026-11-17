"""STEP-02 tests exercise the frozen contract, source trust and single-question boundaries."""

import copy

import json

from pathlib import Path


import pytest

from manualreader.stages.step02.card import (CARD_KEYS, MATERIAL_KEYS, MODES, POLICY_REFS, assemble, link_sources,
                                model_schema, policies, prompt, render, rules)



from manualreader.stages.step02.read_tool import read as tool_read

from manualreader.stages.step02.original import inventory, pinned_bytes, read_region, view







HERE = Path(__file__).parent/'fixtures'

CARD = json.loads((HERE/'golden-card.json').read_bytes())

FIELDS = ('project','chapter','versions','progress','previous','custom_instruction')

@pytest.fixture()
def example():
    files = [{'file_id': entry['file_id'], 'relative_path': entry['relative_path'],
               'progress_pct': entry['progress_before_pct']} for entry in CARD['material_plan']]
    fid = files[0]['file_id']
    context = {'mode':'NORMAL', 'fixed':{k:copy.deepcopy(CARD[k]) for k in FIELDS},
               'approved_styles':copy.deepcopy(CARD['question_styles']), 'files':files,
               'unprocessed_questions':[], 'added_rounds':{fid:1}, 'directory':[], 'scope_hints':{}}
    catalog = {fid:{'type':'pdf_page', 'file_id':fid, 'positions':list(range(1,25)),
                    'headings':[], 'available_image_pages':[9,10]}}
    draft = {'question_styles':[], 'goals':copy.deepcopy(CARD['goals']),
             'material_plan':[{k:copy.deepcopy(r[k]) for k in ('file_id','description','source_ranges')}
                              for r in CARD['material_plan']]}
    return context,catalog,draft,fid

def test_frozen_normal_json_order_markdown_and_card(example):
    ctx,catalog,draft,fid=example
    card, notes=assemble(ctx,catalog,draft)
    assert tuple(card)==CARD_KEYS
    assert tuple(card['material_plan'][0])==MATERIAL_KEYS
    assert card['material_plan'][0]['source_ids']==[]
    assert card['material_plan'][0]['source_ranges']==CARD['material_plan'][0]['source_ranges']
    assert card['previous']==CARD['previous']
    assert len(card['previous']['recaps'])==6
    assert card['question_styles']==CARD['question_styles']
    assert len(card['question_styles'])==4
    assert tuple(map(len,card['goals'].values()))==(6,13)
    assert card['policy_refs']==POLICY_REFS
    assert not notes
    # Export an already approved card without reassigning any identity or wording.
    assert len(CARD['material_plan'][0]['source_ids'])==38
    assert render(CARD,policies())==(HERE/'golden-card.md').read_text('utf8')

def test_frozen_policies_reject_extra_rule_and_reordering():
    value=policies()
    assert tuple(map(len,(value['writing_preferences'],value['format_rules'])))==(5,12)
    altered=copy.deepcopy(value);altered['writing_preferences'].append({'rule_id':'active-visualization'})
    with pytest.raises(ValueError,match='规则版本'):
        rules(altered)
    value['format_rules'].reverse()
    with pytest.raises(ValueError,match='规则版本'):
        rules(value)

@pytest.mark.parametrize('mode',MODES)
def test_system_prompts_independent_and_model_schema_minimal(mode):
    assert len({prompt(mode) for mode in MODES})==4
    assert b'goals.reader' in prompt(mode) and b'goals.teacher' in prompt(mode)
    schema=model_schema(['file'],['qref-1'],mode)
    assert tuple(schema['properties'])==('question_styles','goals','material_plan')
    assert set(schema['properties']['material_plan']['items']['properties'])=={'file_id','description','source_ranges'}
    assert 'source_ids' not in str(schema)

@pytest.mark.parametrize('field',('made_up_reference','domain_detail','unknown_material','duplicate_file','bad_range','extra_identity','no_goals'))
def test_output_rejects_unknown_or_malformed_facts(example,field):
    ctx,catalog,draft,fid=example
    ctx['unprocessed_questions']=[{'ref':'qref-1','text':'这个数怎么得到','question_id':'q-real'}]
    if field in ('made_up_reference','domain_detail'):
        draft['question_styles']=[{'pattern':'这个数是怎样得到的', 'explanation_need':
                                  '复算 Q 中的数字' if field=='domain_detail' else '说明计算的依据',
                                  'evidence_refs':['qref-9' if field=='made_up_reference' else 'qref-1']}]
    elif field=='unknown_material':draft['material_plan'][0]['file_id']='fake'
    elif field=='duplicate_file':draft['material_plan']*=2
    elif field=='bad_range':draft['material_plan'][0]['source_ranges'][0]['end']=400
    elif field=='extra_identity':draft['mode']='OTHER'
    else:draft['goals']['reader']=[]
    with pytest.raises(ValueError):
        assemble(ctx,catalog,draft)

def test_question_style_keeps_real_ids_and_can_return_no_new_styles(example):
    ctx,catalog,draft,fid=example
    ctx['unprocessed_questions']=[{'ref':'qref-1','question_id':'q-real','text':'这个数字从哪里来'}]
    baseline,_=assemble(ctx,catalog,copy.deepcopy(draft))
    assert baseline['question_styles']==CARD['question_styles']
    draft['question_styles']=[{'pattern':'这个数字从哪里来','explanation_need':'补足数字的来源和计算',
                              'evidence_refs':['qref-1']}]
    card,_=assemble(ctx,catalog,draft)
    assert card['question_styles'][-1]['evidence_question_ids']==['q-real']
    assert card['question_styles'][:-1]==CARD['question_styles']

def test_source_index_missing_then_link_never_changes_original_ranges(example):
    ctx,catalog,draft,fid=example
    card,_=assemble(ctx,catalog,draft)
    no_index,notes=link_sources(card,{},catalog)
    assert no_index==card and notes[0]['unlinked_positions']==list(range(9,15))
    single={'id':'source-real', 'position':{'type':'pdf_page','start':10,'end':10}}
    linked,notes=link_sources(card,{fid:{'verified':True,'rows':[single]}},catalog)
    assert linked['material_plan'][0]['source_ids']==['source-real']
    assert linked['material_plan'][0]['source_ranges']==card['material_plan'][0]['source_ranges']
    assert notes[0]['index_status']=='linked_partial'
    assert link_sources(card,{fid:{'verified':False,'rows':[single]}},catalog)[0]==no_index

def test_cross_file_and_open_locator_are_legitimate(example):
    ctx,catalog,draft,fid=example
    ctx['files'].append({'file_id':'extra','relative_path':'sources/t.txt','progress_pct':30})
    ctx['added_rounds']['extra']=2
    catalog['extra']={'file_id':'extra','type':'text_line','positions':[1,2]}
    draft['material_plan'].append({'file_id':'extra','description':'项目附录', 'source_ranges':[]})
    card,notes=assemble(ctx,catalog,draft)
    assert len(card['material_plan'])==2 and notes[-1]['reason']=='no_confirmed_locator'
    assert card['material_plan'][1]['added_round']==2
    assert card['material_plan'][1]['progress_before_pct']==30

def test_fresh_original_text_pdf_html_subtitle_and_image(tmp_path):
    import fitz
    doc=fitz.open()
    page=doc.new_page()
    page.insert_text((50,50),'PDF range for ground truth')
    pdf=doc.tobytes()
    assert view(pdf,'.pdf')[1][0]['text'].strip()=='PDF range for ground truth'
    kind,rows=view(b'<html><body><h2>Title</h2><table><tr><td>A</td><td>B</td></tr></table></body></html>','.html')
    assert kind=='html_dom_unit' and rows[1]['text']=='A | B'
    kind,rows=view(b'{"body":[{"sid":7,"from":0.1,"to":0.5,"content":"real"}]}','.json')
    assert kind=='subtitle_sid' and rows[0]['time']==[.1,.5] and rows[0]['n']==7
    assert view(b'if True:\n    return', '.py')[1][1]['text']=='    return'
    (tmp_path/'a.pdf').write_bytes(pdf)
    from manualreader.stages.step01.scan import fingerprint
    f={**fingerprint(tmp_path,'a.pdf'),'file_id':'id','identity_pending':False,'file_revision':1}
    assert inventory(tmp_path,f)['positions']==[1]
    result=read_region(tmp_path,f,1,1,True)
    assert result['image'].startswith('iVBOR')
    with pytest.raises(ValueError,match='起点'):
        read_region(tmp_path,f,1,2)

def test_m1_raw_access_rejects_same_size_change(tmp_path):
    import os
    from manualreader.stages.step01.scan import fingerprint
    p=tmp_path/'original.md';p.write_bytes(b'abc')
    row={**fingerprint(tmp_path,p.name),'identity_pending':False}
    info=p.stat();p.write_bytes(b'def')
    os.utime(p,ns=(info.st_atime_ns,info.st_mtime_ns))
    with pytest.raises(ValueError,match='M1'):
        pinned_bytes(tmp_path,row)

def test_mcp_only_reads_whitelist_and_logs_actual_rows(tmp_path):
    store={'f':{'type':'pdf_page','rows':{2:{'n':2,'text':'text'}},'images':{}}}
    trace=tmp_path/'trace.jsonl'
    response=tool_read(store,{'requests':[{'file_id':'../secret','start':2,'end':2},
                                          {'file_id':'f','start':2,'end':2}]},trace)
    assert 'access_blocked' in response['content'][0]['text']
    assert 'text' in response['content'][1]['text']
    stdout=[json.loads(r) for r in trace.read_text('utf8').splitlines()]
    assert stdout[1]['actual_positions']==[2] and stdout[0]['actual_positions']==[]

def test_no_question_references_requires_empty_styles():
    schema=model_schema(['f'],[],'ANSWER')
    assert schema['properties']['question_styles']['maxItems']==0
