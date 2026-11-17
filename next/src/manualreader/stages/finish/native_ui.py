"""Load the established Reader and connect its native components to Next data."""

from importlib.resources import files
import re

from .preview import PREVIEW_WRITING_VERSION


ASSETS=files('manualreader.stages.finish').joinpath('static')


def substitute(source,pattern,replacement):
    changed,count=re.subn(pattern,lambda match:replacement,source,count=1,flags=re.S)
    if count!=1:
        raise ValueError('Reader 已更新，需要核对现有组件的接线位置')
    return changed


def install(static, legacy):
    page=static/'index.html'
    index=page.read_text('utf8')
    index=re.sub(r'<script defer src="/reader-handoff\.js"></script>','',index,count=1)
    index=re.sub(r'<button class="icon-btn reader-handoff-trigger"[^>]*></button>','',index,count=1)
    scripts=('<script defer src="/reader-finish-math.js"></script>'
             '<script defer src="/reader-postprocess-preview.js"></script>')
    hook='<link rel="stylesheet" href="/reader-postprocess-preview.css">'
    if '<script defer src="/reader-postprocess-preview.js"></script>' in index:
        index=substitute(index,r'<script defer src="/reader-postprocess-preview\.js"></script>',scripts)
    else:
        index=substitute(index,re.escape(hook),hook+scripts)
    page.write_text(index,'utf8')

    page=static/'app.js'
    app=page.read_text('utf8')
    if 'ReaderHandoff.mount' in app:
        app=substitute(app,r' const writingHandoff=ReaderHandoff\.mount\(.*?\n \};\n(?= const current=)', '')
        app=substitute(app,r"  const handoff=\(\)=>writingHandoff\([^\n]+\);\n",'')
        app=substitute(app,r",\['导出给 ChatGPT',handoff\]",'')
    app=substitute(app,r'(?<=   )for\(const unit of units\)\{const copy=unit\.cloneNode\(true\);',
        """units=window.ReaderPostprocessPreview?.questionUnits(article,units)??units;
   for(const unit of units){const copy=unit.cloneNode(true);""")
    app=substitute(app,r'!p\.progress_exempt\|\|p\.inline_questions',
        "!p.progress_exempt||p.inline_questions||(docId.startsWith('next-final-')&&/^postprocess-preview--(?:block-|source-extra-)/.test(p.id))")
    app=substitute(app,r'function pendingTranslation\(\)\{return document\.createDocumentFragment\(\)\}',
        "function pendingTranslation(){return el('span','reader-translation-pending','待翻译')}")
    # 资源详解和回答追问共用同一条折叠栏，仍分别保留各自的内容。
    app=substitute(app,r"function answerFollowup\(q,segment,excerpt,legacy=false\)\{\n  const box=.*?box\.append\(label\);",
        """function answerFollowup(q,segment,excerpt,legacy=false){
  const box=el('details','followup answer-followup inline-question'),notice=el('span','question-sync-status');
  const label=ReaderUI.followupSummary('针对这段回答提问','',notice),input=el('textarea');
  box.append(label);""")
    app=substitute(app,r"root\.querySelectorAll\('\.inline-question'\)\)if\(!retained\.has\(old\)\)old\.remove\(\)",
        "root.querySelectorAll('.inline-question:not(.reader-resource-details)'))if(!retained.has(old))old.remove()")
    app=substitute(app,r'(?:\}node\.scrollTop=scroll;\n \}\n)(?= function renderOriginal)',
        """}attachInlineQuestions(node);sourceFlow?.reflow(node);linkTerms(node);node.scrollTop=scroll;
 }
""")
    app=substitute(app,r'sourceFlow\?\.activate\(pageId\);const highlights',
        """sourceFlow?.activate(pageId);
   window.ReaderPostprocessPreview?.attachPdfQuestions($('pdf-page'),pageId,material,attachInlineQuestions,
     (input,ids,excerpt)=>{const question=inputBlock(input);if(!question.value.trim()){question.source_refs=ids;question.source_excerpt=excerpt}});
   const highlights""")
    app=substitute(app,r"const value=await api\('/api/coverage\?summary=true'\);",
        """const [value,next]=await Promise.all([api('/api/coverage?summary=true'),
          docId.startsWith('next-final-')?api('/api/postprocess-preview').catch(()=>null):null]);
        if(next){value.nextTranslation=next.translation_processing;value.nextExplanation=next.source_coverage}""")
    app=substitute(app,r"target\.textContent='已读 '\+read\+'% · 译文 '.*?target\.title='[^']+';",
        """const t=value.nextTranslation,e=value.nextExplanation;
        const ratio=(done,total)=>total?(100*done/total).toFixed(2)+'%':'—';
        target.textContent='已读 '+percent(state.reading_progress?.overall??{percent:read})+
          ' · 译文处理 '+(t?ratio(t.processed,t.total):percent(value.overall.translation))+
          ' · 讲解关联 '+(e?ratio(e.direct_semantic_count,e.total):percent(value.overall.explanation));
        target.title=t&&e
          ? '译文处理 '+t.processed+'/'+t.total+'，实际翻译 '+t.translated+'，原样保留 '+t.preserved+
            '，待核 '+t.uncertain+'，未完成 '+t.failed_source_ids.length+
            '\\n讲解关联 '+e.direct_semantic_count+'/'+e.total+'，只计算实质讲解关联。另有 '+e.navigation_only_count+' 条只用于定位。'
          : '按保存的原文、译文、讲解关联和手动已读记录自动计算';""")
    app=substitute(app,r'inlineQuestions:attachInlineQuestions,sourceFlow:',
        """inlineQuestions:attachInlineQuestions,openStudy:tab=>{
          if(mobile)selectPanel('study');
          else if(!visible.study){visible.study=true;setLayout()}
          if(tab==='terms'){restoreTerms();termHoverId=null;termRestore=null;termQuery=''}
          document.querySelector('[data-study="'+tab+'"]')?.click();
        },sourceFlow:""")
    app=substitute(app,r'termHoverId=null,termRestore=null,termTimer=null,',
        'termHoverId=null,termRestore=null,termTimer=null,termClickId=null,')
    app=substitute(app,r'function showTerm\(id,force=false\)\{',
        """function showTerm(id,force=false){
      if(!force&&termClickId)return;
      if(force){
        termClickId=id;
        if(mobile&&mobilePanel!=='study')selectPanel('study');
        else if(!mobile&&!visible.study){visible.study=true;setLayout()}
      }
      """)
    app=substitute(app,r'function releaseTerm\(\)\{clearTimeout\(termTimer\);if\(!ReaderUI\.hoverAuto\)',
        'function releaseTerm(){clearTimeout(termTimer);if(termClickId||!ReaderUI.hoverAuto)')
    app=substitute(app,r'function restoreTerms\(\)\{clearTimeout\(termTimer\);',
        'function restoreTerms(){clearTimeout(termTimer);termClickId=null;')
    app=substitute(app,r'termHoverId=null;termRestore=null;termQuery=\x27\x27;syncQuestionScope\(\)',
        "termHoverId=null;termRestore=null;termClickId=null;termQuery='';syncQuestionScope()")
    app=substitute(app,r"(?<=ReaderUI\.accordion\(d,'terms',t\.id,termOpen\);)",
        """window.ReaderPostprocessPreview?.bindTermLocation(d.querySelector('summary'),t,{
        showPane:pane=>{
          if(mobile?mobilePanel!==pane:!contentUI.snapshot().columns.flat().includes(pane))selectPanel(pane);
        },
        pdfSources:id=>{
          const selected=new Set(contentUI.sourceLinks().map(link=>link.passage_id));
          const file=contentUI.getFile()?.id;
          return material.passages.filter(p=>!p.explanation_id&&!p.duplicate_of&&
            (selected.size?selected.has(p.id):p.file_id===file)&&p.refs?.some(ref=>ref.page_id)&&
            ReaderTerms.includes(state.terms,id,p.en||p.raw_text||p.zh||'')).map(p=>p.id);
        },
        locateSource:id=>sourceFlow?.link({passage_id:id},'term-location',{toggle:false})
      });""")
    page.write_text(app,'utf8')

    page=static/'reader-terms.js'
    terms=page.read_text('utf8')
    term_match=r'const matches=(\[\.\.\.value\.matchAll\(table\.pattern\)\]\.filter\(m=>[^;]+\));'
    legacy_match=re.search(term_match,terms)
    terms=substitute(terms,r'table\.pattern\.lastIndex=0;'+term_match,'const matches=findMentions(table,value);')
    terms=substitute(terms,r'\breturn \{render,parts,modes,settings,',
        """function findMentions(table,value){
   if(!table.pattern)return [];
   table.pattern.lastIndex=0;
   return """+legacy_match.group(1)+""";
 }
 function includes(terms,id,value){
   const table=index(terms);
   return findMentions(table,value).some(match=>table.rows[match.slice(1).findIndex(group=>group!==undefined)]?.id===id);
 }
 return {render,parts,modes,settings,includes,""")
    page.write_text(terms,'utf8')

    page=static/'reader-ui.js'
    ui=page.read_text('utf8')
    ui=substitute(ui,r'function coveragePercent\(metric\)\{[^\n]+',
        """function coveragePercent(metric){
 const value=metric?.total>0&&Number.isFinite(metric.done)?100*metric.done/metric.total:metric?.percent;
 return value==null?['complete','not_required'].includes(metric?.status)?'100.00%':'—':Number(value).toFixed(2)+'%';
}""")
    ui=substitute(ui,r"svg\.setAttribute\('aria-hidden','true'\);",
                  "svg.setAttribute('aria-hidden','true');svg.setAttribute('data-reader-icon','');")
    ui=substitute(ui,r'if\(!target\|\|target===tooltipOwner\)return;clearTimeout\(tooltipTimer\);tooltipTimer=setTimeout\(\(\)=>showTooltip\(target\),180\)',
        "if(!target||target===tooltipOwner||target.contains(e.relatedTarget))return;clearTimeout(tooltipTimer);tooltipTimer=setTimeout(()=>showTooltip(target),80)")
    page.write_text(ui+"""
ReaderUI.followupSummary=function(accessibleName,name,notice,icon='message'){
    const label=ReaderUI.make('summary');
    if (icon==='message') label.title=accessibleName;
    label.setAttribute('aria-label',accessibleName);
    label.append(ReaderUI.icon(icon),ReaderUI.icon('chevron'));
    if(name)label.append(ReaderUI.make('span',null,name));
    label.append(ReaderUI.make('span','inline-question-line'),notice||ReaderUI.make('span','question-sync-status'));
    return label;
};
""",'utf8')

    page=static/'app.css'
    css=page.read_text('utf8')
    # 沿用追问栏已有的行高、间距和图标大小。
    patched,count=re.subn(r'\.question-card \.answer-followup(?=\.inline-question|[>{])',
        ':is(.question-card .answer-followup,.reader-resource-details)',css)
    if not count:
        raise ValueError('Reader 已更新，需要核对追问栏的样式接线位置')
    patched=substitute(patched,
        r'\.reader-media-inline:has\(\.reader-math,\.katex,math\)>\.reader-media-number\{[^}]*\}\s*'
        r'\.reader-media-inline:has\(\.reader-math,\.katex,math\)>\.reader-media-number>\.reader-media-name\{[^}]*\}',
        """.reader-media-number{max-width:100%;flex-wrap:wrap}
.reader-media-number>.reader-media-label{flex:none}
.reader-media-number>.reader-media-name{flex:0 1 auto}
#explanation .reader-resource-details > summary{color:var(--muted)}""")
    patched+="""
button,button.secondary{background:var(--surface);color:var(--text)}
button:where(:hover:not(:disabled)),button.secondary:where(:hover:not(:disabled)){background:var(--hover)}
.trial-sort{margin:.45rem 0;gap:.25rem}
.trial-sort-row{display:grid;grid-template-columns:minmax(0,1fr) repeat(2,2rem);gap:.4rem;padding:.3rem 0}
.trial-sort-label{overflow-wrap:anywhere}
.trial-sort-row button{display:grid;place-items:center;padding:0;margin:0;inline-size:100%;block-size:2rem;
  background:var(--hover);color:var(--text);font-size:1rem;line-height:1;font-weight:500}
.trial-sort-row button:hover:not(:disabled){background:var(--selected)}
.trial-sort-row button:disabled{background:transparent;color:var(--muted)}
@media(pointer:coarse){
  .trial-sort-row{grid-template-columns:minmax(0,1fr) repeat(2,2.75rem)}
  .trial-sort-row button{block-size:2.75rem}
}
.reader-recap-list{list-style:none;padding:0}
.reader-recap-list>li+li{margin-top:1rem}
.reader-recap-list .reader-recap-part{margin:0 0 .4rem;font-size:1em;font-weight:650}
.reader-recap-row{display:flex;align-items:baseline;gap:.55rem;margin:.35rem 0}
.reader-recap-icon{flex:none;color:var(--muted);cursor:help}
.reader-recap-icon svg{height:15px;width:15px;vertical-align:middle}
a.reader-term-located{animation:reader-term-location 2.8s ease-out}
@keyframes reader-term-location{0%,65%{background-color:var(--selected);outline:1px solid var(--muted)}}
.reading-handle.vertical::after{content:"";position:absolute;inset:calc(50% - 12px) 3px;height:24px;
  border-radius:2px;background:var(--muted);opacity:.46}
.reading-handle.vertical:is(:hover,:focus-visible)::after{opacity:1}
.reader-pdf-questions{padding:.45rem .65rem}
.reader-pdf-questions>.original-block{padding:.25rem 0}
.reader-chapter-study{display:flex;flex-wrap:wrap;gap:.4rem;margin:.8rem 0}
.reader-next-delivery{display:flex;align-items:center;gap:.75rem;margin:1rem 0}
.reader-translation-equation{display:grid;grid-template-columns:1fr auto;align-items:center;gap:.7rem;margin:.7rem 0}
.reader-translation-equation>.reader-source-crop{max-width:100%;width:auto;height:auto;aspect-ratio:auto!important;justify-self:center}
.reader-equation-number{color:var(--muted);white-space:nowrap}
"""
    page.write_text(patched,'utf8')

    page=static/'reader-media-pins.js'
    media=page.read_text('utf8')
    media=substitute(media,
        r"if\(node\.parentElement\.closest\(atomic\)\|\|node\.closest\('\.inline-question,\.reader-inline-quiz,\[data-reader-media-controls\]'\)\)continue;",
        """if(node.parentElement.closest(atomic)||node.closest('.inline-question,.reader-inline-quiz,[data-reader-media-controls],.question-actions')||
      node.hasAttribute('data-reader-icon'))continue;""")
    page.write_text(media,'utf8')

    page=static/'workspace-ui.js'
    workspace=page.read_text('utf8')
    workspace=substitute(workspace,r"title\.textContent=heading;a\.append\(title\)",
        "if(e.writing_version===NEXT_PREVIEW_VERSION)ctx.rich(title,heading);else title.textContent=heading;a.append(title)")
    workspace=substitute(workspace,r"if\(step\.premise\)row\.append.*?if\(step\.question\)row\.append\(el\('p','reader-route-question',step\.question\)\);",
        """if(owner.writing_version===NEXT_PREVIEW_VERSION){
    ctx.rich(row.firstElementChild,step.label);
    if(step.description)row.append(ctx.rich(el('p','reader-route-question'),step.description));
   }else{
    if(step.premise)row.append(el('p','reader-route-premise',step.premise.trim().replace(/[。；;，,：:]+$/,'')+'；'));
    if(step.question)row.append(el('p','reader-route-question',step.question));
   }""")
    workspace=substitute(workspace,r"appendPendingChapter\(root,ending,passages\);if\(e\.writing_version!=='external-author-v1'\)",
        """if(e.writing_version!==NEXT_PREVIEW_VERSION)appendPendingChapter(root,ending,passages);
   if(![NEXT_PREVIEW_VERSION,'external-author-v1'].includes(e.writing_version))""")
    workspace=substitute(workspace,r"const ending=ctx\.material\(\)\.chapter_endings\?\.\[chapter\?\.id\];",
        """const ending=e.writing_version===NEXT_PREVIEW_VERSION
    ? {mode:e.narrative.ending_kind}
    : ctx.material().chapter_endings?.[chapter?.id];""")
    workspace=substitute(workspace,r"const passages=endingPassages\(e,passageIndex,ending\);",
        """const passages=e.writing_version===NEXT_PREVIEW_VERSION
    ? e.passage_ids.map(id=>passageIndex.get(id)).filter(p=>p&&!p.display_omit&&!p.presentation_omit)
    : endingPassages(e,passageIndex,ending);""")
    workspace=substitute(workspace,r',outerWeights=\{\}', '')
    workspace=substitute(workspace,r"function layout\(\)\{",
        """function widthHandles(grid,cells,labels,minimum,apply,placeAll){
  const sizes=()=>cells.map(cell=>cell.getBoundingClientRect().width);
  const rails=cells.slice(0,-1).map((cell,i)=>{
   let start;
   const rail=addHandle(grid,'vertical','调整'+labels[i]+'与'+labels[i+1]+'的宽度',(delta,key)=>{
    const columns=key?sizes():start;
    if(!columns)return;
    const left=columns[i],right=columns[i+1];
    const min=Math.min(minimum,(left+right)*.3);
    const amount=Math.max(Math.min(0,min-left),Math.min(Math.max(0,right-min),delta));
    const next=[...columns];
    next[i]+=amount;next[i+1]-=amount;
    apply(next);placeAll();ctx.fit();
   });
   rail.addEventListener('pointerdown',()=>{start=sizes()});
   return rail;
  });
  return ()=>{
   const left=grid.getBoundingClientRect().left;
   rails.forEach((rail,i)=>{rail.style.left=(cells[i].getBoundingClientRect().right-left)+'px'});
  };
 }
 function layout(){""")
    workspace=substitute(workspace,r"area\.style\.gridTemplateColumns=effective\.map\(\(_,i\)=>[^\n]+?;area\.hidden=",
        """const innerKey='inner:'+effective.map(column=>column.join('+')).join('|');
   const legacyWidths=effective.map((_,i)=>widths['inner'+i]);
   if(!widths[innerKey]&&Object.keys(widths).filter(key=>/^inner[0-9]+$/.test(key)).length===effective.length&&
      legacyWidths.every(width=>width>0))widths[innerKey]=legacyWidths;
   const paintInner=()=>{
    area.style.gridTemplateColumns=effective.map((_,i)=>`minmax(0,${!mobile&&widths[innerKey]?.[i]||1}fr)`).join(' ');
   };
   paintInner();area.hidden=""")
    workspace=substitute(workspace,r"workspace\.style\.gridTemplateColumns=outer\.map.*?\n[ \t]+\}\);(?=\n[ \t]+\}\n[ \t]+async function revealLinked)",
        """const outerKey='outer:'+outer.map(([key])=>key).join('|');
   const paintOuter=()=>{
    const saved=widths[outerKey];
    workspace.style.gridTemplateColumns=outer.map(([key],i)=>
      `minmax(${!mobile&&key==='directory'&&!saved?'190px':'0'},${saved?.[i]||defaults[key]}fr)`).join(' ');
   };
   paintOuter();workspace.style.overflowX='hidden';
   cancelAnimationFrame(layoutFrame);
   if(!mobile)layoutFrame=requestAnimationFrame(()=>{
    let placeOuter=()=>{},placeInner=()=>{};
    const placeAll=()=>{placeOuter();placeInner()};
    const outerLabels={directory:'目录',reading:'阅读区',interaction:'问答',study:'术语与练习'};
    placeOuter=widthHandles(workspace,outer.map(([,node])=>node),outer.map(([key])=>outerLabels[key]),120,
      next=>{widths[outerKey]=next;paintOuter()},placeAll);
    const reading=Array.from(area.querySelectorAll(':scope > .reading-column'));
    placeInner=widthHandles(area,reading,effective.map(column=>column.map(key=>names[key]).join('、')),140,
      next=>{widths[innerKey]=next;paintInner()},placeAll);
    placeAll();
   });""")
    workspace="const NEXT_PREVIEW_VERSION="+repr(PREVIEW_WRITING_VERSION)+";\n"+workspace
    page.write_text(workspace,'utf8')

    page=static/'reader-source-flow.js'
    source_flow=page.read_text('utf8')
    source_flow=substitute(source_flow,r'\.question-composer,img,\.reader-source-crop',
                          '.question-composer,.reader-route-node,img,.reader-source-crop')
    source_flow=substitute(source_flow,r"if\(root.dataset.language==='en'\|\|!Array.isArray\(segments\)",
        "if(root.id==='original-reading'||root.dataset.language==='en'||!Array.isArray(segments)")
    source_flow=substitute(source_flow,r"const bundled=group.matches",
        "const bundled=root.id==='original-reading'||group.matches")
    source_flow=substitute(source_flow,r"sameParagraph=role==='paragraph'&&previous\?\.role==='paragraph'&&blockId===previous\.block_id",
        "sameParagraph=['paragraph','equation'].includes(role)&&previous?.role===role&&blockId===previous.block_id")
    source_flow=substitute(source_flow,r'JSON\.stringify\(\[url,box\]\)',
        'JSON.stringify([url,box,group.equation_number||null])')
    source_flow=substitute(source_flow,r"return canvas\.toDataURL\('image/png'\)",
        "return equationCrop(canvas,group.equation_number).toDataURL('image/png')")
    source_flow=substitute(source_flow,r' function crop\(group\)\{',
        """ function equationCrop(canvas,number){
  if(!number||canvas.width<100)return canvas;
  const ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height,data=ctx.getImageData(0,0,w,h).data;
  const marks=new Uint8Array(w);
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){
    const i=4*(y*w+x);
    if(data[i]<170&&data[i+1]<170&&data[i+2]<170&&data[i+3]>200)marks[x]=1;
  }
  const right=marks.lastIndexOf(1);
  const minGap=Math.max(14,Math.floor(w*.12));
  let i=right;
  while(i>=0&&right-i<w*.20){
    if(marks[i]) {i--;continue}
    const end=i;while(i>=0&&!marks[i])i--;
    if(i>w*.18&&end-i>=minGap) {
      const clipped=document.createElement('canvas');clipped.width=i+5;clipped.height=h;
      clipped.getContext('2d').drawImage(canvas,0,0);return clipped;
    }
  }
  return canvas;
 }
 function crop(group){""")
    source_flow=substitute(source_flow,r"ctx\.rich\(text,ReaderUI\.prose\(displayText\)\)\}",
        """const explicitMath=/^\\s*(?:\\\\\\[|\\$\\$|\\\\begin\\{|\\\\\\()/;
    const refs=block.role==='equation'?sourceRefs(sourceIds):[];
    const isPdfEquation=refs.length&&refs.every(ref=>ref.page_id===refs[0].page_id);
    if(isPdfEquation&&!explicitMath.test(displayText)){
      const boxes=refs.map(ref=>ref.bbox);
      const bbox=[Math.min(...boxes.map(b=>b[0])),Math.min(...boxes.map(b=>b[1])),
        Math.max(...boxes.map(b=>b[2])),Math.max(...boxes.map(b=>b[3]))];
      const number=displayText.match(/(?:^|\\n)\\s*\\((\\d+(?:\\.\\d+)*[a-z]?)\\)\\s*$/i)?.[1];
      const image=crop({visual_page_id:refs[0].page_id,bbox,equation_number:number});
      if(image){
        const figure=el('figure','reader-translation-equation');
        image.alt=displayText.trim();figure.append(image);
        if(number)figure.append(el('figcaption','reader-equation-number','('+number+')'));
        text.append(figure);
      }else ctx.rich(text,ReaderUI.prose(displayText));
    }else ctx.rich(text,ReaderUI.prose(displayText))}""")
    source_flow=substitute(source_flow,r"slot\.append\(pdf\);active=id",
        "slot.insertBefore(pdf,slot.querySelector(':scope > .reader-pdf-questions'));active=id")
    source_flow=substitute(source_flow,r"row\.style\.height=\(info\.height\*value\+24\)\+'px';",
        "row.style.minHeight=(info.height*value+24)+'px';")
    source_flow=substitute(source_flow,r"text=ids.map\(id=>source\(id\)\?\.zh\|\|source\(id\)\?\.en\|\|''\)",
        "text=ids.map(id=>{const p=source(id);return (root.id==='original-reading'?p?.en||p?.zh:p?.zh||p?.en)||''})")
    page.write_text(source_flow,'utf8')

    page=static/'statistics.js'
    stats=page.read_text('utf8').replace("Math.round(progress.percent)+'%'",'ReaderUI.coveragePercent(progress)')
    stats=substitute(stats,r"Math\.round\(stats\.answered/stats\.questions\*100\):0",
        '100*stats.answered/stats.questions:0')
    stats=substitute(stats,r"\[stats,runs\]=await Promise\.all\(\[read\('/api/statistics'\+query\),read\('/api/runs'\)\.catch\(\(\)=>\(\{runs:\[\]\}\)\)\]\),coverage=stats\.coverage",
        """[stats,runs,next]=await Promise.all([read('/api/statistics'+query),read('/api/runs').catch(()=>({runs:[]})),
    material.startsWith('next-final-')?read('/api/postprocess-preview').catch(()=>null):null]),
    coverage=next?.coverage_statistics?{...stats.coverage,...next.coverage_statistics,
      overall:{...stats.coverage.overall,...next.coverage_statistics.overall},
      files:stats.coverage.files.map(row=>({...row,...next.coverage_statistics.files?.[row.file_id]})),
      chapters:stats.coverage.chapters.map(row=>row.learning_node_id===next.coverage_statistics.chapter_id?
        {...row,...next.coverage_statistics.overall}:row)}:stats.coverage""")
    page.write_text(stats.replace("['explanation','讲解']","['explanation','讲解关联']").replace(
        "['translation','翻译']","['translation','译文处理']").replace(
        "['translation','缺少译文']","['translation','译文尚未处理']"),'utf8')

    page=static/'quality-review.js'
    review=page.read_text('utf8')
    review=substitute(review,r"const categories=\[\['lessons','讲解'\]",
        """const next=mid.startsWith('next-final-')?await api('/api/next-finish/review',session.pin).catch(()=>null):null;
const categories=[...(next?[['next','Next 运行记录']]:[]),['lessons','讲解']""")
    review=substitute(review,r"entries\.replaceChildren\(\);if\(key==='calls'\)",
        """entries.replaceChildren();
if(key==='next'&&next){
  const names=['原件版本','任务规划','原文解析','首稿','来源映射','教学补充','局部融合','正文结构',
    '原文分组','原文翻译','图片标签','资源详解','术语','练习','章首章末','最终格式','阅读装配',
    '对照与覆盖','浏览器验收','交稿'];
  const table=make('table'),head=make('tr');
  for(const label of ['步骤','记录数','最近完成时间'])head.append(make('th',label));
  table.append(head);
  next.stages.forEach((stage,index)=>{
    const row=make('tr');
    const count=stage.events||Number(stage.id==='STEP-20'&&!!next.delivery);
    const committed=stage.id==='STEP-20'&&next.delivery?.at;
    const when=committed?new Date(committed).toLocaleTimeString():stage.ended_at?new Date(stage.ended_at*1000).toLocaleTimeString():'—';
    for(const value of [stage.id+' '+names[index],String(count),when])row.append(make('td',value));
    table.append(row);
  });
  entries.append(table);return;
}
if(key==='calls')""")
    review=substitute(review,r"await select\('lessons'\)", "await select(next?'next':'lessons')")
    page.write_text(review,'utf8')

    page=static/'reader-markdown.js'
    markdown=page.read_text('utf8')
    markdown=substitute(markdown,r"if\(!span\.dataset\.mathError&&span\.querySelector\('\.katex'\)\)span\.dataset\.mathRendered='true'",
        """if(span.querySelector('.katex'))span.dataset.mathRendered='true';
    else if(span.dataset.mathError){
      span.textContent=part.raw;
      if(!part.declared&&span.dataset.mathFailure!=='load'){
        const literal=document.createElement('span');
        literal.dataset.mathCandidate=part.raw;
        literal.dataset.mathReason=span.dataset.mathError;
        literal.textContent=part.raw;
        span.replaceWith(literal);
      }
    }""")
    markdown=substitute(markdown,r"span\.dataset\.mathError=error\.message",
        "span.dataset.mathError=error.message||String(error);span.dataset.mathFailure='load'")
    page.write_text(markdown,'utf8')

    text=(legacy/'vendor'/'reader-text.js').read_text('utf8')
    text=substitute(text,r'  function katexReady\(\) \{.*?\n  \}(?=\n  async function math)',
        """  function katexReady() {
    if (!document.getElementById('lw-katex-css')) {
      const link=document.createElement('link');
      link.id='lw-katex-css';link.rel='stylesheet';
      link.href=new URL('media-vendor/katex/dist/katex.min.css',base);document.head.append(link);
    }
    if (window.katex) return Promise.resolve();
    if (!ready) {
      const load=()=>new Promise((resolve,reject)=>{
        const script=document.createElement('script');
        script.src=new URL('media-vendor/katex/dist/katex.min.js',base);
        script.onload=()=>window.katex?resolve():reject(new Error('公式组件没有初始化'));
        script.onerror=()=>{script.remove();reject(new Error('公式组件未能加载'))};
        document.head.append(script);
      });
      ready=load().catch(()=>new Promise(resolve=>setTimeout(resolve,150)).then(load)).catch(error=>{
        ready=undefined;throw error;
      });
    }
    return ready;
  }""")
    text=substitute(text,r"span\.dataset\.mathError=error\.message;",
        "span.dataset.mathError=error.message||String(error);span.dataset.mathFailure='load';")
    (static/'reader-next-text.js').write_text(text,'utf8')

    javascript=ASSETS.joinpath('reader-finish.js').read_bytes()
    (static/'reader-postprocess-preview.js').write_bytes(javascript)
    name='reader-finish-math.js'
    (static/name).write_bytes(ASSETS.joinpath(name).read_bytes())
    return javascript
