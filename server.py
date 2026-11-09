from reader_performance import timed_call,asset_digest,version_html,ReadCache
from reader_database import RequestConnection,ReadSnapshot
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote
import copy, json, os, re, secrets, sqlite3, threading, uuid
from reader_questions import has_current_answer,update_round_status,answer_revision
from datetime import datetime, timezone

JSON_RESPONSES=ReadCache(8)
ROOT=Path(__file__).resolve().parent
MATERIAL=json.loads(Path(os.environ.get('MANUAL_READER_MATERIAL',str(ROOT/'material.json'))).read_text('utf8'))
PASSAGES={p['id']:p for p in MATERIAL['passages']}
SENTENCES={s['id']:(p,s) for p in MATERIAL['passages'] for s in p['sentences']}
PORT=int(os.environ.get('MANUAL_READER_PORT','8939'))
DB=Path(os.environ.get('MANUAL_READER_DB',str(ROOT/'reader.sqlite3')))
PIN_FILE=Path(os.environ.get('MANUAL_READER_PIN_FILE',str(ROOT/'connection-pin.txt')))
if not PIN_FILE.exists():PIN_FILE.write_text(str(secrets.randbelow(900000)+100000),'utf8')
PIN=PIN_FILE.read_text('utf8').strip()
LOCK=threading.RLock()
def stamp():return datetime.now(timezone.utc).isoformat()
def default_focus():
 p=next((x['passage_id'] for x in MATERIAL['outline'] if x['title']=='摘要' and x.get('passage_id')),MATERIAL['passages'][0]['id'])
 return {'passage_id':p,'sentence_id':None,'revision':1}
def initial():return {'revision':1,'focus':default_focus(),'drafts':{},'rounds':[],'terms':[],'quizzes':[],'notes':{},'read_passages':{}}
def db():
 conn=sqlite3.connect(DB,timeout=15,factory=RequestConnection);conn.execute('PRAGMA journal_mode=WAL');conn.execute('CREATE TABLE IF NOT EXISTS reader_state (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)');return conn
def read_state(with_snapshot=False):
 with db() as c:
  row=c.execute('SELECT value FROM reader_state WHERE id=1').fetchone()
 if row:serialized=row[0];state=json.loads(serialized)
 else:
  with LOCK,db() as c:
   c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT value FROM reader_state WHERE id=1').fetchone()
   if row:serialized=row[0];state=json.loads(serialized)
   else:
    state=initial();serialized=json.dumps(state,ensure_ascii=False);c.execute('INSERT INTO reader_state VALUES(1,?)',(serialized,))
 return ReadSnapshot(state,serialized) if with_snapshot else state

def mutate(fn,snapshot=None):
 with LOCK,db() as c:
  c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT value FROM reader_state WHERE id=1').fetchone()
  state=snapshot.take(row[0] if row else None) if isinstance(snapshot,ReadSnapshot) else None
  if state is None:state=json.loads(row[0]) if row else initial()
  result=fn(state);state['revision']+=1;c.execute('INSERT INTO reader_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',(json.dumps(state,ensure_ascii=False),));return result


def find_round(state,rid):
 item=next((r for r in state['rounds'] if r['id']==rid),None)
 if not item:raise ValueError('没有找到这一轮问题')
 return item
def text(v,label,limit=20000):
 if not isinstance(v,str) or not v.strip() or len(v)>limit:raise ValueError(label+'不能为空，也不能超过长度限制')
 return v.strip()
def locate(passage_id,sentence_id=None):
 passage=PASSAGES.get(passage_id)
 if not passage:raise ValueError('没有找到这个段落')
 if sentence_id and (sentence_id not in SENTENCES or SENTENCES[sentence_id][0]['id']!=passage_id):raise ValueError('句子不属于所选段落')
 return passage

