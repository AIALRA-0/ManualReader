// 远离视口的裁图不抢先下载；进入视口才请求，移除的元素不继续保留。
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../static/reader-source-flow.js'),'utf8');
const helper=source.slice(source.indexOf(' const deferredCrops='),source.indexOf(' function crop(group)'));
let observer;const scheduled=[];
class Observer {
 constructor(callback){this.callback=callback;this.targets=new Set();observer=this}
 observe(node){this.targets.add(node)}
 unobserve(node){this.targets.delete(node)}
}
const ctx={Map,IntersectionObserver:Observer,queueMicrotask:f=>scheduled.push(f)};
vm.createContext(ctx);vm.runInContext(helper,ctx);
const first={isConnected:true},second={isConnected:true};let calls=0;
ctx.deferCrop(first,()=>calls++);ctx.deferCrop(second,()=>calls+=10);
assert.equal(calls,0);
observer.callback([{target:first,isIntersecting:false}]);assert.equal(calls,0);
observer.callback([{target:first,isIntersecting:true}]);assert.equal(calls,1);
observer.callback([{target:first,isIntersecting:true}]);assert.equal(calls,1);
second.isConnected=false;for(const task of scheduled)task();
assert.equal(observer.targets.size,0);assert.equal(calls,1);
const fallback={Map,queueMicrotask(){}};vm.createContext(fallback);vm.runInContext(helper,fallback);
fallback.deferCrop({},()=>calls++);assert.equal(calls,2);
console.log('SOURCE_IMAGE_PRIORITY_OK');
