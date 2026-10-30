/* 只替换阅读文字的显示方式，原件、问题原话和保存的 Markdown 保持原样。 */
window.ReaderTerms = (() => {
  let registryKey = '', registry, registryTerms, mode = localStorage.getItem('reader-term-display') || 'full';
  const modes = [['full','中英全称'],['zh','中文全称'],['en','英文全称'],['abbr','优先缩写']];
  const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  function parts(term) {
    const name = term.display_name || term.name || '', match = name.match(/^(.*?)\s*[（(]([^（）()]+)[）)]$/);
    const prefix = (match?.[1] || name).trim(), head = prefix.match(/^([A-Za-z][A-Za-z0-9.+-]{1,14})\s+(.+)$/);
    return {id:term.id, full:name, zh:head?.[2] || prefix, en:match?.[2]?.trim() || '', abbr:head?.[1] || term.abbreviation || '', term};
  }
  function index(terms) {
    if(terms===registryTerms && registry)return registry;
    registryTerms=terms;
    const key = JSON.stringify(terms.map(t => [t.id,t.display_name,t.name,t.aliases,t.source_names]));
    if (key === registryKey) return registry;
    registryKey = key; const rows = new Map(), aliases = new Map();
    for (const term of terms) {
      const row = parts(term); rows.set(term.id,row);
      const names = [row.full,row.zh,row.en,row.abbr,...(term.aliases||[]),...(term.source_names||[])];
      if(row.en) { names.push(row.zh+'（'+row.en+'）',row.zh+' ('+row.en+')',row.zh+'('+row.en+')');
        if(row.abbr)names.push(row.zh+'（'+row.en+'，'+row.abbr+'）',row.zh+'（'+row.en+', '+row.abbr+'）'); }
      for(const raw of names) { const name = String(raw||'').trim(); if(name.length<2 || /[\r\n]/.test(name))continue;
        const prior=aliases.get(name); aliases.set(name,aliases.has(name)&&prior!==row.id ? null : row.id); }
    }
    const names=[...aliases].filter(([,id])=>id).map(([name])=>name).sort((a,b)=>b.length-a.length);
    const folded=new Map();for(const [name,id] of aliases){const key=name.toLocaleLowerCase();folded.set(key,folded.has(key)&&folded.get(key)!==id?null:id);}
    registry={rows,aliases,folded,pattern:names.length?new RegExp(names.map(escape).join('|'),'gi'):null}; return registry;
  }
  function label(row) { return mode==='zh'?row.zh:mode==='en'?(row.en||row.zh):mode==='abbr'?(row.abbr||row.zh):row.full; }
  function render(root,terms,hooks={}) {
    if(!root || !terms?.length)return; const table=index(terms); if(!table.pattern)return;
    for(const a of root.querySelectorAll('.term-link[data-term-id]')) {const row=table.rows.get(a.dataset.termId);if(row)a.textContent=label(row);}
    const folded=table.folded;
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT,{acceptNode:n=>n.parentElement?.closest('a,code,pre,.katex,.reader-math,.reader-mermaid,script,style,textarea,input,summary .question-actions,[data-no-term]')?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT});
    const texts=[];while(walker.nextNode())texts.push(walker.currentNode);
    for(const node of texts) {const value=node.textContent;table.pattern.lastIndex=0;
      const matches=[...value.matchAll(table.pattern)].filter(m=>folded.get(m[0].toLocaleLowerCase()) && !(/[A-Za-z0-9_]$/.test(value.slice(0,m.index))&&/^[A-Za-z0-9_]/.test(m[0])) && !(/[A-Za-z0-9_]$/.test(m[0])&&/^[A-Za-z0-9_]/.test(value.slice(m.index+m[0].length))));
      if(!matches.length)continue;const fragment=document.createDocumentFragment();let at=0;
      for(const match of matches) {const row=table.rows.get(folded.get(match[0].toLocaleLowerCase()));fragment.append(document.createTextNode(value.slice(at,match.index)));
        const a=document.createElement('a');a.className='term-link';a.href='#'+row.id;a.dataset.termId=row.id;a.dataset.markdownRaw=match[0];a.textContent=label(row);a.setAttribute('aria-label',row.full);
        a.addEventListener('pointerenter',e=>{if(e.pointerType==='mouse')hooks.show?.(row.id)});a.addEventListener('pointerleave',()=>hooks.release?.());a.addEventListener('focus',()=>hooks.show?.(row.id));a.addEventListener('blur',()=>hooks.release?.());
        a.onclick=e=>{e.preventDefault();e.stopPropagation();if(!getSelection()?.toString().trim())hooks.show?.(row.id,true)};
        fragment.append(a);at=match.index+match[0].length; }
      fragment.append(document.createTextNode(value.slice(at)));node.replaceWith(fragment);
    }
  }
  function setMode(value) {if(!modes.some(([id])=>id===value))return;mode=value;localStorage.setItem('reader-term-display',mode);document.dispatchEvent(new CustomEvent('reader-term-display',{detail:{mode}}));}
  function installControl(parent) {
    if(!parent||document.getElementById('term-display-control'))return;
    const button=ReaderUI.decorate(document.createElement('button'),'translation','术语显示方式');button.id='term-display-control';button.type='button';button.classList.add('icon-btn');
    const menu=document.createElement('div');menu.className='reader-select-menu term-display-menu';menu.setAttribute('popover','auto');menu.setAttribute('role','menu');
    for(const [id,name] of modes){const option=document.createElement('button');option.className='reader-select-option';option.type='button';option.textContent=name;option.dataset.mode=id;option.setAttribute('role','menuitemradio');option.onclick=()=>{setMode(id);menu.hidePopover()};menu.append(option);}
    document.body.append(menu);button.onclick=()=>{for(const option of menu.children)option.setAttribute('aria-checked',String(option.dataset.mode===mode));menu.showPopover();const r=button.getBoundingClientRect();menu.style.left=Math.max(8,Math.min(r.right-menu.offsetWidth,innerWidth-menu.offsetWidth-8))+'px';menu.style.top=(r.bottom+8)+'px'};parent.append(button);
  }
  return {render,parts,modes,setMode,installControl,get mode(){return mode}};
})();
