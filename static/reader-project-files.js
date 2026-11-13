/* 文件标签只查看项目文件，不改变主原文的来源、章节或学习进度。 */
window.ReaderProjectFiles={
 // 写作链接沿用项目相对路径；网址仍在浏览器打开，项目文件在原文标签中查看。
 resolveLink(href,{origin=location.origin,project='',basePath=''}={}){
  const raw=String(href||'').trim();if(!raw||raw.startsWith('#')||raw.startsWith('//'))return null;
  let value,path;
  try{value=new URL(raw,origin+'/')}catch{return null}
  if(value.origin!==origin)return null;
  const identity=value.searchParams.get('material');
  if(/^\/api\/project-files\/(?:raw|preview)$/.test(value.pathname)){
   if(identity&&identity!==project)throw Error('这个链接属于其他项目，请先打开对应项目');
   path=value.searchParams.get('path');
  }else if(!/^(?:[a-z][a-z0-9+.-]*:|\/|\?)/i.test(raw)){
   const filename=raw.split(/[?#]/)[0];if(!filename)return null;
   const parts=basePath.split('/').slice(0,-1);
   for(const part of decodeURIComponent(filename).replace(/\\/g,'/').split('/')){if(!part||part==='.')continue;if(part==='..'){if(!parts.length)throw Error('文件链接超出了当前项目');parts.pop()}else parts.push(part)}
   path=parts.join('/');
  }else return null;
  if(!path||path.startsWith('/')||/[\\:\x00-\x1f]/.test(path)||path.split('/').some(p=>!p||p==='.'||p==='..'))throw Error('文件链接需要使用项目根目录内的相对路径');
  return {path,hash:value.hash}
 },
 position(hash){
  const line=String(hash||'').match(/^#L([1-9]\d*)(?:-L?([1-9]\d*))?$/i);
  const page=String(hash||'').match(/^#page=([1-9]\d*)$/i);
  if(line)return {line:Number(line[1]),end_line:line[2]?Number(line[2]):null};
  return page?{page:Number(page[1])}:{};
 },
 create(ctx){
 const $=id=>document.getElementById(id),el=ReaderUI.make,original=document.querySelector('[data-pane=original]'),directory=document.querySelector('[data-pane=directory]'),head=original.querySelector('.pane-head');
 const button=(icon,label,action)=>{const b=ReaderUI.decorate(el('button','icon-btn'),icon,label);b.type='button';b.onclick=action;return b};
 const blobs=new Set();async function resource(url,tracking=null){const value=await ctx.file(url,tracking);if(value.startsWith('blob:'))blobs.add(value);return value}function clearResources(){for(const value of blobs)URL.revokeObjectURL(value);blobs.clear()}
 let project=null,tabs=[],active='',request=0,split=1,bottomMode='files',thirdMode='practice',weights=[1,1,1],poll=null,presentationId=null;
 let fileWait=null;function clearFileWait(){fileWait?.finish();fileWait=null}
 const strip=el('div','project-file-tabs');strip.setAttribute('role','tablist');strip.setAttribute('aria-label','原文与项目文件');head.querySelector('.content-kind-select')?.after(strip);
 const viewer=el('div','project-file-viewer'),bar=el('div','project-file-toolbar'),body=el('div','project-file-body pane-scroll');viewer.append(bar,body);$('pdf-viewport').after(viewer);viewer.hidden=true;
 const toolMenu=el('div','project-original-tools-menu'),more=button('all','原文窗口工具',()=>{const actions=head.querySelector('.pane-actions');if(!actions)return;const rect=more.getBoundingClientRect();toolMenu.style.left=Math.max(8,Math.min(rect.right-330,innerWidth-338))+'px';toolMenu.style.top=(rect.bottom+5)+'px';toolMenu.append(actions);toolMenu.showPopover()});toolMenu.setAttribute('popover','auto');more.setAttribute('aria-haspopup','menu');more.setAttribute('aria-expanded','false');document.body.append(toolMenu);more.classList.add('project-tools-button');head.append(more);toolMenu.addEventListener('toggle',event=>{more.setAttribute('aria-expanded',String(event.newState==='open'));ReaderUI.decorate(more,event.newState==='open'?'close':'all',event.newState==='open'?'收起原文窗口工具':'原文窗口工具');if(event.newState==='closed'){const actions=toolMenu.querySelector('.pane-actions');if(actions)head.insertBefore(actions,more)}});
 const maximize=button('fullscreen','放大原文窗口',()=>{const expanded=original.classList.toggle('project-viewer-maximized');maximize.setAttribute('aria-pressed',String(expanded));ReaderUI.decorate(maximize,expanded?'close':'fullscreen',expanded?'还原原文窗口':'放大原文窗口')});maximize.id='project-viewer-maximize';head.append(maximize);
 document.addEventListener('keydown',event=>{if(event.key==='Escape'&&original.classList.contains('project-viewer-maximized'))maximize.click()});
 const outline=$('outline'),slots=el('div','directory-slots'),originalDirectoryHead=directory.querySelector('.pane-head'),divider=el('button','directory-divider');divider.type='button';divider.setAttribute('role','separator');divider.setAttribute('aria-label','调整上下目录的高度');divider.setAttribute('aria-orientation','horizontal');
 const modes=[['learning','讲解目录'],['source','原文目录'],['reading','阅读完成度'],['practice','练习完成度'],['both','阅读与练习双环'],['files','文件目录']];
 function slot(index){const section=el('section','directory-slot'),title=index?el('div','pane-head directory-slot-head'):originalDirectoryHead,select=el('select','content-kind-select'),actions=el('div','pane-actions'),content=el('div','directory-slot-content'),tree=el('div','project-file-tree pane-scroll'),nav=index?el('nav','outline pane-scroll'):outline;title.classList.add('directory-slot-head');title.replaceChildren();nav.id=index?'outline-secondary-'+index:'outline';select.setAttribute('aria-label',index?'第 '+(index+1)+' 栏目录显示内容':'目录显示内容');for(const [key,label]of modes){const option=el('option',null,label);option.value=key;select.append(option)}title.append(select,actions);content.append(nav,tree);section.append(title,content);slots.append(section);select.onchange=()=>{if(index===2)thirdMode=select.value;else if(index)bottomMode=select.value;else ctx.setMode(select.value,false);persist();renderDirectory()};return{index,section,title,select,actions,nav,tree}}
 const top=slot(0);slots.append(divider);const bottom=slot(1),divider2=divider.cloneNode();divider2.setAttribute('aria-label','调整中下目录的高度');slots.append(divider2);const third=slot(2);directory.append(slots);divider.hidden=divider2.hidden=bottom.section.hidden=third.section.hidden=true;
 const splitButton=button('layout','上下拆分目录',()=>{split=split%3+1;if(split===2&&bottomMode===ctx.mode())bottomMode=ctx.mode()==='files'?'reading':'files';persist();renderDirectory()});splitButton.id='directory-split';top.actions.append(splitButton);
 const treeUI=ReaderProjectTree.create({api:ctx.api,watch:ctx.watch,open,active:()=>active,status:ctx.status,download:async(url,name)=>{const a=el('a');a.href=await resource(url);a.download=name;a.click()},changed(events){let changed=false,refresh=false;for(const event of events){if(event.type==='moved'&&event.paths.length===2){const [from,to]=event.paths;for(const tab of tabs)if(tab.path===from||tab.path.startsWith(from+'/')){tab.path=to+tab.path.slice(from.length);tab.name=tab.path.split('/').at(-1);if(active===from||active.startsWith(from+'/'))active=to+active.slice(from.length);changed=true}}if(active!==''&&event.paths.some(p=>p===active||p&&active.startsWith(p+'/')))refresh=true}if(changed){applyTab(false);persist()}if(refresh){saveScroll();preview(tabs.find(t=>t.path===active),true).catch(e=>ctx.status(e.message))}}});
 function bindDivider(handle,index){let start=null;handle.onpointerdown=event=>{event.preventDefault();start={y:event.clientY,weights:[...weights]};handle.setPointerCapture(event.pointerId)};handle.onpointermove=event=>{if(!start)return;const delta=(event.clientY-start.y)/(slots.clientHeight/split);weights[index]=Math.max(.25,start.weights[index]+delta);weights[index+1]=Math.max(.25,start.weights[index+1]-delta);applyRatio()};handle.onpointerup=()=>{start=null;persist()};handle.onpointercancel=()=>start=null;handle.onkeydown=event=>{if(!['ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();const change=event.key==='ArrowUp'?-.1:.1;weights[index]=Math.max(.25,weights[index]+change);weights[index+1]=Math.max(.25,weights[index+1]-change);applyRatio();persist()}}
 bindDivider(divider,0);bindDivider(divider2,1);
 function applyRatio(){slots.style.gridTemplateRows=weights.slice(0,split).map(w=>`minmax(0,${w}fr)`).join(' 8px ')}
 function persist(){if(!project)return;localStorage.setItem('reader-project-files:'+project,JSON.stringify({tabs:tabs.map(t=>({path:t.path,page:t.page||0,scroll:t.scroll||0,location:t.location||null})),active,split,weights,bottomMode,thirdMode,mode:ctx.mode()}))}
 function url(kind,path,extra=''){return '/api/project-files/'+kind+'?material='+encodeURIComponent(project)+'&path='+encodeURIComponent(path)+extra}
 function showMain(){if(active==='')return;clearFileWait();saveScroll();stopMedia();active='';request++;clearTimeout(poll);applyTab();persist()}
 function stopMedia(){for(const node of body.querySelectorAll('audio,video')){node.pause();node.removeAttribute('src');node.load()}}
 function saveScroll(){const tab=tabs.find(t=>t.path===active);if(tab)tab.scroll=body.scrollTop}
 function applyTab(scroll=true){treeUI?.paint();const oldScroll=strip.scrollLeft;original.classList.toggle('has-file-tab',active!=='');viewer.hidden=active==='';strip.replaceChildren();const all=[{path:'',name:'主原文'},...tabs];for(const tab of all){const wrap=el('div','project-tab');wrap.dataset.path=tab.path;const b=el('button','project-tab-title');b.append(el('span','project-tab-label',tab.name||tab.path.split('/').at(-1)));b.type='button';b.setAttribute('role','tab');b.setAttribute('aria-selected',String(active===tab.path));b.title=tab.path===''?'当前阅读映射，始终保留':tab.path;b.onclick=()=>{if(performance.now()-lastDrag>300)activate(tab.path).catch(error=>ctx.status(error.message))};b.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const i=all.indexOf(tab),next=event.key==='Home'?0:event.key==='End'?all.length-1:(i+(event.key==='ArrowLeft'?-1:1)+all.length)%all.length;activate(all[next].path);strip.querySelectorAll('[role=tab]')[next]?.focus()};wrap.append(b);if(tab.path!==''){wrap.append(button('close','关闭 '+tab.path.split('/').at(-1),()=>close(tab.path)));wrap.draggable=false;}strip.append(wrap)}
  strip.scrollLeft=oldScroll;if(scroll){const tab=strip.querySelector('[aria-selected=true]'),r=tab?.getBoundingClientRect(),v=strip.getBoundingClientRect();if(r&&r.left<v.left)strip.scrollLeft+=r.left-v.left;else if(r&&r.right>v.right)strip.scrollLeft+=r.right-v.right;}
 }
 let tabDrag=null,lastDrag=-1000;strip.addEventListener('dragstart',event=>event.preventDefault());
 strip.addEventListener('pointerdown',event=>{const tab=event.target.closest('.project-tab');if(!tab||tab.dataset.path===''||event.target.closest('.icon-btn')||event.button!==0)return;tabDrag={id:event.pointerId,path:tab.dataset.path,x:event.clientX,moved:false}});
 strip.addEventListener('pointermove',event=>{if(!tabDrag||tabDrag.id!==event.pointerId||!tabDrag.moved&&Math.abs(event.clientX-tabDrag.x)<10)return;if(!tabDrag.moved)strip.setPointerCapture(event.pointerId);tabDrag.moved=true;lastDrag=performance.now();event.preventDefault();const r=strip.getBoundingClientRect();if(event.clientX<r.left+18)strip.scrollLeft-=20;else if(event.clientX>r.right-18)strip.scrollLeft+=20;const target=document.elementFromPoint(event.clientX,event.clientY)?.closest('.project-tab');if(target?.dataset.path&&target.dataset.path!==''&&target.dataset.path!==tabDrag.path)reorder(tabDrag.path,target.dataset.path)});
 strip.addEventListener('pointerup',()=>{if(tabDrag?.moved)lastDrag=performance.now();tabDrag=null});strip.addEventListener('pointercancel',()=>tabDrag=null);
 function reorder(source,target){const from=tabs.findIndex(t=>t.path===source),to=tabs.findIndex(t=>t.path===target);if(from<0||to<0||from===to)return;tabs.splice(to,0,tabs.splice(from,1)[0]);persist();applyTab(false)}
 async function activate(path){if(path===''){showMain();applyTab();return}if(path===active){if(path==='')applyTab();return}saveScroll();active=path;persist();applyTab();if(path!=='')await preview(tabs.find(t=>t.path===path))}
 function close(path){const index=tabs.findIndex(t=>t.path===path);if(index<0)return;const selected=active===path;tabs.splice(index,1);if(body.dataset.filePath===path){stopMedia();body.replaceChildren();clearResources()}if(selected){clearFileWait();active='';request++;clearTimeout(poll)}persist();applyTab()}
 async function open(path,where={}){
  const identity=project;ctx.showOriginal();
  let tab=tabs.find(t=>t.path===path);
  if(!tab){tab={path,name:path.split('/').at(-1)};tabs.push(tab)}
  if(identity!==project)return false;
  if(where.line!=null||where.page!=null||where.modified){
   saveScroll();active=path;tab.location=where.line!=null?{line:where.line,end:where.end_line||where.line}:null;
   if(where.page!=null)tab.page=where.page-1;
   tab.scroll=0;applyTab();
   const ready=await preview(tab,false,where);
   if(ready&&identity===project&&active===path){persist();renderDirectory();return true}
   return false;
  }
  await activate(path);
  if(active===path&&!body.childNodes.length)await preview(tab);
  renderDirectory();return identity===project&&active===path;
 }
 async function present(intent){
  if(intent?.status!=='pending'||intent.material_id!==project||presentationId===intent.id||document.hidden||Date.now()/1000-intent.requested_at>180)return;
  presentationId=intent.id;
  let result;
  try{result=(await open(intent.path,intent))?'shown':'failed'}
  catch(error){if(project===intent.material_id)ctx.status(error.message);result='failed'}
  if(project===intent.material_id&&presentationId===intent.id){
   try{await ctx.post('/api/content/presentation',{material_id:project,file_displayed:{id:intent.id,status:result}})}
   catch(error){ctx.status(error.message)}
  }
 }
 async function preview(tab,quiet=false,where=null){
  if(!tab)return;stopMedia();body.dataset.filePath=tab.path;clearTimeout(poll);const serial=++request,identity=project,key=identity+':'+tab.path+':'+(tab.page||0);let waiting=null,keepWaiting=false;
  if(!quiet){if(fileWait?.key!==key||!body.contains(fileWait.host)){clearFileWait();clearResources();const host=el('div','project-file-wait');body.replaceChildren(host);bar.replaceChildren();const step=ReaderUI.loading(host,'正在打开文件',[['preview','读取文件状态'],['content','准备文件内容'],['render','显示文件']]),job={key,host,step,statusRead:false,finish(){step();host.remove();if(fileWait===job)fileWait=null}};fileWait=job}waiting=fileWait}
  let data;try{data=await ctx.api(url('preview',tab.path),{},waiting&&!waiting.statusRead?waiting.step.task('preview'):null);if(identity!==project||serial!==request||active!==tab.path)return;
  if(quiet){clearResources();bar.replaceChildren()}
  if(where?.modified&&where.modified!==data.modified)throw Error('文件在打开前已更新，请重新发送展示请求');
  bar.replaceChildren();const location=tab.location;
  const suffix=location?' · 第 '+location.line+(location.end===location.line?'':'–'+location.end)+' 行':'';
  const label=el('span','project-file-path',tab.path+suffix);label.title=tab.path+suffix;const download=ReaderUI.decorate(el('a','icon-btn'),'download','下载原件');download.href=url('raw',tab.path,'&download=1');download.download=data.name;download.onclick=async event=>{event.preventDefault();const a=el('a');a.href=await resource(url('raw',tab.path,'&download=1'));a.download=data.name;a.click()};bar.append(label,download);if(waiting)body.replaceChildren(waiting.host);else body.replaceChildren();
  if(data.kind==='preparing'){if(waiting){waiting.statusRead=true;waiting.step.task('preview').done();waiting.step.stage(data.message||'正在准备文件预览');waiting.step.task('content')({state:'waiting'})}else body.append(el('p','empty',data.message));if(!where){keepWaiting=true;poll=setTimeout(()=>preview(tab),650);return false}
   if((where.attempt||0)>=50)throw Error('文件还在准备中，请稍后重试');
   keepWaiting=true;
   await new Promise(resolve=>setTimeout(resolve,650));
   if(serial!==request||identity!==project||active!==tab.path){keepWaiting=false;return false}
   return preview(tab,false,{...where,attempt:(where.attempt||0)+1});
  }
  if(where?.page!=null&&(data.kind!=='pages'||where.page>data.pages))throw Error('该文件没有指定页');
  if(location&&(typeof data.text!=='string'||location.end<location.line||location.end>data.text.split('\n').length))throw Error('当前预览中没有指定的行');
  let locatedCode;
  if(waiting){waiting.step.task('preview').done();waiting.step.stage('正在读取文件内容');waiting.step.task('content')({state:'waiting'})}
  if(data.kind==='pages'){
   tab.page=Math.max(0,Math.min(data.pages-1,tab.page||0));const number=el('span','file-page-number'),image=el('img','reader-page-preview project-document-page');image.alt=data.name;image.loading='eager';image.src=await resource(url('page',tab.path,'&page='+tab.page+'&scale=1.4'),waiting?.step.task('content'));if(serial!==request||identity!==project)return;let scale=tab.scale||1;const zoom=()=>{image.style.width=(scale*100)+'%';image.style.maxWidth='none';tab.scale=scale};zoom();const prev=button('back','上一文件页',()=>{tab.page--;preview(tab);persist()}),next=button('forward','下一文件页',()=>{tab.page++;preview(tab);persist()});prev.disabled=!tab.page;next.disabled=tab.page>=data.pages-1;number.textContent=(tab.page+1)+' / '+data.pages;bar.prepend(prev,number,next,button('minus','缩小文件',()=>{scale=Math.max(.3,scale/1.2);zoom()}),button('plus','放大文件',()=>{scale=Math.min(4,scale*1.2);zoom()}),button('fit','适配文件宽度',()=>{scale=1;zoom()}));body.append(image);
   if(where){await image.decode();if(serial!==request||identity!==project)return false}
  }else if(location){
   locatedCode=window.ReaderCode?.block(data.text,{path:tab.path})||el('pre',null,data.text);
   locatedCode.classList.add('project-file-code','project-located-code');
   locatedCode.dataset.locatedLine=String(location.line);
   body.append(locatedCode);
  }else if(data.kind==='image'){const image=el('img','project-image');image.alt=data.name;image.src=await resource(url('raw',tab.path),waiting?.step.task('content'));if(serial!==request||identity!==project)return;body.append(image);if(where){await image.decode();if(serial!==request||identity!==project)return false}}
  else if(data.kind==='audio'||data.kind==='video'){const media=el(data.kind);media.controls=true;media.preload='metadata';media.src=await resource(url('raw',tab.path),waiting?.step.task('content'));if(serial!==request||identity!==project)return;body.append(media)}
  else if(data.kind==='markdown'){const block=el('div','markdown-body');block.dataset.projectFileBase=tab.path;ctx.rich(block,data.text);for(const n of block.querySelectorAll('img[src],a[href]')){const key=n.tagName==='IMG'?'src':'href',value=n.getAttribute(key);if(key==='href'){const local=ReaderProjectFiles.resolveLink(value,{project,basePath:tab.path});if(local)n.setAttribute(key,url('raw',local.path)+local.hash)}else if(value&&!/^(?:[a-z]+:|[/]|#)/i.test(value)){const local=ReaderProjectFiles.resolveLink(value,{project,basePath:tab.path});if(local)n.setAttribute(key,await resource(url('raw',local.path)))}}if(serial!==request||identity!==project||active!==tab.path)return;body.append(block);ReaderMarkdown.installSelection?.(block)}
  else if(data.kind==='table'){const table=el('table','project-preview-table');for(const [i,row]of data.rows.entries()){const tr=el('tr');for(const cell of row)tr.append(el(i?'td':'th',null,cell));table.append(tr)}body.append(table)}
  else if(data.kind==='archive'){const list=el('div','project-archive-list');for(const item of data.entries){const row=el('div');row.append(el('span',null,item.name),el('small',null,item.size+' B'));list.append(row)}body.append(list)}
  else{const pre=window.ReaderCode?.block(data.text??data.hex??data.message??'',{path:tab.path,language:data.text===undefined?'plaintext':''})||el('pre',null,data.text??data.hex??data.message??'');pre.classList.add('project-file-code');body.append(pre)}
  if(data.message)body.append(el('p','project-file-note',data.message));if(data.truncated)body.append(el('p','project-file-note','当前显示文件开头，下载原件可查看完整内容'));body.scrollTop=tab.scroll||0;
  if(locatedCode){
   const style=getComputedStyle(locatedCode),height=parseFloat(style.lineHeight)||22;
   const start=(location.line-1)*height,end=location.end*height;
   locatedCode.style.setProperty('--file-line-start',start+'px');
   locatedCode.style.setProperty('--file-line-end',end+'px');
   body.scrollTop=Math.max(0,body.scrollTop+locatedCode.getBoundingClientRect().top-body.getBoundingClientRect().top+start-body.clientHeight*.24);
  }
  if(waiting){waiting.step.task('content').done();waiting.step.task('render').done()}
  return serial===request&&identity===project&&active===tab.path;
  }catch(error){if(identity===project&&serial===request)body.replaceChildren(el('p','empty',error.message));if(where)throw error;return false}finally{if(!keepWaiting)waiting?.finish()}
 }
 function renderDirectory(){if(!ctx.material())return;top.select.value=ctx.mode();bottom.select.value=bottomMode;third.select.value=thirdMode;bottom.section.hidden=split<2;third.section.hidden=split<3;divider.hidden=split<2;divider2.hidden=split<3;ReaderUI.decorate(splitButton,split===3?'layers':split===2?'stack':'layout',split===1?'上下拆分目录':split===2?'增加第三个目录窗口':'还原单个目录窗口');splitButton.setAttribute('aria-pressed',String(split>1));applyRatio();for(const [slot,mode]of [[top,ctx.mode()],[bottom,bottomMode],[third,thirdMode]]){slot.nav.hidden=mode==='files';slot.tree.hidden=mode!=='files';if(slot.section.hidden)continue;if(mode==='files')treeUI.mount(slot);else ctx.drawOutline(slot.nav,mode)}treeUI.paint()}
 function sync(){const identity=ctx.material()?.id;if(identity===project){renderDirectory();return}clearFileWait();project=identity;request++;clearTimeout(poll);stopMedia();clearResources();tabs=[];active='';treeUI.sync(identity);let saved;try{saved=JSON.parse(localStorage.getItem('reader-project-files:'+project)||'{}')}catch{saved={}}tabs=(saved.tabs||[]).filter(t=>typeof t.path==='string').map(t=>({...t,name:t.path.split('/').at(-1)}));split=[1,2,3].includes(saved.split)?saved.split:2;weights=saved.weights?.length===3?saved.weights:[1,1,1];bottomMode=saved.bottomMode||'files';thirdMode=saved.thirdMode||'practice';if(modes.some(([key])=>key===saved.mode))ctx.setMode(saved.mode,false);else ctx.setMode('learning',false);presentationId=null;active=tabs.some(t=>t.path===saved.active)?saved.active:'';applyTab();if(active!=='')preview(tabs.find(t=>t.path===active)).catch(error=>ctx.status(error.message))}
 async function followFileLink(event){
  const link=event.target.closest?.('.markdown-body a[href]');if(!link||link.matches('[data-reader-term],[download]')||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||getSelection()?.toString().trim())return;
  const href=link.getAttribute('href'),identity=project;let target;
  try{target=ReaderProjectFiles.resolveLink(href,{project,basePath:link.closest('[data-project-file-base]')?.dataset.projectFileBase||''})}catch(error){event.preventDefault();event.stopPropagation();ctx.status(error.message);return}
  let registered;try{const u=new URL(href,location.href);if(u.origin===location.origin&&(!u.searchParams.get('material')||u.searchParams.get('material')===project))registered=u.pathname.match(/^\/content-files\/([A-Za-z0-9_-]+)\/original$/)?.[1]}catch{}
  if(!target&&!registered)return;event.preventDefault();event.stopPropagation();
  try{if(registered){const data=await ctx.api(url('tree',''));if(identity!==project)return;const path=data.source_paths?.[registered];if(!path)throw Error('这份原件尚未登记到当前项目文件目录');target={path,hash:new URL(href,location.href).hash}}if(identity===project)await open(target.path,ReaderProjectFiles.position(target.hash))}catch(error){ctx.status(error.message)}
 }
 document.addEventListener('click',followFileLink,true);
 const originalTree=$('source-files');if(originalTree)ReaderUI.decorate(originalTree,'folder','浏览项目文件');if(originalTree)originalTree.onclick=()=>{if(toolMenu.matches(':popover-open'))toolMenu.hidePopover();ctx.setMode('files');renderDirectory();ctx.showDirectory()};
 return{sync,renderDirectory,showMain,open,close,present,dispose:()=>{clearFileWait();document.removeEventListener('click',followFileLink,true);request++;clearTimeout(poll);stopMedia();clearResources();treeUI.dispose()}};
}};