def create_round(state,data):
 pid=data.get('passage_id');locate(pid);values=data.get('questions')
 if not isinstance(values,list) or not values or len(values)>100:raise ValueError('请填写本轮问题')
 if not all(isinstance(row,dict) for row in values):raise ValueError('每个问题必须是一个对象')
 key=text(data.get('request_id'),'提交编号',100)
 prior=next((r for r in state['rounds'] if r.get('request_id')==key),None)
 if prior:
  old=[{'sentence_id':q.get('sentence_id'),'parent_question_id':q.get('parent_question_id'),'text':q['text']} for q in prior['questions']]
  candidate=[{'sentence_id':q.get('sentence_id'),'parent_question_id':q.get('parent_question_id'),'text':str(q.get('text','')).strip()} for q in values]
  if prior['passage_id']!=pid or old!=candidate:raise ValueError('提交编号已用于其他内容')
  return prior
 allq={q['id']:q for r in state['rounds'] for q in r['questions']};questions=[]
 for row in values:
  sid=row.get('sentence_id');locate(pid,sid);parent=row.get('parent_question_id')
  if parent and (parent not in allq or allq[parent]['passage_id']!=pid):raise ValueError('追问不属于当前段落')
  questions.append({'id':'q-'+uuid.uuid4().hex[:16],'passage_id':pid,'sentence_id':sid,'text':text(row.get('text'),'问题',5000),'parent_question_id':parent,'answer':None,'term_ids':[]})
 r={'id':'r-'+uuid.uuid4().hex[:16],'request_id':key,'passage_id':pid,'created_at':stamp(),'questions':questions,'status':'pending'};state['rounds'].append(r)
 drafts=state['drafts'].get(pid,{})
 for q,row in zip(questions,values):
  draft_key=row.get('draft_key') or ('followup:'+q['parent_question_id'] if q.get('parent_question_id') else q.get('sentence_id'))
  if row.get('draft_text') is None or drafts.get(draft_key)==row['draft_text']:drafts.pop(draft_key,None)
 if not drafts:state['drafts'].pop(pid,None)
 return r

