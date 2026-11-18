"""Build and enforce the shared source quality manifest without changing Reader history."""
import asyncio
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from io import BytesIO
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import unquote, urljoin, urlsplit

from PIL import Image, ImageChops, ImageOps
from reader_source_quality_pdf import VISUAL, inspect_page, native_text, short_fragment
from reader_source_wire import (MAX_IMAGE_BYTES, digest, file_hash, put,
                                require_visual_payload, visual_expectations)

VERSION='source-quality-v1'
ALGORITHM_REVISION='layout-grid-v2'
READY={'verified_visual','verified_structured'}


def fingerprint(data):
    return digest(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())


def read(path):
    return json.loads(Path(path).read_bytes())


def decode_image(data):
    if not data or len(data)>MAX_IMAGE_BYTES:
        raise ValueError('image is empty or exceeds the size limit')
    with Image.open(BytesIO(data)) as source:
        if min(source.size)<6 or source.width*source.height>26_000_000:
            raise ValueError('image has unusable dimensions')
        source.load()
        image=ImageOps.exif_transpose(source).convert('RGBA')
    base=Image.new('RGBA',image.size,'white')
    base.alpha_composite(image)
    image=base.convert('RGB')
    sample=image.copy()
    sample.thumbnail((550,550))
    bg=Image.new('RGB',sample.size,sample.getpixel((0,0)))
    ink=ImageChops.difference(sample,bg).convert('L').point(lambda c: 255 if c>=13 else 0)
    if not ink.getbbox() or sum(ink.histogram()[20:])<5:
        raise ValueError('blank or uniform image')
    return image


def store_visual(data,target):
    image=decode_image(data)
    image.thumbnail((3500,3500))
    buffer=BytesIO()
    image.save(buffer,format='PNG')
    packed=buffer.getvalue(); hashed=digest(packed)
    path=target/'assets'/(hashed+'.png')
    if not path.is_file() or file_hash(path)!=hashed:
        put(path,packed)
    return {'path':'assets/'+path.name,'sha256':hashed,'bytes':len(packed),
            'width':image.width,'height':image.height,'mime':'image/png'}


def checked_asset(record,root):
    c=record.get('carrier') or {}
    name=c.get('path','')
    if not re.fullmatch(r'assets/[a-f0-9]{64}\.png',name) or record['status'] not in READY:
        return None
    path=(root/name).resolve()
    if not path.is_relative_to(root.resolve()):
        return None
    try:
        data=path.read_bytes()
        if digest(data)==c['sha256']==path.stem and len(data)==c['bytes']:
            decode_image(data)
            return path
    except (OSError,ValueError,KeyError):
        pass
    return None


def asset_record(file,page,kind,region,sources,method,evidence):
    key=fingerprint([VERSION,file['file_id'],file['original_sha256'],page,kind,region,evidence])
    return {'asset_id':'source-asset-'+key[:24],'file_id':file['file_id'],'file_name':file['file_name'],
            'source_version':file['source_version'],'original_sha256':file['original_sha256'],
            'page_id':page,'bounds':region,'kind':kind,'member_source_ids':sorted(set(sources)),
            'attempts':[{'method':method,'status':'found'}],'evidence':evidence,'status':'discovered'}


def register_group(record):
    return 'source-group-'+fingerprint([record['asset_id'],record['member_source_ids']])[:24]


def validate_manifest(manifest):
    assets=manifest['assets']
    mapped=manifest['sources']
    if any(a['asset_id']!=aid or a['group_id']!=register_group(a)
           or any(sid not in mapped or aid not in mapped[sid]['asset_ids'] for sid in a['member_source_ids'])
           for aid,a in assets.items()):
        raise ValueError('来源分组和原文件的登记编号不一致')
    if any(sid!=r['source_id'] or len(set(r['asset_ids']))!=len(r['asset_ids']) or
           any(a not in assets or sid not in assets[a]['member_source_ids'] for a in r['asset_ids'])
           for sid,r in mapped.items()):
        raise ValueError('来源编号重复，或指向一个没有登记的视觉区域')


