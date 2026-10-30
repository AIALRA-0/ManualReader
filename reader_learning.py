"""学习章节独立于原文件目录；来源、讲解和旧问题均保留自己的编号。"""
import copy,re


def learning_nodes(material,stored=None):
    if stored is not None:return copy.deepcopy(stored)
    nodes=[]
    if material.get('reader_mode')=='authored':
        for section in material.get('authored_sections',[]):
            nodes.append({'id':section['id'],'parent_id':section.get('parent_id'),'title':section['title'],'number':section.get('number',''),
                'legacy_section_ids':[section['id']],'source_links':[],'explanation_ids':[]})
    else:
        for index,e in enumerate(material.get('explanations',[])):
            nodes.append({'id':e['id'],'parent_id':None,'title':e['title'],'number':str(index),
                'legacy_section_ids':[],'source_links':e.get('source_links',[]),'explanation_ids':[e['id']]})
    return nodes


def validate_outline(data,material):
    rows=data.get('nodes')
    if not isinstance(rows,list) or len(rows)>1000 or any(not isinstance(r,dict) for r in rows):raise ValueError('请按顺序提交学习章节列表')
    known={};counts={};clean=[];active=[];titles=set();passages={p['id']:p for p in material['passages']}
    explanation_ids={e['id'] for e in material.get('explanations',[])};sections={s['id'] for s in material.get('authored_sections',[])}
    for row in rows:
        identity=row.get('id');parent=row.get('parent_id') or None
        if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,70}',identity) or identity in known:raise ValueError('学习章节编号格式无效或重复')
        if parent and (parent not in known or parent not in active):raise ValueError('请先提交父章节，再按顺序提交它的子章节')
        active=active[:active.index(parent)+1]+[identity] if parent else [identity]
        index=counts.get(parent,0)+(1 if parent else 0);counts[parent]=counts.get(parent,0)+1
        number=(known[parent]['number']+'.' if parent else '')+str(index)
        if row.get('number') is not None and str(row['number']).rstrip('.')!=number:raise ValueError('章节 '+identity+' 的编号应为 '+number)
        title=row.get('title')
        if not isinstance(title,str) or not title.strip() or '\n' in title or len(title)>300:raise ValueError('章节标题需要是一行文字，最多 300 字')
        prefix=re.match(r'^(\d+(?:\.\d+)*)\.?\s+(.+)$',title.strip())
        if prefix:
            if prefix[1]!=number:raise ValueError('标题编号与章节层级不一致')
            title=prefix[2]
        key=(parent,re.sub(r'\s+','',title).casefold())
        if key in titles:raise ValueError('同一层级的章节标题重复')
        titles.add(key)
        links=row.get('source_links',[]);explanations=row.get('explanation_ids',[]);legacy=row.get('legacy_section_ids',[])
        if not isinstance(links,list) or any(not isinstance(l,dict) for l in links):raise ValueError('原文范围需要按片段提交')
        normalized=[]
        for link in links:
            p=passages.get(link.get('passage_id'))
            if not p or p.get('content_kind')=='explanation' or (link.get('file_id') and link['file_id']!=p.get('file_id')):raise ValueError('原文文件与片段不对应；讲解请登记在 explanation_ids')
            sid=link.get('sentence_id')
            if sid and not any(s['id']==sid for s in p['sentences']):raise ValueError('原句不属于所选片段')
            normalized.append({'file_id':p.get('file_id','primary'),'passage_id':p['id'],'sentence_id':sid})
        if not isinstance(explanations,list) or any(e not in explanation_ids for e in explanations):raise ValueError('关联的讲解不存在')
        if not isinstance(legacy,list) or any(s not in sections for s in legacy):raise ValueError('关联的旧讲解章节不存在')
        item={'id':identity,'parent_id':parent,'title':title.strip(),'number':number,'source_links':normalized,'explanation_ids':list(dict.fromkeys(explanations)),
              'legacy_section_ids':list(dict.fromkeys(legacy)),'source_label':str(row.get('source_label',''))[:200]}
        known[identity]=item;clean.append(item)
    return clean


