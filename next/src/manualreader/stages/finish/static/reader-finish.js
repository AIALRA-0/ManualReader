(() => {
  const manifests = new Map();
  const generations = new WeakMap();
  const slots = new WeakMap();
  const questionResources = new Map();
  const labels = {formula_display:'公式',equation:'公式',image:'图',svg:'图',vector:'图',
    mermaid:'流程图',table:'表',code:'代码',code_block:'代码',fence:'代码'};
  const el = ReaderUI.make;

  function rich(text, ctx, tag='div') {
    const item=el(tag,'markdown-body');
    if (text) ctx.rich(item,text);
    return item;
  }

  function resourceNodes(article,item) {
    const body=article.querySelector(':scope > .chinese.markdown-body');
    if (!body) return [];
    if (['image','svg','vector'].includes(item.kind)) return [...body.querySelectorAll('img[data-markdown-raw], img[src]')];
    if (['formula_display','equation'].includes(item.kind)) return [...body.querySelectorAll('[data-math-display="block"][data-math-raw]')];
    if (item.kind==='mermaid') return [...body.querySelectorAll('.reader-mermaid[data-markdown-raw]')];
    if (item.kind==='table') return [...body.querySelectorAll('table[data-markdown-raw]')];
    return [...body.querySelectorAll('pre[data-markdown-raw]')];
  }

  function exactResource(node,item) {
    if (item.asset_url && node.tagName==='IMG') {
      const img=new URL(node.src,location.origin);
      const ref=new URL(item.asset_url,location.origin);
      return img.pathname===ref.pathname && img.searchParams.get('material')===ref.searchParams.get('material');
    }
    const current=node.dataset.mathRaw || node.dataset.markdownRaw || '';
    const normalize=s=>window.ReaderMarkdown.normalizeMathSource?.(s.trim()) || s.trim();
    return normalize(current)===normalize(item.raw_content || '');
  }

  function adjacent(node,article) {
    const paragraph=node.closest('p');
    const media=window.ReaderMediaPins?.after(node);
    if (paragraph && article.contains(paragraph)) {
      let part=media && paragraph.contains(media)?media:node;
      while (part.parentElement!==paragraph) part=part.parentElement;
      const leading=[];
      for (let sibling=paragraph.firstChild;sibling && sibling!==part;sibling=sibling.nextSibling) {
        if (!sibling.matches?.('.inline-question,[data-reader-media-controls]')) leading.push(sibling);
      }
      if (leading.some(x=>x.textContent.trim() || x.querySelector?.('img,code,math'))) {
        const before=paragraph.cloneNode(false);
        before.append(...leading);
        paragraph.before(before);
      }
      paragraph.dataset.nextResourceAnchor='true';
      const trailing=[];
      for (let sibling=part.nextSibling;sibling;sibling=sibling.nextSibling) trailing.push(sibling);
      if (trailing.some(x=>x.nodeType===Node.ELEMENT_NODE && x.tagName!=='BR' || x.textContent.trim())) {
        const next=paragraph.cloneNode(false);
        while (trailing[0]?.nodeName==='BR' || trailing[0]?.nodeType===Node.TEXT_NODE && !trailing[0].textContent.trim()) {
          trailing.shift().remove();
        }
        next.append(...trailing);
        paragraph.after(next);
      }
      if (media && article.contains(media) && !paragraph.contains(media)) return media;
      return paragraph;
    }
    const container=node.closest('.markdown-table-scroll,.reader-media-inline,figure,pre') || node;
    if (media && article.contains(media) && !container.contains(media)) return media;
    return container;
  }

  function questionExcerpt(item) {
    const name=(labels[item.kind]||'资源')+(item.ordinal?' '+item.ordinal:'');
    const context=[item.heading,item.overview].filter(Boolean).join('\n\n');
    const original=item.asset_url
      ? '!['+name+']('+item.asset_url+')'
      : item.raw_content;
    return [name,original,context].filter(Boolean).join('\n\n').slice(0,8000);
  }

  function displayElement(element,ctx,kind) {
    let raw=element.raw_content.trim();
    const formula=['formula_display','equation'].includes(kind);
    const commandName=/^\\(?:[dt]?frac|sqrt|binom|text|mathrm|mathbf|mathcal|left|right|begin|end)$/;
    const row=el('li');
    if (formula && commandName.test(raw)) {
      row.append(el('code',null,raw),rich(element.meaning,ctx));
      return row;
    }
    if (formula && !/^(?:\\[[(]|\$|\x60)/.test(raw) && !/[\u3400-\u9fff]/.test(raw) &&
        (/[_^{}\\=∑∏≤≥<>]/.test(raw) || /^[a-zA-Z](?:\s*,\s*[a-zA-Z])*$/.test(raw))) {
      raw='\\('+raw+'\\)';
    }
    row.append(rich(raw,ctx),rich(element.meaning,ctx));
    return row;
  }

  function details(item,ctx) {
    const fold=el('details','followup answer-followup inline-question reader-resource-details');
    fold.dataset.resourceId=item.resource_id;
    fold.dataset.status=item.status;
    fold.append(ReaderUI.followupSummary('展开或收起资源解读','',null,'book'));
    const inside=el('div','inline-question-body');
    fold.append(inside);
    fold.addEventListener('toggle',()=>{
      if (!fold.open || inside.childElementCount) return;
      if (item.overview) inside.append(rich(item.overview,ctx));
      if (item.components?.length) {
        const entries=el('ul');
        entries.append(...item.components.map(element=>displayElement(element,ctx,item.kind)));
        inside.append(entries);
      }
      for (const text of item.steps || []) inside.append(rich(text,ctx));
      for (const text of item.limits || []) inside.append(rich(text,ctx));
      if (item.summary) inside.append(rich(item.summary,ctx));
    });
    return fold;
  }

  const recapIcons = [
    ['context','背景','book'], ['problem','问题','message'], ['claim','结论','check'],
    ['reason','原因','graph'], ['evidence','依据','file'], ['implication','用法','target']
  ];

  function showRecaps(root, recaps, ctx) {
    const body=root.querySelector('article[data-passage-id$="--framework-outro"] > .chinese.markdown-body');
    if (!body || !recaps?.length || body.dataset.recapIcons) return;
    const list=el('ul','reader-recap-list');
    if (body.dataset.markdownSource) list.dataset.markdownRaw=body.dataset.markdownSource;
    for (const recap of recaps) {
      const item=el('li');
      item.append(ctx.rich(el('h4','reader-recap-part'),recap.title));
      for (const [key,name,icon] of recapIcons) {
        if (!recap.items[key]) continue;
        const line=el('div','reader-recap-row');
        const marker=el('span','reader-recap-icon');
        marker.title=name;
        marker.dataset.tooltip=name;
        marker.setAttribute('role','img');
        marker.setAttribute('aria-label',name);
        marker.append(ReaderUI.icon(icon));
        line.append(marker,rich(recap.items[key],ctx,'span'));
        item.append(line);
      }
      list.append(item);
    }
    body.replaceChildren(list);
    body.dataset.recapIcons='true';
    ctx.terms?.(body);
  }

  function attachPdfQuestions(pdf,pageId,material,attachInputs,bindInput) {
    if (!pdf || !material?.id) return;
    const page=pdf.closest('.reader-source-page') || pdf.parentElement;
    let box=page.querySelector(':scope > .reader-pdf-questions');
    if (box?.dataset.pageId===pageId) return;
    box?.remove();
    const indexed=new Map(material.passages.filter(p=>!p.explanation_id).map(p=>[p.id,p]));
    const groups=material.reading_view?.groups || [];
    box=el('details','reader-pdf-questions');
    box.dataset.pageId=pageId;
    box.append(el('summary',null,'本页原文提问'));
    for (const group of groups) {
      if (group.page_ids?.at(-1)!==pageId) continue;
      const passages=group.passage_ids.map(id=>indexed.get(id)).filter(p=>p&&!p.heading&&!p.duplicate_of);
      if (!passages.length) continue;
      const excerpt=passages.map(p=>p.en||p.zh||p.alt||'').filter(Boolean).join('\n');
      if (!excerpt) continue;
      const item=el('article','original-block');
      item.dataset.passageId=passages[0].id;
      item.dataset.originalGroup=group.id;
      const body=el('p',null,excerpt.length>150?excerpt.slice(0,150)+'…':excerpt);
      body.dataset.sourceExcerpt=excerpt;
      body.dataset.segmentId='original-'+group.id;
      item.append(body);
      box.append(item);
    }
    if (box.children.length===1) return;
    page.append(box);
    box.addEventListener('toggle',()=>{
      if (!box.open || box.dataset.inputsReady) return;
      attachInputs(box);
      const byId=new Map(groups.map(group=>[group.id,group.passage_ids]));
      for (const item of box.querySelectorAll('[data-original-group]')) {
        const input=item.querySelector('textarea');
        if (input) bindInput(input,byId.get(item.dataset.originalGroup),item.querySelector('p').dataset.sourceExcerpt);
      }
      box.dataset.inputsReady='true';
    });
  }

  function showStudy(root, doc, ctx) {
    if (doc.mode!=='INTRO' || !ctx.openStudy || root.querySelector('.reader-chapter-study')) return;
    const start=root.querySelector('article[data-passage-id$="--framework-start-1"]');
    if (!start) return;
    const links=el('nav','reader-chapter-study');
    for (const [tab,key,label] of [['terms','terms','术语表'],['practice','exercises','练习']]) {
      if (!doc.study?.[key]) continue;
      const button=el('button','small-btn',label+'（'+doc.study[key]+'）');
      button.type='button';
      button.onclick=()=>ctx.openStudy(tab);
      links.append(button);
    }
    if (links.children.length) start.append(links);
  }

  async function showDelivery(root, material, doc, ctx, token) {
    if (doc.mode==='ANSWER' || !doc.delivery_receipt_sha256) return;
    const base='/api/next-finish/delivery';
    const headers={'X-Reader-Material':material,'X-Reader-Finish':'isolated-test'};
    const state=await ctx.api(base,{headers}).catch(()=>null);
    if (!state || state.delivery || generations.get(root)!==token) return;
    const panel=el('div','reader-next-delivery');
    const button=el('button','small-btn','交稿');
    button.type='button';
    button.onclick=async ()=>{
      if (!window.confirm('保存这一版讲解并提交已讲解范围？')) return;
      button.disabled=true;
      const post=(path,value)=>ctx.api(base+path,{method:'POST',headers,body:JSON.stringify(value)});
      try {
        const draft=await post('/prepare',{});
        const published=await post('/commit',{preview_token:draft.preview_token,
          commit_id:draft.commit_id,confirmed:true});
        if (published.status==='applied') panel.remove();
      } catch {
        // Reader only shows confirmed changes. The service retains its receipt for retry.
      } finally {
        button.disabled=false;
      }
    };
    panel.append(button);
    root.append(panel);
  }

  async function load(material,ctx,id) {
    const key=material+'|'+id;
    if (!manifests.has(key)) {
      const fetchManifest=(async()=>{
        for (const pause of [0,180,600]) {
          if (pause) await new Promise(resolve=>setTimeout(resolve,pause));
          try { return await ctx.api('/api/postprocess-preview?explanation='+encodeURIComponent(id)); } catch {}
        }
        manifests.delete(key);
        return null;
      })();
      manifests.set(key,fetchManifest);
      fetchManifest.finally(()=>{if(manifests.get(key)===fetchManifest)manifests.delete(key)});
    }
    return manifests.get(key);
  }

  async function attach(root,material,id,ctx) {
    const token={};
    generations.set(root,token);
    if (!(id==='postprocess-preview'||/^next-chapter-[a-f0-9]{20}$/.test(id)) || !material?.id) return;
    const doc=await load(material.id,ctx,id);
    await new Promise(requestAnimationFrame);
    if (!doc || generations.get(root)!==token) return;
    showRecaps(root,doc.chapter_recaps,ctx);
    showStudy(root,doc,ctx);
    const nextReading=root.querySelector('article[data-passage-id$="--framework-next"] > .chinese');
    if (nextReading && doc.next_reading && nextReading.dataset.markdownSource!==doc.next_reading) {
      nextReading.dataset.markdownSource=doc.next_reading;
      ctx.rich(nextReading,doc.next_reading);
    }
    void showDelivery(root,material.id,doc,ctx,token);
    const blocks=new Map();
    for (const article of root.querySelectorAll('article[data-passage-id]')) {
      let aliases=[];
      try { aliases=JSON.parse(article.dataset.passageIds || '[]'); } catch {}
      for (const identity of [article.dataset.passageId,...aliases]) blocks.set(identity,article);
    }
    const used=new Set();
    const byContainer=new Map();
    const missing=[];
    for (const [block,items] of Object.entries(doc.resources || {})) {
      const article=blocks.get(id+'--'+block);
      if (!article) { missing.push(...items.map(item=>item.resource_id)); continue; }
      for (const item of items) {
        if (root.querySelector('[data-resource-id="'+CSS.escape(item.resource_id)+'"]')) continue;
        const candidates=resourceNodes(article,item).filter(node=>!used.has(node));
        const exact=candidates.find(node=>exactResource(node,item));
        const node=exact || (candidates.length===1 && items.filter(other=>other.kind===item.kind).length===1?candidates[0]:null);
        if (!node) { missing.push(item.resource_id); continue; }
        used.add(node);
        node.dataset.nextResourceBody=item.resource_id;
        const target=adjacent(node,article);
        target.dataset.nextResourceAnchor='true';
        const anchor=byContainer.get(target) || target;
        const hasExplanation=item.overview || item.components?.length || item.steps?.length || item.summary;
        const slot=el('div','reader-resource-question');
        slot.dataset.segmentId='resource:'+item.resource_id;
        slot.dataset.sourceExcerpt=questionExcerpt(item);
        questionResources.set(material.id+'|'+slot.dataset.segmentId,item);
        slot.append(el('span','sr-only','针对这项资源提问'));
        if (hasExplanation) anchor.after(details(item,ctx),slot);
        else anchor.after(slot);
        byContainer.set(target,slot);
        slots.set(article,[...(slots.get(article)||[]),slot]);
      }
    }
    root.dataset.nextUnplacedResources=JSON.stringify(missing);
    root.dataset.nextPresentationReady='true';
    ctx.inlineQuestions?.(root);
  }

  function bindTermLocation(summary, term, {showPane, pdfSources, locateSource}) {
    const selector='a[data-reader-term][data-term-id="'+CSS.escape(term.id)+'"]';
    function links(root) {
      const occurrences=[...root?.querySelectorAll(selector)||[]].filter(link=>
        !link.closest('.reader-resource-details,.reader-inline-quiz,.inline-question'));
      if (root?.id!=='explanation') return occurrences;
      const chapter=occurrences.filter(link=>link.closest('article[data-passage-id*="--block-"]'));
      return chapter.length?chapter:occurrences;
    }
    function targets() {
      const visibleText=[['explanation','explanation'],['translation','translation'],['original','original-reading']]
        .flatMap(([pane,id])=>links(document.getElementById(id)).map(node=>({pane,node})));
      return [...visibleText,...pdfSources(term.id).map(id=>({pane:'original',id}))];
    }
    let next=0;
    summary.addEventListener('click',async ()=>{
      const available=targets();
      if (!available.length) return;
      const selected=available[next++ % available.length];
      showPane(selected.pane);
      await new Promise(requestAnimationFrame);
      if (selected.id) return locateSource(selected.id);
      const target=selected.node.isConnected?selected.node:targets().find(x=>x.pane===selected.pane)?.node;
      if (!target) return;
      document.querySelector('.reader-term-located')?.classList.remove('reader-term-located');
      target.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
      void target.offsetWidth;
      target.classList.add('reader-term-located');
    });
  }

  window.ReaderPostprocessPreview={attach,attachPdfQuestions,bindTermLocation,applyQuestionContext(input,block){
    const item=questionResources.get(block.material_id+'|'+input.dataset.segmentId);
    if(!item) return;
    block.source_refs=[...new Set([block.passage_id,...(item.source_ids||[])].filter(Boolean))];
    if(!block.value.trim()) block.source_excerpt=questionExcerpt(item);
  },questionUnits(article,units){
    const resources=(slots.get(article)||[]).filter(node=>node.isConnected);
    if (!resources.length) return units;
    const anchor='[data-next-resource-anchor],[data-next-resource-body],.reader-resource-question,.reader-resource-details';
    return [...units.filter(unit=>!unit.closest(anchor)&&!unit.querySelector(anchor)),...resources];
  }};
})();
