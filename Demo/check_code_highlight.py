"""在真实浏览器中检查代码识别、主题、复制和后台线程；所有内容均为合成样例。"""
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit
import argparse,json,threading,tempfile

ROOT=Path(__file__).resolve().parents[1]
SAMPLES=[
 ('counter.sv','verilog','module counter(input logic clk);\n always_ff @(posedge clk) count <= count + 1;\nendmodule'),
 ('arb.v','verilog','module arb(input clk);\nreg [3:0] grant;\nalways @(posedge clk) grant <= 4\'b0001;\nendmodule'),
 ('clock.vhd','vhdl','entity clock is\n port(clk: in std_logic);\nend clock;\narchitecture rtl of clock is begin end rtl;'),
 ('constraints.sdc','tcl','set period 10\ncreate_clock -period $period [get_ports clk]'),
 ('app.py','python','def total(values):\n    return sum(value * 2 for value in values)\nprint(total([1, 2]))\n'),
 ('app.js','javascript','const total = values => values.reduce((a, b) => a + b, 0);\nconsole.log(total([1, 2]));'),
 ('app.ts','typescript','interface Item { name: string; count: number }\nconst item: Item = { name: "a", count: 2 };'),
 ('app.tsx','typescript','const Hello = ({name}: {name: string}) => <div>{name}</div>;'),
 ('main.cpp','cpp','#include <vector>\nint main() { std::vector<int> a{1, 2}; return a.size(); }'),
 ('main.c','c','#include <stdio.h>\nint main(void) { printf("hello"); return 0; }'),
 ('app.rs','rust','fn main() { let names = vec!["hello", "world"]; println!("{:?}", names); }'),
 ('app.go','go','package main\nimport "fmt"\nfunc main() { fmt.Println("hello") }'),
 ('Main.java','java','public class Main { public static void main(String[] args) { System.out.println("hello"); } }'),
 ('app.kt','kotlin','fun main() { val name: String = "hello"; println(name) }'),
 ('app.cs','csharp','using System;\nclass MainClass { static void Main() { Console.WriteLine("hello"); } }'),
 ('app.rb','ruby','def greet(name)\n  puts "Hello #{name}"\nend'),
 ('app.swift','swift','func greet(name: String) -> String { return "Hello \\(name)" }'),
 ('app.m','matlab','function y = square(x)\ny = x.^2;\nend'),
 ('objc.m','objectivec','#import <Foundation/Foundation.h>\n@interface Counter : NSObject\n@end'),
 ('solve.jl','julia','function square(x)\n    return x^2\nend'),
 ('model.r','r','values <- c(1, 2, 3)\nmean(values)'),
 ('app.lua','lua','local value = 42\nfunction greet(name) return "Hello " .. name end'),
 ('app.sql','sql','SELECT name, COUNT(*) FROM users WHERE active = 1 GROUP BY name;'),
 ('settings.json','json','{"name": "reader", "enabled": true, "count": 2}'),
 ('ci.yaml','yaml','name: build\njobs:\n  test:\n    runs-on: ubuntu-latest'),
 ('config.toml','ini','[server]\nport = 8939\nenabled = true'),
 ('index.html','xml','<!doctype html>\n<div class="item"><strong>Hello</strong></div>'),
 ('style.css','css','body { color: #eee; background: #222; margin: 0; }'),
 ('theme.scss','scss','$tone: #222;\nbody { color: $tone; a { color: blue; } }'),
 ('run.sh','bash','#!/bin/bash\nfor name in a b; do echo "$name"; done'),
 ('run.ps1','powershell','$files = Get-ChildItem -Path .\nWrite-Output $files.Count'),
 ('Dockerfile','dockerfile','FROM python:3.12\nWORKDIR /app\nCOPY . .\nRUN pip install -r requirements.txt'),
 ('Makefile','makefile','all: app\n\tcc main.c -o app\nclean:\n\trm -f app'),
 ('CMakeLists.txt','cmake','cmake_minimum_required(VERSION 3.20)\nproject(reader)\nadd_executable(reader main.cpp)'),
 ('message.proto','protobuf','syntax = "proto3";\nmessage Item { string name = 1; }'),
 ('formula.tex','latex',r'\begin{aligned}x_1+x_2&\leq 2\\x_2&\geq 0\end{aligned}'),
 ('README.md','markdown','# Reader\n\n**Hello** [guide](./guide.md)'),
 ('.gitignore','plaintext','node_modules/\n*.log\n.cache/'),
 ('.env','ini','PORT=8939\nMODE=local'),
 ('notes.txt','plaintext','这里保存阅读笔记，不应该当成代码。'),
]
HTML='''<!doctype html><html data-theme="dark"><head><meta charset="utf-8">
<link rel="stylesheet" href="/static/app.css"><script src="/vendor/reader-code-highlight.js"></script>
<script src="/static/markdown-vendor.js"></script><script src="/static/reader-markdown.js"></script>
<style>body{display:block;height:auto;padding:16px}main{max-width:900px}pre{padding:12px;overflow:auto}</style></head>
<body><main id="samples"></main></body></html>'''

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  name=urlsplit(self.path).path
  if name=='/':data=HTML.encode();kind='text/html; charset=utf-8'
  else:
   path=(ROOT/name.lstrip('/')).resolve()
   if not any(path.is_relative_to(ROOT/folder) for folder in ['static','vendor']) or not path.is_file():self.send_error(404);return
   data=path.read_bytes();kind='text/javascript; charset=utf-8' if path.suffix=='.js' else 'text/css' if path.suffix=='.css' else 'application/octet-stream'
  self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--browser');parser.add_argument('--output',type=Path);args=parser.parse_args()
 from playwright.sync_api import sync_playwright
 output=args.output or Path(tempfile.mkdtemp(prefix='reader-code-'));output.mkdir(parents=True,exist_ok=True)
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();results={}
 try:
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True,**({'executable_path':args.browser} if args.browser else {}));page=browser.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
   page.goto('http://127.0.0.1:'+str(server.server_port));assert not page.evaluate('ReaderCode.stats.workerRequests')
   results['filenames']=page.evaluate('''async samples=>{const rows=[];for(const [path,expected,text]of samples){const pre=ReaderCode.block(text,{path});document.querySelector('main').append(pre);const code=pre.firstChild,result=await code.codeReady;if(code.textContent!==text)throw Error('代码改变 '+path);if(result.language!==expected)throw Error(path+': '+result.language+' != '+expected);rows.push({path,...result,html:undefined,tokens:code.querySelectorAll('span').length})}return rows}''',SAMPLES)
   results['automatic']=page.evaluate('''async samples=>{const rows=[];for(const [expected,text,options]of samples){const result=await ReaderCode.analyze(text,options);if(result.language!==expected)throw Error(expected+' -> '+result.language);rows.push({...result,html:undefined})}return rows}''',[
    ['python',SAMPLES[4][2],{}],['verilog',SAMPLES[0][2],{}],['vhdl',SAMPLES[2][2],{}],
    ['python','#!/usr/bin/env python3\nprint("hello")',{}],['plaintext','这是中文笔记，需要保持正常文字。',{}],
    ['javascript',SAMPLES[5][2],{'language':'js'}],['verilog',SAMPLES[0][2],{'language':'systemverilog'}],
    ['python',SAMPLES[4][2],{'language':'en','path':'hello.py'}],['plaintext','def example(): pass',{'language':'text'}]])
   results['markdown']=page.evaluate('''async()=>{const node=document.createElement('div');node.dataset.projectFileBase='README.md';document.querySelector('main').prepend(node);ReaderMarkdown.render(node,'```python\\ndef square(x):\\n    return x * x\\n```\\n\\n```\\ndef total(values):\\n    return sum(values)\\n```');await Promise.all([...node.querySelectorAll('pre>code')].map(c=>c.codeReady));const codes=[...node.querySelectorAll('pre>code')];if(codes.some(c=>c.dataset.codeLanguage!=='python'||!c.querySelector('span')))throw Error('Markdown代码没有识别');return codes.map(c=>({language:c.dataset.codeLanguage,text:c.textContent}))}''')
   results['concurrent']=page.evaluate('''async()=>{const text='const key = "<script>window.BAD = true</script>";';const before={...ReaderCode.stats};const nodes=Array.from({length:20},()=>{const pre=ReaderCode.block(text,{path:'unique.js'});document.querySelector('main').append(pre);return pre.firstChild});await Promise.all(nodes.map(n=>n.codeReady));if(nodes.some(n=>n.textContent!==text)||window.BAD)throw Error('代码被修改或执行');return {before,after:{...ReaderCode.stats}}}''')
   assert results['concurrent']['after']['workerRequests']-results['concurrent']['before']['workerRequests']==1
   results['warm_cache']=page.evaluate("""async()=>{const before={...ReaderCode.stats};await ReaderCode.analyze('def total(values):\\n    return sum(value * 2 for value in values)\\nprint(total([1, 2]))\\n',{path:'app.py'});return {before,after:{...ReaderCode.stats}}}""")
   assert results['warm_cache']['after']['cacheHits']==results['warm_cache']['before']['cacheHits']+1
   results['selection']=page.evaluate('''async()=>{const pre=ReaderCode.block('const selection = "preserve";',{path:'selection.js'});document.body.append(pre);const code=pre.firstChild,range=document.createRange();range.setStart(code.firstChild,6);range.setEnd(code.firstChild,15);getSelection().removeAllRanges();getSelection().addRange(range);await code.codeReady;const selected=getSelection().toString();if(selected!=='selection'||code.querySelector('span'))throw Error('着色干扰选区');getSelection().removeAllRanges();document.dispatchEvent(new Event('selectionchange'));if(!code.querySelector('span'))throw Error('取消选区后没有着色');return {selected,text:code.textContent}}''')
   results['large']=page.evaluate('''async()=>{const text='const value = "reader"; // local\\n'.repeat(6500),start=performance.now();let ticks=0;const timer=setInterval(()=>ticks++,10);const result=await ReaderCode.analyze(text,{path:'large.js'});clearInterval(timer);return {...result,html:undefined,length:text.length,elapsed:performance.now()-start,ticks}}''')
   results['viewports']=[]
   for width,height in [(1920,1080),(1280,900),(390,844),(844,390)]:
    page.set_viewport_size({'width':width,'height':height})
    for theme in ['light','dark']:
     page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme);page.wait_for_timeout(100)
     check=page.evaluate('''()=>{const rgb=s=>s.match(/[\\d.]+/g).map(Number),lum=v=>v.slice(0,3).map(x=>(x/=255)<=.04045?x/12.92:((x+.055)/1.055)**2.4).reduce((a,x,i)=>a+x*[.2126,.7152,.0722][i],0),background=n=>{for(;n;n=n.parentElement){const value=rgb(getComputedStyle(n).backgroundColor);if(value.length===3||value[3]>.99)return value}return [255,255,255]};return [...document.querySelectorAll('pre .hljs>span')].map(n=>{const a=lum(rgb(getComputedStyle(n).color)),b=lum(background(n));return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)})}''')
     assert min(check)>=4.5,(width,theme,min(check));results['viewports'].append({'width':width,'height':height,'theme':theme,'minimum_contrast':min(check)})
     page.screenshot(path=str(output/f'code-{theme}-{width}.png'))
   results['errors']=errors;assert not errors;results['stats']=page.evaluate('ReaderCode.stats');browser.close()
 finally:server.shutdown();server.server_close();thread.join()
 (output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),'utf8');print(json.dumps({'passed':True,'languages':results['filenames'][0]['languages'],'filename_cases':len(SAMPLES),'automatic_cases':len(results['automatic']),'viewports':len(results['viewports']),'large_file':results['large'],'stats':results['stats']}))

if __name__=='__main__':main()
