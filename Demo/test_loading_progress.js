// 检查百分比来自实际完成步骤和解压后字节；失败读取可重试，写入不重复发送。
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('node:assert/strict');
let clock=0,calls=0,fetcher;const context={window:{},performance:{now:()=>clock},setTimeout,clearTimeout,setInterval,clearInterval,AbortController,DOMException,Headers,TextDecoder,Uint8Array,TypeError,fetch:(...args)=>fetcher(...args)};
vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/reader-loading.js'),'utf8'),context);const loading=context.window.ReaderLoading;
async function run(){
 const op=loading.create('读取材料',[['material','读取原文'],['render','显示页面']]),update=op.task('material');
 clock=60000;assert.equal(op.snapshot().percent,0);assert.equal(op.snapshot().elapsed_ms,60000);
 const body=JSON.stringify({text:'中文与公式 \\frac{1}{2}'}),bytes=new TextEncoder().encode(body),events=[];
 fetcher=async()=>new Response(new ReadableStream({start(controller){controller.enqueue(bytes.slice(0,12));controller.enqueue(bytes.slice(12));controller.close()}}),{headers:{'Content-Encoding':'gzip','Content-Length':'10','X-Reader-Decoded-Length':String(bytes.length)}});
 const value=await loading.json('/api/material',{},event=>{events.push(event);update(event)});assert.equal(value.text,'中文与公式 \\frac{1}{2}');assert.equal(events.find(e=>e.state==='receiving').total,bytes.length);assert.equal(op.snapshot().percent,50);op.task('render').done();assert.equal(op.snapshot().percent,100);op.finish();
 fetcher=async()=>new Response(bytes,{headers:{'Content-Encoding':'gzip','Content-Length':'10'}});const unknown=[];await loading.json('/api/material',{},e=>unknown.push(e));assert.equal(unknown.find(e=>e.state==='receiving').total,0);
 calls=0;fetcher=async()=>{if(++calls===1)throw new TypeError('temporary network issue');return new Response('{}')};const retry=[];await loading.json('/api/material',{},e=>retry.push(e));assert.equal(calls,2);assert.equal(retry.find(e=>e.state==='retrying').attempt,2);
 calls=0;fetcher=async()=>{calls++;throw new TypeError('temporary network issue')};await assert.rejects(loading.json('/api/result',{method:'POST'},()=>{}));assert.equal(calls,1);
 calls=0;fetcher=async()=>{calls++;return new Response('{"error":"项目权限不足"}',{status:403})};await assert.rejects(loading.json('/api/material',{},()=>{}),e=>e.status===403);assert.equal(calls,1);
 calls=0;fetcher=async(url,init)=>{if(++calls===2)return new Response('{}');return new Promise((resolve,reject)=>init.signal.addEventListener('abort',()=>reject(new DOMException('timeout','AbortError'))))};const stalled=[];await loading.json('/api/material',{},e=>stalled.push(e),{timeoutMs:10});assert.equal(calls,2);assert(stalled.some(e=>e.state==='retrying'));

 for(const tracking of [null,()=>{}]){
  calls=0;fetcher=async(url,init)=>{calls++;assert.equal(init.headers.get('Accept'),'application/json');return new Response('<!doctype html><html>private content</html>',{headers:{'Content-Type':'text/html','X-Reader-Request-ID':'synthetic-request'}})};
  await assert.rejects(loading.json('/api/quizzes/q/answer',{method:'POST'},tracking),e=>e.code==='api_html_response'&&e.status===502&&e.request_id==='synthetic-request'&&!e.message.includes('private content'));assert.equal(calls,1);
 }
 calls=0;fetcher=async()=>{calls++;return new Response('<!doctype html>',{status:503,headers:{'Content-Type':'text/html'}})};await assert.rejects(loading.json('/api/material',{},()=>{}),e=>e.status===503);assert.equal(calls,2);
 fetcher=async()=>({ok:true,status:200,url:'https://example.test/_aialra_auth/sign-in',headers:new Headers({'Content-Type':'text/html'}),text:async()=>'<html>Login</html>'});await assert.rejects(loading.json('/api/quizzes/q/answer',{method:'POST'}),e=>e.code==='reader_auth_required'&&e.status===401);
 fetcher=async()=>new Response('invalid json',{headers:{'Content-Type':'application/json'}});await assert.rejects(loading.json('/api/material'),e=>e.code==='api_invalid_json'&&!e.message.includes('Unexpected token'));
 fetcher=async()=>new Response('{"answer":42}',{headers:{'Content-Type':'application/json'}});assert.equal((await loading.json('/api/material')).answer,42);
 const unmeasured=loading.create('等待后台处理');clock+=50000;assert.equal(unmeasured.snapshot().percent,null);unmeasured.error('连接已断开');assert.equal(loading.snapshot().length,0);
 console.log('LOADING_PROGRESS_OK');
}
run().catch(error=>{console.error(error);process.exitCode=1});
