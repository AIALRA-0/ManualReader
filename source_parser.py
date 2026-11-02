"""把原件转换成可阅读的来源单元，目录和译文均引用这些稳定编号。"""
from pathlib import Path
from collections import Counter
import ast,base64,hashlib,json,re,shutil,statistics,unicodedata,copy
from reader_translation import apply_sentence_alignment,translation_flags
from bs4 import BeautifulSoup,Tag,NavigableString

def normal(text):return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(text or ''))).strip()

class Document:
    def __init__(self,identity,title,path,kind):
        self.data={'id':identity,'title':title,'source':str(path),'kind':kind,'pages':{},'passages':[],'outline':[],'inventory':[],'translation_status':'pending','schema_version':2}
        self.current=None;self.stack=[];self.serial=0
    def add(self,text,kind='paragraph',level=0,refs=None,atoms=None,**extra):
        self.serial+=1;pid=f'{self.data["id"]}-u{self.serial:05d}'
        if kind=='heading' or extra.get('heading_text'):
            level=max(1,int(level or 1))
            while self.stack and self.stack[-1][0]>=level:self.stack.pop()
            parent=self.stack[-1][1] if self.stack else None
            self.current=pid;self.stack.append((level,pid))
            self.data['outline'].append({'id':pid,'title':extra.get('heading_text') or text,'level':level,'parent_id':parent,'passage_id':pid,'section_id':pid})
        section=self.current or self.data['id']+'-root'
        if not self.current and not self.data['outline']:
            self.data['outline'].append({'id':section,'title':self.data['title'],'level':1,'parent_id':None,'passage_id':pid,'section_id':section})
        unit={'id':pid,'section_id':section,'source_ids':atoms or [pid],'en':text,'zh':'','raw_text':text,'kind':kind,'heading':kind=='heading','heading_text':'','bullet':kind=='list','refs':refs or [],'precision':'unit','translatable':bool(text.strip()) and kind not in ('image','visual','page_image','code'),'sentences':[{'id':pid+'-s1','en':text,'zh':'','refs':refs or [],'precision':'unit','source_unit_ids':[pid]}],**extra}
        self.data['passages'].append(unit)
        for aid in unit['source_ids']:self.data['inventory'].append({'id':aid,'passage_id':pid,'kind':kind,'role':extra.get('role','content')})
        return unit
    def finish(self):
        self.data['coverage']=audit(self.data);return self.data

def audit(material):
    passages=material.get('passages',[]);inventory=material.get('inventory',[])
    owned=Counter(a for p in passages for a in p.get('source_ids',[]));ids={p['id'] for p in passages}
    missing=[a['id'] for a in inventory if a['id'] not in owned];duplicates=[k for k,v in owned.items() if v!=1]
    outline={x.get('section_id',x.get('id')) for x in material.get('outline',[])}
    orphan=[p['id'] for p in passages if p.get('section_id') not in outline]
    dangling=[x.get('id') for x in material.get('outline',[]) if x.get('passage_id') not in ids]
    textual=[p for p in passages if p.get('translatable',bool(p.get('en','').strip()))]
    ready=[p for p in textual if p.get('zh','').strip()]
    return {'inventory_units':len(inventory),'assigned_units':len(inventory)-len(missing),'missing':missing,'duplicate_assignments':duplicates,'orphan_passages':orphan,'invalid_outline_targets':dangling,'source_coverage':1 if inventory and not missing and not duplicates else 0,'outline_coverage':1 if passages and not orphan and not dangling else 0,'translation_units':len(textual),'translated_units':len(ready),'translation_coverage':len(ready)/len(textual) if textual else 1}


def _reading_ranges(text,kind):
    if kind in ('code','table','equation','math','heading'):
        return [(0,len(text),True)] if text.strip() else []
    protected=[]
    # 先记下完整代码、表格和公式的范围，换行只划分这些范围之外的普通文字。
    for match in re.finditer(r'(?m)^[ \t]*(`{3,}|~{3,})[^\r\n]*(?:\r?\n|$)',text):
        if any(a<=match.start()<b for a,b in protected):continue
        closing=re.search(r'(?m)^[ \t]*'+re.escape(match[1][0])+r'{'+str(len(match[1]))+r',}[ \t]*(?:\r?$)',text[match.end():])
        protected.append((match.start(),match.end()+closing.end() if closing else len(text)))
    for pattern in (r'\$\$[\s\S]*?\$\$',r'\\\[[\s\S]*?\\\]',r'\\\([\s\S]*?\\\)',r'(?m)^[^\r\n]*\|[^\r\n]*\r?\n[ \t]*\|?[ \t]*:?-{3,}[^\r\n]*(?:\r?\n[^\r\n]*\|[^\r\n]*)*'):
        for match in re.finditer(pattern,text):
            if not any(a<=match.start()<b for a,b in protected):protected.append((match.start(),match.end()))
    result=[];start=0
    for newline in re.finditer(r'\r\n|\r|\n',text):
        if any(a<=newline.start()<b for a,b in protected):continue
        end=newline.start();piece=text[start:end]
        if piece.strip():
            left=start+len(piece)-len(piece.lstrip());right=end-(len(piece)-len(piece.rstrip()))
            result.append((left,right,any(a<right and b>left for a,b in protected)))
        start=newline.end()
    piece=text[start:]
    if piece.strip():
        left=start+len(piece)-len(piece.lstrip());right=len(text)-(len(piece)-len(piece.rstrip()))
        result.append((left,right,any(a<right and b>left for a,b in protected)))
    return result


