"""用真实 Mermaid 与放大查看器核对文字、箭头和缩放，不读取项目数据。"""
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import argparse,json,mimetypes,tempfile,threading

ROOT=Path(__file__).resolve().parents[1]
HTML='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/vendor/reader.css"><link rel="stylesheet" href="/static/app.css">
<script defer src="/static/markdown-vendor.js"></script><script defer src="/static/reader-markdown.js"></script>
<script defer src="/static/reader-ui.js"></script><script defer src="/static/reader-media-view.js"></script>
</head><body class="utility-page"><main data-pane="explanation"><div id="sample"></div></main></body></html>'''
SAMPLES=[
 'flowchart TD\nA[同一组输入] --> B[检查输入条件]\nB --> C[方法甲]\nB --> D[方法乙]\nC --> E[按同一规则计分]\nD --> E\nE --> F[比较结果]',
 'flowchart LR\nA["收到请求<br/>保存请求内容"] --> B{条件是否满足}\nB -->|满足| C[完成操作]\nB -->|等待| D[保留当前请求]',
 'sequenceDiagram\n参与者甲->>参与者乙: 提交请求\n参与者乙-->>参与者甲: 返回处理结果',
 'stateDiagram-v2\n[*] --> Waiting\nWaiting --> Working: 收到请求\nWorking --> Waiting: 完成处理',
]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  name=self.path.split('?')[0]
  if name=='/':data=HTML.encode();kind='text/html'
  else:
   path=(ROOT/name.lstrip('/')).resolve()
   if not any(path.is_relative_to(ROOT/folder)for folder in('static','vendor'))or not path.is_file():self.send_error(404);return
   data=path.read_bytes();kind=mimetypes.guess_type(path.name)[0]or'application/octet-stream'
  self.send_response(200);self.send_header('Content-Type',kind+'; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

SNAPSHOT="""svg=>[...svg.querySelectorAll('text')].map(n=>{const s=getComputedStyle(n),b=n.getBBox();return{text:n.textContent,anchor:s.textAnchor,font:s.fontFamily,size:s.fontSize,weight:s.fontWeight,bbox:[b.x,b.y,b.width,b.height]}})"""
REFERENCES="""svg=>{const ids=new Set([svg,...svg.querySelectorAll('[id]')].map(n=>n.id));const text=[...svg.querySelectorAll('*')].flatMap(n=>[...n.attributes].map(a=>a.value)).concat([...svg.querySelectorAll('style')].map(n=>n.textContent)).join(' ');return [...text.matchAll(/url\\(\\s*['"]?#([\\w:.-]+)['"]?\\s*\\)/g)].filter(m=>!ids.has(m[1])).map(m=>m[1])}"""

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--browser');parser.add_argument('--output',type=Path);args=parser.parse_args()
 from playwright.sync_api import sync_playwright
 output=args.output or Path(tempfile.mkdtemp(prefix='reader-media-diagrams-'));output.mkdir(parents=True,exist_ok=True)
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();rows=[]
 try:
  with sync_playwright()as pw:
   browser=pw.chromium.launch(headless=True,**({'executable_path':args.browser}if args.browser else{}))
   try:
    for width,height in[(1920,1080),(1280,900),(390,844),(844,390)]:
     page=browser.new_page(viewport={'width':width,'height':height});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
     try:
      page.goto('http://127.0.0.1:'+str(server.server_port));page.wait_for_function('window.ReaderMediaView&&window.ReaderMarkdown')
      for theme in['dark','light']:
       page.evaluate('(t)=>document.documentElement.dataset.theme=t',theme)
       for index,sample in enumerate(SAMPLES):
        page.evaluate("s=>ReaderMarkdown.render(document.getElementById('sample'),'```mermaid\\n'+s+'\\n```')",sample);source=page.locator('#sample .reader-mermaid');page.wait_for_selector('#sample .reader-mermaid[data-diagram-rendered=true]')
        svg=source.locator('svg')
        if index==0:svg.evaluate('''n=>{const ns='http://www.w3.org/2000/svg',title=document.createElementNS(ns,'title'),defs=document.createElementNS(ns,'defs'),marker=document.createElementNS(ns,'marker'),rect=document.createElementNS(ns,'rect');title.id='caption.extra';title.textContent='合成流程图';n.prepend(title);n.setAttribute('aria-labelledby',title.id);marker.id='fff';defs.append(marker);rect.setAttribute('class','copy-color-test');rect.setAttribute('fill','#fff');rect.setAttribute('display','none');defs.append(rect);n.append(defs);n.querySelector('style').textContent+='@media (min-width:1px){#'+n.id+' .copy-color-test{fill:#fff;marker-end:url("#fff")}}'}''')
        expected=svg.evaluate(SNAPSHOT);original=svg.evaluate('(n)=>n.outerHTML');assert expected
        for repeat in range(2):
         source.click();page.wait_for_selector('#media-view-dialog[open]');copy=page.locator('.media-view-stage svg')
         for action in[None,'放大','放大','缩小','恢复适配']:
          if action:page.get_by_role('button',name=action,exact=True).click()
          actual=copy.evaluate(SNAPSHOT);assert len(actual)==len(expected)
          for a,b in zip(expected,actual):
           assert all(a[k]==b[k]for k in('text','anchor','font','size','weight')),(width,theme,index,a,b)
           # 浏览器缩放会对字形尺寸取整，核对文字中心与基线位置。
           assert abs((a['bbox'][0]+a['bbox'][2]/2)-(b['bbox'][0]+b['bbox'][2]/2))<2,(width,theme,index,action,a,b)
           assert abs(a['bbox'][1]-b['bbox'][1])<2,(width,theme,index,action,a,b)
          assert not copy.evaluate(REFERENCES)
          if index==0:
           assert copy.locator('.copy-color-test').get_attribute('fill')=='#fff'
           assert copy.evaluate("n=>getComputedStyle(n.querySelector('.copy-color-test')).fill")=='rgb(255, 255, 255)'
           assert copy.get_attribute('aria-labelledby')==copy.locator('title').first.get_attribute('id')
         assert svg.evaluate('(n)=>n.outerHTML')==original
         if index==0 and repeat==0:
          page.mouse.move(8,8);page.wait_for_timeout(400);page.screenshot(path=str(output/('diagram-'+theme+'-'+str(width)+'.png')))
         page.get_by_role('button',name='关闭放大查看',exact=True).click()
        rows.append({'width':width,'height':height,'theme':theme,'sample':index,'text_labels':len(expected),'open_cycles':2,'zoom_checks':10})
      assert not errors,errors
     finally:page.close()
   finally:browser.close()
 finally:server.shutdown();server.server_close();thread.join()
 (output/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),'utf8');print(json.dumps({'passed':True,'cases':len(rows),'open_cycles':sum(r['open_cycles']for r in rows),'zoom_checks':sum(r['zoom_checks']for r in rows)}))

if __name__=='__main__':main()
