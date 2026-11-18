// 四种模式加载和重复装配时，附件数据不应向正文添加导航或交稿控件。
const assert=require('node:assert/strict'),fs=require('fs'),path=require('path'),vm=require('vm');
const filename=process.argv[2]||path.join(__dirname,'../next/src/manualreader/stages/finish/static/reader-finish.js');
const script=fs.readFileSync(filename,'utf8');
(async()=>{
 for(const mode of ['NORMAL','INTRO','FINALE','ANSWER']){
  for(const id of ['postprocess-preview','next-chapter-'+ 'a'.repeat(20)]){
   const calls=[],rendered=[],start={append:node=>rendered.push(node)};
   const root={dataset:{},querySelector:selector=>selector.includes('--framework-start-1')?start:null,
    querySelectorAll:()=>[],append:node=>rendered.push(node)};
   const manifest={mode,study:{terms:32,exercises:8},delivery_receipt_sha256:'sealed',resources:{},chapter_recaps:[]};
   const context={window:{},ReaderUI:{make:(tag,cls,text)=>{
    rendered.push({tag,cls,text});return {append(){},children:[]};}},
    requestAnimationFrame:callback=>callback()};
   vm.createContext(context);vm.runInContext(script,context);
   const ctx={api:async route=>{calls.push(route);return route.startsWith('/api/postprocess-preview')?manifest:{delivery:null};},
    openStudy(){throw Error('正文不应新建术语或练习入口');},inlineQuestions(){}};
   for(let repeat=0;repeat<2;repeat++){
    await context.window.ReaderPostprocessPreview.attach(root,{id:'synthetic-material'},id,ctx);
    await Promise.resolve();
   }
   assert.equal(root.dataset.nextPresentationReady,'true');
   assert.deepEqual(rendered,[],mode+' added a chapter control');
   assert.ok(calls.every(route=>route.startsWith('/api/postprocess-preview')),mode+' requested delivery controls');
  }
 }
 console.log('CHAPTER_CONTROLS_OK · four modes, both ID formats, repeated attachment');
})().catch(error=>{console.error(error);process.exitCode=1;});