def attach_reading_segments(material):
    """增加供阅读与提问使用的细分范围，已有原文、句子和原子编号保持原样。"""
    for passage in material.get('passages',[]):
        segments=[]
        if passage.get('kind') not in ('image','visual','page_image'):
            for language in ('en','zh'):
                text=passage.get(language) or ''
                sentence_ranges=[];cursor=0
                for sentence in passage.get('sentences',[]):
                    value=sentence.get(language) or ''
                    if sentence.get('historical') or not value:continue
                    position=text.find(value,cursor)
                    if position>=0:sentence_ranges.append((position,position+len(value),sentence['id']));cursor=position+len(value)
                for start,end,protected in _reading_ranges(text,passage.get('kind')):
                    piece=text[start:end];segment={'id':passage['id']+'-reading-'+language+'-'+str(start)+'-'+str(end),'parent_id':passage['id'],'text':piece,'language':language,'start':start,'end':end,'protected':protected,'original' if language=='en' else 'translation':piece}
                    sentence_id=next((sid for a,b,sid in sentence_ranges if a<=start and end<=b),None)
                    if sentence_id:segment['sentence_id']=sentence_id
                    segments.append(segment)
        passage['reading_segments']=segments
    return material

def apply_translations(material,rows,complete=True):
    if not isinstance(rows,list):raise ValueError('译文必须按原文编号提交列表')
    known={p['id']:p for p in material['passages'] if p.get('translatable',bool(p.get('en','').strip()))};seen=set()
    for row in rows:
        sid=row.get('source_id')
        if sid not in known:raise ValueError('译文引用了不存在的原文编号：'+str(sid))
        if sid in seen:raise ValueError('同一个原文编号重复提交：'+sid)
        if not isinstance(row.get('text'),str) or not row['text'].strip():raise ValueError('译文不能为空：'+sid)
        if 'placeholder' in translation_flags(known[sid].get('en',''),row['text']):raise ValueError('译文必须是成品，不能提交待翻译占位符')
        apply_sentence_alignment(copy.deepcopy(known[sid]),row)
        seen.add(sid)
    missing=[sid for sid,p in known.items() if sid not in seen and not p.get('zh','').strip()]
    if complete and missing:raise ValueError('还有 '+str(len(missing))+' 个原文单元没有译文，不能标记翻译完成')
    for row in rows:
        p=known[row['source_id']];p['zh']=row['text'].strip();apply_sentence_alignment(p,row);p['translation_audit']=translation_flags(p.get('en',''),p['zh'])
    material['coverage']=audit(material);material['translation_status']='complete' if not missing else 'partial'
    return attach_reading_segments(material)

def _pdf_column_blocks(blocks,width):
    """先拆开提取器误并的左右栏，再让正文按每一栏自己的顺序阅读。"""
    middle=width/2;gap=max(5,width*.012)
    def side(line):
        box=line['bbox']
        if abs(line.get('dir',(1,0))[1])>.2:return None
        return 0 if box[2]<middle-gap else 1 if box[0]>middle+gap else None
    lines=[line for block in blocks if block.get('type')==0 for line in block.get('lines',[])]
    body=[line for line in lines if len(''.join(s['text'] for s in line['spans']).strip())>=30]
    counts=Counter(side(line) for line in body)
    split_columns=counts[0]>=3 and counts[1]>=3
    result=[]
    for index,block in enumerate(blocks):
        if block.get('type')!=0:continue
        sides={side(line) for line in block.get('lines',[])}
        if not split_columns or sides!={0,1}:
            result.append((str(index),block));continue
        for column in (0,1):
            selected=[line for line in block['lines'] if side(line)==column]
            boxes=[line['bbox'] for line in selected]
            projected={**block,'lines':selected,'bbox':(min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes))}
            result.append((str(index)+'.c'+str(column),projected))
    return result


