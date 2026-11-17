from hashlib import sha256


from manualreader.steps.events import RunJournal
from manualreader.steps.step_13_terminology import bind
from manualreader.steps.step_15_chapter_guidance import RECAP_FIELDS, recap_groups
from manualreader.stages.finish.provider import frozen_prompts


def test_file_change_invalidates_only_the_work_that_used_the_old_source(tmp_path):
    journal = RunJournal(tmp_path, 'test', {'demo': {'project_id':'project'}})
    gone = journal.emit('demo',3,'source_ready',unit_id='s1',source_version='old',value='old')
    stay = journal.emit('demo',3,'source_ready',unit_id='s2',source_version='good',value='good')
    child = journal.emit('demo',10,'translation_ready',unit_id='s1',upstream=(gone,),value='译文')
    sibling = journal.emit('demo',10,'translation_ready',unit_id='s2',upstream=(stay,),value='另一译文')
    assert journal.invalidate('demo',{'old'}) == {gone,child}
    for event in journal.nodes.values():
        assert sha256((journal.root/event['artifact']['path']).read_bytes()).hexdigest()==event['artifact']['sha256']
    assert journal.nodes[sibling]['status']=='complete'


def test_actual_new_prompts_load_separately_at_dispatch():
    prompts=frozen_prompts()
    assert all(prompts[part].startswith(heading.encode()) for part,heading in (
        ('F2','# STEP-13'),('F3','# STEP-14'),('F1','# STEP-15'),('B1','# STEP-10')))
    assert prompts['F1']!=prompts['F2']


def test_reuse_native_term_id_and_explanations_when_binding_to_body():
    saved={'id':'t-existing','name':'并行读取','professional_definition':'已有定义','intuitive_explanation':'已有解释'}
    value=bind({'known_terms':[saved],'terms':[{'action':'reuse_existing','term_id':'t-existing',
                 'name':'并行读取','source_ids':['s1','invented']}]},
               {'blocks':[{'block_id':'b1','part_id':'p1','surface':'body','markdown':'这里用并行读取处理原件'}],
                'source_edges':[{'source_id':'s1','block_id':'b1'}]})
    assert (value[0]['reuse_id'],value[0]['definition'],value[0]['intuition'])==('t-existing','已有定义','已有解释')
    assert value[0]['block_ids']==['b1'] and value[0]['source_ids']==['s1']


def test_recap_stays_in_real_part_order_and_skips_empty_slots():
    def complete(pid):
        return {'part_id':pid,**dict.fromkeys(RECAP_FIELDS,'有清楚的来源')}
    rows={'part_recaps':[complete('p2'),{'part_id':'empty','context':'还有缺项'},complete('p1')]}
    parts={k:{'part_id':k,'title':title} for k,title in (('p1','第一节'),('empty','第二节'),('p2','第三节'))}
    recaps=list(recap_groups(rows,parts))
    assert [title for title,_ in recaps]==['第一节','第二节','第三节']
    assert recaps[1][1]=={'context':'还有缺项'}


def test_translation_layout_follows_the_original_boundary_without_splitting_a_shared_sentence():
    from manualreader.stages.finish.preview import layout_translation

    sources={'a':{'heading':True,'level':2},'b':{},'c':{'kind':'equation'},'d':{},'e':{}}
    archived=[{'kind':'paragraph','segments':[
        {'source_ids':['a'],'text':'材料标题'},
        {'source_ids':['b'],'text':'第一段正文'},
        {'source_ids':['c'],'text':r'\[x=2\]'}]},
        {'kind':'paragraph','segments':[{'source_ids':['d','e'],'text':'共享的一句译文'}]}]
    originals=[{'group_id':sid,'kind':'paragraph','source_ids':[sid]} for sid in sources]
    rows=layout_translation(archived,originals,sources)
    assert [r['kind'] for r in rows]==['heading','paragraph','equation','paragraph']
    assert rows[0]['segments'][0]['level']==2
    assert rows[-1]['segments'][0]['source_ids']==['d','e']
    assert [s['text'] for r in rows for s in r['segments']]==[
        s['text'] for r in archived for s in r['segments']]


def test_journal_can_resume_from_interrupted_append(tmp_path):
    specs={'demo': {'project_id':'project'}}
    journal=RunJournal(tmp_path,'run',specs)
    first=journal.emit('demo',3,'source_ready',value='原件')
    with (journal.root/'events.jsonl').open('ab') as stream:
        stream.write(b'{"event_id": "incomplete')
    resumed=RunJournal(tmp_path,'run',specs)
    assert list(resumed.nodes)==[first]
    second=resumed.emit('demo',10,'translation_ready',upstream=[first],value='译文')
    assert list(RunJournal(tmp_path,'run',specs).nodes)==[first,second]


