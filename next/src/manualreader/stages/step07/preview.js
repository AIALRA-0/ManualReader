(() => {
  const data = id => JSON.parse(document.getElementById(id).textContent);
  const media = data('verified-media');
  const anchors = data('body-anchors');
  const backlinks = data('source-backlinks');
  const V = window.ReaderMarkdownVendor;
  const renderer = new V.MarkdownIt({html: false, breaks: true});
  const safe = renderer.utils.escapeHtml;
  const openers = [['\\(', '\\)', false], ['\\[', '\\]', true], ['$$', '$$', true], ['$', '$', false]];
  const isEscaped = (text, start) => {
    let i = start;
    while (i > 0 && text[i - 1] === '\\') i--;
    return (start - i) % 2 === 1;
  };
  const closeAt = (text, closer, from) => {
    for (let end = text.indexOf(closer, from); end !== -1; end = text.indexOf(closer, end + closer.length)) {
      if (!isEscaped(text, end)) return end;
    }
    return -1;
  };
  function math(source, display) {
    try {
      return window.katex.renderToString(source, {displayMode: display, throwOnError: true,
        trust: false, output: 'htmlAndMathml', strict: 'ignore'});
    } catch (_) {
      return '<code class="math-unrendered">' + safe(source) + '</code>';
    }
  }
  const image = renderer.renderer.rules.image;
  renderer.renderer.rules.image = (tokens, i, options, env, self) => {
    const token = tokens[i];
    const original = token.attrGet('src');
    const ref = Object.hasOwn(media, original) ? media[original] : '';
    if (!/^assets\/[a-f0-9]{64}\.png$/.test(ref)) return '<em>当前无法读取这张图</em>';
    token.attrSet('src', location.pathname.replace(/\/$/, '') + '/' + ref);
    token.attrSet('loading', 'lazy');
    return image(tokens, i, options, env, self);
  };
  renderer.block.ruler.before('paragraph', 'reader_math_block', (state, start, end, silent) => {
    const line = n => state.src.slice(state.bMarks[n] + state.tShift[n], state.eMarks[n]).trim();
    const first = line(start);
    const pair = openers.find(([a,,display]) => display && first.startsWith(a));
    if (!pair || state.sCount[start] - state.blkIndent >= 4) return false;
    const [opener, closer] = pair;
    let last = start;
    let source = first.slice(opener.length);
    while (!source.endsWith(closer) || isEscaped(source, source.length - closer.length)) {
      if (++last >= end) return false;
      source += '\n' + line(last);
    }
    if (silent) return true;
    const token = state.push('reader_math_block', '', 0);
    token.map = [start,last+1];
    token.content = source.slice(0,-closer.length);
    state.line = last+1;
    return true;
  }, {alt:['paragraph','reference','blockquote']});
  renderer.renderer.rules.reader_math_block = (tokens, i) => math(tokens[i].content, true) + '\n';
  renderer.inline.ruler.before('escape','reader_math_inline',(state,silent) => {
    const {src,pos} = state;
    const pair = openers.find(([a]) => src.startsWith(a,pos));
    if (!pair || isEscaped(src,pos)) return false;
    const [opener,closer,display] = pair;
    if (opener === '$' && (/\s/.test(src[pos+1] || ' ') || /\d/.test(src[pos-1] || ' '))) return false;
    const end = closeAt(src, closer, pos+opener.length);
    if (end <= pos+opener.length || end+closer.length > state.posMax
        || (opener === '$' && /\s/.test(src[end-1]))) return false;
    if (!silent) {
      const token=state.push('reader_math_inline','',0);
      token.content=src.slice(pos+opener.length,end);
      token.meta={display};
    }
    state.pos=end+closer.length;
    return true;
  });
  renderer.renderer.rules.reader_math_inline = (tokens,i) => math(tokens[i].content,tokens[i].meta.display);

  const fence=renderer.renderer.rules.fence;
  renderer.renderer.rules.fence=(tokens,i,options,env,self) => {
    const token=tokens[i];
    if (token.info.trim().split(/\s+/)[0] !== 'mermaid') return fence(tokens,i,options,env,self);
    return '<pre class="reader-diagram">' + safe(token.content) + '</pre>\n';
  };
  renderer.core.ruler.push('reader_targets',state => {
    if (!state.env.showAnchors) return;
    const indices=state.tokens.map((t,i)=>[t,i]).filter(([t])=>t.level===0 && t.map && t.type!=='inline');
    const markers=new Map();
    for (const [id,line] of Object.entries(anchors)) {
      const owner=indices.find(([t])=>t.map[0]<=line && line<t.map[1]);
      if (!owner) continue;
      const i=owner[1];
      if (!markers.has(i)) markers.set(i,[]);
      markers.get(i).push(id);
    }
    state.tokens=state.tokens.flatMap((token,i) => {
      if (!markers.has(i)) return [token];
      const marker=new state.Token('reader_anchors','',0);
      marker.meta=markers.get(i);
      return [marker,token];
    });
  });
  renderer.renderer.rules.reader_anchors=(tokens,i) => tokens[i].meta.map(id =>
    '<span class="jump-target" id="' + safe(id) + '"></span>').join('');

  const addSourceLinks = (id, container) => {
    if (!backlinks[id]?.length) return;
    const list=document.createElement('small');
    list.className='source-backlinks';
    for (const entry of backlinks[id]) {
      const link=document.createElement('a');
      link.setAttribute('href','#'+entry.sid);
      link.textContent=entry.navigation ? '仅导航原文' : '对应原文';
      link.title=entry.sid;
      list.append(link,' ');
    }
    container.after(list);
  };
  for (const article of document.querySelectorAll('article[data-markdown]')) {
    article.innerHTML=renderer.render(article.textContent,{showAnchors:article.id==='draft'});
    if (article.id !== 'draft') addSourceLinks(article.parentElement.id,article);
    article.classList.add('rendered');
  }
  const style=document.createElement('style');
  style.textContent='article.rendered{white-space:normal}.jump-target{display:block;scroll-margin-top:20px}'
    +'.jump-target.active + *{outline:2px solid #4883ac;outline-offset:4px}.source-backlinks{display:block}'
    +'article img{max-width:100%;height:auto}article table{border-collapse:collapse}'
    +'article :is(td,th){padding:.35em .6em;border:1px solid #8888}article pre{overflow:auto}';
  document.head.append(style);
  const showTarget = () => {
    const id=decodeURIComponent(location.hash.slice(1));
    const target=document.getElementById(id);
    if (!target) return;
    target.classList.add('active');
    for (let current=target; current; current=current.parentElement) {
      if (current.tagName==='DETAILS') current.open=true;
    }
    if (target.classList.contains('jump-target')) {
      target.querySelector('small')?.remove();
      const proxy=document.createElement('span');
      target.append(proxy);
      addSourceLinks(id,proxy);
    }
    target.scrollIntoView({block:'start'});
  };
  window.addEventListener('hashchange',showTarget);
  if (location.hash) showTarget();
  const diagrams=[...document.querySelectorAll('.reader-diagram')];
  if (diagrams.length) {
    const script=document.createElement('script');
    script.src='/step07/vendor/mermaid.min.js';
    script.onload=async () => {
      window.mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'neutral',flowchart:{htmlLabels:false}});
      for (const [i,node] of diagrams.entries()) {
        try {
          const result=await window.mermaid.render('step07-diagram-'+i,node.textContent);
          node.innerHTML=V.DOMPurify.sanitize(result.svg,{USE_PROFILES:{svg:true,svgFilters:true}});
          node.dataset.rendered='true';
        } catch (_) {
          node.dataset.rendered='false';
        }
      }
    };
    document.head.append(script);
  }
})();