class SourceGate:
    def __init__(self,manifest, root):
        if manifest.get('version')!=VERSION:
            raise ValueError('原件需要按当前版本重新核验')
        validate_manifest(manifest)
        self.manifest=manifest
        self.root=Path(root)

    def admit(self,rows,key='id',*,requires_structure=False,max_images=None):
        images=[]; payload=[]; denied={}; checked={}
        for original in rows:
            sid=original[key]; row={**original}
            disposition=self.manifest['sources'].get(sid)
            if not disposition:
                denied[sid]='来源没有通过原件核对'
                continue
            if disposition['status']=='verified_text':
                payload.append(row)
                continue
            if disposition['status'] not in READY:
                denied[sid]=disposition.get('reason') or disposition['status']
                continue
            linked=[]
            for aid in disposition['asset_ids']:
                asset=self.manifest['assets'][aid]
                structure=asset.get('structure') or {}
                if structure.get('format') in ('literal','html_table') and asset['status']=='verified_structured':
                    raw=structure.get('text')
                    hashed=digest(raw.encode()) if raw is not None else fingerprint(structure)
                    if hashed==asset['carrier']['sha256']:
                        row['verified_structure']=structure
                        if 'original' in row and raw is not None:
                            row['original']=raw
                    continue
                if aid not in checked:
                    checked[aid]=checked_asset(asset,self.root)
                path=checked[aid]
                if path and (not requires_structure or asset['status']=='verified_structured'):
                    linked.append((asset,path))
            if not linked and row.get('verified_structure') and len(disposition['asset_ids'])==1:
                payload.append(row)
                continue
            additional=[p for _,p in linked if p not in images]
            if (not linked or len(linked)!=len(disposition['asset_ids']) or
                    max_images is not None and len(set(additional))+len(images)>max_images):
                denied[sid]='现有请求无法携带这个来源所需的全部已验载体'
                continue
            attachments=[]
            for a,path in linked:
                if path not in images:
                    images.append(path)
                attachments.append({'asset_id':a['asset_id'],'group_id':a['group_id'],
                     'attachment_number':images.index(path)+1,'sha256':a['carrier']['sha256'],
                     'page_id':a['page_id'],'bbox':a['bounds'],'kind':a['kind'],
                     'status':a['status'],**({'structure':a['structure']} if a.get('structure') else {})})
            # The raw fragment remains in the immutable snapshot. In semantic requests, the attachment is authoritative.
            for field in ('original','text'):
                if field in row:
                    row[field]=''
            for obsolete in ('media_items','sentences','src'):
                row.pop(obsolete,None)
            row['source_evidence']=attachments
            payload.append(row)
        return payload,tuple(images),denied


def web_bytes(src, file, folder):
    if src.startswith('data:'):
        import base64
        match=re.fullmatch(r'data:image/[a-zA-Z0-9+.-]+;base64,(.*)',src,re.S)
        if not match:
            raise ValueError('原件的嵌入图不是可读取的 Base64 图片')
        yield 'embedded_image',base64.b64decode(match[1],validate=True)
    elif urlsplit(src).scheme in ('http','https'):
        from reader_import import fetch_url
        sources=[src]
        decoded=unquote(src.rsplit('/',1)[-1])
        if urlsplit(decoded).scheme=='https':
            sources.append(decoded)
        for url in dict.fromkeys(sources):
            for attempt in range(2):
                try:
                    raw,_,_=fetch_url(url)
                    yield 'registered_url' if url==src else 'declared_original_image_url',raw
                    break
                except (OSError,ValueError) as exc:
                    yield 'http_retry' if attempt==0 else 'http_error',exc
                    if attempt==0:
                        time.sleep(0.15)
    else:
        leaf=unquote(src.split('?',1)[0].split('#',1)[0]).replace('\\','/')
        scoped=[(folder/'assets',Path(leaf).name)] if leaf.startswith(('/asset/','/content-files/')) else [
            (folder/'source',leaf),(folder/'assets',Path(leaf).name),(folder,leaf)]
        for root,name in scoped:
            path=(root/name).resolve()
            if path.is_relative_to(root.resolve()) and path.is_file():
                yield 'archived_local_asset',path.read_bytes()
        if not leaf.startswith('/') and file.get('source_url'):
            url=urljoin(file['source_url'],src)
            if urlsplit(url).scheme in ('http','https'):
                yield from web_bytes(url,file,folder)


def flat_svg(raw, rowsize):
    if re.search(br'<(?:script|foreignObject)|(?:href|src)\s*=\s*["\'](?!#)|url\((?!#)|<!DOCTYPE|<!ENTITY',raw,re.I):
        raise ValueError('SVG 需要无法独立验证的外部内容')
    import cairosvg
    return cairosvg.svg2png(bytestring=raw, output_width=rowsize)


