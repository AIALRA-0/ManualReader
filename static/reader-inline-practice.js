
window.ReaderObjectiveInput={create({quiz:q,value,disabled=false,onchange=()=>{}}){
 const make=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n},root=make('div'),controls=[];let current=value;
 if(q.type==='code'){const editor=make('textarea','sequence-code-editor');editor.setAttribute('aria-label','编写 SystemVerilog 代码');editor.value=value??q.code_draft??q.starter_code;editor.spellcheck=false;editor.disabled=disabled;editor.oninput=()=>{current=editor.value;onchange(current)};controls.push(editor);root.append(ReaderCodeEditor.wrap(editor));current=editor.value;
 }else if(q.type==='numeric'){const row=make('div','trial-numeric-row'),input=make('input');input.type='text';input.inputMode='decimal';input.setAttribute('aria-label','填写数值答案');input.value=value??'';input.disabled=disabled;input.oninput=()=>{current=input.value;onchange(current)};controls.push(input);row.append(input);if(q.unit)row.append(make('span',null,q.unit));root.append(row);current=input.value;
 }else{current=structuredClone(value??q.items.map(i=>i.id).reverse());const names=new Map(q.items.map(i=>[i.id,i.text])),list=make('div','trial-sort');function draw(){list.replaceChildren();current.forEach((id,i)=>{const row=make('div','trial-sort-row');row.dataset.stepId=id;row.append(make('span','trial-sort-label',(i+1)+' · '+names.get(id)));for(const [delta,glyph,label]of [[-1,'↑','上移'],[1,'↓','下移']]){const b=make('button',null,glyph);b.type='button';b.setAttribute('aria-label',label+'：'+names.get(id));b.disabled=disabled||i+delta<0||i+delta>=current.length;b.onclick=()=>{const j=i+delta;[current[i],current[j]]=[current[j],current[i]];onchange(structuredClone(current));draw()};row.append(b)}list.append(row)})}draw();root.append(list)}
 return {element:root,value:()=>current,disable:()=>{disabled=true;root.querySelectorAll('input,button').forEach(n=>n.disabled=true)}};
}};
/* 正文和练习栏用同一个组件，按题号读取同一份答案与作答记录。 */
window.ReaderInlinePractice={create(ctx){
 const make=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n};
 let queued=false;const pending=new Set(),roots=['explanation','interaction','study'].map(id=>document.getElementById(id));
 function queue(){if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;paint()})}
 function quiz(identity){return ctx.state()?.quizzes?.find(q=>q.id===identity)}
 const drafts=new Map(),revealed=new Set(),errors=new Map();
 function widget(identity,location,index=null){
  const holder=make('section','quiz-card reader-inline-quiz');holder.dataset.inlineQuizId=identity;holder.dataset.quizLocation=location;if(location==='study'){holder.dataset.quizId=identity;holder.dataset.quizIndex=index;}
  holder.addEventListener('click',e=>e.stopPropagation());holder.addEventListener('pointerdown',e=>e.stopPropagation());
  holder.update=()=>{
   const q=quiz(identity);if(!q)return;const signature=JSON.stringify([q,pending.has(identity),!!ctx.autoCheck?.(),ctx.practice?.has(identity),errors.get(identity),holder.dataset.quizIndex]);if(holder.dataset.signature===signature)return;holder.dataset.signature=signature;
   const objective=['numeric','ordering','code'].includes(q.type),recall=q.type==='recall',submitted=q.selected!==null&&q.selected!==undefined,title=ctx.rich(make('h3'),(location==='study'?(Number(holder.dataset.quizIndex)+1)+' · ':'')+q.prompt,true),options=make('div','quiz-options'),feedback=make('div','quiz-answer inline-quiz-feedback'),actions=make('div','control-group'),nodes=[title];feedback.hidden=!submitted&&!errors.has(identity);
   if(location==='study'&&ctx.locate){title.classList.add('quiz-origin-link');title.tabIndex=0;title.setAttribute('role','button');title.title='回到讲解或回答中的这道练习';title.setAttribute('aria-label','定位原题：'+q.prompt);const locate=()=>Promise.resolve(ctx.locate(identity)).catch(error=>{errors.set(identity,error.message);queue()});title.onclick=event=>{if(event.target.closest('a'))return;event.preventDefault();locate()};title.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();locate()}}}
   const order=q.display_order||q.options.map((_,i)=>i);let selected=drafts.get(identity)??q.selected??null;
   const submit=async(value,response)=>{
    if(pending.has(identity))return;pending.add(identity);errors.delete(identity);queue();
    try{const receipt=await ctx.post('/api/quizzes/'+identity+'/answer',{selected:value,...(recall||objective?{response}: {})});q.selected=receipt.selected??value;drafts.delete(identity);if(recall||objective){q.response=response;q.last_grade=receipt}queue();await ctx.refresh();document.dispatchEvent(new CustomEvent('reader-inline-quiz-saved',{detail:{quiz_id:identity,selected:value,correct:receipt.correct}}))}catch(e){errors.set(identity,e.message)}finally{pending.delete(identity);queue()}
   };
   const button=make('button','small-btn','核对答案');button.type='button';button.hidden=!recall&&!objective&&!!ctx.autoCheck?.();button.disabled=pending.has(identity);
   if(objective){
    nodes.push(make('small','trial-badge',q.type==='code'?'SystemVerilog · 编译与测试':q.type==='numeric'?'数值填空':'步骤排序'));
    if(q.type==='code'){
      const editor=make('textarea','sequence-code-editor');editor.setAttribute('aria-label','编写 SystemVerilog 代码');editor.spellcheck=false;editor.value=drafts.get(identity)??q.code_draft??q.response??q.starter_code;editor.disabled=pending.has(identity);let timer;editor.oninput=()=>{drafts.set(identity,editor.value);clearTimeout(timer);timer=setTimeout(()=>ctx.post('/api/quizzes/'+identity+'/draft',{code:editor.value}).catch(e=>errors.set(identity,e.message)),650)};editor.onkeydown=e=>{if(e.key==='Tab'){e.preventDefault();const start=editor.selectionStart,end=editor.selectionEnd;editor.setRangeText('    ',start,end,'end');editor.dispatchEvent(new Event('input'))}};nodes.push(ReaderCodeEditor.wrap(editor));button.textContent=pending.has(identity)?'正在编译与测试':'运行测试';button.onclick=()=>{clearTimeout(timer);return submit(null,editor.value)};
    }else if(q.type==='numeric'){
      const row=make('div','trial-numeric-row'),input=make('input');input.type='text';input.inputMode='decimal';input.setAttribute('aria-label','填写数值答案');input.placeholder='填写数值、小数或分数';input.value=drafts.get(identity)??q.response??'';input.disabled=pending.has(identity);input.oninput=()=>drafts.set(identity,input.value);input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();button.click()}};row.append(input);if(q.unit)row.append(make('span',null,q.unit));nodes.push(row);button.onclick=()=>submit(null,input.value);
    }else{
      const list=make('div','trial-sort');list.setAttribute('aria-label','排列步骤');let order=structuredClone(drafts.get(identity)??q.response??q.items.map(i=>i.id).reverse());const names=new Map(q.items.map(i=>[i.id,i.text]));
      function arrange(){list.replaceChildren();order.forEach((id,i)=>{const row=make('div','trial-sort-row');row.dataset.stepId=id;row.append(make('span','trial-sort-label',(i+1)+' · '+names.get(id)));for(const [delta,glyph,label]of [[-1,'↑','上移'],[1,'↓','下移']]){const b=make('button',null,glyph);b.type='button';b.title=label;b.setAttribute('aria-label',label+'：'+names.get(id));b.disabled=pending.has(identity)||i+delta<0||i+delta>=order.length;b.onclick=()=>{const j=i+delta;[order[i],order[j]]=[order[j],order[i]];drafts.set(identity,structuredClone(order));arrange()};row.append(b)}list.append(row)})}arrange();nodes.push(list);button.onclick=()=>submit(null,order);
    }
   }else if(recall){
    const response=make('textarea','quiz-response');response.setAttribute('aria-label','你的作答');response.placeholder='写下答案，或先在纸上完成，再核对';response.value=drafts.get(identity)??q.response??'';response.disabled=pending.has(identity);response.dataset.responseFor=identity;response.oninput=()=>{drafts.set(identity,response.value);for(const peer of document.querySelectorAll('textarea.quiz-response'))if(peer!==response&&peer.dataset.responseFor===identity)peer.value=response.value};nodes.push(response);
    const reference=make('details','quiz-reference'),summary=make('summary',null,'参考答案'),grades=make('div','control-group quiz-self-grade');ReaderUI.disclosure(summary);reference.append(summary,ctx.rich(make('div','quiz-explanation'),q.reference_answer,true));reference.open=submitted||revealed.has(identity);nodes.push(reference);
    for(const [value,label]of [[0,'还没答对'],[1,'答对了']]){const grade=make('button','small-btn',label);grade.type='button';grade.disabled=pending.has(identity);grade.setAttribute('aria-pressed',String(q.selected===value));grade.onclick=()=>submit(value,response.value);grades.append(grade)}grades.hidden=!reference.open;reference.addEventListener('toggle',()=>{grades.hidden=!reference.open;if(reference.open)revealed.add(identity);else revealed.delete(identity)});button.onclick=()=>{reference.open=true;revealed.add(identity);grades.hidden=false};nodes.push(grades);
   }else{
    order.forEach((actual,index)=>{const label=make('label'),input=make('input');input.type='radio';input.name='quiz:'+location+':'+identity;input.value=String(actual);input.checked=selected===actual;input.disabled=pending.has(identity);const text=ctx.rich(make('span','quiz-option-text'),String.fromCharCode(65+index)+' · '+q.options[actual],true),verdict=make('span','quiz-verdict');input.onchange=()=>{selected=actual;drafts.set(identity,actual);feedback.hidden=true;for(const l of options.children){l.classList.remove('is-correct','is-wrong');l.querySelector('.quiz-verdict').textContent=''}if(ctx.autoCheck?.())button.click()};label.append(input,text,verdict);if(submitted){label.classList.toggle('is-correct',actual===q.correct_index);label.classList.toggle('is-wrong',actual===q.selected&&actual!==q.correct_index);verdict.textContent=actual===q.correct_index?'✓':actual===q.selected?'×':''}options.append(label)});nodes.push(options);
    button.onclick=()=>{if(selected===null){feedback.hidden=false;feedback.textContent='先选一项';return}return submit(selected)};
   }
   actions.append(button);
   if(ctx.practice){const keep=make('button','icon-btn');keep.type='button';const paint=()=>{const added=ctx.practice.has(identity);ReaderUI.decorate(keep,added?'check':'book',added?'已加入错题本':'加入错题本');keep.disabled=!!added;keep.setAttribute('aria-pressed',String(!!added))};paint();keep.onclick=async()=>{keep.disabled=true;try{await ctx.practice.add(identity);paint()}catch(e){errors.set(identity,e.message);queue()}};actions.append(keep)}
   if(submitted){feedback.append(make('p','quiz-feedback',q.selected===q.correct_index?'😊 答对了':'🤔 再想想'));const text=objective?[(q.last_grade||{}).correction,q.explanation].filter(Boolean).join('\n\n'):recall?q.explanation:ReaderUI.quizFeedback(q,q.selected,order);feedback.append(ctx.rich(make('div','quiz-explanation'),text,true))}if(errors.has(identity)){feedback.hidden=false;feedback.append(make('p',null,errors.get(identity)))}
   if(q.type==='code'&&q.last_grade){const report=make('details','sequence-test-report'),summary=make('summary',null,(q.last_grade.passed??0)+' / '+(q.last_grade.total??0)+' 项测试通过');ReaderUI.disclosure(summary);const output=make('pre');output.textContent=JSON.stringify(q.last_grade.first_failure||{status:q.last_grade.status,compile_seconds:q.last_grade.compile_seconds,simulation_seconds:q.last_grade.simulation_seconds},null,2);report.append(summary,output);feedback.append(report)}nodes.push(actions,feedback);const pane=holder.closest('#explanation,#interaction,#study'),top=pane?.scrollTop;holder.replaceChildren(...nodes);if(pane&&pane.scrollTop!==top)pane.scrollTop=top;ctx.linkTerms?.(holder);
  };holder.update();return holder;
 }
 function summary(root,items){
  const ids=new Set(items.map(q=>q.id));for(const child of [...root.children])if(!ids.has(child.dataset?.quizId))child.remove();
  for(const [index,q]of items.entries()){let card=[...root.children].find(c=>c.dataset.quizId===q.id);if(!card)card=widget(q.id,'study',index);card.dataset.quizIndex=index;if(root.children[index]!==card)root.insertBefore(card,root.children[index]||null);card.update()}ctx.linkTerms?.(root);queue();
 }
 function attach(target,ids,location){
  let host=target;if(ids.length>2){host=[...target.children].find(n=>n.classList?.contains('reader-inline-practice-group'));if(!host){host=make('details','reader-inline-practice-group');const summary=make('summary',null,'练习 · '+ids.length+' 道');ReaderUI.disclosure(summary);host.append(summary);host.addEventListener('toggle',queue);target.append(host)}if(!host.open)return;}
  for(const id of ids){if(!quiz(id))continue;let card=[...host.children].find(n=>n.dataset?.inlineQuizId===id);if(!card){card=widget(id,location);host.append(card)}else card.update()}
 }

 function paint(){
  if(!ctx.state()||!ctx.material())return;const placement=ctx.state().inline_practice;if(!placement)return;
  for(const [pid,ids]of Object.entries(placement.lesson||{})){const article=[...document.querySelectorAll('#explanation article[data-passage-id]')].find(n=>n.dataset.passageId===pid);if(article)attach(article,ids,pid)}
  for(const [qid,parts]of Object.entries(placement.answers||{})){
   const card=document.getElementById(qid),body=card?.querySelector(':scope > .answer-section > .question-answer');if(!body||body.dataset.hydrated!=='true')continue;
   const q=ctx.state().rounds.flatMap(r=>r.questions).find(q=>q.id===qid);if(!q)continue;
   if(!q.answer_parts?.length||q.answer_parts.some(p=>!p.markdown&&!p.direct_answer&&!p.explanation)){attach(body,Object.values(parts).flat(),qid+':__answer__');continue;}
   if(parts.__answer__){attach(body,parts.__answer__,qid+':__answer__');continue;}
   const signature=JSON.stringify(q.answer_parts);
   if(body.dataset.inlineParts!==signature){body.dataset.inlineParts=signature;body.replaceChildren();for(const [i,part]of q.answer_parts.entries()){const block=make('div','reader-answer-part');block.dataset.answerPartId=part.id||'ap'+(i+1);if(q.answer_parts.length>1)block.append(ctx.rich(make('h3'),part.question_span));block.append(ctx.rich(make('div','reader-answer-prose'),part.markdown||[part.direct_answer,part.explanation].filter(Boolean).join('\n\n'),true));body.append(block);ctx.linkTerms?.(block)}}
   for(const part of body.children){const ids=parts[part.dataset.answerPartId]||[];attach(part,ids,qid+':'+part.dataset.answerPartId)}
  }
  for(const card of document.querySelectorAll('.reader-inline-quiz'))card.update?.();
 }
 const observer=new MutationObserver(records=>{if(records.some(r=>!r.target.closest?.('.reader-inline-quiz')))queue()});for(const root of roots)observer.observe(root,{childList:true,subtree:true});
 document.addEventListener('reader-practice-change',queue);document.addEventListener('reader-code-editor-closed',()=>{for(const card of document.querySelectorAll('.reader-inline-quiz'))card.dataset.signature='';queue()});
 return {sync:queue,summary};
}};
