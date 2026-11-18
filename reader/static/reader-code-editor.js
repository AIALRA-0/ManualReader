/* 练习正文、汇总和错题复习共用这一处代码展开方式。 */
window.ReaderCodeEditor={wrap(input){
 const wrap=document.createElement('div');wrap.className='sequence-code-wrap';input.wrap='off';wrap.append(input);
 const expand=document.createElement('button');expand.type='button';expand.className='icon-btn sequence-code-expand';ReaderUI.decorate(expand,'external','展开代码编辑器');wrap.append(expand);
 expand.onclick=()=>{
  const dialog=document.createElement('dialog');dialog.className='sequence-code-dialog';
  const head=document.createElement('div');head.className='dialog-head';const title=document.createElement('h2');title.textContent='编写本题代码';const close=document.createElement('button');close.type='button';close.className='icon-btn';ReaderUI.decorate(close,'close','完成编辑');close.onclick=()=>dialog.close();head.append(title,close);
  const editor=document.createElement('textarea');editor.className='sequence-code-editor';editor.setAttribute('aria-label','展开的代码编辑器');editor.wrap='off';editor.spellcheck=false;editor.value=input.value;editor.disabled=input.disabled;
  editor.oninput=()=>{input.value=editor.value;input.dispatchEvent(new Event('input',{bubbles:true}))};editor.onkeydown=e=>{if(e.key==='Tab'){e.preventDefault();editor.setRangeText('    ',editor.selectionStart,editor.selectionEnd,'end');editor.dispatchEvent(new Event('input'))}};
  dialog.append(head,editor);document.body.append(dialog);dialog.addEventListener('close',()=>{dialog.remove();document.dispatchEvent(new Event('reader-code-editor-closed'))});dialog.showModal();editor.focus();
 };
 return wrap;
}};
