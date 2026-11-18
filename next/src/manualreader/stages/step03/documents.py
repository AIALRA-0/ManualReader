"""Discover original content in web, subtitle, Office, image, text and archive files."""

from base64 import b64decode
from io import BytesIO
from pathlib import PurePosixPath
import json
import posixpath
import re
from tempfile import TemporaryDirectory
from pathlib import Path
from xml.etree import ElementTree as XML
from zipfile import ZipFile

from bs4 import BeautifulSoup

from ..step02.html_view import html_document
from .media import original_text, unavailable, visual
from ...source_formats import OFFICE_VECTORS, WORD, decode_text, html_soup, legacy_word, office_vector_png, word_tables, word_text


IMAGES={'.png','.jpg','.jpeg','.webp','.bmp','.gif','.tif','.tiff'}
OFFICE={'.docx','.pptx','.xlsx'}
TIMING=re.compile(r'^(\S+) *--> *(\S+)')
CLOCK=re.compile(r'(?:\d+:)?[0-5]?\d:[0-5]\d[,.]\d{3}\Z')
ALLOWED_MIME=re.compile(r'data:image/(?:png|jpe?g|webp|gif|bmp|tiff);base64\Z',re.I)
CODE={'.py','.js','.c','.h','.cpp','.rs','.java','.ps1','.sh','.go','.sql','.css'}
NATIVE_FORMULA=re.compile(r'(?<!\\)\$\$([\s\S]+?)(?<!\\)\$\$|\\\[([\s\S]+?)\\\]')


def loc(kind,n,**fields):
    return {'type':kind,'number':n,**fields}


def native_structure(asset,structure):
    asset['structure']=structure
    asset['status']='available'
    asset['capabilities'].append('compute_structure')
    return asset


def parse_table(node):
    rows=[]
    for tr in (row for row in node.find_all('tr') if row.find_parent('table') is node):
        cells=[]
        for element in tr.find_all(['th','td'],recursive=False):
            try:
                down,across=[int(element.get(x,'1')) for x in ('rowspan','colspan')]
            except ValueError as error:
                raise ValueError('表格合并单元格属性无效') from error
            if not 0<down<=1000 or not 0<across<=1000:
                raise ValueError('表格合并单元格范围无效')
            cells.append({'text':element.get_text(' ',strip=True),'rowspan':down,'colspan':across,
                          'header':element.name=='th','scope':element.get('scope'),'headers':element.get('headers',[])})
        if cells:
            rows.append(cells)
    return {'rows':rows,'caption':node.caption.get_text(' ',strip=True) if node.caption else ''}


def read_html(raw,cache,related):
    page=html_soup(raw)
    images=list(page.find_all('img'))
    options=[(str(t.get('src') or t.get('data-src') or ''),t.get('alt','')) for t in images]
    for i,tag in enumerate(images):
        tag['src']=f'mr-original-image-{i}'
    # Keep the existing DOM numbering without letting the old projection read image paths.
    with TemporaryDirectory(prefix='mr03-dom-') as temp:
        p=Path(temp)/'snapshot.html'
        p.write_bytes(page.encode('utf-8'))
        rows=html_document(p,'step03',Path(temp))['passages']
    table_nodes=page.find_all('table')
    assets=[]
    for r in rows:
        n=int(r['source_ids'][0][3:])
        point=loc('html_dom_unit',n,dom_id=f'dom{n}')
        if r['kind']=='image':
            suffix=r.get('source_src','').removeprefix('mr-original-image-')
            if not suffix.isdecimal() or int(suffix)>=len(options):
                assets.append(unavailable('image',point,'快照中的图片没有可定位的原件引用'))
                continue
            ref,alt=options[int(suffix)]
            try:
                if ref.startswith('data:'):
                    header,sep,body=ref.partition(',')
                    if not sep or not ALLOWED_MIME.fullmatch(header) or len(body)>40_000_000:
                        raise ValueError('快照内嵌图片格式或大小无效')
                    data=b64decode(body,validate=True)
                else:
                    data=related(ref,point['dom_id'])
                assets.append(visual(cache,'image',point,data,alt=alt,origin='html_snapshot'))
            except (ValueError,OSError) as error:
                assets.append(unavailable('image',point,str(error),alt=alt))
        else:
            a=original_text(r['kind'],point,r['raw_text'])
            if r.get('role'):
                a.update(role=r['role'],translatable=r.get('translatable',True))
            if r['kind']=='table':
                ordinal=r.get('table_index',-1)
                node=table_nodes[ordinal] if 0<=ordinal<len(table_nodes) else None
                try:
                    if node is not None:
                        a=native_structure(a,parse_table(node))
                except ValueError as err:
                    a['structure_error']=str(err)
            assets.append(a)
    # Media containers may lack img children, so list their actual links separately.
    for i,tag in enumerate(page.find_all(['source','video','audio','object','embed','iframe']),1):
        path=tag.get('src') or tag.get('data') or tag.get('poster')
        if path:
            assets.append(unavailable('media',loc('html_element',i,tag=tag.name),
                                      '快照包含媒体引用，当前未登记可读取的原件载体'))
    return {'type':'html','assets':assets}


