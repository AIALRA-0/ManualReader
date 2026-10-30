"""将上传、网页和公开仓库保存到当前项目，再复用同一组原文解析器。"""
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit, unquote
from urllib.request import Request, build_opener, HTTPRedirectHandler
import base64, hashlib, io, ipaddress, json, mimetypes, re, shutil, socket, stat, tempfile, zipfile

from source_parser import parse, audit


MAX_FILE_BYTES=40_000_000
MAX_TOTAL_BYTES=150_000_000
MAX_FILES=2000
MAX_ARCHIVE_ENTRIES=4000
MAX_COMPRESSION_RATIO=200
MAX_PDF_PAGES=500


def relative_name(value):
    name=str(value or '').replace('\\','/')
    parts=PurePosixPath(name).parts
    if (not name or name.startswith('/') or not parts or ':' in name or len(name)>300
        or len(parts)>20 or any(p in ('.','..') or p.endswith((' ','.')) for p in parts)
        or any(ord(c)<32 for c in name)
        or any(re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',p) for p in parts)):
        raise ValueError('请提供项目内的相对文件名，目录中不能包含上级路径、盘符或系统保留名称')
    return PurePosixPath(*parts).as_posix()


def decode_file(value,*,empty=False):
    if not isinstance(value,str) or len(value)>((MAX_FILE_BYTES+2)//3)*4:
        raise ValueError('每个文件最大 40 MB，请按 Base64 提交文件内容')
    try:raw=base64.b64decode(value,validate=True)
    except (ValueError,TypeError) as error:raise ValueError('文件内容不是有效的 Base64') from error
    if len(raw)>MAX_FILE_BYTES or (not raw and not empty):raise ValueError('文件需要有内容，最大 40 MB')
    return raw


def validate_url(url,allow_private=False):
    if not isinstance(url,str) or len(url)>4000 or any(ord(c)<32 for c in url):raise ValueError('请提供 HTTP(S) 来源地址')
    parts=urlsplit(url)
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('来源地址需要使用 HTTP(S)，且不能包含登录凭据或本机文件路径')
    try:port=parts.port or (443 if parts.scheme=='https' else 80)
    except ValueError as error:raise ValueError('来源地址的端口无效') from error
    if not allow_private:
        try:addresses=socket.getaddrinfo(parts.hostname,port,type=socket.SOCK_STREAM)
        except OSError as error:raise ValueError('来源地址的域名暂时无法解析') from error
        if not addresses or any(not ipaddress.ip_address(row[4][0].split('%')[0]).is_global for row in addresses):
            raise ValueError('远程导入只读取公开 HTTP(S) 地址；请把本机文件上传到当前项目')
    return urlunsplit((parts.scheme,parts.netloc,parts.path or '/',parts.query,''))


def fetch_url(url,*,allow_private=False):
    url=validate_url(url,allow_private)
    class Redirects(HTTPRedirectHandler):
        def redirect_request(self,request,fp,code,msg,headers,newurl):
            return super().redirect_request(request,fp,code,msg,headers,validate_url(newurl,allow_private))
    opener=build_opener(Redirects())
    try:
        with opener.open(Request(url,headers={'User-Agent':'ManualReader/1.0','Accept-Encoding':'identity'}),timeout=25) as response:
            final=validate_url(response.geturl(),allow_private)
            if int(response.headers.get('Content-Length','0'))>MAX_FILE_BYTES:raise ValueError('远程文件超过 40 MB，请拆分后上传')
            raw=response.read(MAX_FILE_BYTES+1)
            if len(raw)>MAX_FILE_BYTES:raise ValueError('远程文件超过 40 MB，请拆分后上传')
            if not raw:raise ValueError('来源服务器返回了空文件，请检查地址或上传浏览器保存的网页')
            return raw,final,response.headers.get_content_type()
    except ValueError:raise
    except OSError as error:raise ValueError('读取来源地址失败：'+str(error)[:250]) from error


def repository_archive(url):
    parts=urlsplit(url);host=(parts.hostname or '').lower();segments=[p for p in parts.path.strip('/').split('/') if p]
    if host in ('github.com','www.github.com') and len(segments)>=2:
        owner,repo=segments[:2];repo=repo.removesuffix('.git')
        if len(segments)>2 and segments[2] not in ('tree','archive'):raise ValueError('请提供仓库首页、分支地址或 ZIP 归档地址')
        if len(segments)>2 and segments[2]=='archive':return url
        ref=segments[3] if len(segments)==4 and segments[2]=='tree' else 'HEAD'
        if len(segments)>4:raise ValueError('请导入仓库首页或仓库分支；文件夹地址请改为上传 ZIP')
        return f'https://github.com/{owner}/{repo}/archive/{ref}.zip'
    if host=='gitlab.com' and len(segments)>=2:
        if '-' in segments:
            marker=segments.index('-');repo_parts=segments[:marker]
            if segments[marker+1:marker+2]==['archive']:return url
            if len(segments)!=marker+3 or segments[marker+1]!='tree':raise ValueError('请提供仓库首页、分支地址或 ZIP 归档地址')
            ref=segments[-1]
        else:repo_parts=segments;ref='HEAD'
        repo_parts[-1]=repo_parts[-1].removesuffix('.git');base='/'.join(repo_parts)
        return f'https://gitlab.com/{base}/-/archive/{ref}/{repo_parts[-1]}-{ref}.zip'
    if host in ('codeberg.org','gitea.com') and len(segments)==2:
        return f'{parts.scheme}://{parts.netloc}/{segments[0]}/{segments[1].removesuffix(".git")}/archive/HEAD.zip'
    if parts.path.lower().endswith('.zip'):return url
    raise ValueError('该仓库服务需要提供公开的 archive_url ZIP 地址；导入过程只下载归档，不运行仓库脚本')


def archive_members(raw):
    try:archive=zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile,OSError) as error:raise ValueError('文件不是有效的 ZIP 归档') from error
    members=[];seen=set();total=0
    try:
        entries=archive.infolist()
        if len(entries)>MAX_ARCHIVE_ENTRIES:raise ValueError('ZIP 中的项目超过 4000 项，请拆分后导入')
        for info in entries:
            name=relative_name(info.filename.rstrip('/'));key=name.casefold()
            if key in seen:raise ValueError('ZIP 中存在重复或大小写冲突的路径：'+name)
            seen.add(key);mode=info.external_attr>>16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISDIR(mode) or stat.S_ISREG(mode))):
                raise ValueError('ZIP 中包含符号链接或特殊文件，请改为普通文件：'+name)
            if info.flag_bits&1:raise ValueError('请先解密 ZIP，再上传普通归档')
            if info.is_dir():continue
            if info.file_size>MAX_FILE_BYTES or info.file_size/max(1,info.compress_size)>MAX_COMPRESSION_RATIO:
                raise ValueError('ZIP 中的单个文件超过 40 MB 或压缩比超过 200，请拆分后导入')
            total+=info.file_size
            if total>MAX_TOTAL_BYTES or len(members)>=MAX_FILES:raise ValueError('ZIP 解压后最多 150 MB、2000 个文件，请拆分后导入')
            members.append((name,info))
        if not members:raise ValueError('ZIP 中没有普通文件')
        paths={name.casefold() for name,_ in members}
        if any(any('/'.join(name.casefold().split('/')[:i]) in paths for i in range(1,len(name.split('/')))) for name,_ in members):
            raise ValueError('ZIP 的文件与目录使用了同一路径，请整理后重试')
        return archive,members
    except Exception:archive.close();raise


