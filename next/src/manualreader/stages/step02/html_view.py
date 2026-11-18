from pathlib import Path
import base64
import re
import unicodedata
from bs4 import BeautifulSoup, Tag, NavigableString
from ...source_formats import html_soup

def normal(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(text or ''))).strip()

class Document:
    def __init__(self, *args):
        self.rows = []
    def add(self, value, kind='paragraph', level=0, atoms=None, **extra):
        self.rows.append({'source_ids':atoms or [],'raw_text':value,'kind':kind,**extra})
    def finish(self):
        return {'passages':self.rows}

def _asset(blob, extension, output):
    # Original images need their own verified carrier; this projection indexes DOM positions only.
    return ''

def html_document(path,identity,output,base_url=None,resource_root=None):
    from urllib.parse import urljoin
    soup=html_soup(path.read_bytes());doc=Document(identity,soup.title.get_text(' ',strip=True) if soup.title else path.stem,path,'html')
    if base_url:
        base=soup.find('base',href=True);base_url=urljoin(base_url,base['href']) if base else base_url
    skipped={'script','style','noscript','template','svg','head'};atomic=0
    tables={id(table):i for i,table in enumerate(soup.find_all('table'))}
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
                native_rows=[r for r in node.find_all('tr') if r.find_parent('table') is node]
                rows=[[inline(cell).strip() for cell in tr.find_all(['td','th'],recursive=False)] for tr in native_rows]
                text='\n'.join(' | '.join(r) for r in rows)
                doc.add(text,'table',atoms=[f'dom{atomic}'],rows=rows,table_index=tables[id(node)],
                        header_rows=[i for i,tr in enumerate(native_rows) if tr.find('th')])
            elif node.name=='pre':doc.add(node.get_text(),'code',atoms=[f'dom{atomic}'],language='text')
            else:doc.add(inline(node),'list' if node.name=='li' else 'equation' if node.name=='math' else 'paragraph',atoms=[f'dom{atomic}'])
            for img in node.find_all('img'):visit(img)
            return
        for child in node.children:visit(child)
    visit(soup.body or soup)
    return doc.finish()