def _pdf(path,identity,output):
    import fitz
    pdf=fitz.open(path);doc=Document(identity,pdf.metadata.get('title') or path.stem,path,'pdf');records=[];sizes=[];margins=Counter()
    for index,page in enumerate(pdf):
        raw=page.get_text('dict');page_records=[]
        for bi,block in _pdf_column_blocks(raw['blocks'],page.rect.width):
            if block['type']!=0:continue
            lines=[''.join(s['text'] for s in line['spans']) for line in block['lines']];text='\n'.join(lines).strip()
            if not text:continue
            spans=[s for line in block['lines'] for s in line['spans']];size=max(s['size'] for s in spans);sizes.extend(s['size'] for s in spans if len(s['text'])>12)
            def bold(s):return bool(s['flags']&16 or re.search(r'Bold|Semibold|CMBX|Extrabold',s['font'],re.I))
            first=block['lines'][0]['spans'];leading=''
            for span in first:
                if not bold(span) and span['text'].strip():break
                leading+=span['text']
            if first and re.fullmatch(r'\d+(?:\.\d+)*\.?\s*',first[0]['text']):
                numbered_prefix=first[0]['text']
                for span in first[1:]:
                    if span['text'].strip() and not (bold(span) or span['flags']&2):break
                    numbered_prefix+=span['text']
                if len(numbered_prefix.strip())>len(first[0]['text'].strip())+5:leading=numbered_prefix
            title_text=' '.join(''.join(s['text'] for s in line['spans']) for line in block['lines'] if max(s['size'] for s in line['spans'])>=size*.9)
            rect=list(block['bbox']);record={'text':text,'title_text':title_text,'box':rect,'size':size,'bold':any(bold(s) for s in spans),'leading':leading.strip(),'first_line':lines[0].strip(),'first_bold':all(bold(s) for s in first if s['text'].strip()),'rotated':any(abs(line.get('dir',(1,0))[1])>.2 for line in block['lines']),'atom':f'p{index+1}.b{bi}','page':index+1}
            page_records.append(record)
            if rect[1]<page.rect.height*.12 or rect[3]>page.rect.height*.88:margins[re.sub(r'\d+','#',normal(text))]+=1
        records.append(page_records)
    body=statistics.median(sizes) if sizes else 11
    candidates=[r for r in records[0] if not r['rotated'] and r['box'][1]<pdf[0].rect.height*.28 and len(re.findall(r'[A-Za-z]{2,}',r['text']))>=4 and (r['size']>body*1.12 or r['first_bold'] or r['title_text'].isupper()) and not re.match(r'^(?:Abstract[.:]|\d+(?:\.\d+)*\.?\s)',r['text'],re.I)] if records else []
    title_row=max(candidates,key=lambda r:(r['size'],-r['box'][1])) if candidates else None
    if title_row:doc.data['title']=normal(title_row['title_text'])
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    for index,page in enumerate(pdf):
        page_id=f'{identity}:page:{index+1}';image=f'page-{index+1:04d}.png';layer=f'page-{index+1:04d}.text.json'
        page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).save(out/image)
        lines=[]
        for block in page.get_text('rawdict')['blocks']:
            if block['type']!=0:continue
            for line in block['lines']:
                chars=[{'text':c['c'],'bbox':list(c['bbox'])} for span in line['spans'] for c in span['chars']]
                if chars:lines.append({'bbox':list(line['bbox']),'chars':chars})
        (out/layer).write_text(json.dumps({'page_id':page_id,'lines':lines},ensure_ascii=False),'utf8')
        doc.data['pages'][page_id]={'kind':'pdf','number':index+1,'width':page.rect.width,'height':page.rect.height,'image':image,'text_layer':layer}
        entries=records[index]
        # 两栏正文分栏阅读，跨栏标题仍按垂直位置分隔各区域
        narrow=[r for r in entries if r['box'][2]-r['box'][0]<page.rect.width*.56 and len(r['text'])>30]
        left=[r for r in narrow if (r['box'][0]+r['box'][2])/2<page.rect.width/2];right=[r for r in narrow if (r['box'][0]+r['box'][2])/2>=page.rect.width/2]
        if left and right and max(r['box'][3] for r in left)>min(r['box'][1] for r in right)+page.rect.height*.15:
            wide=sorted([r for r in entries if r['box'][2]-r['box'][0]>=page.rect.width*.56],key=lambda r:r['box'][1]);ordered=[];remaining=list(entries)
            for boundary in wide+[{'box':[0,float('inf'),0,0]}]:
                band=[r for r in remaining if r not in wide and r['box'][1]<boundary['box'][1]]
                ordered.extend(sorted(band,key=lambda r:(int((r['box'][0]+r['box'][2])/2>=page.rect.width/2),r['box'][1],r['box'][0])))
                remaining=[r for r in remaining if r not in band]
                if boundary in remaining:ordered.append(boundary);remaining.remove(boundary)
            entries=ordered+remaining
        else:entries=sorted(entries,key=lambda r:(r['box'][1],r['box'][0]))
        for row in entries:
            text=row['text'];clean=normal(text);margin=row['box'][1]<page.rect.height*.12 or row['box'][3]>page.rect.height*.88
            furniture=row['rotated'] or (margins[re.sub(r'\d+','#',clean)]>1 and margin) or bool(re.fullmatch(r'\d+',clean) and margin) or bool(re.match(r'^\d+\s+[A-Z][A-Z\s]+$',clean) and margin)
            embedded=re.match(r'^(\d{1,3}(?:\.\d+)*\.[ \t]+[^.\n]{2,110}\.)\s+\S',text)
            abstract=re.match(r'^(Abstract|Keywords)[.:]\s*\S',text,re.I)
            words=re.findall(r'[A-Za-z]{2,}',clean);math_heavy=bool(re.search(r'[∑∫∧=×÷√]|\\(?:frac|sum|int)',clean)) and len(words)<4
            numbered=bool(re.match(r'^(?:\d+(?:\.\d+)+\.?|\d+\.)[ \t]+[A-Za-z\u4e00-\u9fff]',text)) and not re.match(r'^\d+\.[ \t]+[A-Z][a-z]+,[ \t]+[A-Z]\.',text)
            named=bool(re.fullmatch(r'(?:Abstract|Introduction|Conclusion[s]?|Methods|Results|Discussion|References|Bibliography|Acknowledg(?:e)?ments?|Data availability|Code availability|Online content|Appendix(?:\s+\w+)?)',clean,re.I))
            exclude=bool(re.match(r'^(?:Proof[.:]|Theorem\s|Lemma\s|Proposition\s|Corollary\s|Definition\s|Remark\s|Example\s|\d{4}\s+Mathematics|(?:Extended Data )?(?:Fig\.?|Figure|Table)\s*\d|\d+\.[ \t]+[A-Z][a-z]+,)',clean))
            leading=row['leading'];firstline=row['first_line']
            lead_heading=leading if len(leading)>=8 and len(leading)<120 and not exclude and not re.match(r'^(?:Abstract|Keywords)[.:]',leading,re.I) else ''
            if lead_heading and re.match(r'^\d+(?:\.\d+)*\.\s',text):lead_heading=(embedded[1] if embedded else lead_heading)
            styled=(row['size']>body*1.17 and len(clean)<150 and len(text.splitlines())<=3) or bool(lead_heading and row['size']>=body*.98)
            heading=not furniture and not math_heavy and not exclude and len(clean)<180 and (numbered or named or styled)
            numbering=re.match(r'^(\d+(?:\.\d+)*)\.?\s+',clean);level=len(numbering[1].split('.')) if numbering else 1
            title=row is title_row
            if title:
                doc.data['title']=normal(row['title_text'])
                if doc.data['outline'] and doc.data['outline'][0]['id'].endswith('-root'):doc.data['outline'][0]['title']=doc.data['title']
            headtext='' if title or furniture or exclude else embedded[1] if embedded else abstract[1] if abstract else lead_heading if styled else ''
            unit=doc.add(text,'heading' if heading and not title else 'equation' if math_heavy else 'paragraph',level,refs=[{'page_id':page_id,'bbox':row['box']}],atoms=[row['atom']],role='page_furniture' if furniture else 'metadata' if title else 'content',heading_text=headtext)
            if title and doc.data['outline'] and doc.data['outline'][0]['id'].endswith('-root'):doc.data['outline'][0]['passage_id']=unit['id']
        for ii,info in enumerate(page.get_images(full=True)):
            for ri,rect in enumerate(page.get_image_rects(info[0])):
                doc.add('','image',refs=[{'page_id':page_id,'bbox':list(rect)}],atoms=[f'p{index+1}.image{ii}.{ri}'],alt='原页图片',translatable=False)
        if not entries:doc.add('','page_image',refs=[{'page_id':page_id,'bbox':list(page.rect)}],atoms=[f'p{index+1}.image-page'],alt='本页没有可提取的文字，保留完整原页',translatable=False)
    bookmarks=pdf.get_toc()
    if bookmarks:
        outline=[];stack=[];starts={}
        for number,(level,title,page_number,*_) in enumerate(bookmarks):
            candidates=[p for p in doc.data['passages'] if any(r.get('page_id')==f'{identity}:page:{page_number}' for r in p.get('refs',[]))]
            if not candidates:continue
            exact=next((p for p in candidates if normal(title).casefold() in normal(p['en']).casefold()),None);target=exact or candidates[0];sid=f'{identity}-toc{number+1}'
            while stack and stack[-1][0]>=level:stack.pop()
            outline.append({'id':sid,'title':title,'level':level,'parent_id':stack[-1][1] if stack else None,'passage_id':target['id'],'section_id':sid,'precision':'unit' if exact else 'page'})
            stack.append((level,sid));starts.setdefault(target['id'],sid)
        if outline:
            section=outline[0]['id']
            for p in doc.data['passages']:
                if p['id'] in starts:section=starts[p['id']]
                p['section_id']=section
            doc.data['outline']=outline;doc.data['outline_source']='pdf_bookmarks'
    pdf.close()
    return doc.finish()

