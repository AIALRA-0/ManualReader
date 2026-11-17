"""Run the Next workflow on one new HTML, PDF or DOCX original in a separate local Reader."""

from argparse import ArgumentParser
import asyncio
from hashlib import sha256
from pathlib import Path
import sys
from time import time
from uuid import uuid4
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from manualreader.stages.step02.html_view import html_document
from manualreader.stages.step02.original import view
from manualreader.stages.step03.cache import atomic, digest
from manualreader.artifacts import read_json as read, write_json as write
from run_whiteboard import Whiteboard


def read_original(path, file_id, project_dir, payload):
    suffix = path.suffix.lower()
    if suffix in ('.html', '.htm'):
        passages = html_document(path, file_id, project_dir)['passages']
        page = BeautifulSoup(payload, 'html.parser')
        title = page.title.get_text(' ', strip=True) if page.title else path.stem
        return title, 'html_dom_unit', [
            (int(row['source_ids'][0].removeprefix('dom')), row) for row in passages
        ]
    if suffix == '.docx':
        kind, rows = view(payload, suffix)
        return path.stem, kind, [
            (row['n'], {'raw_text':row['text'], 'kind':'paragraph',
                        'source_ids':[f'p{row["n"]}'], 'refs':[]}) for row in rows if row['text'].strip()
        ]
    import fitz

    rows = []
    with fitz.open(stream=payload, filetype='pdf') as document:
        title = (document.metadata or {}).get('title') or path.stem
        for page in document:
            number = page.number + 1
            blocks = page.get_text('blocks')
            for i, block in enumerate(blocks):
                text = block[4].strip()
                if block[6] != 0 or not text:
                    continue
                rows.append((number, {'raw_text':text, 'kind':'paragraph',
                    'source_ids':[f'p{number}.b{i}'],
                    'refs':[{'page_id':f'{file_id}:page:{number}', 'bbox':list(block[:4])}]}))
            for i, image in enumerate(page.get_image_info()):
                rows.append((number, {'raw_text':'', 'kind':'image',
                    'source_ids':[f'p{number}.image{i}'],
                    'refs':[{'page_id':f'{file_id}:page:{number}', 'bbox':list(image['bbox'])}]}))
            if not any(rank == number for rank, _ in rows):
                rows.append((number, {'raw_text':'', 'kind':'image',
                    'source_ids':[f'p{number}.page'],
                    'refs':[{'page_id':f'{file_id}:page:{number}', 'bbox':list(page.rect)}]}))
    return title, 'pdf_page', rows


def register(root, original):
    data=root/'original-inputs'
    payload=original.read_bytes()
    checksum=sha256(payload).hexdigest()
    project_id='material-'+checksum[:16]
    file_id='file-'+checksum[:16]
    node_id='chapter-'+checksum[:16]
    project_dir=data/'projects'/project_id
    relative='source/'+original.name
    path=project_dir/'originals'/relative
    atomic(path,payload)
    title, kind, registration = read_original(path,file_id,project_dir,payload)
    if not registration:
        raise ValueError('原件没有可登记的正文')
    directory=[{'node_id':node_id,'parent_node_id':None,'number':'1','title':title}]
    rows=[]
    existing={}
    for serial, (number, passage) in enumerate(registration,1):
        identity=f'{file_id}-u{serial:05d}'
        rows.append({'id':identity,'position':{'type':kind,'start':number,'end':number},'time':None})
        location={'path':relative,'source_unit_ids':passage['source_ids'],'refs':passage.get('refs',[])}
        if kind=='html_dom_unit':
            location['dom_units']=passage['source_ids']
        existing[identity]={**passage,'id':identity,'section_id':node_id,
                            'en':passage['raw_text'],'source_location':location}
    locator={'type':kind,'start':rows[0]['position']['start'],'end':rows[-1]['position']['end']}
    files={file_id:{'file_id':file_id,'relative_path':relative,'sha256':checksum,'size_bytes':len(payload)}}
    project={'project_id':project_id,'name':title,'selection':node_id,'directory':directory,'files':files,
             'versions':{'material_schema':2,'workspace_revision':1,'records_revision':1,
                         'selection_revision':1,'file_manifest_revision':1},
             'progress_input':{'file_coverage':{},'completed_chapter_ids':[],'total_formal_chapters':1},
             'previous':{},'approved_styles':{},'recent_questions':{},'questions':{},
             'scope_hints':{node_id:{file_id:[locator]}},'saved_explanations':[],
             'fixture':None,'fixture_blocked':None}
    source_index={file_id:{'sha256':checksum,'rows':rows,'provenance':'isolated_original'}}
    sources={file_id:existing}
    write(project_dir/'project.json',project)
    write(project_dir/'source-index.json',source_index)
    write(project_dir/'original-registration.json',{'sources':sources,'registration_sha256':digest(sources)})
    write(project_dir/'registered-context.json',{'glossary_status':'registered_snapshot',
        'existing_terms':[],'existing_quizzes':[],'available_code_runners':[],
        'sourced_from_registered_project_state':True})
    sample='new-material-normal'
    manifest={'run_id':'reader-'+uuid4().hex,'started_at':time(),'data_root':str(data),
              'samples':{sample:{'sample_id':sample,'project_id':project_id,'mode':'NORMAL','question_id':None}},
              'deep_browser_samples':[sample],'original_projects':{project_id:{'verified_files':files,
              'registration_sha256':digest(sources),'question_ids':[]}},
              'prior_run_reused':0,'initial_model_store_empty':True}
    write(root/'whiteboard-inputs.json',manifest)
    return manifest


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('material',type=Path)
    parser.add_argument('root',type=Path)
    parser.add_argument('--legacy',type=Path,required=True)
    parser.add_argument('--preview-url',required=True)
    parser.add_argument('--models',type=int,default=12)
    parser.add_argument('--resume-attachments',action='store_true',
                        help='核对已有正文后继续处理本轮附件')
    args=parser.parse_args()
    url=urlsplit(args.preview_url)
    if url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port:
        parser.error('这里使用本机隔离 Reader')
    root=args.root.resolve()
    original=args.material.resolve(strict=True)
    if original.suffix.lower() not in ('.html','.htm','.pdf','.docx'):
        parser.error('请提供 HTML、PDF 或 DOCX 原件')
    sys.path.insert(0,str(args.legacy.resolve(strict=True)))
    if args.resume_attachments:
        manifest=read(root/'whiteboard-inputs.json')
        checksum=sha256(original.read_bytes()).hexdigest()
        registered={row['sha256'] for project in manifest['original_projects'].values()
                    for row in project['verified_files'].values()}
        if registered!={checksum}:
            parser.error('恢复操作必须使用本轮登记的同一份原件')
    else:
        if any((root/name).exists() for name in ('whiteboard-inputs.json','requests','jobs','original-inputs','runs')):
            parser.error('这次验收需要一个新目录')
        manifest=register(root,original)
    asyncio.run(Whiteboard(root,manifest,args.models,args.preview_url,commit_normal=False).run(
        resume_attachments=args.resume_attachments))
    result=read(root/'whiteboard-result.json')
    return int(bool(result['failures']))


if __name__=='__main__':
    raise SystemExit(main())
