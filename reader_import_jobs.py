"""后台解析原件，任务按项目保存，浏览器可以继续阅读并随时查看结果。"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import copy,json,os,re,threading,time,uuid
from reader_workspace import Workspace
POOL=ThreadPoolExecutor(max_workers=2,thread_name_prefix='reader-import');ACTIVE=set();LOCK=threading.Lock()
class ImportJobs:
 def __init__(self,folder,material):self.root=Path(folder);self.folder=self.root/'import-jobs';self.material=material
 def _path(self,identity):
  if not re.fullmatch(r'import-[a-f0-9]{20}',identity):raise ValueError('导入任务编号不正确')
  return self.folder/(identity+'.json')
 def _save(self,row):
  self.folder.mkdir(parents=True,exist_ok=True);path=self._path(row['id']);temporary=path.with_suffix('.next');temporary.write_text(json.dumps(row,ensure_ascii=False),'utf-8');os.chmod(temporary,0o600);temporary.replace(path)
 def read(self,identity):
  path=self._path(identity)
  if not path.exists():raise ValueError('当前项目没有这项导入任务')
  return json.loads(path.read_text('utf-8'))
 def list(self):
  rows=[json.loads(p.read_text('utf-8')) for p in self.folder.glob('import-*.json') if not p.name.endswith('.payload.json')]
  for row in rows:
   if row['status'] in ('queued','running'):self.dispatch(row['id'])
  return sorted(rows,key=lambda r:r['created_at'],reverse=True)[:50]
 def submit(self,data):
  if not isinstance(data,dict) or not any(data.get(k) for k in ('filename','url','repository_url','files')):raise ValueError('请选择文件或填写网址')
  if any(k in data for k in ('path','local_path','source_path')):raise ValueError('请上传原件，不读取服务器本地路径')
  identity='import-'+uuid.uuid4().hex[:20];self.folder.mkdir(parents=True,exist_ok=True);path=self.folder/(identity+'.payload.json');path.write_text(json.dumps(data,ensure_ascii=False),'utf-8');os.chmod(path,0o600)
  row={'id':identity,'material_id':self.material,'name':data.get('filename') or data.get('url') or data.get('repository_url') or '文件夹','status':'queued','created_at':time.time(),'message':'已加入后台导入'};self._save(row);self.dispatch(identity);return row
 def dispatch(self,identity):
  key=str(self._path(identity).resolve())
  with LOCK:
   if key in ACTIVE:return
   ACTIVE.add(key)
  def work():
   try:
    row=self.read(identity);row.update(status='running',message='正在保存原件并解析结构');self._save(row)
    data=json.loads((self.folder/(identity+'.payload.json')).read_text('utf-8'))
    if data.get('repository_url'):data.update(kind='repository',url=data.pop('repository_url'))
    result=Workspace(self.root).add_file(data)
    row.update(status='ready',message='原件已保存，可以开始阅读',file={k:result[k] for k in ('id','name','import_status','coverage','pending') if k in result},completed_at=time.time());self._save(row)
    (self.folder/(identity+'.payload.json')).unlink(missing_ok=True)
   except Exception as error:
    row=self.read(identity);row.update(status='error',message=str(error),completed_at=time.time());self._save(row)
   finally:
    with LOCK:ACTIVE.discard(key)
  POOL.submit(work)
 def retry(self,identity):
  row=self.read(identity)
  if row['status']!='error':raise ValueError('只有未完成的导入任务需要重试')
  row.update(status='queued',message='已重新加入后台导入');self._save(row);self.dispatch(identity);return row
