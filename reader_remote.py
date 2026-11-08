"""网页使用统一登录；远程 Agent 通过项目配对取得独立、可撤销的授权。"""
from reader_agent_contract import CLIENT_PATHS
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,unquote
import base64,hashlib,hmac,io,ipaddress,json,os,re,secrets,sqlite3,threading,time,uuid

ORIGIN=os.environ.get('MANUAL_READER_PUBLIC_ORIGIN','').rstrip('/')
REQUEST=threading.local()
if ORIGIN:
    parsed=urlsplit(ORIGIN)
    if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost')):raise ValueError('远程入口必须使用 HTTPS')
    if parsed.path or parsed.query or parsed.fragment or parsed.username:raise ValueError('远程入口只填写网站的根地址')

def public_base():
    return ORIGIN+('/machine/v1' if getattr(REQUEST,'machine',False) else '') if ORIGIN else 'http://127.0.0.1:'+os.environ.get('MANUAL_READER_PORT','8939')

def digest(value):return hashlib.sha256(value.encode()).hexdigest()

class Pairings:
    def __init__(self,folder):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        key=self.folder/'pairing-key'
        if not key.exists():
            try:
                with os.fdopen(os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as file:file.write(secrets.token_bytes(32))
            except FileExistsError:pass
        self.key=key.read_bytes();self.path=self.folder/'pairings.sqlite3'
        with self.db() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS pairings (id TEXT PRIMARY KEY, owner TEXT NOT NULL, code_hash TEXT NOT NULL UNIQUE, token_hash TEXT NOT NULL UNIQUE, material_id TEXT, can_import INTEGER NOT NULL, can_manage INTEGER NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, code_expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)')
            conn.execute('UPDATE pairings SET revoked=1 WHERE material_id IS NULL OR material_id=""')
            conn.execute('UPDATE pairings SET can_import=0,can_manage=0 WHERE material_id IS NOT NULL')
    def db(self):return sqlite3.connect(self.path,timeout=15)
    def token(self,identity):return 'mr_'+base64.urlsafe_b64encode(hmac.new(self.key,identity.encode(),hashlib.sha256).digest()).decode().rstrip('=')
    @staticmethod
    def public(row):return {**{k:row[k] for k in ['id','material_id','can_import','can_manage','created','expires','code_expires','revoked']},'permanent':row['expires']==0}
    def list(self,owner):
        with self.db() as conn:
            conn.row_factory=sqlite3.Row
            return [self.public(r) for r in conn.execute('SELECT * FROM pairings WHERE owner=? AND (expires=0 OR expires>?) ORDER BY created DESC',(owner,time.time()))]
    def issue(self,owner,data):
        hours=data.get('hours',8)
        if isinstance(hours,bool) or not isinstance(hours,int) or not (hours==0 or 1<=hours<=24):raise ValueError('连接有效期请选择1至24小时或永久有效')
        material_id=data.get('material_id') or None
        if not isinstance(material_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,90}',material_id):raise ValueError('连接必须指定当前项目')
        identity='pair-'+uuid.uuid4().hex;code=secrets.token_urlsafe(24);now=time.time()
        with self.db() as conn:conn.execute('INSERT INTO pairings VALUES(?,?,?,?,?,?,?,?,?,?,0)',(identity,owner,digest(code),digest(self.token(identity)),material_id,0,0,now,0 if hours==0 else now+hours*3600,now+600))
        row=next(r for r in self.list(owner) if r['id']==identity)
        return {**row,'pairing_code':code,'exchange_url':ORIGIN+'/machine/v1/pair/exchange'}
    def exchange(self,code):
        with self.db() as conn:
            conn.row_factory=sqlite3.Row;row=conn.execute('SELECT * FROM pairings WHERE code_hash=?',(digest(code),)).fetchone()
        if not row or not row['material_id'] or row['revoked'] or row['code_expires']<=time.time() or row['expires']!=0 and row['expires']<=time.time():return None
        return {'access_token':self.token(row['id']),'token_type':'Bearer','expires_at':row['expires'] or None,'api_base':ORIGIN+'/machine/v1','material_id':row['material_id'],'capabilities':{'read':True,'write':True,'mark_read':False,'write_user_notes':False,'answer_exercises':False,'import':bool(row['can_import']),'manage':bool(row['can_manage'])},'endpoints':{'guide':'/guide','progress':'/progress','work':'/work'}}
    def authorize(self,token):
        if not token.startswith('mr_') or len(token)>100:return None
        with self.db() as conn:
            conn.row_factory=sqlite3.Row;row=conn.execute('SELECT * FROM pairings WHERE token_hash=?',(digest(token),)).fetchone()
        return dict(row) if row and row['material_id'] and not row['revoked'] and (row['expires']==0 or row['expires']>time.time()) else None
    def revoke(self,owner,identity):
        with self.db() as conn:conn.execute('UPDATE pairings SET revoked=1 WHERE id=? AND owner=?',(identity,owner))
        return {'revoked':True}

STORE=None
STORE_LOCK=threading.Lock()
def pairings():
    global STORE
    with STORE_LOCK:
        if STORE is None:STORE=Pairings(os.environ.get('MANUAL_READER_SECURITY',str(Path(os.environ.get('MANUAL_READER_DB','reader.sqlite3')).parent/'security')))
    return STORE

class RemoteMixin:
    def authenticated(self):return bool(getattr(self,'_remote_authorized',False)) if ORIGIN else super().authenticated()
    def safe_origin(self):
        if not ORIGIN:return super().safe_origin()
        origin=self.headers.get('Origin')
        return (not origin or origin==ORIGIN) if self._remote_machine else origin==ORIGIN and self.headers.get('Sec-Fetch-Site')!='cross-site'
    def context(self):
        super().context()
        if getattr(self,'_grant_material',None):
            # 子类把经过授权的材料编号写入本次请求的上下文
            self.bind_material(self._grant_material)
    def browser_owner(self):
        owners={s.strip().casefold() for s in os.environ.get('MANUAL_READER_OWNERS','').split(',') if s.strip()}
        email=self.headers.get('X-Aialra-Email','').casefold();user=self.headers.get('X-Aialra-User','').casefold()
        secret=os.environ.get('MANUAL_READER_PROXY_SECRET','')
        trusted=bool(secret) and secrets.compare_digest(self.headers.get('X-Reader-Proxy-Secret',''),secret) and ipaddress.ip_address(self.client_address[0]).is_loopback and self.headers.get('X-Aialra-Authenticated')=='1'
        return email or user if trusted and owners.intersection([email,user]) else None
    def json(self,data,status=200):
        if ORIGIN and getattr(self,'_remote_machine',False) and getattr(self,'_mapped_path','')=='/api/guide' and status==200:
            data=json.loads(json.dumps(data,ensure_ascii=False).replace('/api/','/'))
            data['headers']={'Authorization':'Bearer <access_token>','X-Reader-Material':getattr(self,'_grant_material',None) or self.headers.get('X-Reader-Material','')}
            data['api_base']=public_base();data['capabilities']={'read':True,'write':True,'mark_read':False,'write_user_notes':False,'answer_exercises':False,'import':bool(self._grant['can_import']),'manage':bool(self._grant['can_manage'])}
            data['project_scope']={'material_id':self._grant_material,'upload_project_files':True,'other_projects':False}
            for key in ('library','project','lecture'):data.get('endpoints',{}).pop(key,None)
        return super().json(data,status)
    def begin_remote(self,method):
        REQUEST.machine=False;self._remote_machine=False;self._remote_authorized=False;self._grant_material=None
        if not ORIGIN:return False
        path=urlsplit(self.path).path
        for _ in range(3):
            decoded=unquote(path)
            if decoded==path:break
            path=decoded
        if any(part=='..' for part in path.split('/')) or any(c in path for c in ('\\','\x00','?','#')):
            self.json({'error':'接口路径不正确'},400);return True
        if path=='/healthz' and method=='GET':self.json({'application':'codex-manual-reader','status':'ready','authentication':'authentik'});return True
        if path=='/machine/v1/pair/exchange' and method=='POST':
            if self.headers.get('Origin') not in (None,ORIGIN):self.json({'error':'请求来源不正确'},403);return True
            auth=self.headers.get('Authorization','');value=pairings().exchange(auth.removeprefix('Reader-Pairing ')) if auth.startswith('Reader-Pairing ') else None
            self.json(value or {'error':'配对码已过期、撤销或不正确'},200 if value else 401);return True
        if path.startswith('/machine/'):
            self._remote_machine=True;REQUEST.machine=True
            authorization=self.headers.get('Authorization','');grant=pairings().authorize(authorization[7:]) if authorization.startswith('Bearer ') else None
            if not grant:self.json({'error':'请使用有效的 Bearer 连接令牌'},401);return True
            suffix=path.removeprefix('/machine/v1')
            if not path.startswith('/machine/v1/') or '..' in suffix.split('/') or '\\' in suffix:self.json({'error':'接口路径不正确'},404);return True
            mapped=suffix if suffix.startswith(('/page/','/asset/','/content-files/')) or suffix in ('/original','/original.pdf') else '/api'+suffix
            query=parse_qs(urlsplit(self.path).query,keep_blank_values=True)
            requested=[self.headers.get('X-Reader-Material'),*query.get('material',[])]
            if any(value is not None and value!=grant['material_id'] for value in requested):self.json({'error':'本连接只能访问配对时选择的项目'},403);return True
            if method=='POST' and mapped=='/api/read':self.json({'error':'阅读进度只由用户在页面上手动勾选，Agent API 无权修改'},403);return True
            if not self.project_available(grant['material_id']):self.json({'error':'当前项目已移除或不可访问'},403);return True
            self._grant_material=grant['material_id'];self._grant=grant
            readable=mapped in ('/api/writing/rules','/api/format/audit','/api/reading/layout','/api/practice-book','/api/import-jobs') or bool(re.fullmatch(r'/api/import-jobs/import-[a-f0-9]{20}',mapped)) or mapped in ('/api/runs','/api/coverage','/api/learning','/api/content','/api/guide','/api/material','/api/index','/api/progress','/api/work','/api/translations','/api/statistics','/api/samples','/original','/original.pdf') or bool(re.fullmatch(r'/api/questions/[^/]+/history|/api/(?:passages|rounds)/[^/]+|/api/runs/run-[a-f0-9]{20}(?:/(?:groups/[1-4]|reference|caller))?',mapped)) or mapped.startswith(('/page/','/asset/','/content-files/'))
            if mapped in ('/api/project-files/tree','/api/project-files/preview','/api/project-files/raw','/api/project-files/page','/api/project-files/events','/api/project-files/trash'):readable=True
            if mapped in CLIENT_PATHS:readable=True
            if mapped in ('/api/terms','/api/terms/audit','/api/quality-review','/api/quality-review/calls'):readable=True
            if mapped=='/api/learning/exclusions':readable=True
            if not grant['material_id'] and mapped in ('/api/library','/api/library/tree'):readable=True
            writable=bool(re.fullmatch(r'/api/questions/[^/]+/deletion-(?:preview|confirm)',mapped)) or mapped in ('/api/reading/layout','/api/translations/groups','/api/translations/visuals') or mapped=='/api/import-jobs' or bool(re.fullmatch(r'/api/import-jobs/import-[a-f0-9]{20}/retry',mapped)) or mapped in ('/api/content/explanations/format','/api/content/import','/api/questions/sync','/api/teaching/result','/api/learning/outline','/api/learning/outline/edit','/api/learning/select','/api/content/normalize','/api/draft-batches','/api/question-batches','/api/content/files','/api/content/explanations','/api/content/presentation','/api/rounds','/api/terms','/api/translations','/api/focus','/api/history/back','/api/history/forward','/api/drafts') or bool(re.fullmatch(r'/api/rounds/[^/]+/(?:result|status|metrics)|/api/questions/[^/]+(?:/answer)?|/api/quizzes/[^/]+/options',mapped))
            if not grant['material_id'] and grant['can_import'] and mapped in ('/api/projects','/api/library/import','/api/lectures'):writable=True
            if not grant['material_id'] and grant['can_manage'] and (mapped in ('/api/settings','/api/folders','/api/library/clear-trash') or re.fullmatch(r'/api/library/(?:folder|material)/[^/]+/(?:rename|move|trash|restore)',mapped)):writable=True
            if mapped=='/api/runs' or re.fullmatch(r'/api/runs/run-[a-f0-9]{20}/(?:events|groups/[1-4]/result)',mapped):writable=True
            if mapped in('/api/project-files/file','/api/project-files/action'):writable=True
            if mapped=='/api/terms/normalize':writable=True
            if mapped=='/api/learning/exclusions':writable=True
            if (method=='GET' and not readable) or (method=='POST' and not writable) or any(value!='current' for value in query.get('scope',[])):self.json({'error':'本连接没有执行此操作的权限'},403);return True
            self.path=mapped+('?' + urlsplit(self.path).query if urlsplit(self.path).query else '');self._mapped_path=mapped;self._remote_authorized=True
        else:
            owner=self.browser_owner()
            if not owner:self.json({'error':'请先通过网站登录'},401);return True
            self._remote_authorized=True;self._mapped_path=path
            if path=='/api/session' and method=='GET':self.json({'pin':'','api_base':ORIGIN,'material_id':self.default_material_id(),'application':'codex-manual-reader','remote':True,'owner':owner});return True
            if path=='/api/pairings' and method=='GET':self.json({'connections':pairings().list(owner)});return True
            pairing_revoke=re.fullmatch(r'/api/pairings/([A-Za-z0-9_-]+)/revoke',path)
            if method=='POST' and (path=='/api/pairings' or pairing_revoke):
                if not self.safe_origin():self.json({'error':'请求来源不正确'},403);return True
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<=size<=8192:raise ValueError('配对请求过大')
                    data=json.loads(self.rfile.read(size) or b'{}')
                    if not isinstance(data,dict):raise ValueError('请提交 JSON 对象')
                    if not pairing_revoke and not self.project_available(data.get('material_id')):raise ValueError('请选择当前仍存在的项目')
                    self.json(pairings().issue(owner,data) if not pairing_revoke else pairings().revoke(owner,pairing_revoke[1]))
                except (ValueError,TypeError):self.json({'error':'配对参数不正确，请检查范围和有效期'},400)
                return True
        if method=='POST':
            if not self.safe_origin():self.json({'error':'请求来源不正确'},403);return True
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<=size<=64_000_000:raise ValueError('请求内容过大')
                body=self.rfile.read(size);data=json.loads(body or b'{}')
                if not isinstance(data,dict):raise ValueError('请提交 JSON 对象')
                if self._remote_machine and data.get('material_id') is not None and data['material_id']!=self._grant_material:
                    self.json({'error':'请求内容不属于本连接授权的项目'},403);return True
                if self._mapped_path=='/api/library/import' and data.get('source_path'):raise ValueError('远程导入请上传文件内容，不能读取服务器路径')
                self.rfile=io.BytesIO(body)
            except (ValueError,TypeError) as error:self.json({'error':str(error)},400);return True
        return False
    def do_GET(self):
        if not self.begin_remote('GET'):return super().do_GET()
    def do_POST(self):
        if not self.begin_remote('POST'):return super().do_POST()
