"""每个项目独立保存可浏览的文件；原文编号和学习记录继续留在原处。"""
from pathlib import Path, PurePosixPath
from concurrent.futures import ThreadPoolExecutor
import base64, csv, hashlib, io, json, mimetypes, os, shutil, stat, subprocess, tempfile, threading, zipfile

_LOCK = threading.RLock()
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='file-preview')
_JOBS = {}
OFFICE = {'.doc','.docx','.ppt','.pptx','.xls','.xlsx','.odt','.ods','.odp','.rtf'}
PAGED = {'.pdf','.epub','.mobi','.fb2','.xps','.oxps','.cbz'}
IMAGES = {'.png','.jpg','.jpeg','.gif','.webp','.bmp','.svg','.ico','.avif'}
TEXT = {'.txt','.md','.markdown','.rst','.py','.js','.jsx','.ts','.tsx','.c','.h','.cpp','.hpp','.rs','.go','.java','.cs','.sv','.v','.vh','.vhd','.tcl','.sh','.ps1','.bat','.cmd','.json','.jsonl','.xml','.yaml','.yml','.toml','.ini','.cfg','.conf','.env','.log','.sql','.css','.scss','.html','.htm','.tex','.csv','.tsv','.ipynb','.sdc','.sdf','.lib','.lef','.def'}

def relative(value, empty=False):
    name=str(value or '').replace('\\','/')
    if empty and not name:return ''
    if not name or name.startswith('/') or ':' in name or any(ord(c)<32 for c in name) or len(name)>2000 or any(p in ('','.','..') for p in name.split('/')):
        raise ValueError('请使用项目根目录内的相对路径')
    return PurePosixPath(name).as_posix()

