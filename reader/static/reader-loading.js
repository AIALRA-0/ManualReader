// 百分比来自已完成步骤与实际下载字节；等待时间不推动百分比。
window.ReaderLoading=(()=>{
 const active=new Set(),make=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=text;return n};
 const size=n=>n<1024*1024?(n/1024).toFixed(0)+' KB':(n/1024/1024).toFixed(1)+' MB';
 function create(title,steps=[]){
  const tasks=steps.map(([id,label])=>({id,label,state:'pending',loaded:0,total:0}));let host=null,view=null,timer=null,ended=false,stage=title,started=performance.now(),lastPaint=0;
  function snapshot(){const done=tasks.filter(t=>t.state==='done').length,fraction=tasks.reduce((n,t)=>n+(t.state==='done'?1:t.total>0?Math.min(1,t.loaded/t.total):0),0);return{title,stage,elapsed_ms:performance.now()-started,done,total:tasks.length,percent:tasks.length?Math.floor(fraction/tasks.length*100):null,tasks:tasks.map(t=>({...t}))}}
  function draw(force=false){
   if(!view)return;const now=performance.now();if(!force&&now-lastPaint<100)return;lastPaint=now;const s=snapshot(),current=tasks.find(t=>t.state==='retrying')||tasks.find(t=>['waiting','receiving','parsing'].includes(t.state)),parts=[];
   view.label.textContent=stage;view.elapsed.textContent='已等待 '+Math.floor(s.elapsed_ms/1000)+' 秒';
   if(s.total){view.bar.value=s.percent;view.count.textContent='步骤进度 '+s.percent+'% · '+s.done+'/'+s.total+' 步已完成'}else{view.bar.removeAttribute('value');view.count.textContent='正在等待处理结果'}
   if(current){if(current.state==='retrying')parts.push(current.label+'：第 '+current.attempt+' 次读取');else if(current.state==='waiting')parts.push(current.label+'：正在等待服务器');else if(current.state==='parsing')parts.push(current.label+'：正在整理已收到的数据');else parts.push(current.label+'：已接收 '+size(current.loaded)+(current.total?' / '+size(current.total)+'（'+Math.min(100,Math.floor(current.loaded/current.total*100))+'%）':''))}
   view.detail.textContent=parts.join('；');view.detail.hidden=!parts.length;view.bar.setAttribute('aria-label',s.total?'步骤完成比例':'正在等待处理结果');host.dataset.loadingStage=stage;
  }
  const op={mount(root,{startup=false}={}){if(ended||!root)return;host=root;const card=make('div','reader-loading-card'),heading=make('strong','reader-loading-title',title),label=make('p','reader-loading-stage',stage),bar=make('progress','reader-loading-progress'),count=make('span','reader-loading-count'),elapsed=make('span','reader-loading-elapsed'),detail=make('p','reader-loading-detail'),meta=make('div','reader-loading-meta');bar.max=100;bar.removeAttribute('value');bar.setAttribute('aria-live','off');elapsed.setAttribute('aria-hidden','true');meta.append(count,elapsed);card.append(heading,label,bar,meta,detail);if(startup){heading.id='startup-title';label.id='startup-detail';const retry=make('button','', '重新打开');retry.id='startup-retry';retry.type='button';retry.hidden=true;retry.onclick=()=>location.reload();card.append(retry)}root.replaceChildren(card);root.setAttribute('role','status');view={card,heading,label,bar,count,elapsed,detail};timer=setInterval(()=>draw(true),500);draw(true);return op},
   stage(text){stage=text;draw(true)},
   task(id){const t=tasks.find(t=>t.id===id);if(!t)return()=>{};const update=value=>{if(ended)return;Object.assign(t,value);if(['waiting','retrying','parsing'].includes(value.state))stage=t.label;draw(value.state!=='receiving')};update.label=t.label;update.done=()=>update({state:'done'});return update},
   finish(){if(ended)return;ended=true;clearInterval(timer);active.delete(op)},
   error(text){stage=text;for(const task of tasks)if(task.state!=='done')task.state='error';draw(true);op.finish();if(view){view.heading.textContent='读取尚未完成';view.count.textContent='当前步骤没有完成';const retry=view.card.querySelector('#startup-retry');if(retry)retry.hidden=false}},snapshot};active.add(op);return op;
 }
 function responseError(response,kind){
  const auth=response.status===401||/\/(?:_aialra_auth|if\/flow|application\/o\/authorize)(?:\/|$)/.test(response.url||'');
  return Object.assign(Error(auth?'登录已失效，请重新登录后再提交；当前输入保留':response.status===403?'当前账号没有访问权限，当前输入保留':'服务器没有返回有效的处理结果，请稍后重试；当前输入保留'),{status:auth?401:response.status>=400?response.status:502,code:auth?'reader_auth_required':kind,request_id:response.headers.get('X-Reader-Request-ID')||undefined});
 }
 function parseJSON(text,response){
  if(/text\/html/i.test(response.headers.get('Content-Type')||'')||/^\s*<(?:!doctype|html|head|body)\b/i.test(text))throw responseError(response,'api_html_response');
  let value;try{value=JSON.parse(text)}catch{throw responseError(response,'api_invalid_json')}
  if(!response.ok)throw Object.assign(Error(value?.error||(response.status===401?'登录已失效，请重新登录后再提交；当前输入保留':'请求未完成')),{status:response.status,code:value?.code||(response.status===401?'reader_auth_required':undefined)});
  return value;
 }
 async function readBody(response,update,binary=false,touch=()=>{}){
  if(!response.body?.getReader)return binary?new Uint8Array(await response.arrayBuffer()):parseJSON(await response.text(),response);
  const encoded=response.headers.get('Content-Encoding'),reported=Number(response.headers.get('X-Reader-Decoded-Length')),total=reported>0?reported:!encoded?Number(response.headers.get('Content-Length'))||0:0,reader=response.body.getReader(),decoder=binary?null:new TextDecoder();let loaded=0,text='';const chunks=[];
  try{while(true){const {done,value}=await reader.read();if(done)break;touch();loaded+=value.byteLength;if(binary)chunks.push(value);else text+=decoder.decode(value,{stream:true});update?.({state:'receiving',loaded,total})}}
  finally{reader.releaseLock()}
  update?.({state:'parsing',loaded,total});
  if(binary){const result=new Uint8Array(loaded);let offset=0;for(const chunk of chunks){result.set(chunk,offset);offset+=chunk.length}return result}
  return parseJSON(text+decoder.decode(),response);
 }
 async function json(url,init={},update=null,{timeoutMs=30000,retries=1}={}){
  const headers=new Headers(init.headers||{});if(!headers.has('Accept'))headers.set('Accept','application/json');init={...init,headers};
  if(!update)return readBody(await fetch(url,init));
  const retryable=(init.method||'GET').toUpperCase()==='GET',attempts=retryable?retries+1:1;
  for(let attempt=1;attempt<=attempts;attempt++){
   const controller=new AbortController();let timer=null,stalled=false;const reset=()=>{if(!retryable)return;clearTimeout(timer);timer=setTimeout(()=>{stalled=true;controller.abort()},timeoutMs)},cancel=()=>controller.abort(init.signal?.reason);
   if(init.signal?.aborted)throw init.signal.reason||new DOMException('读取已取消','AbortError');init.signal?.addEventListener('abort',cancel,{once:true});reset();update({state:attempt===1?'waiting':'retrying',loaded:0,total:0,attempt});
   try{const response=await fetch(url,{...init,signal:controller.signal});reset();const value=await readBody(response,update,false,reset);update({state:'done'});return value}
   catch(error){if(init.signal?.aborted)throw error;if(retryable&&attempt<attempts&&(stalled||error instanceof TypeError||[429,502,503,504].includes(error.status)))continue;const message=stalled?(update.label||'读取内容')+'等待过久，原页面已保留，请重新读取':error.message;throw Object.assign(Error(message),{status:error.status,code:stalled?'read_stalled':error.code,request_id:error.request_id})}
   finally{clearTimeout(timer);init.signal?.removeEventListener('abort',cancel)}
  }
 }
 const paint=()=>new Promise(resolve=>{let done=false;const finish=()=>{if(!done){done=true;resolve()}};setTimeout(finish,100);if(document.visibilityState==='visible')requestAnimationFrame(finish);else finish()});
 return{create,json,readBody,paint,snapshot:()=>[...active].map(op=>op.snapshot())};
})();