def _asset(blob,extension,output):
    folder=Path(output);folder.mkdir(parents=True,exist_ok=True)
    import hashlib
    name='media-'+hashlib.sha256(blob).hexdigest()[:18]+extension.lower()
    (folder/name).write_bytes(blob)
    return '/asset/'+name

def _html(path,identity,output,base_url=None,resource_root=None):
    from urllib.parse import urljoin
    soup=BeautifulSoup(path.read_text('utf8',errors='replace'),'html.parser');doc=Document(identity,soup.title.get_text(' ',strip=True) if soup.title else path.stem,path,'html')
    if base_url:
        base=soup.find('base',href=True);base_url=urljoin(base_url,base['href']) if base else base_url
    skipped={'script','style','noscript','template','svg','head'};atomic=0
    def inline(node):
        if isinstance(node,NavigableString):return str(node)
        if not isinstance(node,Tag):return ''
        if node.name in skipped or node.has_attr('hidden') or node.get('aria-hidden')=='true' or 'display:none' in node.get('style','').replace(' ',''):return ''
        inner=''.join(inline(c) for c in node.children)
        if node.name in ('strong','b'):return '**'+inner+'**'
        if node.name=='code':return '`'+inner+'`'
        if node.name=='br':return '\n'
        if node.name=='a':
            href=urljoin(base_url,node.get('href','')) if base_url else node.get('href','')
            if str(href).startswith(('http:','https:')):return '['+inner+']('+href+')'
        return inner
    def visit(node):
        nonlocal atomic
        if isinstance(node,NavigableString):
            if normal(node):atomic+=1;doc.add(str(node),'paragraph',atoms=[f'dom{atomic}'])
            return
        if not isinstance(node,Tag) or node.name in skipped or node.has_attr('hidden') or node.get('aria-hidden')=='true' or 'display:none' in node.get('style','').replace(' ',''):return
        if node.name in ('nav','footer') or node.get('role') in ('navigation','contentinfo'):
            atomic+=1;doc.add(inline(node),'paragraph',atoms=[f'dom{atomic}'],role='page_furniture',translatable=False);return
        if node.name=='img':
            atomic+=1;src=node.get('src','');missing=False
            if base_url and src and not src.startswith('data:'):src=urljoin(base_url,src)
            if src.startswith('data:image/'):
                import binascii
                try:
                    header,payload=src.split(',',1);extension='.'+header.split('/')[1].split(';')[0];src=_asset(base64.b64decode(payload),extension,output)
                except (ValueError,binascii.Error):missing=True
            elif src and not re.match(r'^(?:https?:|//)',src):
                local=(path.parent/src.split('#')[0].split('?')[0]).resolve()
                if local.is_relative_to(Path(resource_root or path.parent).resolve()) and local.is_file() and local.suffix.lower() in ('.png','.jpg','.jpeg','.webp','.gif','.svg'):src=_asset(local.read_bytes(),local.suffix,output)
                else:missing=True
            doc.add(node.get('alt',''),'image',atoms=[f'dom{atomic}'],src='' if missing else src,source_src=node.get('src',''),asset_missing=missing,alt=node.get('alt','原文图片'),translatable=False);return
        if re.fullmatch('h[1-6]',node.name):atomic+=1;doc.add(inline(node),'heading',int(node.name[1]),atoms=[f'dom{atomic}']);return
        if node.name in ('p','pre','table','blockquote','math','li'):
            if node.name=='li' and node.find(['ul','ol'],recursive=False):
                atomic+=1;doc.add(''.join(inline(c) for c in node.children if not isinstance(c,Tag) or c.name not in ('ul','ol')),'list',atoms=[f'dom{atomic}'])
                for child in node.find_all(['ul','ol'],recursive=False):visit(child)
                return
            atomic+=1
            if node.name=='table':
                rows=[[inline(cell).strip() for cell in tr.find_all(['td','th'],recursive=False)] for tr in node.find_all('tr')];text='\n'.join(' | '.join(r) for r in rows);doc.add(text,'table',atoms=[f'dom{atomic}'],rows=rows,header_rows=[i for i,tr in enumerate(node.find_all('tr')) if tr.find('th')])
            elif node.name=='pre':doc.add(node.get_text(),'code',atoms=[f'dom{atomic}'],language='text')
            else:doc.add(inline(node),'list' if node.name=='li' else 'equation' if node.name=='math' else 'paragraph',atoms=[f'dom{atomic}'])
            for img in node.find_all('img'):visit(img)
            return
        for child in node.children:visit(child)
    visit(soup.body or soup)
    return doc.finish()

