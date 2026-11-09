// 提问折叠、输入与练习不应被媒体放大的捕获监听器截走。
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('node:assert/strict'),listeners={};
let opens=0;const context={window:{},document:{addEventListener:(type,handler)=>listeners[type]=handler},ReaderUI:{make(){opens++;throw Error('MEDIA_OPEN')}},getSelection:()=>({toString:()=>''})};vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/reader-media-view.js'),'utf8'),context);
const table={closest:s=>s==='[data-pane],.practice-dialog'?{}:null,matches:()=>false};
function click(control){let stopped=false;const target={closest:s=>control&&s.split(',').includes(control)?{}:s==='img,table,.reader-math,.reader-mermaid,.reader-source-crop'?table:null};const event={target,clientX:0,clientY:0,preventDefault(){stopped=true},stopImmediatePropagation(){stopped=true}};listeners.click(event);return stopped}
for(const control of ['summary','.inline-question','.question-composer','.answer-followup-group','.followup','.bp','.reader-inline-quiz','textarea','input','button','a','[contenteditable]','[role=button]'])assert.equal(click(control),false,control+' was intercepted');assert.equal(opens,0);
assert.throws(()=>click(null),/MEDIA_OPEN/);assert.equal(opens,1);console.log('MEDIA_INTERACTIONS_OK');