def extract_archive(raw,destination):
    archive,members=archive_members(raw);root=Path(destination).resolve();root.mkdir(parents=True,exist_ok=True)
    try:
        for name,info in members:
            target=(root/name).resolve()
            if not target.is_relative_to(root):raise ValueError('ZIP 文件路径越出了当前项目')
            target.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(info) as source,target.open('wb') as output:
                written=0
                while True:
                    chunk=source.read(1024*1024)
                    if not chunk:break
                    written+=len(chunk)
                    if written>MAX_FILE_BYTES or written>info.file_size:raise ValueError('ZIP 的实际解压内容超过登记大小')
                    output.write(chunk)
    except (zipfile.BadZipFile,RuntimeError) as error:raise ValueError('ZIP 内容损坏，无法完成解压') from error
    finally:archive.close()
    return [name for name,_ in members]


def render_web_snapshot(url,*,allow_private=False):
    """环境已经安装 Chromium 时读取浏览器生成的 DOM；缺少浏览器时直接返回事实。"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as runtime:
            executable=Path(runtime.chromium.executable_path)
            if not executable.is_file():return None,{'status':'unavailable','message':'服务器尚未安装 Chromium，请上传浏览器保存的网页快照'}
            browser=runtime.chromium.launch(headless=True,timeout=10000)
            try:
                context=browser.new_context(accept_downloads=False,service_workers='block')
                def route_request(route):
                    try:validate_url(route.request.url,allow_private)
                    except ValueError:route.abort();return
                    route.continue_()
                context.route('**/*',route_request)
                page=context.new_page();page.goto(validate_url(url,allow_private),wait_until='networkidle',timeout=15000)
                return page.content(),{'status':'rendered','renderer':'chromium','final_url':page.url}
            finally:browser.close()
    except ImportError:return None,{'status':'unavailable','message':'服务器尚未安装浏览器渲染器，请上传浏览器保存的网页快照'}
    except Exception as error:return None,{'status':'failed','message':'浏览器读取网页失败：'+str(error)[:200]}


def _readable(document):
    return [p for p in document['passages'] if (p.get('raw_text','').strip() and p.get('kind') not in ('image','visual','page_image')) or p.get('kind') in ('image','page_image')]


def _web_body_ready(document):
    content=[p for p in _readable(document) if p.get('kind')!='heading']
    text=' '.join(p.get('raw_text','') for p in content).strip()
    placeholders=r'(?i)(?:loading[.\s…]*|please\s+(?:wait|enable\s+javascript)[.\s…]*|正在加载[.\s…]*|加载中[.\s…]*)'
    return bool(content) and not re.fullmatch(placeholders,text)


def _guard_document(path):
    if path.suffix.lower() in ('.docx','.pptx'):
        archive,members=archive_members(path.read_bytes());archive.close()
    if path.suffix.lower()=='.pdf':
        import fitz
        with fitz.open(path) as pdf:
            if len(pdf)>MAX_PDF_PAGES:raise ValueError('每份 PDF 最多 500 页，请拆分后导入')


def _coverage(document,pending,files):
    result=audit(document);result.update(files_total=len(files),files_parsed=sum(f['status']=='parsed' for f in files),files_pending=sum(f['status']=='pending' for f in files),files_failed=sum(f['status']=='failed' for f in files),files_skipped=sum(f['status']=='skipped' for f in files),readable_units=len(_readable(document)),pending_items=len(pending),original_files_preserved=True)
    return result


class SourceImporter:
    def __init__(self,workspace,*,allow_private_urls=False):
        self.workspace=workspace;self.allow_private_urls=allow_private_urls

    def import_source(self,data):
        if not isinstance(data,dict):raise ValueError('请按对象提交导入来源')
        if any(key in data for key in ('source_path','local_path','path')):raise ValueError('远程导入不读取服务器本地路径，请上传文件、目录清单或 HTTP(S) 地址')
        value=self.workspace.read();expected=data.get('expected_revision')
        if expected is not None and expected!=value['revision']:raise ValueError('文件列表已更新，请刷新后再提交')
        kind=data.get('kind') or data.get('source_kind') or ('directory' if 'files' in data else 'url' if 'url' in data else 'file')
        if kind=='repo':kind='repository'
        if kind not in ('file','directory','zip','url','repository'):raise ValueError('请选择 file、directory、zip、url 或 repository 来源')
        source_url=None;final_url=None;content_type=None;renderer=None
        directory=kind=='directory';archive=kind in ('zip','repository');entries=[]
        if kind in ('url','repository'):
            source_url=validate_url(data.get('url'),self.allow_private_urls)
            fetch=repository_archive(source_url) if kind=='repository' and not data.get('archive_url') else data.get('archive_url') if kind=='repository' else source_url
            raw,final_url,content_type=fetch_url(fetch,allow_private=self.allow_private_urls)
            guessed=unquote(PurePosixPath(urlsplit(final_url).path).name)
            ext=Path(guessed).suffix.lower()
            if kind=='repository':name=relative_name(data.get('filename') or 'repository.zip')
            elif content_type in ('text/html','application/xhtml+xml') or (not ext and raw.lstrip().lower().startswith((b'<!doctype html',b'<html'))):name=relative_name(data.get('filename') or 'snapshot.html')
            else:name=relative_name(data.get('filename') or guessed or ('source'+(mimetypes.guess_extension(content_type) or '.txt')))
            archive=archive or Path(name).suffix.lower()=='.zip'
        elif directory:
            rows=data.get('files')
            if not isinstance(rows,list) or not 1<=len(rows)<=MAX_FILES:raise ValueError('目录需要包含 1 至 2000 个文件对象')
            seen=set();total=0
            for item in rows:
                if not isinstance(item,dict):raise ValueError('目录中的每个文件都需要 filename 和 content_base64')
                entry_name=relative_name(item.get('filename') or item.get('name'));blob=decode_file(item.get('content_base64'),empty=True);key=entry_name.casefold()
                if key in seen:raise ValueError('目录中存在重复或大小写冲突的文件名：'+entry_name)
                seen.add(key);total+=len(blob)
                if total>MAX_TOTAL_BYTES:raise ValueError('目录内容最多 150 MB，请拆分后导入')
                entries.append((entry_name,blob))
            name=relative_name(data.get('filename') or 'directory.zip');buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_STORED) as zipped:
                for entry_name,blob in sorted(entries):zipped.writestr(zipfile.ZipInfo(entry_name,date_time=(1980,1,1,0,0,0)),blob)
            raw=buffer.getvalue()
        else:
            name=relative_name(data.get('filename'));raw=decode_file(data.get('content_base64'));archive=archive or Path(name).suffix.lower()=='.zip'
        snapshot=data.get('snapshot_html')
        if snapshot is not None and (not isinstance(snapshot,str) or len(snapshot.encode('utf8'))>MAX_FILE_BYTES):raise ValueError('浏览器 HTML 快照需要是最多 40 MB 的文字')
        fingerprint=hashlib.sha256(kind.encode()+b'\0'+name.encode()+b'\0'+(source_url or '').encode()+b'\0'+raw+b'\0'+(snapshot or '').encode('utf8')).hexdigest()
        identity=data.get('file_id') or 'file-'+fingerprint[:20]
        if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,70}',identity) or identity=='primary':raise ValueError('新增文件编号需要使用字母、数字、下划线或连字符，且不能使用 primary')
        old=next((row for row in value['files'] if row['id']==identity),None)
        if old:
            if old.get('source_fingerprint')==fingerprint:return old
            # 旧 API 已登记的文件仍按原字节判断重复，避免导入服务升级后破坏幂等行为。
            stored=self.workspace.files/identity/old['stored_name']
            if not old.get('source_fingerprint') and stored.is_file() and stored.read_bytes()==raw:return old
            raise ValueError('该文件编号已存在，请使用新编号保存新来源，旧文件的定位继续保留')
        self.workspace.files.mkdir(parents=True,exist_ok=True)
        target=self.workspace.files/identity
        if target.exists():raise ValueError('该文件目录已存在，请使用新的 file_id')
        with tempfile.TemporaryDirectory(prefix='.import-',dir=self.workspace.files) as temporary:
            folder=Path(temporary);source=folder/'source';source.mkdir();stored='original'+('.zip' if archive or directory else Path(name).suffix.lower())
            (folder/stored).write_bytes(raw)
            if archive or directory:
                paths=extract_archive(raw,source)
                for path in paths:
                    try:_guard_document(source/path)
                    except ValueError:raise
                    except Exception:pass
                document=parse(source,identity,folder/'assets');document['title']=data.get('title') or Path(name).stem
                files=document.get('source_files',[]);pending=list(document.get('pending',[]))
            else:
                path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);_guard_document(path)
                document=parse(path,identity,folder/'assets',source_path=name,source_url=final_url)
                pending=[];files=[{'path':name,'kind':document['kind'],'status':'parsed','passages':len(document['passages']),'coverage':document['coverage']}]
                if document['kind']=='html' and (snapshot is not None or not _web_body_ready(document)) and kind=='url':
                    snapshot=data.get('snapshot_html')
                    if snapshot is None:snapshot,renderer=render_web_snapshot(final_url,allow_private=self.allow_private_urls)
                    if snapshot:
                        snapshot_name='browser-snapshot.html' if name!='browser-snapshot.html' else 'rendered-browser-snapshot.html'
                        rendered=source/snapshot_name;rendered.write_text(snapshot,'utf8')
                        document=parse(rendered,identity,folder/'assets',source_path=snapshot_name,source_url=final_url)
                        if data.get('snapshot_html') is not None:renderer={'status':'provided','message':'本次使用了调用方提供的浏览器 HTML 快照'}
                        files[0].update(parsed_path=snapshot_name,passages=len(document['passages']),coverage=document['coverage'])
                if not _readable(document) or document['kind']=='binary' or (kind=='url' and document['kind']=='html' and not _web_body_ready(document)):
                    browser=kind=='url' and document['kind']=='html'
                    pending.append({'path':name,'status':'needs_browser_snapshot' if browser else 'pending','action':'upload_browser_snapshot' if browser else 'provide_readable_source','url':source_url,'message':'原始网页已保存，但还没有可读取的正文。请在浏览器打开来源地址，保存完成加载后的 HTML，再提交 snapshot_html' if browser else '原件已保存；请提供可读取的文本、PDF、Office 文档或图片后继续解析'})
                    files[0]['status']='pending'
            document['source']='source' if archive or directory else 'source/'+document.get('source_path',name)
            document['source_url']=source_url;document['original_name']=name
            for unit in document['passages']:
                if unit.get('src','').startswith('/asset/'):unit['src']='/content-files/'+identity+'/asset/'+unit['src'].split('/')[-1]
                if not unit.get('en') and unit.get('kind') in ('image','visual','page_image'):
                    label=unit.get('alt') or unit.get('path') or name;unit['alt']=label
                    unit['sentences']=[{'id':unit['id']+'-s1','en':label,'zh':'','refs':unit.get('refs',[]),'precision':'object','source_unit_ids':unit.get('source_ids',[])}]
            status='needs_browser_snapshot' if any(p['status']=='needs_browser_snapshot' for p in pending) else 'partial' if pending and _readable(document) else 'pending' if pending else 'ready'
            coverage=_coverage(document,pending,files);document['coverage']=coverage;document['pending']=pending;document['import_status']=status
            row={'id':identity,'name':name,'title':data.get('title') or document['title'],'kind':document['kind'],'stored_name':stored,'original':True,'outline':document['outline'],'passage_ids':[p['id'] for p in document['passages']],'document':document,'source_kind':kind,'source_url':source_url,'final_url':final_url,'source_tree':'source','source_files':files,'source_fingerprint':fingerprint,'coverage':coverage,'pending':pending,'import_status':status,'model_calls':0}
            if renderer:row['browser_snapshot']=renderer
            provenance={k:row[k] for k in ('id','name','source_kind','source_url','final_url','source_tree','source_files','source_fingerprint','coverage','pending','import_status','model_calls')}
            (folder/'import-source.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2),'utf8')
            value['files'].append(row)
            shutil.move(str(folder),str(target))
            try:self.workspace.write(value,value['revision'])
            except Exception:
                shutil.rmtree(target);raise
        return row