def _markdown(path,identity,output,resource_root=None):
    lines=path.read_text('utf8',errors='replace').splitlines(keepends=True);doc=Document(identity,path.stem,path,'markdown');i=0
    while i<len(lines):
        start=i;text=lines[i];head=re.match(r'^(#{1,6})\s+(.+)',text);fence=re.match(r'^\s*(`{3,}|~{3,})(\w*)',text)
        if head:doc.add(head[2].strip(),'heading',len(head[1]),atoms=[f'line{i+1}'],line_start=i+1,line_end=i+1);i+=1;continue
        if fence:
            i+=1
            while i<len(lines) and not re.match(r'^\s*'+re.escape(fence[1][0])+r'{'+str(len(fence[1]))+r',}\s*$',lines[i]):i+=1
            if i<len(lines):i+=1
            doc.add(''.join(lines[start:i]),'code',atoms=[f'line{j+1}' for j in range(start,i)],line_start=start+1,line_end=i,language=fence[2] or 'text');continue
        if i+1<len(lines) and re.match(r'^\s*(?:=+|-+)\s*$',lines[i+1]) and normal(text):
            doc.add(text.strip(),'heading',1 if '=' in lines[i+1] else 2,atoms=[f'line{i+1}',f'line{i+2}']);i+=2;continue
        i+=1
        while i<len(lines) and normal(lines[i]) and not re.match(r'^(?:#{1,6}\s|\s*`{3,}|\s*~{3,})',lines[i]):i+=1
        value=''.join(lines[start:i]);kind='table' if re.search(r'^\s*\|?\s*:?-{3,}',value,re.M) else 'list' if re.match(r'\s*(?:[-*+]\s|\d+[.)]\s)',value) else 'paragraph'
        unit=doc.add(value,kind,atoms=[f'line{j+1}' for j in range(start,i)],line_start=start+1,line_end=i,role='content' if normal(value) else 'whitespace')
        for number,image in enumerate(re.finditer(r'!\[([^\]]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)',value)):
            alt,src=image.groups();source=src
            if not re.match(r'^(?:https?:|//)',src):
                local=(path.parent/src).resolve();src=_asset(local.read_bytes(),local.suffix,output) if local.is_relative_to(Path(resource_root or path.parent).resolve()) and local.is_file() and local.suffix.lower() in ('.png','.jpg','.jpeg','.webp','.gif','.svg') else ''
            unit.setdefault('inline_media',[]).append(image[0]);doc.add('','image',atoms=[f'line{start+1}.image{number}'],src=src,source_src=source,asset_missing=not bool(src),alt=alt or '原文图片',translatable=False)
    return doc.finish()