def effective_links(node,nodes,material):
    links=list(node.get('source_links',[]))
    links.extend(link for e in material.get('explanations',[]) if e['id'] in node.get('explanation_ids',[]) for link in e.get('source_links',[]))
    if not links and node.get('parent_id'):
        parent=next((n for n in nodes if n['id']==node['parent_id']),None)
        if parent:links=effective_links(parent,nodes,material)
    seen=set();result=[]
    for link in links:
        key=(link['passage_id'],link.get('sentence_id'))
        if key not in seen:result.append(copy.deepcopy(link));seen.add(key)
    return result


def learning_payload(material,stored,state):
    nodes=learning_nodes(material,stored.get('learning_outline'));passages=material['passages']
    for node in nodes:
        body=[p for p in passages if p.get('explanation_id') in node.get('explanation_ids',[]) or (p.get('file_id')=='primary' and p.get('section_id') in node.get('legacy_section_ids',[]))]
        node['status']='ready' if any(not p.get('heading') and (p.get('zh') or p.get('en') or '').strip() for p in body) else 'planned'
        node['passage_ids']=[p['id'] for p in body]
        node['level']=node.get('number','').count('.')+1
        node['effective_source_links']=effective_links(node,nodes,material)
        visible=[p for p in body if p.get('explanation_id') in node.get('explanation_ids',[])] if node.get('explanation_ids') else body
        visible=[p for p in visible if not p.get('heading') and not p.get('duplicate_of') and p.get('role') not in ('page_furniture','metadata','whitespace') and not re.fullmatch(r'\s*#{1,6}\s+[^\n]+\s*',(p.get('zh') or p.get('en') or ''))]
        node['reading_passage_ids']=[p['id'] for p in visible]
        done=sum(bool(state.get('read_passages',{}).get(p['id'])) for p in visible);total=len(visible)
        node['own_reading_progress']={'done':done,'total':total,'percent':round(done/total*100) if total else 0}
    for node in nodes:
        descendants={node['id']}
        while True:
            expanded=descendants|{n['id'] for n in nodes if n.get('parent_id') in descendants}
            if expanded==descendants:break
            descendants=expanded
        own=[n for n in nodes if n['id'] in descendants]
        reading_ids=sorted({pid for n in own for pid in n['reading_passage_ids']})
        node['subtree_passage_ids']=sorted({pid for n in own for pid in n['passage_ids']})
        total=len(reading_ids);done=sum(bool(state.get('read_passages',{}).get(pid)) for pid in reading_ids)
        node['reading_progress']={'done':done,'total':total,'percent':round(done/total*100) if total else 0,'descendant_count':len(descendants)-1}
    ids={n['id'] for n in nodes};selected=state.get('learning_node_id')
    if selected not in ids:selected=next((n['id'] for n in nodes if n['status']=='ready'),nodes[0]['id'] if nodes else None)
    active=stored.get('learning_outline') is not None or material.get('reader_mode')=='authored' or state.get('learning_node_id') in ids
    current=next((n for n in nodes if n['id']==selected),None) if active else None
    related=set((current or {}).get('passage_ids',[]));related.update(l['passage_id'] for l in (current or {}).get('effective_source_links',[]))
    for p in passages:
        if p['id'] in related:related.update(p.get('origin_ids',[]))
    registered={q['id']:q for r in state.get('rounds',[]) for q in r['questions']}
    def belongs(q,seen=None):
        if not current:return q.get('passage_id') in related
        owner=q.get('learning_node_id') or q.get('target',{}).get('learning_node_id')
        if owner:return owner==current['id']
        seen=(seen or set())|{q['id']};parent=registered.get(q.get('parent_question_id'))
        if parent and parent['id'] not in seen:return belongs(parent,seen)
        owners=[n['id'] for n in nodes if q.get('passage_id') in n['passage_ids']]
        if len(owners)==1:return owners[0]==current['id']
        return False
    questions=[q for q in registered.values() if not q.get('deleted_at') and belongs(q)]
    qids={q['id'] for q in questions};tids={tid for q in questions for tid in q.get('term_ids',[])}
    terms=[t['id'] for t in state.get('terms',[]) if t['id'] in tids or qids.intersection(t.get('question_ids',[]))]
    return {'nodes':nodes,'selected_id':selected,'scope_active':active,
            'context':{'material_id':material['id'],'learning_node_id':(current or {}).get('id'),'source_links':(current or {}).get('effective_source_links',[]),
                       'explanation_ids':(current or {}).get('explanation_ids',[]),'passage_ids':sorted(related),'question_ids':[q['id'] for q in questions],'term_ids':terms},
            'default_directory':'learning' if stored.get('learning_outline') or material.get('reader_mode')=='authored' or material.get('kind')=='project' else 'source'}


