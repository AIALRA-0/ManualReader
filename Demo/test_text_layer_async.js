// 文字层延迟或旧请求返回时，原图联动仍立即完成，旧页不能覆盖新页。
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const draw=source.slice(source.indexOf('async function drawPage('),source.indexOf(' function sourceDisplay('));
function node(){return {dataset:{},style:{},textContent:'',replaceChildren(...items){this.items=items},append(){},querySelectorAll(){return []}}}
const nodes=new Map(['pdf-img','page-info','pdf-highlights','pdf-status','pdf-text','pdf-viewport'].map(x=>[x,node()]));
const pending=new Map(),events=[];
const ctx={console,Map,Error,Promise,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail}},
 material:{material_revision:1,pages:{a:{width:100,height:100,number:1,image:'a.png',text_layer:'a.json'},b:{width:100,height:100,number:2,image:'b.png',text_layer:'b.json'}}},docId:'demo',drawSeq:0,currentPage:null,textLayerKey:'',currentScale:1,appliedTextScale:'',textLayerCache:new Map(),pageFitLocked:false,preview:null,autoFit:true,
 $:id=>nodes.get(id),sourceFlow:{prepare(){},activate(){}},fileURL:x=>x,displaySentence:()=>null,displayPassage:()=>({precision:'sentence'}),ReaderUI:{highlightLayer:()=>null},
 fetch:url=>new Promise(resolve=>pending.set(url,resolve)),el:()=>node(),document:{createDocumentFragment:()=>node(),dispatchEvent:e=>events.push(e)},status(){},setScale(){},fitPageWidth(){},fitCurrent(){},requestAnimationFrame:f=>f()};
vm.createContext(ctx);vm.runInContext(draw,ctx);
(async()=>{
 await ctx.drawPage('a',[]);assert.equal(ctx.currentPage,'a');assert.equal(events.length,0);
 await ctx.drawPage('b',[]);assert.equal(ctx.currentPage,'b');
 pending.get('/page/a.json')({ok:true,json:async()=>({lines:[]})});
 await new Promise(setImmediate);assert.equal(events.length,0);
 pending.get('/page/b.json')({ok:true,json:async()=>({lines:[{bbox:[0,0,10,10],chars:[{text:'B',bbox:[0,0,10,10]}]}]})});
 await new Promise(setImmediate);
 assert.equal(events.length,1);assert.equal(events[0].detail.page_id,'b');assert.equal(ctx.textLayerKey,'demob');
 assert.equal(nodes.get('pdf-text').items.length,1);
 console.log('TEXT_LAYER_ASYNC_OK');
})().catch(e=>{console.error(e);process.exitCode=1});