def _slide_previews(source, identity, output):
    """按 PPT 中保存的位置展示文字、表格和图片，原始 PPTX 仍可单独下载。"""
    from html import escape
    import textwrap
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    width=source.slide_width/12700;height=source.slide_height/12700;pages={}
    for i,slide in enumerate(source.slides,1):
        image=f'page-{i:04d}.svg';layer=f'page-{i:04d}.text.json';pid=f'{identity}:slide:{i}'
        parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/>'];lines=[]
        def shapes(items,dx=0,dy=0):
            for shape in items:
                x=shape.left/12700+dx;y=shape.top/12700+dy;w=shape.width/12700;h=shape.height/12700
                if shape.shape_type==6:
                    # 组内坐标由 PPT 保存的子形状位置提供，后续仍能定位到完整组。
                    shapes(shape.shapes);continue
                if shape.shape_type==13:
                    raw=base64.b64encode(shape.image.blob).decode();mime=shape.image.content_type
                    parts.append(f'<image x="{x}" y="{y}" width="{w}" height="{h}" href="data:{mime};base64,{raw}"/>');continue
                if shape.has_table:
                    table=shape.table;top=y
                    for ri,row in enumerate(table.rows):
                        rh=row.height/12700;left=x
                        for ci,cell in enumerate(row.cells):
                            cw=table.columns[ci].width/12700
                            parts.append(f'<rect x="{left}" y="{top}" width="{cw}" height="{rh}" fill="white" stroke="#777" stroke-width="0.6"/>')
                            text=escape(cell.text);parts.append(f'<text x="{left+4}" y="{top+14}" font-size="11" fill="black">{text}</text>');left+=cw
                        top+=rh
                    continue
                if shape.has_text_frame:
                    top=y+4
                    for paragraph in shape.text_frame.paragraphs:
                        size=next((r.font.size.pt for r in paragraph.runs if r.font.size),None) or (24 if shape==slide.shapes.title else 18)
                        text=paragraph.text;capacity=max(1,int(max(10,w-8)/(size*.53)))
                        wrapped=textwrap.wrap(text,capacity,replace_whitespace=False,break_long_words=False) or ['']
                        for text in wrapped:
                            top+=size*1.2;bold=any(r.font.bold for r in paragraph.runs)
                            parts.append(f'<text x="{x+4}" y="{top}" font-family="Arial, sans-serif" font-size="{size}" font-weight="{700 if bold else 400}" fill="black">{escape(text)}</text>')
                            chars=[{'text':c,'bbox':[x+4+j*size*.53,top-size,x+4+(j+1)*size*.53,top+size*.2]} for j,c in enumerate(text)]
                            if chars:lines.append({'bbox':[x,top-size,x+w,top+size*.2],'chars':chars})
                    continue
                # 原生几何形状保留其范围和名称，供用户就该对象提问。
                parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="#555" stroke-width="0.8"/>')
        shapes(slide.shapes);parts.append('</svg>');(out/image).write_text(''.join(parts),'utf8')
        (out/layer).write_text(json.dumps({'page_id':pid,'lines':lines},ensure_ascii=False),'utf8')
        pages[pid]={'kind':'slide','number':i,'width':width,'height':height,'image':image,'text_layer':layer}
    return pages

def _render_slides(path, source, identity, output):
    """服务器优先用完整排版引擎转换原页，提问编号仍沿用 PPT 对象。"""
    import os, subprocess, tempfile
    binary=os.environ.get('MANUAL_READER_OFFICE_BIN')
    if not binary:
        return _slide_previews(source,identity,output),'structured'
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='reader-office-') as temporary:
            directory=Path(temporary);profile=directory/'profile'
            subprocess.run([binary,'-env:UserInstallation='+profile.as_uri(),'--headless','--norestore','--convert-to','pdf:impress_pdf_Export','--outdir',str(directory),str(Path(path).resolve())],check=True,capture_output=True,timeout=180)
            rendered=directory/(Path(path).stem+'.pdf')
            import fitz
            with fitz.open(rendered) as document:
                if len(document)!=len(source.slides):raise ValueError('转换后幻灯片页数不一致')
                pages={}
                for i,page in enumerate(document,1):
                    image=f'page-{i:04d}.png';layer=f'page-{i:04d}.text.json';pid=f'{identity}:slide:{i}'
                    page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).save(out/image)
                    lines=[]
                    for block in page.get_text('rawdict')['blocks']:
                        if block['type']!=0:continue
                        for line in block['lines']:
                            chars=[{'text':c['c'],'bbox':list(c['bbox'])} for span in line['spans'] for c in span['chars']]
                            if chars:lines.append({'bbox':list(line['bbox']),'chars':chars})
                    (out/layer).write_text(json.dumps({'page_id':pid,'lines':lines},ensure_ascii=False),'utf8')
                    pages[pid]={'kind':'slide','number':i,'width':page.rect.width,'height':page.rect.height,'image':image,'text_layer':layer}
                return pages,'libreoffice'
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        # 原件与对象仍可阅读；转换错误作为解析事实保存，避免吞掉原材料。
        return _slide_previews(source,identity,output),'structured: '+str(error)[:200]