def second(value):
    if not CLOCK.fullmatch(value):
        raise ValueError('字幕时间格式无效')
    return sum(float(x.replace(',','.'))*60**i for i,x in enumerate(reversed(value.split(':'))))


def subtitles(raw,suffix):
    text, _, _ = decode_text(raw)
    if suffix=='.json':
        contents=json.loads(text)
        if isinstance(contents,dict):
            contents=contents.get('body')
        if not isinstance(contents,list) or not contents or not all(isinstance(r,dict) and 'sid' in r and
                'content' in r and 'from' in r and 'to' in r for r in contents):
            return None
    else:
        contents=[]
        for block in re.split(r'\r?\n\s*\r?\n',text.strip()):
            lines=block.splitlines()
            idx=next((i for i,t in enumerate(lines) if TIMING.match(t)),None)
            if idx is None:
                continue
            start,end=TIMING.match(lines[idx]).groups()
            contents.append({'sid':len(contents)+1,'from':second(start),'to':second(end),'content':'\n'.join(lines[idx+1:])})
    assets=[]
    previous=-1
    prior_end=-1
    seen=set()
    for r in contents:
        n=int(r['sid'])
        t0,t1=float(r['from']),float(r['to'])
        point=loc('subtitle_sid',n,time=[t0,t1])
        raw_text=r['content']
        if n in seen or not 0<=t0<=t1<float('inf') or t0<previous or not isinstance(raw_text,str):
            assets.append(unavailable('subtitle',point,'字幕序号或起止时间无效'))
            continue
        speaker=r.get('speaker') or r.get('speaker_name')
        assets.append(original_text('subtitle',point,raw_text,overlaps_previous=t0<prior_end,
                                    **({'speaker':speaker} if isinstance(speaker,str) and speaker else {})))
        seen.add(n)
        previous=t0
        prior_end=t1
    return {'type':'subtitles','assets':assets}


def checked_zip(raw):
    z=ZipFile(BytesIO(raw))
    try:
        files=[entry for entry in z.infolist() if not entry.is_dir()]
        if len(files)>3000 or sum(entry.file_size for entry in files)>128*1024**2:
            raise ValueError('压缩包超过可读取的成员数或总大小')
        seen=set()
        for item in z.infolist():
            name=item.filename
            parts=PurePosixPath(name).parts
            if not parts or '\\' in name or ':' in name or name.startswith('/') or '..' in parts:
                raise ValueError('压缩包中有越出材料目录的路径')
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('压缩包中含符号链接')
            key=name.rstrip('/').casefold()
            if key in seen:
                raise ValueError('压缩包中有重复成员路径')
            seen.add(key)
            if item.file_size>32*1024**2 or (item.file_size>64*1024 and
                    item.file_size>180*max(1,item.compress_size)):
                raise ValueError('压缩成员超过允许的体积或压缩比')
        return z,files
    except BaseException:
        z.close()
        raise


def tag(element):
    return element.tag.rsplit('}',1)[-1]


def nodes(root,name):
    return (n for n in root.iter() if tag(n)==name)


