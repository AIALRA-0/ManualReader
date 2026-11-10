"""用真实样式检查代码文字的对比度；只打开临时合成页面，不读取项目数据。"""
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import argparse,json,threading,tempfile

ROOT=Path(__file__).resolve().parents[1]
HTML='''<!doctype html><html data-theme="dark"><head><meta charset="utf-8">
<link rel="stylesheet" href="/vendor/reader.css"><link rel="stylesheet" href="/static/app.css">
<link rel="stylesheet" href="/static/reader-project-files.css"></head><body class="utility-page"><main>
<div class="original-block"><pre><code>reg [3:0] grant;</code></pre></div>
<div class="chinese"><pre>always @(posedge clk) grant &lt;= request;</pre></div>
<pre id="source-code"><span class="code-line marked">grant = request;</span></pre>
<div class="markdown-body"><pre><code class="hljs"><span class="hljs-keyword">if</span> (ready)
<span class="hljs-comment">// 有请求时更新结果</span>
<span class="hljs-string">"ready"</span> <span class="hljs-number">42</span></code></pre>
<code>grant</code><pre class="reader-selection-source">```verilog
reg [3:0] grant;
```</pre></div>
<div class="project-file-body"><pre class="project-file-code">module arb; endmodule</pre></div>
<textarea class="sequence-code-editor">grant = request;</textarea>
</main></body></html>'''
FILES={'/vendor/reader.css':ROOT/'vendor/reader.css','/static/app.css':ROOT/'static/app.css','/static/reader-project-files.css':ROOT/'static/reader-project-files.css'}

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  name=self.path.split('?')[0]
  if name!='/'and name not in FILES:self.send_error(404);return
  data=HTML.encode()if name=='/'else FILES[name].read_bytes()
  self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8'if name=='/'else'text/css');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

CHECK="""()=>{const parse=s=>s.match(/[\\d.]+/g).map(Number),background=n=>{for(;n;n=n.parentElement){const v=parse(getComputedStyle(n).backgroundColor);if(v.length===3||v[3]>.99)return v}return[255,255,255]},luminance=v=>v.slice(0,3).map(x=>(x/=255)<=.04045?x/12.92:((x+.055)/1.055)**2.4).reduce((a,x,i)=>a+x*[.2126,.7152,.0722][i],0);return [...document.querySelectorAll('pre,pre code,pre span,.markdown-body>code,textarea')].map(n=>{const a=luminance(parse(getComputedStyle(n).color)),b=luminance(background(n));return {element:n.id||n.className||n.tagName,contrast:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)}})}"""

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--browser',help='本机 Chromium 或 Edge 的可执行文件');parser.add_argument('--output',type=Path);args=parser.parse_args()
 from playwright.sync_api import sync_playwright
 output=args.output or Path(tempfile.mkdtemp(prefix='reader-code-theme-'));output.mkdir(parents=True,exist_ok=True)
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start();results=[]
 try:
  with sync_playwright()as pw:
   browser=pw.chromium.launch(headless=True,**({'executable_path':args.browser}if args.browser else{}))
   try:
    page=browser.new_page()
    for width,height in[(1920,1080),(1280,900),(390,844),(844,390)]:
     page.set_viewport_size({'width':width,'height':height});page.goto('http://127.0.0.1:'+str(server.server_port))
     for theme in['dark','light','dark']:
      page.evaluate('(t)=>document.documentElement.dataset.theme=t',theme);page.wait_for_timeout(400);checks=page.evaluate(CHECK)
      for item in checks:assert item['contrast']>=4.5,(width,theme,item)
      results.append({'width':width,'height':height,'theme':theme,'checks':checks});page.screenshot(path=str(output/('code-'+theme+'-'+str(width)+'.png')))
   finally:browser.close()
 finally:server.shutdown();server.server_close();worker.join()
 (output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),'utf8');print(json.dumps({'passed':True,'cases':len(results),'contrast_checks':sum(len(r['checks'])for r in results)}))

if __name__=='__main__':main()