def html_inventory(path):
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(path.read_text('utf8',errors='replace'),'html.parser')
    rows=[]
    for index,tag in enumerate(soup.find_all(('img','svg','math','table')),1):
        hidden=any(p.has_attr('hidden') or p.get('aria-hidden')=='true' or
                   'display:none' in p.get('style','').replace(' ','') for p in (tag,*tag.parents)
                   if getattr(p,'name',None))
        sizes=[tag.get(k,'') for k in ('width','height')]
        icon=(tag.name=='svg' and all(re.fullmatch(r'\d+(?:px)?',v) and int(v.removesuffix('px'))<=24
                                      for v in sizes) and not tag.find(('text','title','desc')))
        note='hidden_in_original_dom' if hidden else 'small_unlabelled_svg' if icon else 'candidate'
        rows.append({'dom_visual_order':index,'tag':tag.name,'decision':note,
                     'width':sizes[0],'height':sizes[1],'role':tag.get('role'),
                     'aria_label':tag.get('aria-label'),'dom_id':tag.get('id'),
                     'src':str(tag.get('src',''))[:450] if tag.name=='img' else ''})
    return rows


def html_table(path, expected):
    if not expected:
        return None
    from bs4 import BeautifulSoup
    document=BeautifulSoup(path.read_text('utf8',errors='replace'),'html.parser')
    matches=[]
    norm=lambda t:' '.join(t.split())
    for table in document.find_all('table'):
        if table.find('table'):
            continue
        rows=[]; fields=[]
        for tr in table.find_all('tr'):
            logical=[]; cells=[]
            for cell in tr.find_all(('th','td'),recursive=False):
                value=norm(cell.get_text(' ',strip=True))
                try:
                    rowspan=int(cell.get('rowspan','1')); colspan=int(cell.get('colspan','1'))
                except ValueError:
                    break
                if not 1<=rowspan<=100 or not 1<=colspan<=100:
                    break
                cells.append({'text':value,'rowspan':rowspan,'colspan':colspan,'header':cell.name=='th'})
                logical.append(value)
            else:
                fields.append(cells); rows.append(logical)
                continue
            break
        else:
            if rows==[[norm(v) for v in values] for values in expected]:
                matches.append({'format':'html_table','cells':fields,'basis':'archived_dom',
                    'caption':table.caption.get_text(' ',strip=True) if table.caption else ''})
    return matches[0] if len(matches)==1 else None


def office_crop(file,row,folder):
    from reader_source_quality_pdf import coords
    for ref in row.get('refs',[]):
        page=file.get('document',{}).get('pages',{}).get(ref.get('page_id'),{})
        if not page or file['document'].get('preview_renderer')!='libreoffice':
            continue
        path=(folder/'assets'/Path(page['image']).name).resolve()
        if not path.is_relative_to(folder.resolve()) or not path.is_file():
            continue
        r=coords(ref)
        if r.is_empty:
            continue
        image=decode_image(path.read_bytes())
        x,y=image.width/page['width'],image.height/page['height']
        out=BytesIO()
        image.crop((max(0,int(r.x0*x)),max(0,int(r.y0*y)),min(image.width,int(r.x1*x+1)),
                    min(image.height,int(r.y1*y+1)))).save(out,format='PNG')
        return out.getvalue(),ref
    return None