class ProjectFiles:
    def __init__(self, identity, folder, settings_path, original=None):
        self.identity=identity;self.folder=Path(folder).resolve();self.settings_path=Path(settings_path);self.original=original or {}
        self.config=self.folder/'project-files.json';self.cache=self.folder/'file-previews'

    def ensure(self):
        with _LOCK:
            if self.config.exists():value=json.loads(self.config.read_text('utf8'))
            else:
                options=json.loads(self.settings_path.read_text('utf8')) if self.settings_path.exists() else {}
                base=Path(options.get('project_root_base') or self.settings_path.parent/'project-files').expanduser().resolve()
                value={'version':1,'root':str(base/self.identity),'sources':{}}
            root=Path(value['root'])
            if root.is_symlink() or self.folder.is_relative_to(root.resolve()):raise ValueError('项目文件根目录不能指向阅读器保存数据库和笔记的目录，请选择独立位置')
            root.mkdir(parents=True,exist_ok=True)
            # 只有原件进入文件根目录，数据库、令牌和笔记不会被当作项目文件展示。
            work=self.folder/'reading-workspace.json';signature=[work.stat().st_mtime_ns if work.exists() else 0,self.original.get('original_file'),'project-files-v1']
            if value.get('seed_revision')==signature:return value
            workspace=json.loads(work.read_text('utf8')) if work.exists() else {'files':[]}
            candidates=[]
            name=self.original.get('original_file')
            if name:
                source=(self.folder/name).resolve()
                if not source.is_file():source=(Path(__file__).resolve().parent/name).resolve()
                if source.is_file():candidates.append(('primary',self.original.get('original_name') or source.name,source,None))
            for row in workspace.get('files',[]):
                source=self.folder/'reading-files'/row['id']/row['stored_name']
                if source.is_file():candidates.append((row['id'],row['name'],source,source.parent/row.get('source_tree','source')))
            changed=not self.config.exists()
            for fid,label,source,tree in candidates:
                if fid in value['sources']:continue
                try:label=relative(label)
                except ValueError:label=source.name
                target=root/'sources'/label
                if target.exists():target=root/'sources'/fid/label
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
                if tree and tree.is_dir() and source.suffix=='.zip':
                    destination=target.with_name(target.stem+'-files');shutil.copytree(tree,destination,dirs_exist_ok=True,symlinks=True)
                value['sources'][fid]=target.relative_to(root).as_posix();changed=True
            if changed or value.get('seed_revision')!=signature:
                value['seed_revision']=signature
                self.folder.mkdir(parents=True,exist_ok=True);temporary=self.config.with_suffix('.next.json');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf8');os.replace(temporary,self.config)
            return value

    def target(self,name,exists=True):
        root=Path(self.ensure()['root']).resolve();path=root/relative(name)
        if not path.resolve().is_relative_to(root):raise ValueError('文件路径越出了当前项目')
        if exists and not path.exists():raise ValueError('这个文件已移走，请刷新文件目录')
        return path

    def listing(self,name='',offset=0,limit=500):
        config=self.ensure();root=Path(config['root']).resolve();directory=self.target(name) if name else root
        if not directory.is_dir():raise ValueError('请选择项目内的文件夹')
        offset=max(0,int(offset));limit=min(1000,max(1,int(limit)))
        with os.scandir(directory) as scan:paths=sorted(scan,key=lambda p:(not p.is_dir(follow_symlinks=False),p.name.casefold()))
        total=len(paths);entries=[]
        for item in paths[offset:offset+limit]:
            p=Path(item.path)
            try:
                info=p.lstat();inside=p.resolve().is_relative_to(root);folder=inside and p.is_dir()
                entries.append({'name':p.name,'path':p.relative_to(root).as_posix(),'kind':'directory' if folder else 'file','size':info.st_size,'modified':str(info.st_mtime_ns),'hidden':p.name.startswith('.') or bool(getattr(info,'st_file_attributes',0)&2),'symlink':p.is_symlink(),'accessible':inside and (folder or p.is_file())})
            except OSError:entries.append({'name':p.name,'path':p.relative_to(root).as_posix(),'kind':'file','accessible':False})
        entries.sort(key=lambda p:(p['kind']!='directory',p['name'].casefold()))
        return {'material_id':self.identity,'root':str(root),'path':name,'entries':entries,'total':total,'next_offset':offset+limit if offset+limit<total else None,'source_paths':config['sources'],'includes_hidden':True}

    def put(self,data):
        path=self.target(data.get('path'),False)
        if relative(data.get('path')) in self.ensure()['sources'].values():raise ValueError('该文件是已登记原件，请通过材料导入保存新版本，保留原有定位')
        if path.is_symlink():raise ValueError('请直接写入普通文件，不覆盖符号链接')
        raw=base64.b64decode(data.get('content_base64',''),validate=True)
        if len(raw)>40_000_000:raise ValueError('单次写入最大 40 MB')
        if path.exists() and str(data.get('expected_modified'))!=str(path.stat().st_mtime_ns):raise ValueError('文件已存在，覆盖前请读取并提交 expected_modified')
        path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as output:output.write(raw);temporary=Path(output.name)
        os.replace(temporary,path)
        return {'path':relative(data['path']),'size':len(raw),'modified':str(path.stat().st_mtime_ns)}

    def _key(self,path):
        info=path.stat();return hashlib.sha256((str(path.resolve())+str(info.st_mtime_ns)+str(info.st_size)).encode()).hexdigest()[:32]

    def _paged(self,path):
        if path.suffix.lower() not in OFFICE:return path
        key=self._key(path);destination=self.cache/key;pdf=destination/(path.stem+'.pdf')
        if pdf.exists():return pdf
        program=os.environ.get('MANUAL_READER_LIBREOFFICE') or shutil.which('libreoffice') or shutil.which('soffice')
        if not program:
            for candidate in ('C:/Program Files/LibreOffice/program/soffice.exe','/Applications/LibreOffice.app/Contents/MacOS/soffice'):
                if Path(candidate).is_file():program=candidate;break
        if not program:return None
        destination.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='reader-office-') as temporary:
            profile=Path(temporary)/'profile';profile.mkdir()
            # 临时独立配置关闭宏和外部链接；转换进程不打开用户的办公软件会话。
            (profile/'user').mkdir();(profile/'user/registrymodifications.xcu').write_text('<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item><item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="LinkUpdateMode" oor:op="fuse"><value>0</value></prop></item></oor:items>','utf8')
            result=subprocess.run([program,'-env:UserInstallation='+profile.as_uri(),'--headless','--convert-to','pdf','--outdir',str(destination),str(path)],capture_output=True,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode or not pdf.exists():raise ValueError('本机办公转换器未能打开这个文件，可下载原件查看')
        return pdf

    def preview(self,name):
        path=self.target(name)
        if not path.is_file():raise ValueError('请选择普通文件')
        extension=path.suffix.lower();info=path.stat();result={'path':relative(name),'name':path.name,'size':info.st_size,'modified':str(info.st_mtime_ns),'kind':'binary','mime':mimetypes.guess_type(path.name)[0] or 'application/octet-stream'}
        if extension in OFFICE or extension in PAGED:
            key=str(path.resolve())+self._key(path)
            if extension in OFFICE:
                with _LOCK:
                    if len(_JOBS)>64:
                        for old in [k for k,v in _JOBS.items() if v.done()][:-32]:_JOBS.pop(old,None)
                    if key not in _JOBS:_JOBS[key]=_POOL.submit(self._paged,path)
                    job=_JOBS[key]
                if not job.done():return {**result,'kind':'preparing','message':'正在本机准备文件预览'}
                try:document=job.result()
                except Exception:return self.office_structure(path,result)
                if document is None:return self.office_structure(path,result)
            else:document=path
            import fitz
            try:
                with fitz.open(document) as doc:return {**result,'kind':'pages','pages':len(doc),'engine':'LibreOffice + PyMuPDF' if extension in OFFICE else 'PyMuPDF'}
            except Exception:return {**result,'message':'这个文件无法生成分页预览，可以下载原件查看'}
        if extension in IMAGES:return {**result,'kind':'image'}
        if result['mime'].startswith(('audio/','video/')):return {**result,'kind':result['mime'].split('/')[0]}
        if extension=='.zip':
            try:
                with zipfile.ZipFile(path) as archive:items=[{'name':i.filename,'size':i.file_size} for i in archive.infolist()[:10000]]
                return {**result,'kind':'archive','entries':items}
            except zipfile.BadZipFile:pass
        with path.open('rb') as stream:raw=stream.read(1_000_001)
        text=None
        if extension in TEXT or (b'\0' not in raw[:8192]):
            for encoding in ('utf-8-sig','utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'gb18030'):
                try:text=raw[:1_000_000].decode(encoding);break
                except UnicodeError:pass
        if text is not None:
            if extension in ('.csv','.tsv'):
                rows=list(csv.reader(io.StringIO(text),delimiter='\t' if extension=='.tsv' else ','))[:2000]
                return {**result,'kind':'table','rows':rows,'truncated':len(raw)>1_000_000 or len(rows)==2000}
            return {**result,'kind':'markdown' if extension in ('.md','.markdown') else 'text','text':text,'truncated':len(raw)>1_000_000}
        return {**result,'hex':raw[:4096].hex(' '),'message':'显示文件字节，可下载原件用专用程序打开'}

    def office_structure(self,path,result):
        """未安装办公转换器时，仍可在本机阅读 OOXML 的正文、表格和幻灯片文本。"""
        blocks=[]
        try:
            if path.suffix.lower()=='.docx':
                from docx import Document
                doc=Document(path);blocks=[p.text for p in doc.paragraphs if p.text]+['\n'.join(' | '.join(c.text for c in row.cells) for row in table.rows) for table in doc.tables]
            elif path.suffix.lower()=='.pptx':
                from pptx import Presentation
                blocks=['第 '+str(i+1)+' 页\n'+'\n'.join(s.text for s in slide.shapes if s.has_text_frame) for i,slide in enumerate(Presentation(path).slides)]
            elif path.suffix.lower()=='.xlsx':
                import openpyxl
                book=openpyxl.load_workbook(path,read_only=True,data_only=True)
                for sheet in book:blocks.append(sheet.title+'\n'+'\n'.join(' | '.join('' if v is None else str(v)for v in row)for row in sheet.iter_rows(max_row=2000,values_only=True)))
                book.close()
        except Exception:pass
        return {**result,'kind':'text','text':'\n\n'.join(blocks),'message':'当前为文档内容预览；安装 LibreOffice 后可按原版式分页查看'} if blocks else {**result,'message':'安装 LibreOffice 后可在此按原版式查看，也可以下载原件'}

    def page(self,name,page,scale=1):
        import fitz
        path=self.target(name);document=self._paged(path)
        if document is None:raise ValueError('当前环境尚未安装办公转换器')
        with fitz.open(document) as doc:
            if not 0<=page<len(doc):raise ValueError('页码超出文件范围')
            rect=doc[page].rect;scale=min(2,max(.3,scale),(16_000_000/max(1,rect.width*rect.height))**.5)
            return doc[page].get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('png')
