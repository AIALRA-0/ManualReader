"""为原材料建立可恢复的展示分组；文字、编号、坐标和用户状态保持原样。"""
from pathlib import Path
from collections import Counter,defaultdict
import copy,json,re,os,tempfile
from reader_translation import active_groups,legacy_segments
from reader_performance import ScopedRLock

VERSION=8
_LOCK=ScopedRLock(lambda:'layout')

def _text(p):return p.get('zh') or p.get('en') or ''
def _box(p,page=None):
    refs=[r['bbox'] for r in p.get('refs',[]) if r.get('bbox') and (page is None or r.get('page_id')==page)]
    return [min(r[0] for r in refs),min(r[1] for r in refs),max(r[2] for r in refs),max(r[3] for r in refs)] if refs else None
def _inside(box,region):return box and region[0]-2<=(box[0]+box[2])/2<=region[2]+2 and region[1]-2<=(box[1]+box[3])/2<=region[3]+2
def _save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.next',dir=path.parent);temporary=Path(name)
    try:
        with os.fdopen(fd,'w',encoding='utf8')as output:json.dump(value,output,ensure_ascii=False)
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)
def page_ids(material,ids,pages_for=None):
    selected=set(ids)
    found=set().union(*(pages_for.get(identity,set())for identity in selected))if pages_for is not None else {r['page_id'] for p in material['passages'] if p['id'] in selected for r in p.get('refs',[]) if r.get('page_id') in material.get('pages',{})}
    return [pid for pid in material.get('pages',{}) if pid in found]
def scope_index(material):
    sources={p['id']:p for p in material['passages'] if p.get('content_kind')!='explanation'}
    positions=defaultdict(list);pages_for=defaultdict(set);sources_on_page=defaultdict(set)
    for i,p in enumerate(material['passages']):
        positions[p['id']].append(i)
        pages_for[p['id']].update(r['page_id'] for r in p.get('refs',[]) if r.get('page_id') in material.get('pages',{}))
    for p in sources.values():
        for ref in p.get('refs',[]):
            sources_on_page[ref.get('page_id')].add(p['id'])
    return sources,positions,pages_for,sources_on_page

def scope(material,links,index=None):
    sources,positions,pages_for,sources_on_page=index if index is not None else scope_index(material)
    ids=list(dict.fromkeys(l['passage_id'] for l in links if l.get('passage_id') in sources))
    pages=page_ids(material,ids,pages_for);slide_pages={pid for pid in pages if material['pages'][pid].get('kind')=='slide' or material['pages'][pid].get('width',1)>1.15*material['pages'][pid].get('height',1)}
    # 幻灯片以整页为阅读范围，竖版文档仍保留用户选择的题目或段落范围。
    selected=set(ids)
    for pid in slide_pages:selected.update(sources_on_page.get(pid,()))
    ordered=[material['passages'][i]['id'] for i in sorted(i for identity in selected for i in positions[identity])]
    return {'passage_ids':ordered,'page_ids':pages,'pages':[{'id':pid,'number':material['pages'][pid]['number'],'file_id':material['pages'][pid].get('file_id','primary')} for pid in pages]}

def link_index(material):
    sources={s['id']:s for s in material['passages'] if s.get('content_kind')!='explanation'}
    sentences={s['id']:x['id'] for x in sources.values() for s in x.get('sentences',[]) if not s.get('historical')}
    atoms={a:x['id'] for x in sources.values() for a in x.get('source_ids',[])}
    return sources,sentences,atoms