def web_asset(meta,file,folder,row,source,target,override=None):
    sid=row['id']
    kind=row['kind']
    native_path=row.get('src') or row.get('source_src') or row.get('asset') or ''
    if source and file['kind']=='html' and kind=='table':
        structure=html_table(source,row.get('rows'))
        if structure:
            a=asset_record(meta,None,kind,None,[sid],'html_table_cells_and_spans',
                {'source_locator':row.get('source_location')})
            a.update(status='verified_structured',structure=structure,
                     carrier={'sha256':fingerprint(structure),'mime':'application/json',
                              'bytes':len(json.dumps(structure,ensure_ascii=False).encode())})
            return a
    if not native_path and kind in ('table','code','code_block'):
        val=native_text(row)
        raw=source.read_text('utf8',errors='replace') if source else ''
        if val.strip() and val in raw:
            kind='code' if kind.startswith('code') else kind
            a=asset_record(meta,None,kind,None,[sid],'literal_original_content',{'source_locator':row.get('source_location')})
            if kind=='code':
                a.update(status='verified_structured',structure={'format':'literal','text':val},
                         carrier={'sha256':digest(val.encode()),'bytes':len(val.encode()),'mime':'text/plain'})
                return a
    evidence={'registered_src':native_path,'source_locator':row.get('source_location')}
    if override:
        evidence['supplement_sha256']=file_hash(override)
    a=asset_record(meta,None,kind,None,[sid],'registered_html_or_attached_asset',evidence)
    if not native_path and file['kind'] in ('pptx','docx'):
        crop=office_crop(file,row,folder)
        if crop:
            pixels,ref=crop
            a=asset_record(meta,ref['page_id'],kind,ref['bbox'],[sid],
                'original_office_preview_area',evidence)
            try:
                a.update(carrier=store_visual(pixels,target),status='verified_visual')
                return a
            except (OSError,ValueError):
                pass
        a.update(status='retryable_infrastructure_failure',reason='当前没有可用于核对的 Office 原页渲染')
        return a
    marker=target/'regions'/(a['asset_id']+'.json')
    if marker.is_file():
        cached=read(marker)
        if checked_asset(cached,target):
            return cached|{'cache_hit':True}
    available=([('user_supplied_region',Path(override).read_bytes())] if override else
               web_bytes(native_path,file,folder) if native_path else [])
    for method,val in available:
        try:
            if isinstance(val,Exception):
                raise val
            if val.lstrip().startswith((b'<svg',b'<?xml')):
                val=flat_svg(val,1300)
            a['carrier']=store_visual(val,target)
            a['attempts'].append({'method':method,'status':'ok'})
            a['status']='verified_visual'
            break
        except (ValueError,OSError,RuntimeError) as exc:
            a['attempts'].append({'method':method,'status':'failed','error':type(exc).__name__,'detail':str(exc)[:180]})
    if a['status'] not in READY:
        pending_fetch=any(x['method']=='http_error' and not re.search(r'\b(404|410)\b',x.get('detail',''))
                          for x in a['attempts'])
        a.update(status='retryable_infrastructure_failure' if pending_fetch else 'blocked_user_input',
                 reason='已登记的图片地址暂时无法读取' if pending_fetch else '原件没有可读取的图片字节',
                 failure_kind='download' if pending_fetch else 'missing_or_unreadable_asset')
    put(marker,a)
    return a


def page_number(ref,pages):
    k=ref.get('page_id')
    if k in pages:
        return pages[k]
    found=re.search(r':page:(\d+)$',k or '')
    return int(found[1]) if found else None


def info(folder,file):
    source=(folder/(file.get('stored_name') or '__missing__')).resolve()
    if not source.is_relative_to(folder.resolve()) or not source.is_file():
        source=None
    sha=file_hash(source) if source else None
    return {'file_id':file['id'],'file_name':file.get('name',file['id']),'original_sha256':sha,
            'source_version':file.get('source_fingerprint') or sha or 'missing'},source


def missing_asset(meta,row,page,status,reason):
    a=asset_record(meta,page,row['kind'],None,[row['id']],'original_lookup',{})
    a.update(status=status,reason=reason,failure_kind='missing_original' if status=='blocked_user_input' else 'reader')
    return a


