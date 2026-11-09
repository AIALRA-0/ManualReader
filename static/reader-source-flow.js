/* 展示分组只移动已有节点；出处编号、输入框和用户已读状态不在这里改写。 */
window.ReaderSourceFlow={create(ctx){
 const $=id=>document.getElementById(id),el=ReaderUI.make;
 let key='',focusKey='',pages=[],active=null,localRefs=null,scale=1,stack=null;
 const viewport=$('pdf-viewport'),pdf=$('pdf-page'),cropImages=new Map(),cropResults=new Map(),groupOpen=new Map();
 const model=()=>ctx.material(),ui=()=>ctx.content();let indexedModel=null,sourceIndex=new Map(),questionCache=new WeakMap();function source(id){if(model()!==indexedModel){indexedModel=model();sourceIndex=new Map((indexedModel?.passages||[]).map(p=>[p.id,p]));questionCache=new WeakMap()}return sourceIndex.get(id)}
 let linked=null,linkSerial=0,linkRequest=0;const painted=new Set(),domIndexes=new WeakMap();
 const parseIds=value=>{try{return JSON.parse(value||'[]')}catch{return []}};
 function linkData(target){
  const p=source(target.passage_id),entry=model()?.alignment?.explanations?.[p?.id];
  const links=target.links?.length?target.links:target.source_ids?.length?target.source_ids.map(id=>({passage_id:id,sentence_id:target.sentence_ids?.length===1?target.sentence_ids[0]:null})):entry?.links?.length?entry.links:p?.content_kind==='explanation'?p.source_links||[]:p?[{passage_id:p.id,sentence_id:target.sentence_id||null}]:[];
  const ids=[...new Set(links.map(l=>l.passage_id).filter(id=>source(id)?.content_kind!=='explanation'&&source(id)))],sentences=[...new Set(links.map(l=>l.sentence_id).filter(Boolean))];
  const explanations=target.explanation_ids?.length?target.explanation_ids:p?.content_kind==='explanation'?[p.id]:[...new Set(ids.flatMap(id=>model()?.alignment?.sources?.[id]?.explanation_ids||model()?.passages.filter(p=>p.content_kind==='explanation'&&p.source_links?.some(l=>l.passage_id===id)).map(p=>p.id)||[]))].filter(id=>{const rows=model()?.alignment?.explanations?.[id]?.links||[];return !sentences.length||!rows.length||rows.some(l=>!l.sentence_id||sentences.includes(l.sentence_id))});
  const visible=new Set(ui()?.explanation()?.passage_ids||[]),current=ui()?.activeNode()?explanations.filter(id=>visible.has(id)):explanations,precise=current.filter(id=>['block','quote'].includes(model()?.alignment?.explanations?.[id]?.precision));const chosen=!(target.explanation_ids?.length)&&p?.content_kind!=='explanation'&&precise.length?precise:current;return {ids,sentences,explanations:chosen,refs:sourceRefs(ids,sentences),key:JSON.stringify([ids,sentences,chosen])};
 }
 function clearLinked(){for(const n of painted)n.classList.remove('reader-linked','reader-linked-preview');painted.clear()}
 function matching(root,data,kind){
  if(!root)return [];
  const ids=new Set(kind==='explanation'?data.explanations:data.ids),out=[];let index=domIndexes.get(root);if(!index){index={dirty:true,map:new Map()};index.observer=new MutationObserver(()=>index.dirty=true);index.observer.observe(root,{childList:true,subtree:true});domIndexes.set(root,index)}if(index.observer.takeRecords().length)index.dirty=true;if(index.dirty){index.map.clear();for(const n of root.querySelectorAll('article[data-passage-id]'))for(const id of [n.dataset.passageId,...parseIds(n.dataset.passageIds)]){if(!index.map.has(id))index.map.set(id,[]);index.map.get(id).push(n)}index.dirty=false}const all=[...new Set([...ids].flatMap(id=>index.map.get(id)||[]))];
  for(const article of all){
   if(article.closest('.reader-coherent-fragments')&&root.dataset.language==='zh')continue;const figure=article.closest('.reader-figure-fragments')?.closest('.reader-group-figure');
   const own=[article.dataset.passageId,...parseIds(article.dataset.passageIds)];if(!own.some(id=>ids.has(id)))continue;
   const exact=kind!=='explanation'&&data.sentences.length?[...article.querySelectorAll('[data-sentence-id],.reader-coherent-sentence')].filter(n=>data.sentences.includes(n.dataset.sentenceId)||parseIds(n.dataset.sentenceIds).some(id=>data.sentences.includes(id))):[];
   const coherent=kind!=='explanation'?[...article.querySelectorAll('.reader-coherent-sentence')].filter(n=>parseIds(n.dataset.sourceIds).some(id=>ids.has(id))):[];
   out.push(...(figure?[figure]:exact.length?exact:coherent.length?coherent:[article]));
  }
  return out;
 }
 function paintLinked(data){
  const cls='reader-linked',targets={},next=new Set();
  for(const kind of ['original','translation','explanation']){targets[kind]=matching(kind==='original'?$('original-reading'):$(kind),data,kind);for(const n of targets[kind])next.add(n)}
  const questions=new Map((ctx.questions?.()||[]).map(q=>[q.id,q]));for(const card of $('interaction').querySelectorAll('.question-card')){const q=questions.get(card.id);if(!q)continue;let qdata=questionCache.get(q);if(!qdata){qdata=linkData({...q.alignment,passage_id:q.passage_id,sentence_id:q.sentence_id});questionCache.set(q,qdata)}if(qdata.ids.some(id=>data.ids.includes(id))&&(!data.sentences.length||!qdata.sentences.length||qdata.sentences.some(id=>data.sentences.includes(id))))next.add(card)}
  for(const n of painted)if(!next.has(n))n.classList.remove(cls);for(const n of next)if(!painted.has(n))n.classList.add(cls);painted.clear();for(const n of next)painted.add(n);return targets;
 }
 function reveal(root,target){if(!target||!root||root.closest('[hidden]')||!target.getClientRects().length)return;const a=target.getBoundingClientRect(),v=root.getBoundingClientRect();if(a.top<v.bottom-24&&a.bottom>v.top+24)return;const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;root.scrollTo({top:Math.max(0,root.scrollTop+a.top-v.top-20),behavior:reduced?'instant':'smooth'})}
 async function linkSelection(target,origin='translation',{toggle=true}={}){
  if(ui()?.isSelecting?.())return false;ctx.main?.();sync();const data=linkData(target);if(!data.ids.length&&!data.explanations.length)return false;
  const request=++linkRequest;let serial=++linkSerial;if(toggle&&linked?.key===data.key){linked=null;localRefs=null;clearLinked();if(active)ctx.paintRefs?.([]);return true}
  await ui()?.revealLinked?.(data.ids,data.explanations);if(request!==linkRequest)return true;sync();serial=++linkSerial;linked=data;localRefs=data.refs;
  let targets=paintLinked(data);const page=data.refs.find(r=>pages.includes(r.page_id))?.page_id||data.refs[0]?.page_id;
  if(page){if(page===active&&ctx.paintRefs)ctx.paintRefs(data.refs);else await ctx.draw(page,data.refs,{passive:true});if(serial!==linkSerial)return true;
   if(origin!=='original'){const box=data.refs.find(r=>r.page_id===page)?.bbox;if(box){const top=offset()+box[1]*scale,bottom=offset()+box[3]*scale;if(top<viewport.scrollTop+12||bottom>viewport.scrollTop+viewport.clientHeight-12)viewport.scrollTo({top:Math.max(0,top-24),behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'})}}
  }
  if(serial!==linkSerial)return true;targets=paintLinked(data);
  for(const kind of ['original','translation','explanation'])if(kind!==origin)reveal(kind==='original'?viewport:$(kind),targets[kind]?.[0]);
  document.dispatchEvent(new CustomEvent('reader-source-linked',{detail:{source_ids:data.ids,explanation_ids:data.explanations,origin}}));return true;
 }
 let originOperation=0;
 async function link(target,origin='translation',options={}){
  if(['study','term','terms'].includes(origin))return false;
  const originalText=$('original-reading'),operation=++originOperation,own=origin==='original'?(originalText?.clientHeight?originalText:viewport):origin==='question'?$('interaction'):['translation','explanation'].includes(origin)?$(origin):null,position=own?[own.scrollLeft,own.scrollTop]:null;let interrupted=false;const interrupt=()=>interrupted=true;
  for(const type of ['wheel','touchmove','keydown'])own?.addEventListener(type,interrupt,{passive:true});
  try{return await linkSelection(target,origin,options)}finally{
   const restore=()=>{if(position&&own.isConnected&&!interrupted&&operation===originOperation&&(Math.abs(own.scrollTop-position[1])>1||Math.abs(own.scrollLeft-position[0])>1))own.scrollTo({left:position[0],top:position[1],behavior:'instant'})};restore();requestAnimationFrame(()=>{restore();for(const type of ['wheel','touchmove','keydown'])own?.removeEventListener(type,interrupt)})
  }
 }
 function restoreLinked(){if(linked)paintLinked(linked);else clearLinked()}
 // 三栏和问题的映射只由显式点击触发；滚动、悬停和输入不改变定位。
 for(const kind of ['original','translation','explanation']){
  const root=kind==='original'?viewport:$(kind);
  root.addEventListener('click',event=>{
   if(event.button||getSelection()?.toString().trim()||event.target.closest('a,button,input,textarea,summary,.inline-question,.question-composer,img,.reader-source-crop'))return;
   if(kind==='original'&&!event.target.closest('#original-reading'))return;
   const node=event.target.closest('.reader-coherent-sentence,[data-source-ids],[data-sentence-id],article[data-passage-id]'),article=node?.closest('article[data-passage-id]');
   const pid=node?.dataset.passageId||article?.dataset.passageId;if(!pid)return;
   event.stopImmediatePropagation();link({passage_id:pid,sentence_id:node.dataset.sentenceId,source_ids:parseIds(node.dataset.sourceIds),sentence_ids:parseIds(node.dataset.sentenceIds)},kind);
  },true);
 }
 function range(){const m=model(),u=ui(),node=u?.activeNode();if(!m)return [];if(node&&!u.isBrowsing()&&ctx.scope()!=='all')return (node.source_range?.page_ids||[]).filter(id=>m.pages[id]);const fid=u?.getFile()?.id||'primary';return Object.keys(m.pages||{}).filter(id=>(m.pages[id].file_id||'primary')===fid)}
 function imageURL(info){return ctx.fileURL(info.file_id?'/content-files/'+info.file_id+'/asset/'+info.image.split('/').at(-1):'/page/'+info.image.split('/').at(-1))}
 function loadSourceImage(url){
  if(cropImages.has(url))return cropImages.get(url);
  let pending;pending=new Promise((resolve,reject)=>{const image=new Image();let retry=false;image.onload=()=>resolve(image);image.onerror=()=>{if(!retry){retry=true;image.src=url+(url.includes('?')?'&':'?')+'reader_retry=1'}else reject(Error('原图暂未加载'))};image.src=url}).catch(error=>{if(cropImages.get(url)===pending)cropImages.delete(url);throw error});
  cropImages.set(url,pending);if(cropImages.size>12)cropImages.delete(cropImages.keys().next().value);return pending;
 }
 function pageLabel(id){const m=model(),p=m.pages[id],file=m.reading_files?.find(f=>f.id===(p.file_id||'primary'));return (pages.some(other=>(m.pages[other].file_id||'primary')!==(p.file_id||'primary'))?(file?.name||'原文')+' · ':'')+'第 '+p.number+' 页'}
 function sync(){const m=model();if(!m)return;const next=JSON.stringify([m.id,m.material_revision,ui()?.activeNode()?.id,ui()?.isBrowsing(),ctx.scope(),ui()?.getFile()?.id]);const f=JSON.stringify([m.id,ctx.focus()?.passage_id,ctx.focus()?.sentence_id,ctx.focus()?.revision]);if(f!==focusKey){focusKey=f;localRefs=null}if(next!==key){key=next;linked=null;++linkSerial;clearLinked();pages=range();localRefs=null;active=null;if(stack){viewport.append(pdf);stack.remove();stack=null}}}
 function prepare(target){sync();if(target&&!pages.includes(target)){pages=[...pages,target];if(stack){viewport.append(pdf);stack.remove();stack=null}}if(!stack){stack=el('div','reader-page-stack');stack.id='reader-page-stack';for(const id of pages){const info=model().pages[id],item=el('section','reader-source-page'),label=el('div','reader-page-label',pageLabel(id)),image=el('img','reader-page-preview');item.dataset.pageId=id;label.id='page-label-'+id.replace(/[^a-z0-9_-]/gi,'-');item.setAttribute('aria-labelledby',label.id);let retried=false;const originalUrl=imageURL(info);image.onerror=()=>{if(!retried){retried=true;image.src=originalUrl+(originalUrl.includes('?')?'&':'?')+'reader_retry=1'}else{image.dataset.loadState='error';image.alt=pageLabel(id)+'，点击重试'}};image.onload=()=>image.dataset.loadState='ready';image.alt=pageLabel(id);image.loading='lazy';image.decoding='async';image.width=info.width;image.height=info.height;image.src=originalUrl;item.append(label,image);stack.append(item);image.addEventListener('click',()=>ctx.draw(id,ctx.refs(),{passive:true}));}viewport.append(stack);rescale(scale)}activate(target)}
 function activate(id){if(!stack)return;const savedScroll=viewport.scrollTop;const slot=[...stack.children].find(n=>n.dataset.pageId===id);if(!slot)return;for(const row of stack.children){row.classList.toggle('is-current',row===slot);row.querySelector('.reader-page-preview').hidden=row===slot}slot.append(pdf);active=id;rescale(scale);viewport.scrollTop=savedScroll;const index=pages.indexOf(id),info=model().pages[id];$('page-info').textContent='第 '+info.number+' 页'+(pages.length>1?' · 本范围 '+(index+1)+' / '+pages.length:'');$('page-info').title=pages.map(pageLabel).join('、');const at=pages.indexOf(id);$('page-prev').disabled=at<=0;$('page-next').disabled=at<0||at===pages.length-1}
 function rescale(value){scale=value;if(!stack)return;for(const row of stack.children){const info=model().pages[row.dataset.pageId];row.style.width=info.width*value+'px';row.style.height=(info.height*value+24)+'px';const image=row.querySelector('.reader-page-preview');image.style.width=info.width*value+'px';image.style.height=info.height*value+'px'}}
 function offset(){return stack&&pdf.parentElement?.classList.contains('reader-source-page')?pdf.getBoundingClientRect().top-viewport.getBoundingClientRect().top+viewport.scrollTop:0}
 function badge(){const node=ui()?.activeNode(),ids=node?.source_range?.page_ids||pages;if(!ids.length)return '';const numbers=ids.map(id=>model().pages[id]?.number),sameFile=new Set(ids.map(id=>model().pages[id]?.file_id||'primary')).size===1,continuous=numbers.every((n,i)=>i===0||n===numbers[i-1]+1);return '本章原文：'+(sameFile?(continuous&&numbers.length>2?'第 '+numbers[0]+'–'+numbers.at(-1)+' 页':'第 '+numbers.join('、')+' 页'):'共 '+ids.length+' 页，来自 '+new Set(ids.map(id=>model().pages[id]?.file_id||'primary')).size+' 份文件')}
 function scrollTo(root,node){if(!node||root.hidden||root.closest('[hidden]'))return;const rect=node.getBoundingClientRect(),view=root.getBoundingClientRect();root.scrollTo({top:root.scrollTop+rect.top-view.top-12,behavior:'auto'})}
 $('original-full').after($('fit-page-lock'));
 for(const name of ['translation','explanation']){const pane=document.querySelector('[data-pane='+name+']'),button=el('button','icon-btn'),storage='reader-hide-inline-questions:'+name;let hidden=localStorage.getItem(storage)==='true';button.id='toggle-inline-'+name;button.type='button';const update=()=>{pane.classList.toggle('inline-questions-hidden',hidden);ReaderUI.decorate(button,'message',hidden?'显示段落提问入口':'隐藏段落提问入口');button.setAttribute('aria-pressed',String(hidden))};button.onclick=()=>{hidden=!hidden;localStorage.setItem(storage,String(hidden));update()};(pane.querySelector('.pane-actions')||pane.querySelector('.pane-head')).append(button);update()}
 // 原图只裁成静态图片；打开后沿用同一个图片查看器，不在图上叠加提问或点击区域。
 const deferredCrops=new Map();
 const cropObserver=typeof IntersectionObserver==='function'?new IntersectionObserver(entries=>{
  for(const entry of entries){const image=entry.target;if(!image.isConnected||entry.isIntersecting){cropObserver.unobserve(image);const load=deferredCrops.get(image);deferredCrops.delete(image);if(image.isConnected)load?.()}}
 },{rootMargin:'200px'}):null;
 function deferCrop(image,load){
  if(!cropObserver){load();return}
  deferredCrops.set(image,load);cropObserver.observe(image);
  queueMicrotask(()=>{for(const old of deferredCrops.keys())if(!old.isConnected){cropObserver.unobserve(old);deferredCrops.delete(old)}});
 }
 function crop(group){const info=model().pages[group.visual_page_id],box=group.bbox;if(!info||!box||box[2]<=box[0]||box[3]<=box[1])return null;const image=el('img','reader-source-crop'),url=imageURL(info),x=Math.max(0,box[0]-3),y=Math.max(0,box[1]-3),w=Math.min(info.width-x,box[2]-box[0]+6),h=Math.min(info.height-y,box[3]-box[1]+6);image.alt='对应原图 · '+pageLabel(group.visual_page_id);image.dataset.originalSrc=url;image.width=w;image.height=h;image.style.aspectRatio=w+' / '+h;image.tabIndex=0;image.setAttribute('role','button');image.setAttribute('aria-label','放大查看'+image.alt);image.loading='lazy';deferCrop(image,()=>{const cacheKey=JSON.stringify([url,box]);if(!cropResults.has(cacheKey)){cropResults.set(cacheKey,loadSourceImage(url).then(original=>{const sx=original.naturalWidth/info.width,sy=original.naturalHeight/info.height,ratio=Math.min(sx,2048/w),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(w*ratio));canvas.height=Math.max(1,Math.round(h*ratio));canvas.getContext('2d').drawImage(original,x*sx,y*sy,w*sx,h*sy,0,0,canvas.width,canvas.height);return canvas.toDataURL('image/png')}));if(cropResults.size>48)cropResults.delete(cropResults.keys().next().value)}const requested=cropResults.get(cacheKey);requested.then(src=>{image.src=src;image.dataset.cropReady='true'}).catch(()=>{if(cropResults.get(cacheKey)===requested)cropResults.delete(cacheKey);image.src=url;image.dataset.cropReady='fallback';image.alt='原页 · '+pageLabel(group.visual_page_id)});});return image}



 function validRefs(refs){return (refs||[]).filter(ref=>{const page=model()?.pages?.[ref.page_id],b=ref.bbox;return page&&Array.isArray(b)&&b.length===4&&b.every(Number.isFinite)&&b[2]>b[0]&&b[3]>b[1]})}
 function sourceRefs(ids,sentenceIds=[]){return validRefs(ids.flatMap(id=>{const p=source(id),sentences=(p?.sentences||[]).filter(row=>sentenceIds.includes(row.id));return sentences.length?sentences.flatMap(row=>row.refs||[]):p?.refs||[]}))}
 function locate(article,sourceIds,sentenceIds=[]){return link({source_ids:sourceIds,sentence_ids:sentenceIds},'translation')}
 function composer(boxes,key){
  if(boxes.length===1){const box=boxes[0];box.querySelector(':scope > summary')?.removeAttribute('hidden');return box}
  const disclosure=el('details','reader-group-composer'),summary=el('summary'),notice=el('span','question-sync-status',boxes.some(box=>box.querySelector('textarea')?.value.trim())?'已保存':'');
  summary.append(ReaderUI.icon('message'),ReaderUI.icon('chevron'),el('span','inline-question-line'),notice);summary.setAttribute('aria-label','展开对应内容的问题');disclosure.append(summary);
  disclosure.open=groupOpen.get(key)||false;disclosure.ontoggle=()=>groupOpen.set(key,disclosure.open);
  for(const box of boxes){disclosure.append(box);box.open=true}
  return disclosure
 }
 function coherent(root,group,members,wrapper){
  const segments=group.translation_segments;if(root.dataset.language==='en'||!Array.isArray(segments)||!segments.length)return;
  const ids=new Set(members.map(n=>n.dataset.passageId)),covered=segments.flatMap(s=>s.source_ids||[]);if(new Set(covered).size!==ids.size||covered.some(id=>!ids.has(id))||new Set(segments.map(s=>s.id)).size!==segments.length||segments.some(s=>!s.text?.trim()||!s.source_ids?.length))return;
  wrapper.classList.add('reader-coherent-group');wrapper.classList.remove('reader-continuation');const fragments=el('div','reader-coherent-fragments');
  for(const member of members)fragments.append(member);for(const child of [...wrapper.children])if(!child.classList.contains('reader-source-crop'))child.remove();wrapper.append(fragments);
  // 句子锚点仍各自保留，但连续句子组成一个阅读段落和一个提问入口。
  const blocks=[];for(const segment of segments){const original=source(segment.source_ids[0]),role=segment.role||(group.kind==='list'?'list_item':group.kind==='markdown'?(original?.heading?'heading':original?.bullet?'list_item':'paragraph'):group.kind),blockId=segment.block_id||(group.kind==='paragraph'?'':segment.source_ids.join('|')),previous=blocks.at(-1),sameParagraph=role==='paragraph'&&previous?.role==='paragraph'&&blockId===previous.block_id;if(sameParagraph)previous.parts.push(segment);else blocks.push({role,block_id:blockId,parts:[segment]})}
  const newParts=el('div');newParts.id=root.id;

  for(const block of blocks){const segment=block.parts[0],sourceIds=[...new Set(block.parts.flatMap(s=>s.source_ids))],article=el('article','passage reader-coherent-segment'),chinese=el('div','chinese'),text=el('div','reading-segment');article.dataset.passageId=sourceIds[0];article.dataset.passageIds=JSON.stringify(sourceIds);text.dataset.segmentId='translation-'+group.id+'-'+segment.id;text.dataset.sourceExcerpt=block.parts.map(s=>s.text).join(' ');text.dataset.sourceIds=JSON.stringify(sourceIds);text.dataset.sentenceIds=JSON.stringify(block.parts.flatMap(s=>s.sentence_ids||[]));
   const inline=block.parts.length>1&&block.role==='paragraph'&&block.parts.every(s=>!/[\r\n]{2}|^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s)/.test(s.text));
   if(inline){const paragraph=el('p');for(const part of block.parts){const span=el('span','reader-coherent-sentence');span.dataset.sourceIds=JSON.stringify(part.source_ids);span.dataset.translationSegment=part.id;span.dataset.sentenceIds=JSON.stringify(part.sentence_ids||[]);ctx.rich(span,ReaderUI.prose(part.text));paragraph.append(span,document.createTextNode(' '))}text.append(paragraph)}else{let displayText=text.dataset.sourceExcerpt;if(block.role==='heading'&&!/^\s*#{1,6}\s/.test(displayText))displayText='#'.repeat(Math.max(1,Math.min(6,Number(segment.level)||2)))+' '+displayText;if(block.role==='list_item'&&!/^\s*(?:[-*+] |\d+[.)] )/.test(displayText))displayText=(segment.list_order?segment.list_order+'. ':'- ')+displayText;ctx.rich(text,ReaderUI.prose(displayText))}
   chinese.append(text);article.append(chinese);newParts.append(article);article.addEventListener('click',event=>{if(getSelection()?.toString().trim()||event.target.closest('a,button,input,textarea,summary,.inline-question'))return;const exact=event.target.closest('.reader-coherent-sentence');locate(article,exact?JSON.parse(exact.dataset.sourceIds):sourceIds,JSON.parse((exact||text).dataset.sentenceIds||'[]'))})
  }
  ctx.attachInputs?.(newParts);
  // 重排后沿用原输入框，保留 client_id 和已保存的问题；不在段末再放一份旧入口。
  const relocated=new Map();
  for(const box of fragments.querySelectorAll('.inline-question')){
   const input=box.querySelector('textarea');if(!input?.value.trim())continue;
   const target=[...newParts.children].find(article=>JSON.parse(article.dataset.passageIds).includes(input.dataset.passageId)&&article.querySelector('.reading-segment'));
   if(!target)continue;
   if(!relocated.has(target))relocated.set(target,[]);relocated.get(target).push(box)
  }
  for(const [article,boxes] of relocated){const text=article.querySelector('.reading-segment'),fresh=[...text.querySelectorAll('.inline-question')];for(const box of fresh){if(box.querySelector('textarea')?.value.trim())boxes.push(box);else box.remove()}text.append(composer([...new Set(boxes)],group.id+':'+article.dataset.passageId))}
  for(const article of [...newParts.children])wrapper.insertBefore(article,fragments);if(root.dataset.language==='both'&&root.dataset.order==='en-first')wrapper.prepend(fragments);
  for(const text of wrapper.querySelectorAll('.reader-coherent-segment .reading-segment')){const input=text.querySelector('textarea');if(input)ctx.groupInput?.(input,JSON.parse(text.dataset.sourceIds),text.dataset.sourceExcerpt)}
 }
 function reflow(root){sync();if(root.querySelector('.authored-content'))return;const articles=[...root.querySelectorAll(':scope > article[data-passage-id]')],byId=new Map(articles.map(n=>[n.dataset.passageId,n]));if(!articles.length)return;const ordered=[],used=new Set();for(const group of model()?.reading_view?.groups||[]){const members=group.passage_ids.map(id=>byId.get(id)).filter(Boolean);if(!members.length)continue;const wrapper=el('section','reader-source-group reader-group-'+group.kind);wrapper.classList.toggle('reader-continuation',group.kind==='paragraph'&&members.length>1);wrapper.dataset.groupId=group.id;wrapper.dataset.passageIds=JSON.stringify(members.map(n=>n.dataset.passageId));wrapper.dataset.pageIds=JSON.stringify(group.page_ids||[]);const first=members[0];first.before(wrapper);let content=wrapper;if(group.kind==='figure'){const figure=crop(group);if(figure){const labels=el('div','reader-figure-fragments');labels.hidden=true;const caption=el('figcaption','reader-figure-name',group.caption||group.title||pageLabel(group.visual_page_id)+' · 图 '+(ordered.filter(g=>g.classList.contains('reader-group-figure')).length+1));wrapper.append(figure,caption,labels);content=labels}}if(group.kind==='table'&&group.cells){const table=el('table','reader-reflow-table'),body=el('tbody');for(const cells of group.cells){const tr=el('tr');for(const ids of cells){const td=el('td');for(const id of ids){const a=byId.get(id);if(a){td.append(a);used.add(a)}}tr.append(td)}body.append(tr)}table.append(body);wrapper.append(table)}for(const a of members)if(!used.has(a)){content.append(a);used.add(a)}coherent(root,group,members,wrapper);ordered.push(wrapper)}
  // 普通段落的输入框留在对应文字后；图表和拼回的句子共用一个入口，已保存的问题仍各自保留。
  for(const group of ordered){const boxes=[...group.querySelectorAll('.inline-question')].filter(box=>!box.closest('.reader-coherent-segment'));if(!boxes.length)continue;const bundled=group.matches('.reader-group-figure,.reader-group-table,.reader-continuation,.reader-coherent-group');if(!bundled)continue;const footer=el('div','reader-group-questions'),saved=[];let empty=null;for(const box of boxes){const input=box.querySelector('textarea');if(input?.value.trim())saved.push(box);else if(!empty)empty=box;else box.remove()}if(group.classList.contains('reader-coherent-group')&&empty){empty.remove();empty=null}const ids=JSON.parse(group.dataset.passageIds),text=ids.map(id=>source(id)?.zh||source(id)?.en||'').filter(Boolean).join('\n');if(empty)ctx.groupInput?.(empty.querySelector('textarea'),ids,text);if(saved.length){footer.append(composer([...saved,...(empty?[empty]:[])],group.dataset.groupId))}else if(empty)footer.append(empty);if(footer.childElementCount)group.append(footer)}
  let previous=null;for(const group of ordered){const pid=JSON.parse(group.dataset.pageIds||'[]')[0];if(pid&&pid!==previous){const label=el('div','reader-translation-page',pageLabel(pid));group.before(label);previous=pid}}}
 function annotate(root){sync();requestAnimationFrame(restoreLinked)}

 return {link,restoreLinked,selection:()=>linked,userMoving:()=>false,sync,prepare,activate,rescale,offset,reflow,annotate,range,refs:()=>localRefs,pageStep:delta=>{const id=pages[pages.indexOf(active)+delta];if(id){ctx.draw(id,ctx.refs()).then(()=>scrollTo(viewport,[...stack.children].find(n=>n.dataset.pageId===id)));}},page:()=>active};
}};
