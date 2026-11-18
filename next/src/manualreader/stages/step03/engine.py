"""Coordinate technical source inspection, M1/M2 binding and carrier reads."""

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import PurePosixPath
from threading import Lock, BoundedSemaphore
from time import perf_counter
from urllib.parse import unquote, urlsplit
import posixpath
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError

from ..step01.scan import inside
from ..step02.original import pinned_bytes
from ...store import index_for, read
from .cache import Cache, digest
from .documents import IMAGES, parse_document
from .media import message, unavailable
from .pdf import CONTINUED, page_job


REVISION='step03-source-v4'
DOCUMENT_REVISION='step03-document-v5'


class VersionChanged(ValueError):
    pass


def overlaps(position, span):
    if not position:
        return False
    if span['type']=='whole_file':
        return True
    return (position['type']==span['type'] and position['start']<=span['end']
            and span['start']<=position['end'])


def physical(locator):
    kind=locator['type']
    n=locator.get('number')
    if kind=='pdf_bbox':
        return {'type':'pdf_page','start':n,'end':n}
    if type(n) is int:
        return {'type':kind,'start':n,'end':locator.get('end',n)}
    return None


def matches(locator, span):
    if span['type']=='subtitle_time' and locator.get('time'):
        a,b=locator['time']
        return overlaps({'type':'subtitle_time','start':a,'end':b},span)
    return overlaps(physical(locator),span)


def source_pool(project, entry):
    return index_for(read(project.index),entry).get('rows',[])


def identify(file_id, change, asset, ordinal):
    point=asset['locator']
    return {**asset,'asset_id':'asset-'+digest([file_id, change, point,asset['kind'],ordinal])[:24]}