def group_pdf_figures(document,path,output):
    """把有图注的矢量图及其短标签归为一个展示对象，原文字和坐标不删除。"""
    import fitz
    from pathlib import Path
    from source_parser import _asset,audit
    if document.get('figure_groups_version')==1:return document
    output=Path(output);groups=[];pdf=fitz.open(path);original=list(document['passages'])
    for index,page in enumerate(pdf,1):
        page_id=next((identity for identity,p in document['pages'].items() if p.get('number')==index),None)
        if not page_id or not hasattr(page,'cluster_drawings'):continue
        captions=[p for p in original if re.match(r'^\s*(?:Figure|Fig\.)\s+\d',p.get('en',''),re.I) and any(r.get('page_id')==page_id for r in p['refs'])]
        for caption in captions:
            box=next(r['bbox'] for r in caption['refs'] if r.get('page_id')==page_id)
            candidates=[r for r in page.cluster_drawings() if r.width>30 and r.height>20 and r.y1<=box[1]+3 and box[1]-r.y1<70]
            if not candidates:continue
            drawing=max(candidates,key=lambda r:r.get_area());region=fitz.Rect(drawing.x0-20,drawing.y0-5,drawing.x1+20,drawing.y1+10)&page.rect
            labels=[]
            for p in original:
                if p is caption or len(p.get('en','').strip())>45:continue
                refs=[r for r in p['refs'] if r.get('page_id')==page_id]
                if refs and any(region.contains(fitz.Point((r['bbox'][0]+r['bbox'][2])/2,(r['bbox'][1]+r['bbox'][3])/2)) for r in refs):labels.append(p)
            if not labels:continue
            region|=fitz.Rect(box);number=re.search(r'(\d+(?:\.\d+)*)',caption['en'])[1];identity=document['id']+'-figure-'+number.replace('.','-')
            src=_asset(page.get_pixmap(matrix=fitz.Matrix(2,2),clip=region,alpha=False).tobytes('png'),'.png',output)
            for p in labels:p['display_role']='figure_label';p['figure_owner']=identity;p['translatable']=False
            caption['display_role']='figure_caption';caption['figure_owner']=identity
            groups.append({'id':identity,'section_id':caption['section_id'],'kind':'image','en':'图 '+number,'zh':caption.get('zh',''),'raw_text':'','heading':False,'bullet':False,
                'source_ids':[],'origin_ids':[p['id'] for p in labels]+[caption['id']],'caption_id':caption['id'],'display_generated':True,'alt':'图 '+number,'src':src,
                'translatable':False,'precision':'object','refs':[{'page_id':page_id,'bbox':list(region)}],
                'sentences':[{'id':identity+'-s1','en':'图 '+number,'zh':'','refs':[{'page_id':page_id,'bbox':list(region)}],'precision':'object'}]})
    for p in original:
        if p.get('role')=='page_furniture':p['translatable']=False
        if p['kind']=='image' and p.get('refs') and all((r['bbox'][3]-r['bbox'][1])<2 for r in p['refs']):p['display_role']='decoration'
    document['passages'].extend(groups);document['figure_groups_version']=1;document['coverage']=audit(document);pdf.close();return document