def xml(z,name):
    raw=z.read(name)
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('不读取 Office 内的外部实体声明')
    return XML.fromstring(raw)


def child_text(element,name):
    return next((e.text for e in nodes(element,name) if e.text),None)


def attr_local(element,name):
    return next((v for k,v in element.attrib.items() if k.rsplit('}',1)[-1]==name),None)


def office_table(element):
    rows=[]
    for tr in nodes(element,'tr'):
        rows.append([{'text':' '.join(t.text for t in nodes(tc,'t') if t.text),
                      'grid_span':next((attr_local(n,'val') for n in nodes(tc,'gridSpan')),None),
                      'vertical_merge':next((attr_local(n,'val') or 'continue' for n in nodes(tc,'vMerge')),None)}
                     for tc in tr if tag(tc)=='tc'])
    return rows


def read_office(z,files,suffix,cache):
    assets=[]
    names={f.filename for f in files}
    rendered={}

    def embedded(member,location,**evidence):
        extension=PurePosixPath(member).suffix.lower()
        if extension in IMAGES:
            return visual(cache,'image',location,z.read(member),**evidence)
        if extension in OFFICE_VECTORS:
            if member not in rendered:
                rendered[member]=office_vector_png(z.read(member),extension)
            if rendered[member]:
                return visual(cache,'image',location,rendered[member],origin='office_vector_preview',**evidence)
        return unavailable('image',location,'当前无法读取这种 Office 媒体',**evidence)
    if suffix=='.docx':
        main=xml(z,'word/document.xml')
        body=next(nodes(main,'body'))
        for i,node in enumerate(body,1):
            point=loc('document_paragraph',i,member='word/document.xml',element=tag(node))
            txt=' '.join(t.text for t in nodes(node,'t') if t.text)
            if tag(node)=='tbl':
                if node.tag==WORD+'tbl':
                    tables=word_tables(node)
                    structure={'rows':tables[0]['cells'],'nested_tables':tables[1:],'header_rows':tables[0]['header_rows']}
                else:
                    structure={'rows':office_table(node)}
                assets.append(native_structure(original_text('table',point,txt),structure))
            elif txt:
                assets.append(original_text('text',point,txt))
            for embed in nodes(node,'blip'):
                rel=attr_local(embed,'embed')
                if rel:
                    assets.append(original_text('media_reference',point,rel))
        for member in ('word/footnotes.xml','word/endnotes.xml'):
            if member not in names:
                continue
            for i,node in enumerate(xml(z,member),1):
                text=word_text(node)
                if text.strip() and not node.get(WORD+'type'):
                    assets.append(original_text('text',loc('office_member',i,member=member,note_id=node.get(WORD+'id')),text))
    if suffix=='.pptx':
        slides=sorted((int(m[1]),f) for f in names if (m:=re.fullmatch(r'ppt/slides/slide(\d+)\.xml',f)))
        for number,name in slides:
            slide=xml(z,name)
            for i,node in enumerate(next(nodes(slide,'spTree'),()),1):
                what=tag(node)
                if what not in ('sp','pic','graphicFrame','grpSp','cxnSp'):
                    continue
                text=' '.join(t.text for t in nodes(node,'t') if t.text)
                p=loc('slide',number,member=name,object=i)
                if what=='graphicFrame' and (table:=next(nodes(node,'tbl'),None)) is not None:
                    assets.append(native_structure(original_text('table',p,text),{'rows':office_table(table)}))
                else:
                    placement=next(nodes(node,'xfrm'),None)
                    bounds={tag(x):x.attrib for x in placement} if placement is not None else {}
                    assets.append(original_text('slide_object',p,text,object=what,geometry_emu=bounds))
    if suffix=='.xlsx':
        shared=[]
        if 'xl/sharedStrings.xml' in names:
            shared=[''.join(t.text or '' for t in nodes(si,'t')) for si in nodes(xml(z,'xl/sharedStrings.xml'),'si')]
        sheets=sorted((int(m[1]),f) for f in names if (m:=re.fullmatch(r'xl/worksheets/sheet(\d+)\.xml',f)))
        for n,name in sheets:
            cells=[]
            root=xml(z,name)
            for cell in nodes(root,'c'):
                value=child_text(cell,'v')
                if cell.get('t')=='s' and value is not None:
                    value=shared[int(value)]
                elif cell.get('t')=='inlineStr':
                    value=''.join(e.text or '' for e in nodes(cell,'t'))
                cells.append({'address':cell.get('r'),'formula':child_text(cell,'f'),'cached_value':value,'type':cell.get('t')})
            merged=[r.get('ref') for r in nodes(root,'mergeCell')]
            assets.append(native_structure(original_text('sheet',loc('sheet',n,member=name),''),
                                          {'cells':cells,'merged_cells':merged,'calculation_performed':False}))
    for ordinal,member in enumerate(sorted(names),1):
        prefix=member.split('/')[0]
        if prefix not in ('word','ppt','xl'):
            continue
        if '/media/' in member:
            assets.append(embedded(member,loc('office_member',ordinal,member=member)))
        elif '/_rels/' in member and member.endswith('.rels'):
            for relation in xml(z,member):
                if relation.get('TargetMode')=='External':
                    assets.append(unavailable('external_relationship',loc('office_member',ordinal,member=member,
                            relationship=relation.get('Id')),'外部关系不在原件包内'))
                elif relation.get('Type','').endswith('/image'):
                    relative=relation.get('Target','')
                    directory=member.rsplit('/_rels/',1)[0]
                    target=posixpath.normpath(posixpath.join(directory,relative))
                    location=loc('office_member',ordinal,member=member,relationship=relation.get('Id'))
                    if target in names and '/media/' in target:
                        assets.append(embedded(target,location,target_member=target))
                    else:
                        assets.append(unavailable('image',location,'对应的同包图像不可用'))
    return {'type':'office','assets':assets,'layout_rendered':False}


