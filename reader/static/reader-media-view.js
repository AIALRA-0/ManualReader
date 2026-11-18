/* 图片只展示静态内容；缩放和拖动由查看器处理，不改写原件或阅读进度。 */
window.ReaderMediaView=(()=>{
 let dialog,stage,viewport,heading,original,scale=1,start=null,drag=null,serial=0,baseWidth=0,baseHeight=0;
 const escapePattern=value=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
 function mediaSelectorMap(ids){const names=new Map([...ids].map(([before,after])=>[CSS.escape(before),CSS.escape(after)]));if(!names.size)return value=>value;const pattern=new RegExp('#('+[...names.keys()].sort((a,b)=>b.length-a.length).map(escapePattern).join('|')+')(?![\\w-]|\\\\)','g');return value=>value.replace(pattern,(_,id)=>'#'+names.get(id))}
 function remapMediaURLs(value,ids){return value.replace(/url\(\s*(['"]?)#([^\s)'"\\]+)\1\s*\)/gi,(match,quote,id)=>ids.has(id)?'url('+quote+'#'+ids.get(id)+quote+')':match)}
 // SVG 重新编号后，样式的选择器和箭头引用必须一起更新；颜色值不作为编号处理。
 function remapMediaStyles(root,ids){const selector=mediaSelectorMap(ids);for(const style of root.querySelectorAll('style')){const rules=[...(style.sheet?.cssRules||[])];if(!rules.length)continue;const update=rule=>{if(rule.selectorText)rule.selectorText=selector(rule.selectorText);if(rule.style)for(let i=0;i<rule.style.length;i++){const name=rule.style[i],value=rule.style.getPropertyValue(name),next=remapMediaURLs(value,ids);if(next!==value)rule.style.setProperty(name,next,rule.style.getPropertyPriority(name))}if(rule.cssRules)for(const child of rule.cssRules)update(child)};rules.forEach(update);style.textContent=rules.map(rule=>rule.cssText).join('\n')}}
 const init=()=>{if(dialog)return;const {make:el}=ReaderUI;dialog=el('dialog','reader-media-dialog');dialog.id='media-view-dialog';const head=el('div','dialog-head'),tools=el('div','control-group');heading=el('h2');stage=el('div','media-view-stage');viewport=el('div','media-view-viewport');viewport.append(stage);const button=(name,label,action)=>{const b=ReaderUI.decorate(el('button','icon-btn'),name,label);b.type='button';b.onclick=action;return b};original=ReaderUI.decorate(el('a','icon-btn'),'external','打开原图');original.target='_blank';original.rel='noopener';tools.append(button('minus','缩小',()=>zoom(scale/1.2)),button('plus','放大',()=>zoom(scale*1.2)),button('fit','恢复适配',()=>{zoom(1);viewport.scrollTo(0,0)}),original,button('close','关闭放大查看',()=>dialog.close()));head.append(heading,tools);dialog.append(head,viewport);document.body.append(dialog);dialog.addEventListener('close',()=>{stage.replaceChildren();drag=null});dialog.addEventListener('keydown',e=>{if(e.key==='+'||e.key==='='){e.preventDefault();zoom(scale*1.2)}else if(e.key==='-'){e.preventDefault();zoom(scale/1.2)}});
  viewport.addEventListener('pointerdown',e=>{if(e.button!==0||!stage.querySelector('img,svg'))return;drag={id:e.pointerId,x:e.clientX,y:e.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};viewport.setPointerCapture(e.pointerId);viewport.classList.add('is-dragging');e.preventDefault()});viewport.addEventListener('pointermove',e=>{if(!drag||drag.id!==e.pointerId)return;viewport.scrollLeft=drag.left+drag.x-e.clientX;viewport.scrollTop=drag.top+drag.y-e.clientY});for(const event of ['pointerup','pointercancel','lostpointercapture'])viewport.addEventListener(event,()=>{drag=null;viewport.classList.remove('is-dragging')});
 };
 function zoom(value){scale=Math.max(.25,Math.min(6,value));const image=stage.querySelector(':scope > img');if(image){image.style.width=baseWidth*scale+'px';image.style.height=baseHeight*scale+'px';stage.style.width=Math.max(viewport.clientWidth-24,baseWidth*scale)+'px';stage.style.height=Math.max(viewport.clientHeight-24,baseHeight*scale)+'px';stage.style.zoom=''}else{stage.style.width='max-content';stage.style.height='auto';stage.style.zoom=String(scale)}viewport.dataset.zoom=String(scale)}
 function snapshot(node){
  const copy=node.cloneNode(true);
  const canvases=node.matches('canvas')?[node]:[...node.querySelectorAll('canvas')];
  const canvasCopies=copy.matches('canvas')?[copy]:[...copy.querySelectorAll('canvas')];
  canvases.forEach((canvas,index)=>{
   if(canvas.width&&canvas.height)canvasCopies[index].getContext('2d')?.drawImage(canvas,0,0);
  });
  copy.querySelectorAll('.inline-question,.question-actions,[data-reader-media-controls],.reader-code-copy,script,textarea,input,button').forEach(n=>n.remove());
  copy.removeAttribute('onclick');
  for(const n of [copy,...copy.querySelectorAll('[tabindex],[role=button]')]){
   n.removeAttribute('tabindex');
   if(n.getAttribute('role')==='button')n.removeAttribute('role');
  }
  return copy;
 }
 function mountCopy(source,host){
  const copy=snapshot(source),map=new Map();
  for(const n of [copy,...copy.querySelectorAll('[id]')])if(n.id){
   const before=n.id;n.id='media-'+(++serial)+'-'+before;map.set(before,n.id);
  }
  for(const n of [copy,...copy.querySelectorAll('*')])for(const a of [...n.attributes]){
   let value=remapMediaURLs(a.value,map);
   if((a.localName==='href'||a.name==='xlink:href')&&value.startsWith('#')&&map.has(value.slice(1)))value='#'+map.get(value.slice(1));
   if(a.name==='aria-labelledby'||a.name==='aria-describedby')value=value.split(/\s+/).map(id=>map.get(id)||id).join(' ');
   if(value!==a.value)n.setAttribute(a.name,value);
  }
  host.append(copy);
  remapMediaStyles(copy,map);
  return copy;
 }
 function open(node,label){
  init();stage.replaceChildren();
  heading.id='media-view-title';
  dialog.setAttribute('aria-labelledby',heading.id);
  heading.textContent=label||(node.matches('img,picture,.reader-source-crop')?'图片':node.matches('table')?'表格':node.matches('.reader-mermaid')?'流程图':node.matches('pre')?'代码':node.matches('audio')?'音频':node.matches('video')?'视频':'公式');
  const url=node.dataset.originalSrc||node.getAttribute('src');original.hidden=!url;if(url)original.href=url;
  const copy=mountCopy(node,stage);
  if(copy.tagName==='IMG'){copy.removeAttribute('width');copy.removeAttribute('height');}
  if(!dialog.open)dialog.showModal();
  viewport.scrollTo(0,0);
  if(copy.tagName==='IMG'){copy.draggable=false;const fit=()=>{const w=copy.naturalWidth||node.width||600,h=copy.naturalHeight||node.height||400,ratio=Math.min(3,Math.max(100,viewport.clientWidth-24)/w,Math.max(100,viewport.clientHeight-24)/h);baseWidth=w*ratio;baseHeight=h*ratio;zoom(1)};copy.onload=fit;if(copy.complete)fit()}else zoom(1)
 }
 document.addEventListener('pointerdown',e=>start={x:e.clientX,y:e.clientY},true);
 document.addEventListener('click',event=>{if(dialog?.contains(event.target)||event.target.closest('a,button,input,textarea,select,summary,[role=button],[contenteditable],.inline-question,.question-composer,.answer-followup-group,.followup,.bp,.reader-inline-quiz'))return;if(getSelection()?.toString().trim())return;if(start&&Math.hypot(event.clientX-start.x,event.clientY-start.y)>6)return;const node=event.target.closest('img,table,.reader-math,.reader-mermaid,.reader-source-crop');if(!node||!node.closest('[data-pane],.practice-dialog')||node.closest('.reader-selection-source,#pdf-page')||node.matches('.reader-page-preview'))return;event.preventDefault();event.stopImmediatePropagation();open(node)},true);
 document.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.matches('.reader-source-crop')){event.preventDefault();open(event.target)}});
 return {open,snapshot,mountCopy};
})();