class QualityRunner:
    """Share the per-page process pool and the I/O pool between projects."""
    def __init__(self, workers=4, io_workers=8):
        self.cpu=ProcessPoolExecutor(max_workers=workers)
        self.io=ThreadPoolExecutor(max_workers=io_workers)
        self.capacity={'cpu':workers,'io':io_workers}
        self.sems={k:asyncio.Semaphore(v) for k,v in self.capacity.items()}
        self.inflight=Counter()
        self.peaks=Counter()
        self.metrics=[]

    async def call(self,pool,job,worker,*args):
        start=time.perf_counter()
        async with self.sems[pool]:
            began=time.perf_counter()
            self.inflight[pool]+=1
            self.peaks[pool]=max(self.peaks[pool],self.inflight[pool])
            state='ok'
            try:
                return await asyncio.get_running_loop().run_in_executor(getattr(self,pool),worker,*args)
            except Exception:
                state='failed'
                raise
            finally:
                self.inflight[pool]-=1
                self.metrics.append({'job':job,'pool':pool,'started':began,
                    'wait_seconds':round(began-start,5),'seconds':round(time.perf_counter()-began,5),
                    'status':state})

    async def project(self, root,target, selected=None, *, scan_all=True, overrides=None):
        doc=read(root/'reading-workspace.json')
        files=[x for x in doc['files'] if x.get('original')]
        wanted=None if selected is None else set(selected)
        overrides=overrides or {}
        assets={}; sources={}; manifests=[]
        async def one(file):
            folder=root/'reading-files'/file['id']
            meta,source=await self.call('io',file['id']+':hash',info,folder,file)
            rows=file.get('document',{}).get('passages',[])
            active=[r for r in rows if wanted is None or r['id'] in wanted]
            if not active and not scan_all:
                return
            out=[]; pages={}; dom=[]
            file_type=file['kind']
            if file_type=='html' and source and scan_all:
                dom=await self.call('io',file['id']+':dom',html_inventory,source)
            file_error=None
            if file_type=='pdf' and source:
                try:
                    total=await self.call('cpu',file['id']+':open',pdf_size,source)
                except (OSError,ValueError,RuntimeError) as error:
                    total=0
                    file_error=type(error).__name__+': '+str(error)[:180]
            if file_type=='pdf' and source and not file_error:
                page_map={int(p['number']):pid for pid,p in file['document'].get('pages',{}).items()}
                registered={pid:n for n,pid in page_map.items()}
                members=defaultdict(list)
                for row in rows:
                    for n in {page_number(r,registered) for r in row.get('refs',[])}-{None}:
                        members[n].append(row)
                working=(range(1,total+1) if scan_all else sorted(
                    n for n, group in members.items() if any(r['id'] in wanted for r in group)))
                async def page(n):
                    group=members.get(n,[])
                    pid=page_map.get(n) or file['id']+':page:'+str(n)
                    if n<1 or n>total:
                        return {'page':n,'page_id':pid,'counts':{},'regions':[],
                                'error':'PDF 原件缺少此前登记的这一页','missing_original_page':True}
                    try:
                        return await self.call('cpu',pid,inspect_page,source,n,pid,group,meta,target)
                    except Exception as exc:
                        return {'page':n,'page_id':pid,'counts':{},'regions':[],
                                'error':type(exc).__name__+' '+str(exc)[:180]}
                results=await asyncio.gather(*(page(n) for n in sorted(set(working)|{n for n in members if n>total})))
                pages={p['page_id']:p for p in results}
                for p in results:
                    out.extend(p['regions'])
            elif source and not file_error:
                jobs=[r for r in (rows if scan_all else active) if r['kind'] in VISUAL or r['kind'] in ('code','code_block')]
                if jobs:
                    async def web(row):
                        return await self.call('io',row['id'],web_asset,meta,file,folder,row,source,target,overrides.get(row['id']))
                    out.extend(await asyncio.gather(*(web(r) for r in jobs)))
            by_source=defaultdict(dict)
            for a in out:
                for sid in a['member_source_ids']:
                    by_source[sid][a['asset_id']]=a
            page_only={sid for p in pages.values() for sid in p.get('verified_page_numbers',[])}
            for r in active:
                sid=r['id']
                locs=[q.get('page_id') for q in r.get('refs',[]) if q.get('page_id')]
                required=(r['kind'] in VISUAL or file_type=='pdf' and short_fragment(native_text(r)) and sid not in page_only)
                a=list(by_source[sid].values())
                if file_type=='pdf' and overrides.get(sid):
                    candidate=Path(overrides[sid])
                    evidence={'supplement_sha256':file_hash(candidate),'original_locator':r.get('refs',[])}
                    repair=asset_record(meta,locs[0] if locs else None,r['kind'],r['refs'][0].get('bbox'),
                                        [sid],'user_supplied_region',evidence)
                    # Keep the original's identity and location; the replacement bytes have a separate hash.
                    try:
                        repair.update(status='verified_visual',carrier=store_visual(candidate.read_bytes(),target))
                        a=[repair];out.append(repair)
                        for original in by_source[sid].values():
                            original['member_source_ids'].remove(sid)
                    except (OSError,ValueError):
                        pass
                page_error=any(pages.get(k,{}).get('error') for k in locs)
                if not a and (required or not source or page_error or file_error):
                    missing_page=any(pages.get(k,{}).get('missing_original_page') for k in locs)
                    reason=('原文件或页确实缺失' if not source or missing_page else
                            '已登记的原文坐标目前不能回读出图像')
                    a=[missing_asset(meta,r,locs[0] if locs else None,
                         'blocked_user_input' if not source or missing_page else 'retryable_infrastructure_failure',reason)]
                    out.extend(a)
                if a and all(v['status'] in READY for v in a):
                    status='verified_structured' if all(v['status']=='verified_structured' for v in a) else 'verified_visual'
                elif a:
                    status=('retryable_infrastructure_failure' if any(v['status']=='retryable_infrastructure_failure' for v in a)
                            else 'blocked_user_input')
                else:
                    status='verified_text'
                sources[sid]={'source_id':sid,'file_id':file['id'],'kind':r['kind'],'status':status,
                    'locator':r.get('source_location') or {'refs':r.get('refs',[])},
                    'asset_ids':list(dict.fromkeys(v['asset_id'] for v in a)),
                    **({'reason':'；'.join(dict.fromkeys(v['reason'] for v in a if v.get('reason')))} if status not in READY|{'verified_text'} else {})}
            for a in out:
                a['member_source_ids']=[k for k in a['member_source_ids'] if k in sources]
                a['group_id']=register_group(a)
                current=assets.get(a['asset_id'])
                if current:
                    current['member_source_ids']=sorted(set(current['member_source_ids']+a['member_source_ids']))
                    current['group_id']=register_group(current)
                else:
                    assets[a['asset_id']]=a
            manifests.append({**meta,'kind':file_type,'scope_source_ids':[r['id'] for r in active],
                 'page_count':len(pages),'file_error':file_error,'dom_visual_candidates':dom,
                 'pages':[{**{k:v for k,v in p.items() if k!='regions'},
                            'asset_ids':list(dict.fromkeys(a['asset_id'] for a in p['regions']))}
                            for p in pages.values()]})
        ids=[r['id'] for f in files for r in f.get('document',{}).get('passages',[]) if wanted is None or r['id'] in wanted]
        if len(set(ids))!=len(ids) or wanted is not None and set(ids)!=wanted:
            raise ValueError('原件登记中存在重复或缺失来源编号')
        await asyncio.gather(*(one(file) for file in files))
        manifest={'version':VERSION,'sources':{k:sources[k] for k in ids},'assets':dict(sorted(assets.items())),
                  'files':sorted(manifests,key=lambda d:d['file_id'])}
        validate_manifest(manifest)
        manifest['ready_version']=fingerprint({
            'version':VERSION,'algorithm_revision':ALGORITHM_REVISION,'sources':manifest['sources'],
            'assets':{k:{a:v.get(a) for a in ('status','carrier','group_id','structure')} for k,v in manifest['assets'].items()},
            'files':[(f['file_id'],f['original_sha256']) for f in manifest['files']]})
        put(target/'source-ready.json',manifest)
        prompts=defaultdict(list)
        for r in manifest['sources'].values():
            if r['status']=='blocked_user_input':
                prompts[r['file_id']].append({'source_id':r['source_id'],'kind':r['kind'],'locator':r['locator'],
                    'attempts':[manifest['assets'][a]['attempts'] for a in r['asset_ids']],
                    'reason':r.get('reason')})
        put(target/'source-supplement-requests.json',[
            {'file_id':k,'file_name':next(f['file_name'] for f in manifest['files'] if f['file_id']==k),
             'items':v,'requested_material':'请提供对应的原始文件、清晰页截图或原图'} for k,v in prompts.items()])
        return manifest

    def close(self):
        self.cpu.shutdown()
        self.io.shutdown()