def save_result(state,rid,data):
 r=find_round(state,rid);known={q['id']:q for q in r['questions']};answers=data.get('answers',[]);terms=data.get('terms',[]);quizzes=data.get('quizzes',[])
 if not all(isinstance(x,list) for x in (answers,terms,quizzes)):raise ValueError('回答、术语和练习必须是列表')
 if not all(isinstance(row,dict) for group in (answers,terms,quizzes) for row in group):raise ValueError('每个回答、术语和练习必须是对象')
 if len({a.get('question_id') for a in answers})!=len(answers):raise ValueError('同一问题不能提交多条回答')
 for a in answers:
  if a.get('question_id') not in known:raise ValueError('回答的问题不属于这一轮')
  q=known[a['question_id']]
  if q.get('deleted_at'):raise ValueError('问题已删除，请重新读取当前问题池')
  if (q.get('client_id') or a.get('question_version') is not None) and a.get('question_version')!=q.get('version',1):raise ValueError('问题已更新，请使用本次 /work 返回的 question_version 写回')
  text(a.get('text'),'回答',30000)
  if has_current_answer(q) and q['answer']!=a['text'].strip():
   if a.get('revision_action')!='replace':raise ValueError('已有回答，修订时请明确提交 revision_action=replace')
   if not a.get('expected_answer_revision') or a['expected_answer_revision']!=answer_revision(q):raise ValueError('回答已有新版本，请读取新稿后再修订，不能覆盖后续写回')
 for t in terms:
  if t.get('term_id'):
   if not any(old['id']==t['term_id'] for old in state['terms']):raise ValueError('要复用的术语不存在')
  else:text(t.get('name'),'术语名称',500);text(t.get('definition'),'术语定义',12000)
  if any(q not in known for q in t.get('question_ids',[])):raise ValueError('术语关联的问题不属于这一轮')
 for q in quizzes:
  q.update(__import__('reader_contract').normalize_quiz(q))
  text(q.get('prompt'),'练习题',5000);text(q.get('explanation'),'练习解析',12000)
  options=q.get('options');correct=q.get('correct_index');qids=q.get('question_ids',[])
  if not isinstance(options,list) or len(options)<2 or len(options)>6 or any(not isinstance(o,str) or not o.strip() for o in options):raise ValueError('练习题需要二至六个选项')
  if isinstance(correct,bool) or not isinstance(correct,int) or not 0<=correct<len(options):raise ValueError('正确选项编号不在范围内')
  if not qids or any(qid not in known for qid in qids):raise ValueError('练习必须关联本轮问题')
 for a in answers:
  q=known[a['question_id']]
  if q.get('answer') and not has_current_answer(q):q.setdefault('answer_history',[]).append({'text':q['answer'],'question_text':q.get('answered_question_text'),'question_version':q.get('answered_question_version',1),'saved_at':stamp(),'action':'reanswer',**({'parts':copy.deepcopy(q['answer_parts'])} if q.get('answer_parts') else {})})
  if not has_current_answer(q) and q.get('answer_parts'):
   if not q.get('answer'):q.setdefault('answer_parts_history',[]).append({'parts':copy.deepcopy(q['answer_parts']),'question_version':q.get('answered_question_version'),'saved_at':stamp(),'action':'reanswer'})
   q.pop('answer_parts',None)
  prior_revision=answer_revision(q)
  if has_current_answer(q) and q['answer']!=a['text'].strip():q.setdefault('answer_history',[]).append({'text':q['answer'],'question_text':q['text'],'question_version':q.get('version',1),'saved_at':stamp(),'action':'revise','answer_revision':prior_revision,'writer_source':data.get('_writer_source','reader'),**({'parts':copy.deepcopy(q['answer_parts'])}if q.get('answer_parts')else{})})
  q['answer']=a['text'].strip();q['answered_at']=q.get('answered_at') or stamp();q['answered_question_version']=q.get('version',1);q['answered_question_text']=q['text'];q.pop('answer_outdated',None)
  q.pop('answer_deleted_at',None);q.pop('answer_revision',None)
  if prior_revision!=answer_revision(q):q.setdefault('answer_changes',[]).append({'at':stamp(),'before_revision':prior_revision,'after_revision':answer_revision(q),'writer_source':data.get('_writer_source','reader'),'action':'revise'if prior_revision else'answer'})
 for t in terms:
  name=str(t.get('name','')).strip();existing=next((x for x in state['terms'] if x['id']==t.get('term_id') or (name and x['name'].casefold()==name.casefold())),None)
  if not existing:existing={'id':'t-'+uuid.uuid4().hex[:16],'name':name,'definition':t['definition'].strip(),'question_ids':[]};state['terms'].append(existing)
  for qid in t.get('question_ids',[]):
   if qid not in existing['question_ids']:existing['question_ids'].append(qid)
   if existing['id'] not in known[qid]['term_ids']:known[qid]['term_ids'].append(existing['id'])
 for quiz in quizzes:
  existing=next((x for x in state['quizzes'] if x['round_id']==rid and x['prompt']==quiz['prompt'].strip() and x.get('question_ids')==quiz.get('question_ids') and x.get('options')==quiz.get('options') and x.get('correct_index')==quiz.get('correct_index') and (not quiz.get('placement')or x.get('placement')==quiz['placement'])),None)
  if not existing:state['quizzes'].append({'id':'z-'+uuid.uuid4().hex[:16],'round_id':rid,'prompt':quiz['prompt'].strip(),'options':quiz['options'],'correct_index':quiz['correct_index'],'explanation':quiz['explanation'].strip(),'option_explanations':__import__('reader_contract').option_explanations(quiz),'question_ids':quiz['question_ids'],'selected':None,**__import__('reader_contract').quiz_fields(quiz)})
 for q in r['questions']:update_round_status(state,q['id'])
 r['updated_at']=stamp();return r

