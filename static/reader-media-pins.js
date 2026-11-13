/* 讲解按章节编号，问答按当前回答编号。两个置顶区各自滚动。 */
function createReaderMediaPins(pane){
 const {make:el,decorate}=ReaderUI;
 const displayMath='.reader-math[data-math-display="block"],.katex-display,.reader-math-display,math[display="block"]';
 const mathNodes='.reader-math,.katex,math,.katex-display,.reader-math-display';
 const candidates='picture,img,table,pre,.reader-mermaid,.reader-learning-route,.reader-source-crop,.reader-page-preview,svg,video,audio,canvas,meter,progress,hr,'+mathNodes;
 const atomic='picture,.reader-mermaid,.reader-source-crop,.reader-page-preview,svg,video,audio,canvas,pre,'+mathNodes;
 const controls=new WeakMap();
 const entries=new WeakMap();
 const labels=[
  ['table','表'],['pre','代码'],[mathNodes,'公式'],
  ['video','视频'],['audio','音频'],['canvas,meter,progress','图表'],['hr','分隔线']
 ];
 const layouts=[['single','file','只显示一份'],['side','columns','左右并排'],['stack','stack','上下排列']];
 const pinned=new Map();
 const inAnswer=pane==='interaction';
 const domId='reader-media-'+pane+'-';
 const sizeKey=inAnswer?'reader-answer-media-dock-height':'reader-media-dock-height',minSize=18,maxSize=76;
 let dock,tabs,tools,stage,handle,notice,views,root,project,visible=[],selected,layout='single',chosenLayout=false,items=[];
 let size=48,drag=null;

 function button(icon,label,run){
  const b=decorate(el('button','icon-btn'),icon,label);
  b.type='button';
  b.addEventListener('click',event=>{event.stopPropagation();run(event)});
  return b;
 }
 function init(body){
  dock=el('section','reader-media-dock');
  dock.setAttribute('aria-label','置顶素材');
  dock.hidden=true;
  tabs=el('div','reader-media-tabs');
  tabs.setAttribute('role','tablist');
  tabs.setAttribute('aria-label','置顶素材标签');
  tools=el('div','reader-media-layouts');
  for(const [mode,icon,label] of layouts){
   const b=button(icon,label,()=>{chosenLayout=true;setLayout(mode)});
   b.dataset.mediaLayout=mode;
   tools.append(b);
  }
  const head=el('div','reader-media-dock-head');
  head.append(tabs,tools);
  stage=el('div','reader-media-dock-stage');
  views=[el('div','reader-media-preview markdown-body'),el('div','reader-media-preview markdown-body')];
  views.forEach((view,index)=>{view.id=domId+'preview-'+index;view.setAttribute('role','tabpanel');view.tabIndex=0});
  stage.append(...views);
  handle=el('button','reader-media-resize');
  handle.type='button';
  handle.setAttribute('role','separator');
  handle.setAttribute('aria-label','拖动调整置顶区高度');
  handle.setAttribute('aria-orientation','horizontal');
  handle.setAttribute('aria-controls',pane);
  handle.setAttribute('aria-valuemin',minSize);
  handle.setAttribute('aria-valuemax',maxSize);
  try{
   const saved=Number(localStorage.getItem(sizeKey));
   if(saved>=minSize&&saved<=maxSize)size=saved;
  }catch{}
  resize(size);
  handle.addEventListener('pointerdown',event=>{
   if(event.button!==0||drag)return;
   event.preventDefault();
   drag={id:event.pointerId,y:event.clientY,height:body.parentElement.clientHeight,size};
   handle.setPointerCapture(event.pointerId);
  });
  handle.addEventListener('pointermove',event=>{
   if(drag?.id===event.pointerId&&drag.height)
    resize(drag.size+100*(event.clientY-drag.y)/drag.height);
  });
  for(const event of ['pointerup','pointercancel','lostpointercapture'])handle.addEventListener(event,e=>{
   if(drag?.id===e.pointerId){drag=null;saveSize()}
  });
  handle.addEventListener('keydown',event=>{
   const next={ArrowUp:size-4,ArrowDown:size+4,Home:minSize,End:maxSize}[event.key];
   if(next===undefined)return;
   event.preventDefault();
   resize(next);
   saveSize();
  });
  dock.append(head,stage,handle);
  notice=el('span','sr-only');
  notice.setAttribute('role','status');
  body.before(dock,notice);
 }
 function resize(percent){
  size=Math.max(minSize,Math.min(maxSize,Math.round(percent)));
  dock.style.setProperty('--reader-media-dock-height',size+'%');
  handle.setAttribute('aria-valuenow',size);
  handle.setAttribute('aria-valuetext','置顶区占'+(inAnswer?'问答栏':'讲解栏')+' '+size+'%');
 }
 function saveSize(){
  try{localStorage.setItem(sizeKey,String(size))}catch{}
 }
 function setLayout(mode){
  layout=mode;
  const other=visible.find(key=>key!==selected)??[...pinned.keys()].find(key=>key!==selected);
  visible=[selected,...(mode!=='single'&&other?[other]:[])].filter(Boolean);
  paint();
 }
 function choose(key){
  if(layout==='single')visible=[key];
  else if(!visible.includes(key)){
   if(visible.length<2)visible.push(key);
   else visible[visible.indexOf(selected)===0?0:1]=key;
  }
  selected=key;
 }
 function change(item){
  if(pinned.has(item.key)){
   pinned.delete(item.key);
   visible=visible.filter(key=>key!==item.key);
   if(selected===item.key)selected=visible[0]??pinned.keys().next().value;
   if(selected&&!visible.includes(selected))visible.push(selected);
   if(layout!=='single'&&visible.length===1){
    const other=[...pinned.keys()].find(key=>key!==selected);
    if(other)visible.push(other);
   }
   paint();
   notice.textContent='已取消置顶'+item.label;
   return;
  }
  pinned.set(item.key,{key:item.key,label:item.label,section:item.section,name:item.name,
   snapshot:ReaderMediaView.snapshot(item.node)});
  if(pinned.size===2&&!chosenLayout)layout=root.clientWidth>=530?'side':'stack';
  choose(item.key);
  paint();
  revealTab();
  notice.textContent='已置顶'+item.label;
 }
 function mediaName(node,article,group){
  const explicit=node.closest('figure')?.querySelector(':scope > figcaption')?.textContent
   ||node.querySelector(':scope > caption,:scope > title')?.textContent||node.getAttribute('title')
   ||node.getAttribute('aria-label')||node.getAttribute('alt')||node.querySelector('img')?.alt;
  let detail='';
  if(group==='表')detail=[...node.querySelectorAll('thead th, tr:first-child th')].slice(0,3).map(cell=>cell.textContent.trim()).join(' / ');
  if(group==='公式'){
   const raw=node.querySelector('annotation')?.textContent||node.dataset.mathSource||node.dataset.mathRaw||node.textContent;
   detail=raw.split('=')[0].replace(/\\(?:left|right|mathrm|text|quad)\b|[\\{}$]/g,'').trim();
  }
  if(group==='代码'){
   const code=node.querySelector('code');
   const language=[...(code?.classList||[])].find(cls=>cls.startsWith('language-'))?.slice(9);
   const text=(code||node).textContent.trim();
   detail=[language,text.match(/\b(?:class|def|function|module|entity)\s+([\w$]+)/)?.[1]||text.split('\n')[0].slice(0,36)].filter(Boolean).join(' ');
  }
  if(node.matches('.reader-mermaid')){
   const source=node.dataset.markdownRaw||'';
   detail=source.match(/^\s*title\s*:?\s+([^\n]+)/m)?.[1]
    ||[...source.matchAll(/\[\s*"?([^\]"\n]+)"?\s*\]/g)].slice(0,2).map(match=>match[1].trim()).join(' → ');
  }
  if(node.matches('.reader-learning-route'))detail='阅读流程';
  if(node.matches('meter,progress'))detail=(node.matches('meter')?'刻度':'进度')+' '+node.value+' / '+node.max;
  if(node.matches('video,audio')){
   const src=node.getAttribute('src')||node.querySelector('source')?.getAttribute('src');
   if(src&&!src.startsWith('data:')){
    try{
     detail=decodeURIComponent(new URL(src,location.href).pathname.split('/').at(-1));
     if(!detail.includes('.'))detail='';
    }catch{}
   }
  }
  if(group==='分隔线')detail='段落分隔';
  const heading=article.querySelector('h1,h2,h3,h4,h5,h6')?.textContent;
  const fallback={图:'本节插图',表:'本节数据',公式:'本节算式',代码:'本节代码',视频:'本节视频',音频:'本节音频',图表:'本节图表',分隔线:'段落分隔'}[group];
  const tidy=value=>value?.trim().replace(/^(?:图表|图|表|公式|代码|视频|音频|分隔线)\s*\d+(?:[.-]\d+)*(?=\s|[:：、·-]|$)[\s:：、·-]*/,'').replace(/\s+/g,' ');
  const title=tidy(explicit)||tidy(detail)||tidy(heading)||fallback;
  return title.length>80?title.slice(0,79)+'…':title;
 }
 function revealTab(){
  const target=tabs.querySelector('[data-active=true]');
  if(!target)return;
  if(target.offsetLeft<tabs.scrollLeft||target.offsetLeft+target.offsetWidth>tabs.scrollLeft+tabs.clientWidth)
   tabs.scrollLeft=target.offsetLeft;
 }
 function tabTitle(item){
  return [...pinned.values()].some(other=>other.key!==item.key&&other.label===item.label)&&item.section
   ? item.label+' · '+item.section : item.label;
 }
 function paint(){
  root.parentElement.classList.toggle('has-media-pins',pinned.size>0);
  dock.hidden=!pinned.size;
  stage.dataset.layout=visible.length===2?layout:'single';
  tools.hidden=pinned.size<2;
  for(const b of tools.children)b.setAttribute('aria-pressed',String(b.dataset.mediaLayout===layout));
  tabs.replaceChildren();
  const keys=[...pinned.keys()];
  keys.forEach((key,index)=>{
   const item=pinned.get(key),name=tabTitle(item),tab=el('button','reader-media-tab',name);
   tab.type='button';
   tab.title=name+' '+item.name;
   tab.setAttribute('aria-label',name);
   tab.append(el('span','reader-media-tab-name',item.name));
   tab.id=domId+'tab-'+index;
   tab.setAttribute('role','tab');
   tab.setAttribute('aria-selected',String(visible.includes(key)));
   if(visible.includes(key))tab.setAttribute('aria-controls',views[visible.indexOf(key)].id);
   tab.dataset.active=String(key===selected);
   tab.tabIndex=key===selected?0:-1;
   const pick=next=>{
    choose(next);
    paint();
    tabs.querySelector('[data-active=true]').focus({preventScroll:true});
    revealTab();
   };
   tab.addEventListener('click',()=>pick(key));
   tab.addEventListener('keydown',event=>{
    const next={ArrowLeft:(index+keys.length-1)%keys.length,ArrowRight:(index+1)%keys.length,Home:0,End:keys.length-1}[event.key];
    if(next===undefined)return;
    event.preventDefault();
    pick(keys[next]);
   });
   const close=button('close','取消置顶'+name,()=>{
    change(item);
    (tabs.querySelector('[data-active=true]')??items.find(row=>row.key===item.key)?.pin)?.focus({preventScroll:true});
    if(pinned.size)revealTab();
   });
   const row=el('div','reader-media-tab-item');
   row.append(tab,close);
   tabs.append(row);
  });
  for(const [index,view] of views.entries()){
   const key=visible[index],item=pinned.get(key);
   view.hidden=!item;
   if(!item){
    view.replaceChildren();
    view.mediaSnapshot=null;
    continue;
   }
   view.setAttribute('aria-labelledby',domId+'tab-'+keys.indexOf(key));
   if(view.mediaSnapshot===item.snapshot)continue;
   view.replaceChildren();
   ReaderMediaView.mountCopy(item.snapshot,view);
   view.mediaSnapshot=item.snapshot;
  }
  for(const item of items){
   const active=pinned.has(item.key);
   item.pin.setAttribute('aria-pressed',String(active));
   const label=(active?'取消置顶':'置顶')+item.label;
   item.pin.title=label;
   item.pin.dataset.tooltip=label;
   item.pin.setAttribute('aria-label',label);
  }
 }
 function capture(item){
  const entry=pinned.get(item.key);
  if(!entry)return;
  entry.label=item.label;
  entry.section=item.section;
  entry.name=item.name;
  entry.snapshot=ReaderMediaView.snapshot(item.node);
 }
 function addControls(item){
   const {node,label,name}=item;
  const bar=el('span','reader-media-number');
  bar.dataset.readerMediaControls='';
  const number=el('span','reader-media-label');
  number.dataset.label=label;
  number.setAttribute('role','img');
  number.setAttribute('aria-label',label);
  const title=el('span','reader-media-name',name);
  item.pin=button('pin','置顶'+label,()=>{change(item);if(pinned.has(item.key))refreshWhenReady(item)});
  bar.append(number,title,item.pin,button('fullscreen','放大'+label,()=>ReaderMediaView.open(node,label+' '+name)));
  controls.set(node,bar);
  const table=node.closest('.markdown-table-scroll');
  if(node.matches('table,pre,hr,.reader-mermaid,.reader-learning-route,div.reader-source-crop,div.reader-math-display')){
   bar.classList.add('is-block');
   const target=node.matches('table')&&table?.querySelector('table')===node?table:node;
   target.after(bar);
   if(target!==node){
    const question=node.nextElementSibling;
    if(question?.matches('.inline-question'))bar.after(question);
   }
   return;
  }
  const link=node.closest('a');
  const partSelector=inAnswer?'.reader-answer-part':'article';
  const inlineTarget=link&&link.closest(partSelector)===node.closest(partSelector)?link:node;
  const wrap=el('span','reader-media-inline');
  if(node.matches(displayMath)||node.matches('video,audio,canvas,.reader-source-crop,.reader-page-preview')||
     node.matches('img,picture,svg')&&!inlineTarget.closest('p')?.textContent.trim()){
   wrap.classList.add('is-block');
  }
  inlineTarget.before(wrap);
  wrap.append(inlineTarget,bar);
  if(node.matches('.reader-math')){
   const question=node.nextElementSibling;
   if(question?.matches('.inline-question'))wrap.after(question);
  }
 }
 function refreshWhenReady(item){
  if(!item.node.mediaReady||item.node.querySelector('svg,.katex'))return;
  item.node.mediaReady.then(()=>{
   if(!pinned.has(item.key)||!items.includes(item)||!item.node.isConnected)return;
   capture(item);
   paint();
  });
 }
 function sync(body,projectId,chapter={}){
  if(!dock)init(body);
  root=body;
  if(project!==projectId){
   project=projectId;
   pinned.clear();
   visible=[];
   selected=undefined;
   layout='single';
   chosenLayout=false;
   notice.textContent='';
  }
  items=[];
  const counts=new Map(),position=new Map();
  const nodes=body.querySelectorAll((inAnswer?'.reader-answer-prose':'article')+' :is('+candidates+')');
  for(const node of nodes){
   if(node.parentElement.closest(atomic)||node.closest('.inline-question,.reader-inline-quiz,[data-reader-media-controls]'))continue;
   const article=node.closest(inAnswer?'[data-answer-part-id]':'[data-passage-id]');
   if(!article)continue;
   const card=inAnswer?article.closest('.question-card'):null;
   if(inAnswer&&!card?.open)continue;
   const group=labels.find(([selector])=>node.matches(selector))?.[1]||'图';
   if(group==='公式'&&!node.matches(displayMath))continue;
   const groupKey=card?card.id+'|'+group:group;
   const count=(counts.get(groupKey)||0)+1,index=(position.get(article)||0)+1;
   counts.set(groupKey,count);
   position.set(article,index);
   const item=entries.get(node)||{node};
   const label=group+' '+(inAnswer?count:chapter.number+'-'+count);
   Object.assign(item,{key:JSON.stringify([card?.id,article.dataset.answerPartId||article.dataset.passageId,index,group]),
    label,section:card?.querySelector(':scope>summary .summary-title')?.textContent.trim()||chapter.title||'',
    name:item.name||mediaName(node,article,group)});
   if(node.matches('audio,video'))node.controls=true;
   if(!item.pin){addControls(item);entries.set(node,item);capture(item)}
   if(pinned.has(item.key))refreshWhenReady(item);
   items.push(item);
  }
  paint();
 }
 return {sync,after:node=>controls.get(node)||controls.get(node.closest('.reader-math'))};
}
window.ReaderMediaPins=createReaderMediaPins('explanation');
window.ReaderAnswerMediaPins=createReaderMediaPins('interaction');
