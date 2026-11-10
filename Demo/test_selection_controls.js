// 保留正文选区时，点击折叠栏和输入控件仍应执行它们自己的动作。
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../static/reader-markdown.js'),'utf8');
const line=source.split('\n').find(row=>row.startsWith(' function preserveSelectionClick('));
assert(line);assert(source.includes("root.addEventListener('click',preserveSelectionClick,true)"));
let collapsed=false;
const handle=vm.runInNewContext(line+';preserveSelectionClick',{getSelection:()=>({isCollapsed:collapsed})});
function click(control,markdown=true){let prevented=false;const target={closest:selector=>selector==='.markdown-body'?(markdown?{}:null):selector.split(',').includes(control)?{}:null};handle({target,preventDefault(){prevented=true}});return prevented}
for(const control of ['summary','a','button','input','textarea','select','[role=button]','[contenteditable]','.inline-question','.reader-group-composer','.question-composer','.followup','.bp','.reader-inline-quiz'])assert.equal(click(control),false,control);
assert.equal(click(null),true);assert.equal(click(null,false),false);
collapsed=true;assert.equal(click(null),false);
console.log('SELECTION_CONTROLS_OK');
