"""Project Next artifacts into the established Reader interface on a private localhost instance."""

from collections import defaultdict
from hashlib import sha256
from pathlib import PurePosixPath
import re
import shutil
from threading import Lock
from urllib.parse import quote
import os

from ..step03.cache import atomic
from manualreader.artifacts import read_json as read, write_json as write


EXPLANATION = 'postprocess-preview'
SAMPLE = re.compile(r'[a-z0-9][a-z0-9-]{0,79}\Z')
IDENTITY = re.compile(r'[A-Za-z0-9_-]{1,70}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
PDF_RENDER_LOCKS = defaultdict(Lock)
PROJECTION_VERSION = 'native-projection-v18'
PREVIEW_WRITING_VERSION = 'next-reader-preview-v1'


def optional(folder, name, default):
    path=folder/name
    return read(path) if path.is_file() else default


def records(value, *keys):
    if isinstance(value,list):
        return value
    if isinstance(value,dict):
        for key in keys:
            if isinstance(value.get(key),list):
                return value[key]
    return []


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def render_id(identifier):
    # The native Reader accepts ASCII IDs. Preserve the original Next ID in the sidecar.
    return identifier if IDENTITY.fullmatch(identifier) else 'next-'+sha256(identifier.encode()).hexdigest()[:40]


def coverage_statistics(source, links, edges, chapter_id, title, files, processor, totals):
    source_rows = source['sources']
    semantic = {edge['source_id'] for edge in edges if edge['relation_type']=='direct_semantic'}
    navigation = {edge['source_id'] for edge in edges} - semantic
    by_file = defaultdict(list)
    for row in source_rows:
        by_file[row['file_id']].append(row)

    def metrics(counts, direct, total, navigation_count):
        def share(done, units, label):
            return {'done':done,'total':units,'percent':100*done/units if units else None,
                    'status':('complete' if done==units else 'partial' if done else 'pending') if units else 'empty',
                    'label':label}
        translated = counts['translated']
        preserved = counts['preserved']
        return {
            'translation': share(counts['processed'],counts['total'],
                f"已处理 {counts['processed']}/{counts['total']} 个文本单元，其中翻译 {translated}、保留 {preserved}"),
            'explanation': share(direct,total,
                f'实质讲解 {direct}/{total} 个来源单元，另有 {navigation_count} 条只用于定位'),
        }

    translation = processor(source, links)
    file_metrics = {}
    for fid, rows in by_file.items():
        counts = processor({**source,'sources':rows},links)
        ids = {row['source_id'] for row in rows}
        file_metrics[fid] = metrics(counts,len(ids & semantic),len(ids),len(ids & navigation))

    failed = set(translation['failed_source_ids'])
    missing = {'translation':[], 'explanation':[]}
    for source_row in source_rows:
        sid = source_row['source_id']
        row = {'passage_id':sid,'file_name':files.get(source_row['file_id'],{}).get('name') or source_row['file_id'],
               'learning_node_id':chapter_id,'chapter':title,
               'excerpt':(source_row.get('text') or source_row.get('caption') or source_row.get('kind') or '')[:260]}
        if sid in failed:
            missing['translation'].append(row)
        if sid not in semantic:
            missing['explanation'].append(row)
    overall = metrics(translation,totals.get('direct_semantic_count', len(semantic)),
                      totals.get('total',len(source_rows)),totals.get('navigation_only_count',len(navigation)))
    return translation, {'overall':overall,'files':file_metrics,'chapter_id':chapter_id,'missing':missing}


def setup(folder, legacy, port):
    folder,legacy=folder.resolve(),legacy.resolve(strict=True)
    if not (legacy/'reader_server.py').is_file() or not (legacy/'static/index.html').is_file():
        raise ValueError('请提供当前已使用的 Reader 源码目录')
    folder.mkdir(parents=True,exist_ok=True)
    for path in ('library','project-files'):
        (folder/path).mkdir(exist_ok=True)
    seed=folder/'material.json'
    if not seed.exists():
        shutil.copy2(legacy/'material.json',seed)
    settings=folder/'settings.json'
    if not settings.exists():
        write(settings,{'project_root_base':str(folder/'project-files')})
    os.environ.pop('MANUAL_READER_PUBLIC_ORIGIN',None)
    os.environ.pop('MANUAL_READER_WORKER_URL',None)
    os.environ.pop('MANUAL_READER_PROXY_URL',None)
    os.environ.pop('MANUAL_READER_ROLE',None)
    paths={'PORT':port,'LIBRARY':folder/'library','DB':folder/'state.sqlite3',
           'PIN_FILE':folder/'pin.txt','SETTINGS':settings,'MATERIAL':seed,
           'STATIC':legacy/'static','SECURITY':folder/'security','POSTPROCESS_PREVIEW':'1'}
    os.environ.update({f'MANUAL_READER_{key}':str(value) for key,value in paths.items()})
    return folder


def file_config(source, card, original_projects, project_id):
    """Get only upstream-project originals whose complete file digests match the frozen source index."""
    selected=source.get('source_files',{})
    names={r.get('file_id'):r.get('relative_path') for r in card.get('material_plan',[])}
    location=(original_projects/project_id) if original_projects and SAMPLE.fullmatch(project_id) else None
    files={}
    if location and location.is_dir():
        frozen=read(location/'project.json')
        for fid,row in frozen.get('files',{}).items():
            if isinstance(row,dict):
                names.setdefault(fid,row.get('relative_path'))
    for fid,expected in selected.items():
        if not isinstance(fid,str) or not IDENTITY.fullmatch(fid) or not isinstance(expected,str):
            continue
        relative=names.get(fid) or ''
        name=PurePosixPath(relative).name if isinstance(relative,str) else ''
        if not name or name in ('.','..'):
            name='原文 '+fid
        files[fid]={'name':name,'sha256':expected}
        if location and relative and isinstance(relative,str):
            base=(location/'originals').resolve()
            candidate=(base/relative).resolve()
            if candidate.is_relative_to(base) and candidate.is_file() and digest(candidate)==expected:
                files[fid]['path']=candidate
    return files


def register_media(root,destination,pid,sha):
    if not isinstance(sha,str) or not SHA.fullmatch(sha):
        return ''
    source=root/'media'/(sha+'.png')
    if not source.is_file() or digest(source)!=sha:
        return ''
    target=destination/'postprocess-preview/assets'/source.name
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        shutil.copy2(source,target)
    return '/postprocess-preview-asset/'+source.name+'?material='+quote(pid)


def render_pdf(root, file_dir, original, file_sha, data):
    """Restore the actual printed pages and text coordinates used by the existing PDF pane."""
    import fitz

    wanted={ref['page_id'] for p in data['passages'] for ref in p['refs']}
    cache=root/'reader-isolated'/'page-cache'/file_sha
    assets=file_dir/'assets'
    assets.mkdir(parents=True,exist_ok=True)
    with fitz.open(original) as pdf:
        for page_id in sorted(wanted):
            match=re.fullmatch(r'[A-Za-z0-9_-]+:page:([1-9][0-9]*)',page_id)
            if not match:
                continue
            number=int(match[1])
            if number>len(pdf):
                continue
            page=pdf[number-1]
            image=f'page-{number:04d}-{file_sha[:12]}.png'
            layer=f'page-{number:04d}-{file_sha[:12]}.text.json'
            with PDF_RENDER_LOCKS[(file_sha,number)]:
                if not (cache/layer).is_file() or not (cache/image).is_file():
                    cache.mkdir(parents=True,exist_ok=True)
                    page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).save(cache/image)
                    lines=[]
                    for block in page.get_text('rawdict',flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']:
                        for line in block.get('lines',[]):
                            chars=[{'text':c['c'],'bbox':list(fitz.Rect(c['bbox'])*page.rotation_matrix)}
                                   for span in line['spans'] for c in span['chars']]
                            if chars:
                                lines.append({'bbox':list(fitz.Rect(line['bbox'])*page.rotation_matrix),'chars':chars})
                    write(cache/layer,{'lines':lines})
            for name in (image,layer):
                if not (assets/name).is_file():
                    shutil.copy2(cache/name,assets/name)
            if page.rotation:
                converted=set()
                for passage in data['passages']:
                    for ref in [*passage['refs'],*(ref for sentence in passage['sentences'] for ref in sentence['refs'])]:
                        if ref['page_id']==page_id and id(ref) not in converted and isinstance(ref.get('bbox'),list):
                            ref['bbox']=list(fitz.Rect(ref['bbox'])*page.rotation_matrix)
                            converted.add(id(ref))
            data['pages'][page_id]={'kind':'pdf','number':number,'width':page.rect.width,'height':page.rect.height,
                                    'image':image,'text_layer':layer}


def source_documents(root,destination,pid,source,files):
    from source_parser import Document
    from .content import code_source, formula_source
    docs={}
    source_rows={}
    for row in source['sources']:
        sid=row['source_id']; fid=row['file_id']
        if not IDENTITY.fullmatch(sid) or not IDENTITY.fullmatch(fid):
            raise ValueError('来源编号不符合 Reader 的本地展示标识约定')
        if fid not in docs:
            meta=files.get(fid,{})
            suffix=PurePosixPath(meta.get('name','')).suffix.lower()
            kind={'pdf':'pdf','html':'web','htm':'web','txt':'text','json':'subtitle'}.get(suffix.lstrip('.'),'text')
            docs[fid]=Document(fid,meta.get('name') or fid,'',kind)
        doc=docs[fid]
        kind=str(row.get('kind') or 'paragraph')
        if kind in ('page_image','visual','scan','figure'):
            kind='image'
        elif kind in ('math','formula','formula_display','formula_inline'):
            kind='equation'
        original=row.get('text') or ''
        code=kind=='code' or code_source(original)
        if code:
            kind='code'
        elif (row.get('position') or {}).get('type')=='pdf_page' and formula_source(original):
            kind='equation'
        refs=[]
        for ref in row.get('refs') or []:
            if not isinstance(ref,dict) or not ref.get('page_id'):
                continue
            refs.append(ref)
        original_role=row.get('role') or 'content'
        role='next_navigation' if original_role in ('page_furniture','metadata','whitespace') else original_role
        passage=doc.add(original,kind,level=1,refs=refs,atoms=[sid],role=role,next_source_role=original_role)
        if code and code_source(original):
            passage['code_language']='matlab'
        generated=passage['id']
        passage.update(id=sid,file_id=fid,source_ids=[sid],content_kind='source',
                       registered_unit_ids=row.get('registered_unit_ids',[]),
                       position=row.get('position'),time=row.get('time'))
        sentence=passage['sentences'][0]
        sentence.update(id=sid+'-s1',source_unit_ids=[sid])
        for outline in doc.data['outline']:
            for key in ('passage_id','id','section_id','parent_id'):
                if outline.get(key)==generated:
                    outline[key]=sid
        if doc.current==generated:
            doc.current=sid
        doc.stack=[(level,sid if key==generated else key) for level,key in doc.stack]
        if passage['section_id']==generated:
            passage['section_id']=sid
        assets=[source.get('assets',{}).get(a,{}) for a in row.get('asset_refs',[])]
        if kind=='image':
            for asset in assets:
                url=register_media(root,destination,pid,asset.get('sha256'))
                if url:
                    passage.update(src=url,alt=original or '原图',asset_id=asset.get('asset_id'),asset_sha=asset.get('sha256'))
                    break
        for unit in doc.data['inventory'][-1:]:
            unit['passage_id']=sid
        source_rows[sid]=passage
    rows=[]
    for fid,document in docs.items():
        data=document.finish()
        metadata=files.get(fid,{})
        file_dir=destination/'reading-files'/fid
        stored=''
        if original:=metadata.get('path'):
            stored='original'+original.suffix
            file_dir.mkdir(parents=True,exist_ok=True)
            path=file_dir/stored
            if not path.is_file() or digest(path)!=metadata['sha256']:
                raw=original.read_bytes()
                if sha256(raw).hexdigest()!=metadata['sha256']:
                    raise ValueError('原件在装配期间发生变化')
                atomic(path,raw)
            if original.suffix.lower()=='.pdf':
                render_pdf(root,file_dir,path,metadata['sha256'],data)
        rows.append({'id':fid,'name':data['title'],'kind':data['kind'],'outline':data['outline'],
                     'passage_ids':[p['id'] for p in data['passages']],'primary':False,
                     'original':bool(stored),'stored_name':stored,'document':data})
    return rows, source_rows


def native_translation(folder, source_rows):
    value=optional(folder,'translation-groups.json',{})
    groups=records(value,'groups','translation_groups','items')
    converted=[]
    for g in groups:
        members=[]
        for segment in g.get('segments',g.get('translations',[])):
            ids=segment.get('source_ids') or [segment.get('source_id')]
            text=segment.get('text') or segment.get('translation') or segment.get('translated')
            if (isinstance(text,str) and text.strip() and ids and all(s in source_rows for s in ids)
                    and segment.get('status','complete') in ('complete','translated','identity','preserved_math','uncertain')):
                if segment.get('status')=='uncertain':
                    text='〔译文待核对〕'+text
                members.append({'source_ids':ids,'text':text})
        ids=list(dict.fromkeys(i for m in members for i in m['source_ids']))
        if ids and len({source_rows[i]['file_id'] for i in ids})==1:
            kind=g.get('kind') or ('heading' if len(ids)==1 and source_rows[ids[0]].get('heading') else 'paragraph')
            converted.append({'source_ids':ids,'segments':members,'kind':kind if kind in (
                'paragraph','list','heading','table','figure','markdown','code','equation') else 'paragraph'})
    used=set()
    for group in converted:
        used.update(group['source_ids'])
    for row in records(optional(folder,'source-translation-links.json',[]),'links','source_translation_links','items'):
        sid=row.get('source_id')
        if sid in source_rows and sid not in used and row.get('relation') in ('identity','preserved_math','preserved_code'):
            text=source_rows[sid]['en']
            if text.strip():
                kind={'preserved_math':'equation','preserved_code':'code'}.get(row['relation'],'paragraph')
                if row.get('provenance')=='provider_preserved' and source_rows[sid].get('kind')!='equation':
                    kind='paragraph'
                converted.append({'source_ids':[sid],'segments':[{'source_ids':[sid],'text':text}],'kind':kind})
                used.add(sid)
    reading = records(optional(folder,'reading-groups.json',[]),'groups','reading_groups')
    return layout_translation(converted,reading,source_rows)


def layout_translation(translated, reading_groups, source_rows):
    """Lay out translations by original structure; preserve a multi-source sentence as a unit."""
    scope = {sid:n for n,group in enumerate(reading_groups) for sid in group['source_ids']}
    positions = {sid:i for i,sid in enumerate(source_rows)}
    regrouped = defaultdict(list)
    for group_index,group in enumerate(translated):
        cross_boundary = any(len({scope.get(sid) for sid in part['source_ids']})>1 for part in group['segments'])
        for segment in group['segments']:
            ids = segment['source_ids']
            owners = {scope.get(sid) for sid in ids}
            only = next(iter(owners)) if len(owners)==1 else None
            if cross_boundary:
                key=('original',group_index)
            else:
                key=('reading',only) if only is not None else ('segment',tuple(ids))
            regrouped[key].append((segment,group['kind']))

    structural = {'heading':'heading','list':'list_item','equation':'equation','table':'table','code':'code'}
    results = []
    for key, values in regrouped.items():
        values.sort(key=lambda value:min(positions[sid] for sid in value[0]['source_ids']))
        original = reading_groups[key[1]] if key[0]=='reading' else {}
        ids = list(dict.fromkeys(sid for segment,_ in values for sid in segment['source_ids']))
        kind = original.get('kind',values[0][1])
        if kind not in ('paragraph','list','heading','table','figure','markdown','code','equation'):
            kind = 'paragraph'
        if len(ids)==1:
            sid = ids[0]
            if source_rows[sid].get('heading'):
                kind='heading'
            if source_rows[sid].get('kind') in ('equation','code'):
                kind=source_rows[sid]['kind']
            elif values[0][1] in ('equation','code') and original.get('kind') not in ('heading','figure','table'):
                kind=values[0][1]
        parts=[]
        for segment,_ in values:
            part={**segment, 'block_id':original.get('group_id') or ids[0]}
            first=source_rows[segment['source_ids'][0]]
            if kind in structural:
                part['role']=structural[kind]
            if kind=='heading':
                part['level']=first.get('level') or 2
            if kind=='list':
                mark=re.match(r'^\s*(\d+)[.)、]\s*',first.get('en',''))
                if mark:
                    part['list_order']=int(mark[1])
            parts.append(part)
        results.append({'source_ids':ids,'segments':parts,'kind':kind})
    ordered=sorted(results,key=lambda g:min(positions[sid] for sid in g['source_ids']))
    arranged=[]
    for current in ordered:
        last=arranged[-1] if arranged else None
        if last and last['kind']==current['kind']=='equation':
            before=last['segments'][-1]['text']
            after=current['segments'][0]['text']
            left_id=last['source_ids'][-1]
            right_id=current['source_ids'][0]
            a=(source_rows[left_id].get('refs') or [{}])[0]
            b=(source_rows[right_id].get('refs') or [{}])[0]
            first,second=a.get('bbox'),b.get('bbox')
            if (positions[right_id]==positions[left_id]+1 and first and second and
                    a.get('page_id')==b.get('page_id') and a.get('page_id') and
                    not re.search(r'(?:^|\n)\s*\(\d+(?:\.\d+)*[a-z]?\)\s*$',before) and
                    not any(t.lstrip().startswith(('\\[','$$','\\begin{','\\(')) for t in (before,after)) and
                    min(first[3],second[3])>=max(first[1],second[1])-2 and
                    max(first[0],second[0])-min(first[2],second[2])<=26):
                block_id=last['segments'][0]['block_id']
                last['source_ids']+=current['source_ids']
                last['segments'].extend({**segment,'block_id':block_id} for segment in current['segments'])
                continue
        arranged.append(current)
    return arranged


def native_visual(folder, source_rows, resources):
    from reader_translation import visual_signature
    value=optional(folder,'translation-visuals.json',[])
    candidates=records(value,'translations','images','groups','items')
    asset_to_source=defaultdict(list)
    for sid,row in source_rows.items():
        if row.get('asset_sha'):
            asset_to_source[row['asset_sha']].append(sid)
    actual={r['resource_id']:r for r in resources if r.get('resource_id')}
    by_source=defaultdict(list)
    for item in candidates:
        rid=item.get('resource_id')
        sid=item.get('image_source_id') or item.get('source_id')
        digest=item.get('image_fingerprint') or item.get('media_sha256') or item.get('asset_sha256') or actual.get(rid,{}).get('media_sha256')
        for sid in ([sid] if sid in source_rows else asset_to_source.get(digest,[])):
            if source_rows[sid].get('kind') not in ('image','visual','page_image','figure','scan'):
                continue
            for label in item.get('labels') or [item]:
                original=label.get('original') or label.get('original_text') or label.get('raw_content')
                translation=label.get('translation') or label.get('translated') or label.get('translated_text')
                if isinstance(original,str) and original.strip() and isinstance(translation,str) and translation.strip():
                    by_source[sid].append({'original':original,'translation':translation})
    result=[]
    for sid,labels in by_source.items():
        p=source_rows[sid]
        result.append({'image_id':sid,'original_signature':visual_signature(p),'status':'needs_review',
                       'unreadable':['尚未核实这张图的全部文字'],
                       'labels':list({(x['original'],x['translation']):x for x in labels}.values()),
                       'evidence':{'asset_sha256':p.get('asset_sha'),'method':'按已定位的图中文字投影'}})
    return result


def save_preview_terms(state,terms,chapter_id,passage_ids,stamp):
    from reader_term_quality import validate_asset_term
    from reader_teaching import append_assets

    saved, rejected = [], []
    for term in terms:
        name = term.get('term') or term.get('name')
        reuse = term.get('reuse_id')
        row = {'name':name,'aliases':term.get('aliases',[]),'term_id':reuse}
        if not reuse:
            row.update(professional_definition=term.get('professional_definition') or term.get('definition'),
                       intuitive_explanation=term.get('intuitive_explanation') or term.get('intuition'))
        try:
            if term.get('status')=='needs_review':
                raise ValueError(term.get('reason') or '当前术语没有可确认的正文归属')
            historical=[old for old in state['terms']
                        if name in old.get('source_names',[]) and (not reuse or old['id']==reuse)]
            if len(historical)==1:
                row={'name':historical[0]['name'],'term_id':historical[0]['id']}
            existing=validate_asset_term(row,state['terms'])
            if existing:
                row={'name':existing['name'],'term_id':existing['id']}
            receipt=append_assets(state,{'terms':[row],'quizzes':[]},chapter_id,EXPLANATION,passage_ids,stamp)
            if not receipt['saved']['terms']:
                raise ValueError(next(iter(receipt['issues']),'原生词库没有保存该词'))
            identity=receipt['saved']['terms'][0]
            item=next(t for t in state['terms'] if t['id']==identity)
            refs=[EXPLANATION+'--'+bid for bid in term.get('block_ids',[]) if EXPLANATION+'--'+bid in passage_ids]
            item['related_passage_ids']=list(dict.fromkeys([*item.get('related_passage_ids',[]),*refs]))
            if item['related_passage_ids']:
                item.setdefault('passage_id',item['related_passage_ids'][0])
            saved.append({'term_id':identity,'reused':existing is not None})
        except (ValueError,KeyError,TypeError) as exc:
            rejected.append({'name':name,'reason':str(exc)})
    return {'saved':saved,'rejected':rejected}



def project_native(root, sample, server, backend, original_projects, *, destination_library=None):
    if not SAMPLE.fullmatch(sample):
        raise ValueError('预览任务编号无效')
    inputs=root/'inputs'/sample
    folder=root/'jobs'/sample
    source=read(inputs/'source-index.json')
    run=read(inputs/'step04-run.json')
    card=read(inputs/'task-card.json')
    digest_file=folder/'body.final.md'
    structure=read(folder/'part-blocks.json')
    pid='next-final-'+sample
    destination=(destination_library if destination_library is not None else server/'library')/pid
    side=destination/'postprocess-preview'
    body_sha=digest(digest_file)
    built=side/'chapter.md'
    manifest=side/'manifest.json'
    artifacts=('reading-groups.json','translation-groups.json','source-translation-links.json',
               'translation-visuals.json','resource-explanations.json','chapter-guidance.json',
               'term-candidates.json','quiz-candidates.json','block-source-links.json','part-blocks.json',
               'source-gap-recovery.json','run.json')
    signature=sha256((PROJECTION_VERSION+''.join(digest(folder/name) for name in artifacts if (folder/name).is_file())).encode()).hexdigest()
    if manifest.is_file():
        existing=read(manifest)
        if (existing.get('projection_signature')==signature and existing.get('body_sha256')==body_sha
                and built.is_file() and digest(built)==body_sha):
            return {**existing['projection_receipt'],'reused_projection':True}
    title=(card.get('chapter') or {}).get('title') or (card.get('question') or {}).get('text') or sample
    files=file_config(source,card,original_projects,run['project_id'])
    file_rows,source_rows=source_documents(root,destination,pid,source,files)
    chapter=card.get('chapter') or {}
    chapter_id=render_id(chapter.get('node_id') or ('preview-'+run['mode'].lower()))
    initial={'id':pid,'title':(card.get('project') or {}).get('name') or title,'source':'','kind':'text','pages':{},'passages':[],'outline':[],
             'inventory':[],'translation_status':'pending','schema_version':2}
    write(destination/'material.json',initial)
    work=backend.workspace(pid)
    with work.lock:
        if not work.path.exists():
            work.write({'revision':0,'files':file_rows,'explanations':[],'presentation':{},
                        'planning_mode':'none','learning_outline':[]})
        else:
            existing_work=work.read()
            managed=set(optional(side,'manifest.json',{}).get('original_files',{}))
            managed.update(row['id'] for row in file_rows)
            # Keep files registered outside the Next source projection, including answer images.
            combined=file_rows+[r for r in existing_work['files'] if r['id'] not in managed]
            if existing_work['files']!=combined:
                existing_work['files']=combined
                work.write(existing_work)
    backend.CONTEXT.material_id=pid
    material=backend.material(pid)
    native_groups=native_translation(folder,source_rows)
    from reader_translation import save_groups, save_visuals
    translated=save_groups(destination,material,native_groups,list(source_rows),partial=True) if native_groups else {'saved':0}
    native_visuals=native_visual(folder,source_rows,structure['resources'])
    visual_receipt=save_visuals(destination,material,native_visuals) if native_visuals else {'saved':0}
    groups=records(optional(folder,'reading-groups.json',[]),'groups','reading_groups')
    kinds={'image':'figure','visual':'figure','page_image':'figure','formula':'equation','list_item':'list'}
    overrides=[]
    for group in groups:
        ids=group.get('source_ids') or group.get('passage_ids') or []
        kind=kinds.get(group.get('kind'),group.get('kind'))
        if ids and all(i in source_rows for i in ids):
            if all(source_rows[i]['kind']=='code' for i in ids):
                kind='code'
            overrides.append({'id':group.get('group_id') or ids[0],'passage_ids':ids,
                              'kind':kind if kind in ('paragraph','list','figure','table','heading','code','markdown','equation') else 'paragraph'})
    if overrides:
        write(destination/'reading-layout-overrides.json',{'revision':1,'groups':overrides})
    edges=records(optional(folder,'block-source-links.json',structure.get('source_edges',[])),'links','source_edges','edges')
    repairs=optional(folder,'source-gap-recovery.json',{})
    repairs=repairs.get('items',[]) if repairs.get('body_sha256')==body_sha else []
    extras=defaultdict(list)
    real_blocks={row['block_id'] for row in structure['blocks']}
    for item in repairs:
        sid,bid=item['source_id'],item['block_id']
        if sid not in source_rows or bid not in real_blocks:
            continue
        if item['disposition']=='already_explained':
            edges.append({'source_id':sid,'block_id':bid,'relation_type':'direct_semantic'})
        elif item['disposition']=='add' and item.get('addition'):
            note='source-extra-'+sha256(sid.encode()).hexdigest()[:20]
            extras[bid].append({'id':note,'markdown':item['addition'],'reading_heading':'原文补充','progress_exempt':True,
                                'source_ranges':[{'file_id':source_rows[sid]['file_id'],'passage_id':sid}],
                                'explained_source_ids':[sid]})
            edges.append({'source_id':sid,'block_id':note,'relation_type':'direct_semantic'})
    by_block=defaultdict(dict)
    for edge in edges:
        sid,bid=edge.get('source_id'),edge.get('block_id')
        if sid in source_rows and bid:
            by_block[bid][sid]=edge.get('relation_type','direct_semantic')
    p2=optional(folder,'resource-explanations.json',{})
    status={r['resource_id']:r for r in records(p2,'coverage_by_resource','coverage') if r.get('resource_id')}
    explanations={g['group_id']:g for g in records(p2,'groups') if g.get('group_id')}
    shown=defaultdict(list)
    resources_by_block=defaultdict(list)
    for resource in structure['resources']:
        rid=resource['resource_id']; bid=resource.get('block_id')
        if not bid:
            continue
        state=status.get(rid,{})
        src=register_media(root,destination,pid,resource.get('media_sha256'))
        resources_by_block[bid].append((resource.get('media_path'),resource.get('verified_media_path'),src))
        if not state.get('eligible'):
            continue
        group=explanations.get(state.get('group_id'),{})
        components=[]
        for element in group.get('components',[]):
            if element.get('component_id'):
                components.append({**element,'raw_content':element.get('raw_content',element.get('raw','')),
                                   'meaning':element.get('meaning',element.get('explanation',''))})
        step_values=group.get('composition_or_steps') or group.get('composition') or group.get('relations') or []
        if isinstance(step_values,str):
            step_values=[step_values]
        steps=[x if isinstance(x,str) else x.get('explanation',x.get('text',''))
               for x in step_values if isinstance(x,(str,dict))]
        phase=state.get('status') or 'pending'
        shown[render_id(bid)].append({'resource_id':rid,'group_id':group.get('group_id'),
            'kind':resource['kind'],'raw_content':resource.get('raw_content',''),
            'overview':group.get('overview',''),'steps':steps,'status':phase,'components':components,
            'summary':group.get('summary',''),'limits':group.get('assumptions_and_limits',[]),
            'ordinal':state.get('ordinal'),'start':resource.get('start',0),
            'source_ids':resource.get('source_ids') or list(by_block[bid]),'asset_url':src,
            'heading':resource.get('heading'),
            'asset_sha256':resource.get('media_sha256'),'surface':resource.get('surface','body')})
    for items in shown.values():
        items.sort(key=lambda item:item['start'])
    guide=optional(folder,'chapter-guidance.json',{})
    guide=guide.get('guidance',guide)
    parts={p['part_id']:p for p in structure['parts']}
    body_parts=[p for p in parts.values() if p.get('surface')=='body' and p.get('block_ids')]
    part_title=lambda p:'开篇' if p['title'].strip() in ('导读','章节导读') else p['title']
    part_starts={p['block_ids'][0]:part_title(p) for p in body_parts}
    routes=[]
    focus={step['part_id']:step.get('focus','') for step in guide.get('reading_flow',[])}
    if run['mode']!='ANSWER':
        routes=[{'part_id':render_id(p['block_ids'][0]),'label':part_title(p),
                 'description':focus.get(p['part_id'],'')} for p in body_parts]
    blocks=[]
    previous_transition=guide.get('previous_transition') and run['mode'] not in ('INTRO','ANSWER')
    if previous_transition:
        blocks.append({'id':'framework-previous','markdown':guide['previous_transition'],
                       'reading_heading':'章节导读','progress_exempt':True})
    if guide.get('chapter_intro'):
        heading='' if previous_transition else '章节导读'
        blocks.append({'id':'framework-start-1','markdown':guide['chapter_intro'],
                       'reading_heading':heading,'progress_exempt':True})
    if routes:
        blocks.append({'id':'route','markdown':'阅读路线','reading_heading':'阅读路线','progress_exempt':True})
    deferred=[]
    for row in structure['blocks']:
        bid=row['block_id']; text=row.get('markdown') or ''
        if not text.strip():
            continue
        refs=[{'file_id':source_rows[sid]['file_id'],'passage_id':sid} for sid in by_block[bid]]
        for relative,verified,url in resources_by_block[bid]:
            if url:
                for ref in {relative,verified}-{None,''}:
                    text=text.replace(']('+ref+')',']('+url+')')
        item={'id':render_id(bid),'markdown':text,'source_ranges':refs,
              'explained_source_ids':[sid for sid,rel in by_block[bid].items() if rel=='direct_semantic']}
        if bid in part_starts and not re.match(r'^ {0,3}#{1,6}\s', text):
            item['reading_heading']=part_starts[bid]
        elif bid in part_starts and part_starts[bid]=='开篇':
            item['markdown']=re.sub(r'^( {0,3}#{1,6}\s+)章节?导读(?=\s|$)',r'\g<1>开篇',text,count=1)
            item['markdown']=re.sub(r'^( {0,3}#{1,6}\s+)导读(?=\s|$)',r'\g<1>开篇',item['markdown'],count=1)
        if row.get('surface')=='deferred':
            deferred.append(item)
        else:
            blocks.append(item)
            blocks.extend(extras.get(bid,[]))
    if deferred:
        blocks.append({'id':'framework-deferred','markdown':'## 延伸阅读','progress_exempt':True})
        blocks.extend({**row,'progress_exempt':True} for row in deferred)
    from reader_connected import recap_markdown
    from ...steps.step_15_chapter_guidance import RECAP_FIELDS, next_reading, recap_groups
    recap_parts={key:{**p,'title':part_title(p)} for key,p in parts.items()}
    recaps=list(recap_groups(guide,recap_parts)) if run['mode']!='ANSWER' else []
    synthesis=guide.get('chapter_synthesis') or guide.get('chapter_outro')
    if recaps:
        complete=[(name,fields) for name,fields in recaps if all(key in fields for key in RECAP_FIELDS)]
        recap_text=recap_markdown(complete) if len(complete)==len(recaps) else '\n\n'.join(
            '- **'+name+'**\n\n  '+'  \n  '.join(fields[key] for key in RECAP_FIELDS if key in fields)
            for name,fields in recaps)
        blocks.append({'id':'framework-outro','markdown':recap_text,
                       'reading_heading':'把这一节串起来','progress_exempt':True})
    if run['mode']!='ANSWER' and synthesis:
        blocks.append({'id':'framework-synthesis','markdown':synthesis,
                       'reading_heading':'章节总结','progress_exempt':True})
    following=optional(inputs,'reader-context.json',{}).get('adjacent_chapters',{}).get('next')
    ending_kind=('complete' if run['mode']=='ANSWER' else 'next' if following else 'open')
    transition=('如果还需要理解某一步，可以在对应段落或资源下继续追问。' if run['mode']=='ANSWER'
                else next_reading(guide,card,following))
    blocks.append({'id':'framework-next','markdown':transition,
                   'reading_heading':'章际承接' if run['mode']!='ANSWER' else '继续追问','progress_exempt':True})
    if not blocks:
        raise ValueError('成品没有可供阅读的讲解片段')
    all_links=list({r['passage_id']:r for b in blocks for r in b.get('source_ranges',[])}.values())
    source_links=all_links[:300]
    payload={'id':EXPLANATION,'title':str(title)[:300],'writing_version':PREVIEW_WRITING_VERSION,'blocks':blocks,
             'source_links':source_links,'narrative':{'flow':routes,'ending_kind':ending_kind}}
    current=work.read()
    previous=next((x for x in current['explanations'] if x['id']==EXPLANATION),None)
    if previous:
        payload['expected_version']=previous['version']
    material=backend.material(pid)
    state=backend.core.read_state()
    used={r['passage_id'] for r in state['rounds']} | set(state['read_passages'])
    node={'id':chapter_id,'title':str(title)[:300],'parent_id':None,'number':str(chapter.get('number') or '0'),
          'source_links':all_links,'explanation_ids':[EXPLANATION]}
    current=work.read()
    if current.get('learning_outline')!=[node] or current.get('planning_mode')!='none':
        current.update(learning_outline=[node],planning_mode='none')
        work.write(current)
    payload['learning_node_id']=chapter_id
    lesson=work.explain(payload,material,used_passages=used)
    if state.get('learning_node_id')!=chapter_id:
        backend.core.mutate(lambda current:current.update(learning_node_id=chapter_id))
    material=backend.material(pid)
    work.present({'layout':'three','columns':[['original'],['translation'],['explanation']],
                  'file_id':file_rows[0]['id'] if file_rows else None,'explanation_id':EXPLANATION},material)
    terms=[]
    for term in records(optional(folder,'term-candidates.json',[]),'terms','candidates','items'):
        name=term.get('term') or term.get('name')
        if name:
            terms.append({**term,'term':name,'term_id':term.get('term_id') or term.get('reuse_id') or
                          'term-'+sha256(name.encode()).hexdigest()[:20],
                          'block_ids':[render_id(x) for x in term.get('block_ids',[])],
                          'definition':term.get('definition') or ''})
    raw_quizzes=records(optional(folder,'quiz-candidates.json',[]),'questions','quizzes','items','candidates')
    from ...steps.step_14_practice import native_question
    adapted=[]
    quiz_issues=[]
    for q in raw_quizzes:
        entry=parts.get(q.get('part_id'),{})
        if not entry.get('block_ids'):
            continue
        after=render_id(entry['block_ids'][-1])
        try:
            adapted.append(native_question({**q,'placement':{'kind':'lesson','after_part_id':after}}))
        except (ValueError,KeyError,TypeError,AttributeError) as exc:
            quiz_issues.append({'id':q.get('id'),'reason':str(exc)})
    from reader_teaching import append_assets
    context=optional(inputs,'reader-context.json',{})
    term_receipt={'saved':[],'rejected':[]}
    if terms or adapted or context.get('existing_terms'):
        def install_assets(state):
            known={t['id'] for t in state['terms']}
            state['terms'].extend(t for t in context.get('existing_terms',[]) if t.get('id') not in known)
            term_receipt.update(save_preview_terms(state,terms,chapter_id,lesson['passage_ids'],backend.core.stamp))
            if adapted:
                from reader_contract import current_quizzes
                prior=[row for row in current_quizzes(state['quizzes'])
                       if (row.get('placement') or {}).get('kind')=='lesson'
                       and (row.get('placement') or {}).get('explanation_id')==EXPLANATION]
                response=append_assets(state,{'terms':[],'quizzes':adapted},
                                       chapter_id,EXPLANATION,lesson['passage_ids'],backend.core.stamp)
                saved=set(response['saved']['quizzes'])
                if len(saved)==len(adapted):
                    for old in prior:
                        if old['id'] not in saved and old.get('selected') is None:
                            old['practice_archived']='lesson_replaced'
                return response
        backend.core.mutate(install_assets)
    first=next((b['id'] for b in blocks if not b.get('progress_exempt')),blocks[0]['id'])
    coverage=structure.get('coverage',{})
    receipt={'sample_id':sample,'material_id':pid,'body_sha256':body_sha,
             'blocks':len(blocks),'source_units':len(source_rows),'translation_groups':translated['saved'],
             'resources':sum(len(items) for items in shown.values()),'quizzes':len(adapted),
             'terms':len(term_receipt['saved']),'term_receipt':term_receipt,'quiz_issues':quiz_issues,
             'recaps':int(bool(recaps or synthesis)),'recap_parts':len(recaps),
             'pdf_pages':sum(len(r['document']['pages']) for r in file_rows),'visual_translation_groups':visual_receipt['saved'],
             'original_files':sum(bool(r.get('stored_name')) for r in file_rows)}
    side.mkdir(parents=True,exist_ok=True)
    shutil.copy2(digest_file,built)
    from .content import translation_processing
    navigation=dict(structure.get('source_navigation',{}))
    route={e['source_id'] for e in edges if e.get('source_id') in source_rows}
    direct={e['source_id'] for e in edges if e.get('source_id') in source_rows and e.get('relation_type')=='direct_semantic'}
    navigation.update(linked=len(route),direct_semantic_count=len(direct),
                      navigation_only_count=len(route-direct),unresolved=len(source_rows)-len(route))
    processing,statistics=coverage_statistics(
        source,records(optional(folder,'source-translation-links.json',[]),'links'),
        edges,chapter_id,title,files,translation_processing,navigation)
    write(side/'manifest.json',{'sample_id':sample,'projection_signature':signature,'projection_receipt':receipt,
          'direct_sources_by_block':{render_id(bid):[sid for sid,kind in members.items() if kind=='direct_semantic']
                                     for bid,members in by_block.items()},'status':'preview_ready','quality_status':'partial'
          if any(item['status']=='needs_review' for rows in shown.values() for item in rows) else 'available',
          'body_sha256':body_sha,'first_block_id':first,
          'delivery_receipt_sha256':digest(folder/'run.json') if optional(folder,'run.json',{}).get('format_status')=='complete' else None,
          'resources':dict(shown),'mode':run['mode'],'coverage':coverage,
          'chapter_recaps':[{'title':title,'items':fields} for title,fields in recaps],
          'study':{'terms':receipt['terms'],'exercises':receipt['quizzes']},
          'translation_processing':processing,
          'coverage_statistics':statistics,
          'source_coverage':navigation,
          'relation_counts':dict((kind,sum(1 for e in edges if e.get('relation_type')==kind))
                                 for kind in ('direct_semantic','navigation_only')),
          'block_ids':{row['block_id']:render_id(row['block_id']) for row in structure['blocks']},
          'read_only_source_versions':source['source_files'],
          'original_files':{r['id']:r['stored_name'] for r in file_rows if r.get('stored_name')}})
    return receipt