def mapped_links(p,material,fallback=(),index=None):
    sources,sentences,atoms=index if index is not None else link_index(material)
    rows=list(p.get('source_ranges',[]))
    rows.extend({'passage_id':identity if identity in sources else sentences.get(identity) or atoms.get(identity),'sentence_id':identity if identity in sentences else None} for identity in p.get('explained_source_ids',[]))
    result=[];seen=set()
    for row in rows:
        pid=row.get('passage_id');sid=row.get('sentence_id')
        if pid not in sources or sid and sentences.get(sid)!=pid:continue
        key=(pid,sid)
        if key not in seen:result.append({'file_id':sources[pid].get('file_id','primary'),'passage_id':pid,'sentence_id':sid});seen.add(key)
    if result:return result,'block'
    # 旧讲解明确写出页码或引用了原页图片时，只恢复到该页，不冒充句级出处。
    fids={l.get('file_id','primary') for l in fallback};mentioned=set(int(n) for n in re.findall(r'第\s*(\d+)\s*页',_text(p)))
    image_numbers=set(int(n) for n in re.findall(r'!\[[^\]]*\]\([^\n)]*page[-_](\d+)\.(?:png|jpe?g)',_text(p),re.I))
    if len(fids)==1 and (mentioned or image_numbers):
        wanted={pid for pid,info in material.get('pages',{}).items() if info.get('file_id','primary') in fids and info.get('number') in mentioned|image_numbers}
        if wanted:
            links=[{'file_id':s.get('file_id','primary'),'passage_id':s['id'],'sentence_id':None} for s in sources.values() if any(r.get('page_id') in wanted for r in s.get('refs',[]))]
            if links:return links,'page'
    # 旧稿仅在原句逐字匹配且唯一时推导块级定位，避免靠相似词把出处乱挂。
    quoted=re.findall(r'[“「]([^”」]{12,})[”」]|(?:原文|原句)[：:]\s*([^\n]{12,})',_text(p))
    for values in quoted:
        quote=next((v.strip() for v in values if v),'');matches=[s for s in sources.values() if quote and any(quote in s.get(k,'') for k in ('en','zh'))]
        if len(matches)==1:return [{'file_id':matches[0].get('file_id','primary'),'passage_id':matches[0]['id'],'sentence_id':None}],'quote'
    return copy.deepcopy(list(fallback)),'chapter' if fallback else 'none'

def annotate_explanations(material):
    owners={e['id']:e for e in material.get('explanations',[])}
    index=None
    for p in material['passages']:
        if p.get('content_kind')=='explanation':
            if index is None:index=link_index(material)
            owner=owners.get(p.get('explanation_id'),{});links,precision=mapped_links(p,material,owner.get('source_links',p.get('source_links',[])),index)
            p.update(source_links=links,source_link_precision=precision)

def _sources(folder,original,files):
    result={}
    for fid,row in [('primary',original),*[(f['id'],f) for f in files]]:
        if fid=='primary':
            candidates=[folder/str(original.get('original_file') or ''),folder/'original.pdf',folder/'original.pptx']
        else:candidates=[folder/'reading-files'/fid/str(row.get('stored_name') or '')]
        result[fid]=next((p for p in candidates if p.is_file() and p.suffix.lower()=='.pdf'),None)
    return result

def geometry(material,folder,original,files):
    paths=_sources(folder,original,files);signature={k:[str(p),p.stat().st_mtime_ns,p.stat().st_size] for k,p in paths.items() if p};cache=folder/'reading-geometry.json'
    if cache.exists():
        cached=json.loads(cache.read_text('utf8'))
        if cached.get('version')==VERSION and cached.get('signature')==signature:return cached['pages']
    result={};by_page=defaultdict(list)
    for p in material['passages']:
        if p.get('content_kind')!='explanation':
            for r in p.get('refs',[]):by_page[r.get('page_id')].append(p)
    try:import fitz
    except ImportError:return result
    for fid,path in paths.items():
        if not path:continue
        try:
            with fitz.open(path) as pdf:
                for pid,info in material.get('pages',{}).items():
                    if info.get('file_id','primary')!=fid or not 0<int(info.get('number',0))<=len(pdf):continue
                    members=by_page[pid];short=[p for p in members if 0<len(_text(p).strip())<=45 and not p.get('heading')]
                    if len(short)<3:continue
                    page=pdf[info['number']-1];drawings=page.cluster_drawings() if hasattr(page,'cluster_drawings') else []
                    boxes=[list(r) for r in drawings if r.width>25 and r.height>20 and r.width*r.height<page.rect.get_area()*.85]
                    tables=[]
                    if len(short)>7 and hasattr(page,'find_tables'):
                        try:
                            for t in page.find_tables().tables:
                                if t.row_count>1 and t.col_count>1:tables.append({'bbox':list(t.bbox),'cells':[[list(c) if c else None for c in row.cells] for row in t.rows]})
                        except (ValueError,RuntimeError):pass
                    result[pid]={'figures':boxes,'tables':tables}
        except (OSError,ValueError,RuntimeError):continue
    _save(cache,{'version':VERSION,'signature':signature,'pages':result});return result

def _kind(p):
    if p.get('heading'):return 'heading'
    if p.get('kind') in ('image','visual','page_image'):return 'figure'
    if p.get('kind') in ('table','code','equation','formula','math','markdown'):return p['kind']
    if p.get('bullet') or re.match(r'^\s*(?:[-*•▪◦]|\d+[.)、])\s+',_text(p)):return 'list'
    return 'paragraph'