def _office(path,identity,output):
    doc=Document(identity,path.stem,path,path.suffix[1:])
    if path.suffix.lower()=='.docx':
        from docx import Document as WordDocument
        from docx.text.paragraph import Paragraph
        from docx.table import Table
        source=WordDocument(path)
        for i,node in enumerate(source.element.body):
            if node.tag.endswith('}p'):
                p=Paragraph(node,source);style=p.style.name if p.style else '';head=re.search(r'Heading\s*(\d)',style,re.I);is_title=style=='Title'
                if is_title:doc.data['title']=p.text
                formatted=''.join(('**'+run.text+'**' if run.bold else '*'+run.text+'*' if run.italic else run.text) for run in p.runs)
                doc.add(formatted or p.text,'heading' if head or is_title else 'paragraph',int(head[1]) if head else 1 if is_title else 0,atoms=[f'word{i}'])
                for n,image in enumerate(node.xpath('.//*[local-name()="blip"]')):
                    relation=image.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed');part=source.part.related_parts.get(relation);src=_asset(part.blob,Path(part.partname).suffix,output) if part else ''
                    doc.add('','image',atoms=[f'word{i}.image{n}'],src=src,alt='文档图片',translatable=False)
                for n,formula in enumerate(node.xpath('.//*[local-name()="oMath"]')):doc.add(''.join(formula.itertext()),'equation',atoms=[f'word{i}.equation{n}'])
            elif node.tag.endswith('}tbl'):
                rows=[[c.text for c in row.cells] for row in Table(node,source).rows];doc.add('\n'.join(' | '.join(row) for row in rows),'table',atoms=[f'word{i}'],rows=rows)
    else:
        from pptx import Presentation
        source=Presentation(path)
        doc.data['pages'],doc.data['preview_renderer']=_render_slides(path,source,identity,output)
        for i,slide in enumerate(source.slides):
            def position(shape):return [{'page_id':f'{identity}:slide:{i+1}','bbox':[shape.left/12700,shape.top/12700,(shape.left+shape.width)/12700,(shape.top+shape.height)/12700]}]
            title=slide.shapes.title.text if slide.shapes.title else f'第 {i+1} 页';doc.add(title,'heading',1,atoms=[f'slide{i+1}.title'],refs=position(slide.shapes.title) if slide.shapes.title else [])
            for j,shape in enumerate(slide.shapes):
                if shape==slide.shapes.title:continue
                atom=f'slide{i+1}.shape{j}'
                if shape.has_table:
                    rows=[[c.text for c in row.cells] for row in shape.table.rows];doc.add('\n'.join(' | '.join(row) for row in rows),'table',atoms=[atom],rows=rows,refs=position(shape))
                elif shape.has_text_frame:doc.add(shape.text,'paragraph',atoms=[atom],refs=position(shape))
                elif shape.shape_type==13:doc.add('','image',atoms=[atom],src=_asset(shape.image.blob,'.'+shape.image.ext,output),alt=shape.name or '幻灯片图片',translatable=False,refs=position(shape))
                else:doc.add('','visual',atoms=[atom],alt=shape.name or '幻灯片图形',translatable=False,refs=position(shape))
    return doc.finish()

CODE_EXTENSIONS={'.py','.pyi','.js','.jsx','.mjs','.cjs','.ts','.tsx','.sv','.svh','.v','.vh','.vhd','.vhdl','.c','.cpp','.cc','.cxx','.h','.hpp','.java','.kt','.kts','.go','.rs','.rb','.php','.swift','.scala','.sh','.bash','.zsh','.ps1','.bat','.cmd','.sql','.r','.R','.m','.lua','.pl','.ex','.exs','.erl','.clj','.cs','.fs','.dart','.vue','.svelte','.css','.scss','.sass','.less','.json','.jsonl','.yaml','.yml','.toml','.ini','.cfg','.conf','.xml','.xsd','.gradle','.cmake','.tex'}
IMAGE_EXTENSIONS={'.png','.jpg','.jpeg','.webp','.gif','.svg'}
IGNORED_DIRECTORIES={'.git','node_modules','__pycache__','.venv','venv'}


def _locate(document,relative):
    """每个片段记录原文件和原单元，页框与行号继续使用解析器给出的坐标。"""
    document['source_path']=relative
    for unit in document['passages']:
        unit.setdefault('path',relative)
        location={'path':relative,'source_unit_ids':list(unit.get('source_ids',[]))}
        if unit.get('line_start') is not None:location.update(line_start=unit['line_start'],line_end=unit['line_end'])
        if unit.get('refs'):location['refs']=unit['refs']
        if document['kind']=='html':location['dom_units']=list(unit.get('source_ids',[]))
        unit['source_location']=location
    return document