def round_context(state,rid):
 r=find_round(state,rid);p=PASSAGES[r['passage_id']];allq={q['id']:q for row in state['rounds'] for q in row['questions']};items=[]
 for q in r['questions']:
  sentence=SENTENCES.get(q.get('sentence_id'),(None,None))[1]
  parent=allq.get(q.get('parent_question_id'))
  refs=(sentence or {}).get('refs') or p['refs'];pages=[]
  for ref in refs:
   page=MATERIAL['pages'].get(ref.get('page_id'),{})
   if page.get('image'):
    filename=Path(page['image']).name;item={'url':f'http://127.0.0.1:{PORT}/page/{filename}','local_file':str(ROOT/'paper/05_writing/source-location/version-0001/pages'/filename),'bbox':ref.get('bbox')}
    if item not in pages:pages.append(item)
  items.append({**q,'source':{'original':(sentence or {}).get('en') or p['en'],'translation':(sentence or {}).get('zh') or p['zh'],'precision':(sentence or {}).get('precision') or p['precision'],'refs':refs,'page_images':pages,'notice':'原文文字由 PDF 提取，数学符号可能失真；需要核对时请查看原页图像'},'previous_question':parent})
 return {'material_id':MATERIAL['id'],'passage':p,'round':{**r,'questions':items},'terms':state['terms']}