def groups(material,geometries,overrides=()):
    sources=[p for p in material['passages'] if p.get('content_kind')!='explanation'];by_id={p['id']:p for p in sources};positions={p['id']:i for i,p in enumerate(sources)};owned=set();result=[];issues=[]
    pages_for=defaultdict(set)
    for p in material['passages']:pages_for[p['id']].update(r['page_id']for r in p.get('refs',[])if r.get('page_id')in material.get('pages',{}))
    def add(ids,kind,**extra):
        ids=list(dict.fromkeys(i for i in ids if i in by_id and i not in owned))
        if not ids:return
        fids={by_id[i].get('file_id','primary') for i in ids}
        if len(fids)!=1:return
        owned.update(ids);result.append({'id':ids[0]+'-reading-block','file_id':next(iter(fids)),'kind':kind,'passage_ids':ids,'page_ids':page_ids(material,ids,pages_for),**extra})
    for row in overrides:
        ids=row.get('passage_ids',[])
        if not isinstance(ids,list) or not ids or any(i not in by_id or i in owned for i in ids):issues.append({'code':'invalid_override','id':row.get('id')});continue
        kind=row.get('kind')
        if kind not in ('paragraph','list','figure','table','heading','code','markdown','equation'):issues.append({'code':'invalid_kind'});continue
        add(ids,kind,agent_layout=True,**({'translation_segments':row['translation_segments']} if row.get('translation_segments') else {}))
    by_page=defaultdict(list)
    for p in sources:
        for pid in dict.fromkeys(r.get('page_id') for r in p.get('refs',[])):by_page[pid].append(p)
    for pid,members in by_page.items():
        meta=geometries.get(pid,{})
        for table in meta.get('tables',[]):
            cells=[];ids=[]
            for row in table['cells']:
                current=[]
                for cell in row:
                    matches=[p['id'] for p in members if p['id'] not in owned and cell and _inside(_box(p,pid),cell) and p.get('kind') not in ('image','visual','page_image')]
                    current.append(matches);ids.extend(matches)
                cells.append(current)
            occupied=sum(bool(cell) for row in cells for cell in row);slots=sum(len(row) for row in cells)
            populated_rows=sum(sum(bool(cell) for cell in row)>=2 for row in cells)
            # 矩形剖分图也有许多线段，只有文字确实组成规则的多行多列时才按表格排。
            only_numbers=ids and all(re.fullmatch(r'[\d\s.,+−\-/]+',_text(by_id[i]).strip()) for i in ids)
            if len(set(ids))>=4 and len(ids)==len(set(ids)) and occupied>=slots*.45 and populated_rows>=2 and not only_numbers:add(ids,'table',cells=cells)
        for box in sorted(meta.get('figures',[]),key=lambda b:(b[1],b[0])):
            labels=[p['id'] for p in members if p['id'] not in owned and not p.get('heading') and 0<len(_text(p).strip())<=45 and _inside(_box(p,pid),box)]
            if len(labels)>=2:add(labels,'figure',visual_page_id=pid,bbox=box)
        for image in members:
            if image['id'] in owned or image.get('kind') not in ('image','visual','page_image'):continue
            box=_box(image,pid);labels=[p['id'] for p in members if p['id'] not in owned and p['id']!=image['id'] and not p.get('heading') and len(_text(p).strip())<=45 and _inside(_box(p,pid),box)] if box else []
            add([image['id'],*labels],'figure')
    previous=None
    for p in sources:
        if p['id'] in owned:previous=None;continue
        kind=_kind(p);fid=p.get('file_id','primary');pages=page_ids(material,[p['id']],pages_for);b=_box(p,pages[0]) if pages else None
        merge=False
        if previous and previous['kind']==kind and kind in ('paragraph','list') and previous['file_id']==fid and previous['page_ids']==pages:
            old=by_id[previous['passage_ids'][-1]];a=_box(old,pages[0]) if pages else None
            if kind=='list':merge=old.get('section_id')==p.get('section_id')
            elif a and b:merge=abs(a[0]-b[0])<14 and -.5<=b[1]-a[3]<=max(5,(a[3]-a[1])*.65) and not re.search(r'[。！？.!?:：;；]$',(old.get('en') or _text(old)).strip()) and len(old.get('en') or _text(old))>25 and len(p.get('en') or _text(p))>15
        if not merge and previous and kind==previous['kind']=='paragraph' and previous['file_id']==fid and len(pages)==1 and previous['page_ids']:
            old=by_id[previous['passage_ids'][-1]];last=previous['page_ids'][-1];a=_box(old,last);before=material.get('pages',{}).get(last,{});after=material.get('pages',{}).get(pages[0],{})
            merge=bool(a and b and before.get('number',0)+1==after.get('number') and before.get('width',1)<before.get('height',0) and old.get('section_id')==p.get('section_id') and abs(a[0]-b[0])<14 and a[3]>before.get('height',1)-100 and b[1]<100 and len(old.get('en') or _text(old))>25 and len(p.get('en') or _text(p))>15 and not re.search(r'[。！？.!?:：;；]$',(old.get('en') or _text(old)).strip()))
        if merge:previous['passage_ids'].append(p['id']);previous['page_ids']=list(dict.fromkeys(previous['page_ids']+pages));owned.add(p['id'])
        else:add([p['id']],kind);previous=result[-1]
    result.sort(key=lambda g:min(positions[i] for i in g['passage_ids']))
    counts=Counter(i for g in result for i in g['passage_ids']);missing=[p['id'] for p in sources if not counts[p['id']]]
    for pid in missing:add([pid],_kind(by_id[pid]))
    for group in result:
        if group.get('translation_segments') or group['kind']=='figure':continue
        segments=[]
        for identity in group['passage_ids']:
            block_id=group['id'] if group['kind']=='paragraph' else identity
            segments.extend(legacy_segments(by_id[identity],block_id))
        if segments:
            group['translation_segments']=segments
            group['translation_origin']='existing_text'
    return result,{'source_passages':len(sources),'assigned_passages':len(counts),'missing':missing,'duplicate_assignments':[i for i,c in counts.items() if c>1],'issues':issues}