def test_delivery_recovers_after_multiple_incomplete_model_responses(tmp_path):
    import asyncio
    from manualreader.stages.step05.inference import read,write
    from manualreader.steps.step_12_resource_recovery import missing_delivery,retry_delivery

    path=tmp_path/'resource-explanations.json'
    write(path,{'coverage_by_resource':[
        {'resource_id':'a','eligible':True,'status':'partial','group_id':'a-group'},
        {'resource_id':'b','eligible':True,'status':'missing'},
        {'resource_id':'c','eligible':True,'status':'missing'}],
        'groups':[{'group_id':'a-group','resource_ids':['a']}]})
    waits=[]
    requested=[]
    async def pause(delay):
        waits.append(delay)
    async def refresh(cycle):
        value=read(path)
        requested.append(missing_delivery(value))
        if cycle in (2,5):
            rid='b' if cycle==2 else 'c'
            row=next(r for r in value['coverage_by_resource'] if r['resource_id']==rid)
            row.update(group_id=rid+'-group',status='explained')
            value['groups'].append({'group_id':row['group_id'],'resource_ids':[rid]})
            write(path,value)
    asyncio.run(retry_delivery(tmp_path,refresh,pause=pause))
    assert waits==[2,4,8,16,32]
    assert requested==[{'b','c'},{'b','c'},{'c'},{'c'},{'c'}]
    assert read(path)['coverage_by_resource'][0]['status']=='partial'


def test_embedded_image_progress_requires_an_unambiguous_verified_hash():
    from base64 import b64encode
    from manualreader.stages.finish.coverage import embedded_image_ranges

    raw=b'verified image bytes'
    payload=b64encode(raw)
    real=b'<img alt="source" src="data:image/png;base64,'+payload+b'">'
    unrelated=real.replace(b' src=',b' data-src=')
    found=embedded_image_ranges(unrelated+real)
    start,end=found[sha256(raw).hexdigest()]
    assert start>len(unrelated)
    assert (unrelated+real)[start:end]==payload
    assert not embedded_image_ranges(real+real)



def test_pdf_progress_maps_only_streams_for_verified_text_pages():
    import fitz
    from manualreader.stages.finish.coverage import directly_explained_ranges

    document=fitz.open()
    for text in ('A direct passage that is covered','A separate passage that is pending'):
        page=document.new_page()
        page.insert_text((52,48),text)
    raw=document.tobytes(deflate=True)
    starts=[document.xref_stream_raw(page.get_contents()[0]) for page in document]
    document.close()
    first={'id':'first','en':'A direct passage that is covered',
           'position':{'type':'pdf_page','start':1,'end':1}}
    spans=directly_explained_ranges(raw,'.pdf',[first])
    assert list(spans)==['first']
    assert {raw[a:b] for a,b in spans['first']}=={starts[0]}
    assert starts[1] not in {raw[a:b] for a,b in spans['first']}
    assert not directly_explained_ranges(raw,'.pdf',[{**first,'en':'wrong version'}])


def test_docx_progress_requires_the_entire_compressed_member():
    from io import BytesIO
    from zipfile import ZIP_DEFLATED, ZipFile
    from manualreader.stages.finish.coverage import directly_explained_ranges

    stream=BytesIO()
    with ZipFile(stream,'w',compression=ZIP_DEFLATED) as out:
        out.writestr('word/document.xml',
                     '<w:document xmlns:w="urn:test"><w:body><w:p><w:r><w:t>Covered first unit</w:t>'
                     '</w:r></w:p><w:p><w:r><w:t>Covered second unit</w:t></w:r></w:p></w:body></w:document>')
        out.writestr('extra.bin',b'Not explained')
        size=out.getinfo('word/document.xml').compress_size
    rows=[{'id':str(i),'en':text,'position':{'type':'document_paragraph','start':i,'end':i}}
          for i,text in enumerate(('Covered first unit','Covered second unit'),1)]
    raw=stream.getvalue()
    assert not directly_explained_ranges(raw,'.docx',rows[:1])
    spans=directly_explained_ranges(raw,'.docx',rows)
    assert list(spans)==['1','2'] and spans['1']==spans['2']
    start,end=next(iter(spans['1']))
    assert end-start==size


def test_delivery_compares_the_scanned_project_version_with_the_sealed_preview():
    from manualreader.stages.finish.commit import verify_commit_sources

    preview={'read_only_source_versions':{'selected':'original'}}
    files=[{'file_id':'selected','status':'active','content_sha256':'latest'}]
    import pytest
    with pytest.raises(ValueError,match='版本不同'):
        verify_commit_sources(preview,{'files':files})
    files[0]['content_sha256']='original'
    verify_commit_sources(preview,{'files':files+[{'file_id':'another','status':'active','content_sha256':'other'}]})