class Engine:
    def __init__(self, root, workers=4, *, parallel=True):
        self.cache=Cache(root)
        self.tasks=ThreadPoolExecutor(max_workers=max(2,workers),thread_name_prefix='step03-files')
        self.pdf=ProcessPoolExecutor(max_workers=workers,mp_context=get_context('spawn')) if parallel else None
        self.limits=BoundedSemaphore(max(2,workers*2))
        self.warm_slots=BoundedSemaphore(max(2,workers*2))
        self.ocr_slots=BoundedSemaphore(2)
        self.warmed=set()
        self.guard=Lock()
        self.active=0
        self.peak=0

    def close(self):
        self.tasks.shutdown()
        if self.pdf:
            self.pdf.shutdown()

    def entry(self, project, rid, file_id):
        current=project.locate_round(rid)
        files=current['m1']['manifest']['files']
        match=next((f for f in files if f['file_id']==file_id and f['status']=='active'
                    and not f['identity_pending']),None)
        if match is None:
            raise ValueError('原件不在本轮授权文件清单中')
        return current,match

    def raw(self, project, entry):
        try:
            return pinned_bytes(inside(project.root,'originals'),entry)
        except (FileNotFoundError,PermissionError) as error:
            raise FileNotFoundError('找不到本轮原件，或当前没有读取权限') from error
        except ValueError as error:
            if 'M1' in str(error) or '变化' in str(error):
                raise VersionChanged('原件内容与 M1 版本不同') from error
            raise

    def warm(self, project, rid, file_id):
        self.entry(project,rid,file_id)
        key=project.identity,rid,file_id
        with self.guard:
            if key in self.warmed or not self.warm_slots.acquire(blocking=False):
                return False
            self.warmed.add(key)
        def work():
            try:
                self.inspect(project,rid,file_id)
            finally:
                with self.guard:self.warmed.discard(key)
                self.warm_slots.release()
        self.tasks.submit(work)
        return True

    def related_entry(self, round_data, entry, reference):
        url=urlsplit(reference)
        if url.scheme or url.netloc or not url.path or url.path.startswith(('/', '\\')):
            raise ValueError('快照引用未登记外部资源，未读取网络')
        ref=unquote(url.path)
        if '\\' in ref or ':' in ref:
            raise ValueError('图片引用不是允许的相对路径')
        target=posixpath.normpath(posixpath.join(posixpath.dirname(entry['relative_path']),ref))
        if target.startswith('../') or target=='..':
            raise ValueError('快照图片不在授权项目目录')
        item=next((f for f in round_data['m1']['manifest']['files'] if f['relative_path']==target
                   and f['status']=='active' and not f['identity_pending']
                   and PurePosixPath(target).suffix.lower() in IMAGES),None)
        if item is None:
            raise ValueError('项目未登记快照引用的图片原件')
        return item

    def pdf_file(self,raw,selected=None,refresh=False):
        import fitz
        identity=self.cache.blob(raw)
        path=self.cache.root/'blobs'/identity
        with fitz.open(path) as pdf:
            if pdf.needs_pass:
                raise ValueError('PDF 已加密，当前不能读取')
            amount=len(pdf)
        pages=range(1,amount+1) if selected is None else sorted(set(selected))
        if any(type(n) is not int or not 1<=n<=amount for n in pages):
            raise ValueError('请求的 PDF 页码不在原件中')
        results={}
        def failed(n,err):
            return {'number':n,'assets':[unavailable('page_visual',{'type':'pdf_page','number':n},
                                                     str(err))],
                    'geometry':None,'observed':{},'cache':'miss','wall_ms':0}
        if self.pdf:
            jobs={self.pdf.submit(page_job,path,self.cache.root,n,REVISION,refresh,perf_counter()):n for n in pages}
            for job in as_completed(jobs):
                n=jobs[job]
                try:
                    results[n]=job.result()
                except (OSError,ValueError,RuntimeError) as err:
                    results[n]=failed(n,err)
        else:
            for n in pages:
                try:
                    results[n]=page_job(path,self.cache.root,n,REVISION,refresh)
                except (OSError,ValueError,RuntimeError) as err:
                    results[n]=failed(n,err)
        ordered=[results[n] for n in pages]
        groups=[]
        for a,b in zip(ordered,ordered[1:]):
            if a['number']+1!=b['number']:
                continue
            # Only connect a native continuation label to a matching preceding caption.
            left=[r for r in a['assets'] if r['kind']=='caption']
            right=[r for r in b['assets'] if r['kind']=='caption' and CONTINUED.search(r['text'])]
            for x in right:
                import re
                prefix=re.match(r'(?i)\s*(?:fig(?:ure)?\.?|table|图|表)\s*\d+(?:\.\d+)*',x['text'])
                if prefix:
                    previous=next((r for r in left if r['text'].strip().lower().startswith(prefix[0].strip().lower())),None)
                    if previous:
                        groups.append({'kind':'continued_caption','members':[
                            dict(previous['locator'],number=a['number']),dict(x['locator'],number=b['number'])]})
        return {'type':'pdf','page_count':amount,'pages':ordered,'groups':groups,
                'cacheable':all(p['geometry'] is not None for p in ordered)}

    def inputs(self, project, rid, fid, pages=None):
        if pages is not None and (not isinstance(pages,list) or not 1<=len(pages)<=500
                                  or any(type(n) is not int for n in pages)):
            raise ValueError('请选择原件中的有效页码')
        round_data,entry=self.entry(project,rid,fid)
        raw=self.raw(project,entry)
        suffix=PurePosixPath(entry['relative_path']).suffix.lower()
        dependencies={}
        if suffix in ('.html','.htm'):
            from bs4 import BeautifulSoup
            for tag in BeautifulSoup(raw,'html.parser').find_all('img'):
                ref=tag.get('src') or tag.get('data-src') or ''
                try:
                    attached=self.related_entry(round_data,entry,ref)
                except ValueError:
                    continue
                try:
                    dependencies[ref]=self.raw(project,attached)
                except (OSError,ValueError) as error:
                    dependencies[ref]=str(error)
        return round_data,entry,raw,suffix,dependencies

    def stored_attachments(self, project, entry):
        path=self.cache.root/'web-attachments'/f'{digest([project.identity,entry["file_id"],entry["content_sha256"]])}.json'
        if not path.is_file():
            return {}
        data=read(path)
        if data.get('html_sha256')!=entry['content_sha256'] or data.get('file_id')!=entry['file_id']:
            return {}
        return {(r['ref'],r['dom_id']):r['sha256'] for r in data['images']}

    def inspect(self,project,rid,fid,pages=None,*,refresh=False):
        began=perf_counter()
        round_data,entry,raw,suffix,deps=self.inputs(project,rid,fid,pages)
        if pages is not None and suffix!='.pdf':
            raise ValueError('此格式不使用 PDF 页码')
        selected=sorted(set(pages)) if pages else None
        deps_key=sorted((key,digest(value) if isinstance(value,bytes) else value) for key,value in deps.items())
        recorded=self.stored_attachments(project,entry) if suffix in ('.html','.htm') else {}
        safe_attachments={}
        for key,carrier in recorded.items():
            try:
                safe_attachments[key]=self.cache.carrier(carrier)
            except (ValueError,OSError):
                continue
        key=[project.identity,entry['content_sha256'],suffix,entry['relative_path'],deps_key,
             sorted((k,recorded[k]) for k in safe_attachments),selected,REVISION if suffix=='.pdf' else DOCUMENT_REVISION]
        def related(ref,dom_id):
            data=deps.get(ref)
            if isinstance(data,bytes):
                return data
            if isinstance(data,str):
                raise ValueError(data)
            if (ref,dom_id) in safe_attachments:
                return safe_attachments[ref,dom_id]
            self.related_entry(round_data,entry,ref)
            raise ValueError('未找到快照引用的当前图片字节')
        def build():
            requested=perf_counter()
            with self.limits:
                queue_ms=(perf_counter()-requested)*1000
                with self.guard:
                    self.active+=1
                    self.peak=max(self.peak,self.active)
                try:
                    document=(self.pdf_file(raw,selected,refresh) if suffix=='.pdf' else parse_document(
                        raw,suffix,self.cache,related))
                except (BadZipFile,ParseError) as error:
                    raise ValueError(f'原件的容器或结构无法读取：{error}') from error
                finally:
                    with self.guard:self.active-=1
                return {**document,'queue_ms':queue_ms}
        document,hit,waiting=self.cache.calculate('files',key,build,refresh=refresh)
        name=entry['content_sha256']
        assets=[identify(fid,name,a,i) for i,a in enumerate(document.get('assets',[]))]
        result_pages=[]
        for p in document.get('pages',[]):
            result_pages.append({**p,'assets':[identify(fid,name,dict(a,locator={
                **a['locator'],'number':p['number']}),i) for i,a in enumerate(p['assets'])]})
        groups=[{**g,'source_group_id':'group-'+digest([fid,name,g])[:24]} for g in document.get('groups',[])]
        for p in result_pages:
            for a in p['assets']:
                if a.get('members'):
                    groups.append({'source_group_id':'group-'+digest([fid,name,a['locator'],a['members']])[:24],
                                   'kind':a['kind'],'asset_id':a['asset_id'],'locator':a['locator'],
                                   'member_asset_ids':[r['asset_id'] for r in p['assets']
                                                       if r['locator'].get('line') in a['members']]})
        return {**document,'file_id':fid,'relative_path':entry['relative_path'],'content_sha256':name,
                'file_revision':entry['file_revision'],'revision':REVISION,
                'assets':assets,'pages':result_pages,'groups':groups,'cache':hit,
                'queue_ms':waiting if hit=='shared' else document['queue_ms'] if hit=='miss' else 0,
                'wall_ms':(perf_counter()-began)*1000}

    def consume(self,project,rid,fid,asset_id,capability,page=None,source_id=None):
        data=self.inspect(project,rid,fid,[page] if page is not None else None)
        choices=flat(data)
        asset=next((a for a in choices if a['asset_id']==asset_id),None)
        if asset is None:
            raise ValueError('资产编号与本轮来源不对应')
        if source_id is not None:
            _,entry=self.entry(project,rid,fid)
            source=next((r for r in source_pool(project,entry) if r['id']==source_id),None)
            if not source or not source.get('position') or not matches(asset['locator'],source['position']):
                raise ValueError('资产不在所给来源编号的原文范围')
        try:
            block=message(self.cache,asset,capability)
        except (ValueError,OSError):
            if capability!='inspect_visual' or not asset.get('carrier'):
                raise
            # A corrupt or evicted cache entry can be recovered from its pinned original.
            repair_page=asset['locator']['number'] if data['type']=='pdf' else None
            recovered=self.inspect(project,rid,fid,[repair_page] if repair_page else None,refresh=True)
            replacement=next((a for a in flat(recovered) if a['asset_id']==asset_id),None)
            if not replacement:
                raise ValueError('重新读取原件后，无法核对同一资产编号')
            asset=replacement
            block=message(self.cache,asset,capability)
        return {'file_id':fid,'asset_id':asset_id,'locator':asset['locator'],'source_id':source_id,
                'carrier':asset.get('carrier'),'content':block}

    def exact_locator(self,project,rid,fid,source_id,quote,page):
        _,entry=self.entry(project,rid,fid)
        found=next((r for r in source_pool(project,entry) if r['id']==source_id),None)
        if not isinstance(quote,str) or len(quote.strip())<3 or len(quote)>500 or not found:
            raise ValueError('缺少可查的来源或原文片段')
        data=self.inspect(project,rid,fid,[page] if page else None)
        hits=[]
        for r in flat(data):
            text=r.get('text','')
            if found.get('position') and matches(r['locator'],found['position']) and quote in text:
                if text.count(quote)!=1:
                    raise ValueError('该片段出现在多个原文位置')
                point=r['locator']
                offset=text.index(quote)
                if point.get('type')=='pdf_bbox':
                    # The exact line coordinates are native. Character offsets are within that line.
                    point={**point,'line_char_start':offset,'line_char_end':offset+len(quote)}
                else:
                    point={**point,'char_start':offset,'char_end':offset+len(quote)}
                hits.append(point)
        if len(hits)!=1:
            raise ValueError('无法唯一定位这段原文')
        return {'source_id':source_id,'locator':hits[0],'file_sha256':entry['content_sha256']}

    def binding(self,project,rid,card):
        state=project.locate_round(rid)
        if (state.get('m2') or {}).get('status')!='ready':
            raise ValueError('合流需要本轮已完成的 M2 文件核对')
        statements=[]
        for row in card['material_plan']:
            fid=row['file_id']
            sources=row['source_ids']
            find=[]
            if fid not in state.get('selected',{}):
                statements.append({'file_id':fid,'status':'unavailable','reason':'文件不在本轮 M2 选取范围',
                                   'source_ids':sources})
                continue
            try:
                _,entry=self.entry(project,rid,fid)
                indexed={s['id']:s for s in source_pool(project,entry)}
                data=self.inspect(project,rid,fid)
                candidates=flat(data)
                scoped=[a for a in candidates if any(matches(a['locator'],sel) for sel in row['source_ranges'])]
                for sid in sources:
                    value=indexed.get(sid)
                    admitted=[s for s in (value or {}).values() if isinstance(s,dict) and
                              {'type','start','end'}<=s.keys() and any(overlaps(s,sel) for sel in row['source_ranges'])]
                    find.append({'source_id':sid,'registered':bool(admitted),'locators':admitted})
                state_of=('available' if scoped or not sources else 'unavailable')
                if any(not loc['registered'] for loc in find) or any(a['status']=='unavailable' for a in scoped):
                    state_of='partial' if scoped else 'unavailable'
                statements.append({'file_id':fid,'status':state_of,'source_ids':sources,'locators':find,
                    'content_sha256':data['content_sha256'],'asset_count':len(scoped),
                    'unavailable_assets':[a['asset_id'] for a in scoped if a['status']=='unavailable']})
            except (OSError,ValueError,RuntimeError) as error:
                statements.append({'file_id':fid,'status':'unavailable','reason':str(error),'source_ids':sources})
        return {'task_sha256':digest(card),'mode':state['mode'],'revision':REVISION,'materials':statements,
                'status':'available' if all(s['status']=='available' for s in statements) else 'partial'}


def flat(doc):
    return doc['assets']+[a for p in doc['pages'] for a in p['assets']]