def prepare(material,folder,original,files):
    folder=Path(folder);cache=folder/'reading-layout.json';override=folder/'reading-layout-overrides.json'
    signature=[[p.name,p.stat().st_mtime_ns,p.stat().st_size] for p in [folder/'material.json',folder/'reading-workspace.json',override,folder/'translation-groups.json',folder/'reading-visual-language.json'] if p.exists()]
    with _LOCK.for_scope(str(folder.resolve())):
        if cache.exists():
            cached=json.loads(cache.read_text('utf8'))
            if cached.get('version')==VERSION and cached.get('signature')==signature:return cached
        custom=json.loads(override.read_text('utf8')).get('groups',[]) if override.exists() else []
        translated=active_groups(folder,material)
        translated_ids={i for g in translated for i in g['source_ids']}
        custom=[{'passage_ids':g['source_ids'],'kind':g['kind'],'translation_segments':g['segments']} for g in translated]+[g for g in custom if not translated_ids.intersection(g.get('passage_ids',[]))]
        display,audit=groups(material,geometry(material,folder,original,files),custom)
        owners={i:g['id'] for g in display for i in g['passage_ids']};assigned={link['passage_id'] for n in material.get('learning',{}).get('nodes',[]) for link in n.get('effective_source_links',[])}
        result={'version':VERSION,'signature':signature,'groups':display,'by_passage':owners,'audit':audit,'unassigned_to_lesson':[p['id'] for p in material['passages'] if p.get('content_kind')!='explanation' and p['id'] not in assigned and p.get('role') not in ('page_furniture','whitespace')],
                'explanation_links':{p['id']:{'links':p.get('source_links',[]),'precision':p.get('source_link_precision','none')} for p in material['passages'] if p.get('content_kind')=='explanation'}}
        _save(cache,result);return result

def save_layout(folder,material,data):
    wanted=data.get('groups')
    if not isinstance(wanted,list) or len(wanted)>5000:raise ValueError('请按列表提交展示分组')
    ids={p['id'] for p in material['passages'] if p.get('content_kind')!='explanation'}
    if any(not isinstance(g,dict) or not isinstance(g.get('passage_ids'),list) or any(i not in ids for i in g['passage_ids']) for g in wanted):raise ValueError('展示分组引用了不属于当前项目的原文编号')
    # 缺项由同一脚本补回；展示方案不合法不会删除已有原文、译文或问题。
    _,audit=groups(material,{},wanted)
    path=Path(folder)/'reading-layout-overrides.json';old=json.loads(path.read_text('utf8')) if path.exists() else {'revision':0}
    if data.get('expected_revision') is not None and data['expected_revision']!=old['revision']:raise ValueError('展示方案已更新，请重新读取')
    value={'revision':old['revision']+1,'groups':wanted};_save(path,value)
    return {'revision':value['revision'],'audit':audit,'original_content_changed':False}