class Handler(BaseHTTPRequestHandler):
 def log_message(self,fmt,*args):return
 def json(self,data,status=200,cache_tag=None):
  vary='Accept-Encoding, Cookie, Authorization, X-Reader-Material' if cache_tag else 'Accept-Encoding'
  if status==304:
   self.send_response(304);self.send_header('ETag',cache_tag);self.send_header('Cache-Control','private, no-cache');self.send_header('Vary',vary);self.end_headers();return
  accepts_gzip=bool(re.search(r'(?:^|,)\s*gzip(?:\s*;\s*q=(?!0(?:\.0*)?(?:\s|,|$))[^,]+)?(?:\s*,|\s*$)',self.headers.get('Accept-Encoding',''),re.I))
  def encode():
   value=timed_call('project',data)if callable(data)else data
   payload=timed_call('encode',__import__('reader_performance').json_bytes,value)
   decoded_length=len(payload);compressed=decoded_length>2048 and accepts_gzip
   if compressed:payload=timed_call('compress',__import__('gzip').compress,payload,compresslevel=3)
   return payload,compressed,decoded_length
  payload,compressed,decoded_length=JSON_RESPONSES.get(cache_tag,accepts_gzip,encode) if cache_tag else encode()
  self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','private, no-cache' if cache_tag else 'no-store');self.send_header('Vary',vary)
  if cache_tag:self.send_header('ETag',cache_tag)
  if compressed:self.send_header('Content-Encoding','gzip')
  self.send_header('X-Reader-Decoded-Length',str(decoded_length))
  self.send_header('Content-Length',str(len(payload)));self.end_headers()
  if self.command!='HEAD':self.wfile.write(payload)
 def file(self,path,ctype):
  if not path.is_file():self.send_error(404);return
  asset_path=urlsplit(self.path).path
  from urllib.parse import parse_qs
  versioned=path.suffix in ('.js','.css') and parse_qs(urlsplit(self.path).query).get('v')==[asset_digest(path)]
  cacheable=versioned or asset_path.startswith(('/page/','/asset/','/vendor/')) and asset_path!='/vendor/math-text.js' or bool(__import__('re').fullmatch(r'/content-files/[A-Za-z0-9_-]+/asset/[^/]+',asset_path))
  cache_control='private, max-age=31536000, immutable' if versioned else 'private, max-age=300' if cacheable else 'no-store'
  tag=f'W/"{path.stat().st_mtime_ns}-{path.stat().st_size}"'
  if cacheable and self.headers.get('If-None-Match')==tag:
   self.send_response(304);self.send_header('ETag',tag);self.send_header('Cache-Control',cache_control);self.send_header('Vary','Cookie, Authorization, X-Reader-Material');self.end_headers();return
  data=timed_call('asset_read',path.read_bytes)
  if path.suffix=='.html' and asset_path in ('/','/settings','/statistics','/quality-review'):data=version_html(data.decode('utf8'),path.parent).encode('utf8')
  self.send_response(200);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control',cache_control);self.send_header('X-Content-Type-Options','nosniff')
  if cacheable:self.send_header('ETag',tag);self.send_header('Vary','Cookie, Authorization, X-Reader-Material')
  if 'image/svg+xml' in ctype:self.send_header('Content-Security-Policy',"default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox")
  self.end_headers()
  if self.command!='HEAD':self.wfile.write(data)
 def do_HEAD(self):return self.do_GET()
 def authenticated(self):return secrets.compare_digest(self.headers.get('X-Reader-Pin',''),PIN)
 def safe_origin(self):
  origin=self.headers.get('Origin')
  return not origin or urlsplit(origin).netloc==self.headers.get('Host')
 def do_GET(self):
  path=unquote(urlsplit(self.path).path)
  if path=='/':return self.file(ROOT/'static/index.html','text/html; charset=utf-8')
  if path=='/app.js':return self.file(ROOT/'static/app.js','text/javascript; charset=utf-8')
  if path=='/app.css':return self.file(ROOT/'static/app.css','text/css; charset=utf-8')
  if path=='/original.pdf':return self.file(ROOT/'paper/00_source/materials/source-0001/original.pdf','application/pdf')
  if path=='/api/session':return self.json({'pin':PIN,'api_base':f'http://127.0.0.1:{PORT}','material_id':MATERIAL['id'],'application':'codex-manual-reader','instance_path':str(ROOT)})
  if path.startswith('/page/'):
   name=path.rsplit('/',1)[-1]
   if not re.fullmatch(r'page-\d{4}\.(png|text\.json)',name):self.send_error(404);return
   return self.file(ROOT/'paper/05_writing/source-location/version-0001/pages'/name,'image/png' if name.endswith('.png') else 'application/json; charset=utf-8')
  if path.startswith('/vendor/'):
   relative=path.removeprefix('/vendor/');base=Path(os.environ.get('MANUAL_READER_VENDOR',str(ROOT/'vendor'))).resolve();target=(base/relative).resolve()
   if not target.is_relative_to(base):self.send_error(404);return
   kind='text/css' if target.suffix=='.css' else 'text/javascript' if target.suffix=='.js' else 'font/woff2' if target.suffix=='.woff2' else 'application/octet-stream'
   return self.file(target,kind)
  if not path.startswith('/api/'):self.send_error(404);return
  if not self.authenticated():return self.json({'error':'连接码不正确'},403)
  try:
   if path=='/api/material':return self.json(MATERIAL)
   if path=='/api/index':return self.json({'id':MATERIAL['id'],'title':MATERIAL['title'],'outline':MATERIAL['outline'],'passages':[{'id':p['id'],'section_id':p['section_id'],'source_ids':p['source_ids'],'preview':p['zh'][:100]} for p in MATERIAL['passages']]})
   if path=='/api/progress':return self.json(read_state())
   if path.startswith('/api/rounds/'):return self.json(round_context(read_state(),path.split('/')[3]))
   if path.startswith('/api/passages/'):
    pid=path.split('/')[3];return self.json(locate(pid))
   if path=='/api/guide':return self.json({'material_id':MATERIAL['id'],'description':'阅读器不启动模型。先读取进度和待处理问题，再使用当前 Codex 会话回答并写回。','endpoints':{'index':'GET /api/index','material':'GET /api/material','progress':'GET /api/progress','passage':'GET /api/passages/{passage_id}','round':'GET /api/rounds/{round_id}','focus':'POST /api/focus','result':'POST /api/rounds/{round_id}/result'},'result_example':{'answers':[{'question_id':'问题编号','text':'回答正文'}],'terms':[{'name':'术语名','definition':'定义','question_ids':['问题编号']}],'quizzes':[{'prompt':'题目','options':['选项一','选项二'],'correct_index':0,'explanation':'解析','question_ids':['问题编号']}]}})
   return self.json({'error':'没有这个接口'},404)
  except (ValueError,KeyError) as error:return self.json({'error':str(error)},400)
 def do_POST(self):
  path=unquote(urlsplit(self.path).path)
  if not self.safe_origin() or not self.authenticated():return self.json({'error':'连接码或提交来源不正确'},403)
  try:
   size=int(self.headers.get('Content-Length','0'))
   if size>2_000_000:raise ValueError('提交内容过大')
   data=json.loads(self.rfile.read(size) or b'{}')
   if not isinstance(data,dict):raise ValueError('提交内容必须是对象')
   if path=='/api/focus':
    p=locate(data.get('passage_id'),data.get('sentence_id'))
    def save_focus(state):
     state['focus']={'passage_id':p['id'],'sentence_id':data.get('sentence_id'),'revision':state['focus']['revision']+1};return state['focus']
    return self.json(mutate(save_focus))
   if path=='/api/read':
    pid=data.get('passage_id');locate(pid)
    if not isinstance(data.get('read'),bool):raise ValueError('请指定是否标记已读')
    def mark_read(state):
     records=state.setdefault('read_passages',{})
     if data['read']:records[pid]=stamp()
     else:records.pop(pid,None)
     return {'passage_id':pid,'read':data['read']}
    return self.json(mutate(mark_read))
   if path=='/api/drafts':
    pid=data.get('passage_id');locate(pid);values=data.get('values')
    if not isinstance(values,dict):raise ValueError('草稿格式不正确')
    existing=read_state()
    for sid,val in values.items():
     if sid.startswith('followup:'):
      qid=sid.removeprefix('followup:')
      if not any(q['id']==qid and q['passage_id']==pid for r in existing['rounds'] for q in r['questions']):raise ValueError('追问草稿不属于当前段落')
     else:locate(pid,sid)
     if not isinstance(val,str) or len(val)>15000:raise ValueError('草稿内容超出长度限制')
    def save_draft(state):state['drafts'][pid]=values;return {'saved':True}
    return self.json(mutate(save_draft))
   if path=='/api/rounds':return self.json(mutate(lambda state:create_round(state,data)))
   match=re.fullmatch(r'/api/rounds/([^/]+)/result',path)
   if match:return self.json(mutate(lambda state:save_result(state,match.group(1),data)))
   match=re.fullmatch(r'/api/quizzes/([^/]+)/answer',path)
   if match:
    def answer(state):
     q=next((x for x in state['quizzes'] if x['id']==match.group(1)),None)
     if not q:raise ValueError('没有找到这道题')
     selected=data.get('selected')
     if isinstance(selected,bool) or not isinstance(selected,int) or not 0<=selected<len(q['options']):raise ValueError('请选择一个选项')
     q['selected']=selected;return {'correct':selected==q['correct_index'],'explanation':q['explanation']}
    return self.json(mutate(answer))
   if path=='/api/notes':
    qid=text(data.get('question_id'),'问题编号',100);value=data.get('text')
    if not isinstance(value,str) or len(value)>12000:raise ValueError('理解记录超出长度限制')
    def note(state):
     if not any(q['id']==qid for r in state['rounds'] for q in r['questions']):raise ValueError('没有找到这个问题')
     state['notes'][qid]=value;return {'saved':True}
    return self.json(mutate(note))
   return self.json({'error':'没有这个接口'},404)
  except (ValueError,KeyError,TypeError,json.JSONDecodeError) as error:return self.json({'error':str(error)},400)
if __name__=='__main__':
 read_state();ThreadingHTTPServer(('127.0.0.1',PORT),Handler).serve_forever()
