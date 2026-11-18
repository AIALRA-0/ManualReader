/* 原文、译文和文件标签共用高亮；先显示原代码，语法解析不阻塞页面。 */
window.ReaderCode=(()=>{
 let worker=null,serial=0,active=null,bytes=0;const queue=[],pending=new Map(),cache=new Map(),versions=new WeakMap(),waiting=new Set();
 const stats={requests:0,workerRequests:0,cacheHits:0,coalesced:0,timeouts:0};
 function remember(key,result){const size=key.length+(result.html?.length||0);if(size>4_000_000)return;cache.set(key,{result,size});bytes+=size;while(bytes>8_000_000||cache.size>96){const oldest=cache.keys().next().value;bytes-=cache.get(oldest).size;cache.delete(oldest)}}
 function finish(result){if(!active)return;clearTimeout(active.timer);const job=active;active=null;pending.delete(job.key);if(!result.timedOut)remember(job.key,result);job.resolve(result);pump()}
 function pump(){if(active||!queue.length)return;active=queue.shift();try{if(!worker){worker=new Worker('/vendor/code-highlight/worker.js?v=11.12.0-1');worker.onmessage=event=>{if(event.data.id===active?.id)finish(event.data)};worker.onerror=()=>{worker?.terminate();worker=null;finish({language:'plaintext',html:null})}}
 active.timer=setTimeout(()=>{stats.timeouts++;worker?.terminate();worker=null;finish({language:'plaintext',html:null,timedOut:true})},8000);stats.workerRequests++;worker.postMessage({id:active.id,text:active.text,options:active.options})
 }catch{worker?.terminate();worker=null;finish({language:'plaintext',html:null})}}
 function analyze(text,options={}){text=String(text??'');stats.requests++;if(text.length>1_000_000)return Promise.resolve({language:'plaintext',html:null,large:true});const key=JSON.stringify([options.language||'',options.path||'',text]);if(cache.has(key)){stats.cacheHits++;const value=cache.get(key);cache.delete(key);cache.set(key,value);return Promise.resolve(value.result)}if(pending.has(key)){stats.coalesced++;return pending.get(key)}let resolve;const promise=new Promise(done=>resolve=done);pending.set(key,promise);queue.push({id:++serial,key,text,options,resolve});pump();return promise}
 function selected(node){const selection=getSelection();if(!selection?.rangeCount||selection.isCollapsed)return false;try{return selection.getRangeAt(0).intersectsNode(node)}catch{return false}}
 function apply(item){const {node,text,version,result}=item;if(versions.get(node)!==version||node.textContent!==text)return true;if(selected(node)||node.closest('.reader-selection-source'))return false;
 if(result.html!==null&&result.html!==undefined)node.innerHTML=result.html;node.classList.add('hljs');node.dataset.codeLanguage=result.language;node.dataset.codeState=result.timedOut?'plain':'ready';node.title='代码语言：'+result.language;return true}
 document.addEventListener('selectionchange',()=>{if(!getSelection()?.isCollapsed)return;for(const item of waiting)if(apply(item))waiting.delete(item)});
 function highlight(node,options={}){const text=node.textContent,version={};versions.set(node,version);node.dataset.codeState='pending';const ready=analyze(text,options).then(result=>{const item={node,text,version,result};if(!apply(item))waiting.add(item);return result});node.codeReady=ready;return ready}
 function block(text,options={}){const pre=document.createElement('pre'),code=document.createElement('code');code.textContent=String(text??'');pre.append(code);highlight(code,options);return pre}
 function all(root,options={}){for(const code of root.querySelectorAll('pre>code')){if(code.matches('.language-mermaid')||code.dataset.codeState)continue;const marked=[...code.classList].find(c=>/^(?:language-|lang-)/.test(c));highlight(code,{...options,path:/\.(?:md|markdown|mdown)$/i.test(options.path||'')?'':options.path,language:marked?.replace(/^(?:language-|lang-)/,'')||code.dataset.codeHint||''})}return root}
 return {analyze,highlight,block,all,stats}
})();