def pdf_size(path):
    import fitz
    with fitz.open(path) as file:
        return len(file)


async def build(root,target,selection=None,*,scan_all=False):
    runner=QualityRunner()
    try:
        return await runner.project(Path(root),Path(target),selection,scan_all=scan_all)
    finally:
        runner.close()



async def writer_packet(packet, root):
    selection=[r['id'] for r in packet['sources']]
    ticket=fingerprint([VERSION,ALGORITHM_REVISION,packet['snapshot_revision'],packet['file_signatures'],selection])[:24]
    target=root/'source-quality'/ticket
    path=target/'source-ready.json'
    manifest=read(path) if path.is_file() else await build(root,target,selection)
    sources,images,denied=SourceGate(manifest,target).admit(packet['sources'])
    if path.is_file() and denied and any(
            manifest['sources'].get(k,{}).get('status') in READY|{'retryable_infrastructure_failure'}
            for k in denied):
        manifest=await build(root,target,selection)
        sources,images,denied=SourceGate(manifest,target).admit(packet['sources'])
    mandatory={r['id'] for r in packet['sources'] if not r['context_only']}
    return {**packet,'sources':sources,'source_blocked':{k:v for k,v in denied.items() if k in mandatory},
            'source_quality_version':manifest['ready_version'],
            'source_media_attachments':[{'sha256':p.stem,'src':f'/api/source-quality/{ticket}/{p.name}'}
                                        for p in images],
            'source_supplement_requests':read(target/'source-supplement-requests.json')}