def _directory(path,identity,output):
    doc=Document(identity,path.name,path,'repository');manifest=[];pending=[]
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    for file in sorted(path.rglob('*')):
        if not file.is_file():continue
        rel=file.relative_to(path).as_posix()
        if file.is_symlink() or not file.resolve().is_relative_to(path.resolve()):
            pending.append({'path':rel,'status':'skipped','action':'upload_regular_file','message':'该项是符号链接，请上传它指向的普通文件后再导入'});manifest.append({'path':rel,'kind':'symlink','status':'skipped','passages':0});continue
        if any(x in IGNORED_DIRECTORIES for x in file.relative_to(path).parts):
            pending.append({'path':rel,'status':'skipped','action':'review_excluded_file','message':'原目录保留了该文件；解析时跳过依赖、版本库和缓存目录'});manifest.append({'path':rel,'kind':'excluded','status':'skipped','passages':0});continue
        heading=doc.add(rel,'heading',1,atoms=[rel+'#file'],path=rel,translatable=False)
        token=hashlib.sha256(rel.encode('utf8')).hexdigest()[:16];child_id=identity+'-src-'+token;child_output=output/('source-'+token)
        try:
            child=parse(file,child_id,child_output,source_path=rel,resource_root=path)
        except Exception as error:
            doc.add('','visual',atoms=[rel+'#unparsed'],path=rel,alt='尚未解析的文件：'+rel,translatable=False)
            issue={'path':rel,'status':'failed','action':'review_file','message':'原件已保留，解析失败：'+str(error)[:250]};pending.append(issue)
            manifest.append({'path':rel,'kind':file.suffix[1:] or 'binary','status':'failed','passages':0,'pending':[issue]});continue
        # 每个 PDF/PPT 的页预览先在自己的目录生成，再用文件路径派生的前缀存放，避免相互覆盖。
        renamed={}
        if child_output.exists():
            for asset in child_output.iterdir():
                if not asset.is_file():continue
                name=asset.name if asset.name.startswith('media-') else 'source-'+token+'-'+asset.name
                target=output/name
                if not target.exists():shutil.copyfile(asset,target)
                renamed[asset.name]=name
            shutil.rmtree(child_output)
        for page in child['pages'].values():
            for key in ('image','text_layer'):
                if page.get(key) in renamed:page[key]=renamed[page[key]]
            page['path']=rel
        for unit in child['passages']:
            if unit.get('src','').startswith('/asset/'):
                name=unit['src'].split('/')[-1];unit['src']='/asset/'+renamed.get(name,name)
            unit['source_ids']=[rel+'#'+atom for atom in unit.get('source_ids',[])]
        for atom in child['inventory']:atom['id']=rel+'#'+atom['id'];atom['path']=rel
        for outline in child['outline']:
            outline['level']=outline.get('level',1)+1
            if not outline.get('parent_id'):outline['parent_id']=heading['id']
            outline['path']=rel
        doc.data['passages'].extend(child['passages']);doc.data['inventory'].extend(child['inventory']);doc.data['outline'].extend(child['outline']);doc.data['pages'].update(child['pages'])
        issues=[]
        if not any(p.get('raw_text','').strip() or p['kind'] in ('image','page_image') for p in child['passages']) or child.get('kind')=='binary':
            issue={'path':rel,'status':'pending','action':'provide_readable_source','message':'原件已保留；请提供可读取的文本、PDF、Office 文档或图片后继续解析'};issues.append(issue);pending.append(issue)
        manifest.append({'path':rel,'kind':child['kind'],'status':'parsed' if not issues else 'pending','passages':len(child['passages']),'coverage':child['coverage'],'pending':issues})
    doc.data['source_files']=manifest;doc.data['pending']=pending
    return doc.finish()


def parse(path,identity=None,output=None,*,source_path=None,source_url=None,resource_root=None):
    path=Path(path).resolve();identity=identity or 'doc-'+re.sub(r'[^A-Za-z0-9_-]','-',path.stem)[:60];output=Path(output or path.parent/(path.stem+'-assets'))
    if path.is_dir():return attach_reading_segments(_directory(path,identity,output))
    document=_parse_file(path,identity,output,source_url,resource_root)
    return attach_reading_segments(_locate(document,source_path or path.name))


def _parse_file(path,identity,output,source_url=None,resource_root=None):
    ext=path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        doc=Document(identity,path.stem,path,'image');src=_asset(path.read_bytes(),ext,output)
        p=doc.add('','image',src=src,alt=path.name,translatable=False)
        p['sentences'][0]['en']=path.name;p['sentences'][0]['precision']='object'
        return doc.finish()
    if ext=='.pdf':
        from reader_learning import group_pdf_figures
        return group_pdf_figures(_pdf(path,identity,output),path,output)
    if ext in ('.html','.htm'):return _html(path,identity,output,source_url,resource_root)
    if ext in ('.md','.markdown'):return _markdown(path,identity,output,resource_root)
    if ext in ('.docx','.pptx'):return _office(path,identity,output)
    raw=path.read_bytes();code=ext in CODE_EXTENSIONS or path.name.lower() in ('dockerfile','makefile','cmakelists.txt','justfile')
    try:
        text=raw.decode('utf-8-sig')
        if '\x00' in text:raise UnicodeError('文件包含二进制字节')
    except UnicodeError:
        doc=Document(identity,path.name,path,'binary');doc.add('','visual',alt='二进制文件：'+path.name,translatable=False);return doc.finish()
    doc=Document(identity,path.name,path,'code' if code else 'text')
    count=max(1,len(text.splitlines()));doc.add(text,'code' if code else 'paragraph',atoms=[f'line{i+1}' for i in range(count)],language=ext[1:],line_start=1,line_end=count)
    return doc.finish()