def parse_document(raw,suffix,cache,related,encoding=None):
    if suffix=='.doc':
        from .cache import digest
        with legacy_word(raw) as file:
            converted=file.read_bytes()
            report=parse_document(converted,'.docx',cache,related)
        for item in report['assets']:
            item['locator']['coordinate_space']='converted_docx'
            item['locator']['converted_sha256']=digest(converted)
        return {**report,'converted_carrier':cache.blob(converted)}
    if suffix in IMAGES:
        return {'type':'image','assets':[visual(cache,'image',loc('image',1),raw)]}
    if suffix in ('.html','.htm'):
        return read_html(raw,cache,related)
    if suffix in ('.srt','.vtt','.json'):
        parsed=subtitles(raw,suffix)
        if parsed is not None:
            return parsed
    if suffix in OFFICE or suffix=='.zip':
        z,files=checked_zip(raw)
        with z:
            if suffix in OFFICE:
                return read_office(z,files,suffix,cache)
            a=[]
            for i,f in enumerate(files,1):
                where=loc('archive_member',i,member=f.filename)
                ext=PurePosixPath(f.filename).suffix.lower()
                if ext in IMAGES:
                    a.append(visual(cache,'image',where,z.read(f)))
                elif ext in CODE|{'.txt','.md','.json','.csv','.log'}:
                    a.append(original_text('archive_text',where,z.read(f).decode('utf-8-sig',errors='replace')))
                else:
                    a.append(original_text('archive_member',where,f.filename,bytes=f.file_size))
            return {'type':'archive','assets':a}
    text, codec, offset=decode_text(raw,encoding)
    lines=text.splitlines(keepends=True)
    values=[]
    for number,line in enumerate(lines,1):
        end=offset+len(line.encode(codec))
        values.append(original_text('code' if suffix in CODE else 'text',
                      loc('text_line',number,byte_start=offset,byte_end=end),line))
        offset=end
    if suffix in ('.md','.markdown','.tex'):
        for match in NATIVE_FORMULA.finditer(text):
            start=text.count('\n',0,match.start())+1
            end=text.count('\n',0,match.end())+1
            point=loc('text_line',start,end=end,char_start=match.start(),char_end=match.end())
            # Native TeX is an original transcription, not a verification of mathematical meaning.
            values.append(original_text('formula',point,match[0],encoding='native_tex'))
    return {'type':'text','encoding':codec,'assets':values}
