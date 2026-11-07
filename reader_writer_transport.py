"""在本机限定写作请求可用的工具，只把凭证转发到原 Codex 服务。"""
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json,threading,time
import requests,zstandard

UPSTREAM='https://chatgpt.com/backend-api/codex'
POLICY={'allowed_tool_types':[],'forbidden':['exec','function','custom','mcp','tool_search','programmatic_tool_calling'],'model':'gpt-6.1-terra','effort':'high','service_tier':'default','research_stage':'主调度在资料准备时搜索，写作者只接收已取得的材料与来源'}
def restrict_request(value):
 value=dict(value)
 if value.get('model')!=POLICY['model']or value.get('reasoning',{}).get('effort')!='high':raise ValueError('本次只接受约定的 Terra high')
 previous=value.get('tools')or[]
 value['tools']=[]
 value['tool_choice']='none'
 value['parallel_tool_calls']=False
 return value,{'previous_tool_types':[t.get('type')for t in previous],'sent_tool_types':[],'tool_choice':value['tool_choice'],'generic_execution_removed':True,'research_stage':POLICY['research_stage']}

class Gate:
 def __init__(self,log,port=8985):self.log=Path(log);self.port=port;self.lock=threading.Lock();self.server=None
 def record(self,value):
  with self.lock:
   self.log.parent.mkdir(parents=True,exist_ok=True)
   with self.log.open('a',encoding='utf8')as f:f.write(json.dumps(value,ensure_ascii=False)+'\n')
 def start(self):
  outer=self
  class Handler(BaseHTTPRequestHandler):
   protocol_version='HTTP/1.1'
   def log_message(self,*args):pass
   def do_GET(self):
    self.send_response(426);self.send_header('Content-Length','0');self.send_header('Connection','close');self.end_headers()
   def do_POST(self):
    if self.path!='/responses':self.send_error(404);return
    began=time.perf_counter()
    try:
     raw=self.rfile.read(int(self.headers.get('Content-Length',0)))
     if self.headers.get('Content-Encoding')=='zstd':raw=zstandard.ZstdDecompressor().decompress(raw,max_output_size=32*1024*1024)
     value,audit=restrict_request(json.loads(raw));headers={k:v for k,v in self.headers.items()if k.lower()not in('host','content-length','content-encoding','connection','upgrade','accept-encoding')}
     headers['Content-Type']='application/json';headers['Accept-Encoding']='identity'
     response=requests.post(UPSTREAM+'/responses',data=json.dumps(value,ensure_ascii=False).encode(),headers=headers,stream=True,timeout=(20,None),allow_redirects=False)
     outer.record({'at':time.time(),'status':response.status_code,'preparation_and_headers_seconds':round(time.perf_counter()-began,3),'body_bytes':len(raw),**audit})
     self.send_response(response.status_code);self.send_header('Content-Type',response.headers.get('Content-Type','application/json'));self.send_header('Connection','close');self.end_headers()
     for chunk in response.iter_content(chunk_size=4096):
      if chunk:self.wfile.write(chunk);self.wfile.flush()
     self.close_connection=True
    except Exception as e:
     outer.record({'at':time.time(),'error_type':type(e).__name__,'credentials_logged':False})
     try:self.send_error(502,'writer transport unavailable')
     except OSError:pass
  self.server=ThreadingHTTPServer(('127.0.0.1',self.port),Handler);self.port=self.server.server_address[1];threading.Thread(target=self.server.serve_forever,daemon=True).start();return self
 def stop(self):
  if self.server:self.server.shutdown();self.server.server_close()
