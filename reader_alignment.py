"""从稳定编号建立双向定位；只派生索引，不改原件、正文和用户记录。"""
import re

VERSION=1


def build_alignment(material):
    passages={p['id']:p for p in material.get('passages',[])}
    sources={i:p for i,p in passages.items() if p.get('content_kind')!='explanation'}
    by_source={i:{'translation_group_ids':[],'explanation_ids':[]} for i in sources}
    groups={}
    for group in material.get('reading_view',{}).get('groups',[]):
        ids=list(dict.fromkeys(i for i in group['passage_ids'] if i in sources))
        groups[group['id']]=ids
        for i in ids:by_source[i]['translation_group_ids'].append(group['id'])
    explanations={};unlinked=[];headings=[];bodies=set();invalid=[]
    for pid,p in passages.items():
        if p.get('content_kind')!='explanation' or p.get('display_omit'):continue
        links=[];registered=p.get('source_links',[])
        if not registered and p.get('file_id')=='primary':
            owners=[n for n in material.get('learning',{}).get('nodes',[]) if p.get('section_id') in n.get('legacy_section_ids',[])]
            if len(owners)==1:registered=owners[0].get('effective_source_links',[])
        for row in registered:
            source=sources.get(row.get('passage_id'))
            if not source:invalid.append({'explanation_id':pid,'source_id':row.get('passage_id')});continue
            sid=row.get('sentence_id')
            if sid and not any(s['id']==sid for s in source.get('sentences',[])):
                invalid.append({'explanation_id':pid,'sentence_id':sid});continue
            links.append({'passage_id':source['id'],'sentence_id':sid})
        quote=re.sub(r'\s+','',p.get('source_quote') or '')
        if len(quote)>=8:
            narrowed=[]
            for link in links:
                if link.get('sentence_id'):narrowed.append(link);continue
                source=sources[link['passage_id']]
                matches=[s for s in source.get('sentences',[]) if not s.get('historical') and any(
                    len(text)>=8 and (quote in text or text in quote)
                    for text in (re.sub(r'\s+','',s.get('en') or ''),re.sub(r'\s+','',s.get('zh') or '')))]
                narrowed.extend([dict(link,sentence_id=s['id']) for s in matches] or [link])
            links=narrowed
        ids=list(dict.fromkeys(l['passage_id'] for l in links))
        is_heading=p.get('heading') or re.fullmatch(r'\s*#{1,6}\s+[^\n]+\s*',p.get('zh') or p.get('en') or '')
        if not is_heading:bodies.add(pid)
        if not ids:
            (headings if is_heading else unlinked).append(pid)
        explanations[pid]={'source_ids':ids,'links':links,'precision':p.get('source_link_precision','none') if p.get('source_links') else 'chapter' if links else 'none'}
        for i in ids:by_source[i]['explanation_ids'].append(pid)
    return {'version':VERSION,'sources':by_source,'translation_groups':groups,'explanations':explanations,
            'audit':{'source_total':len(sources),'source_group_mapped':sum(bool(v['translation_group_ids']) for v in by_source.values()),
                     'explanation_total':len(explanations),'explanation_mapped':sum(bool(v['source_ids']) for v in explanations.values()),
                     'explanation_body_total':len(bodies),'explanation_body_mapped':sum(bool(explanations[i]['source_ids']) for i in bodies),
                     'unlinked_explanation_ids':unlinked,'standalone_heading_ids':headings,'invalid_links':invalid},
            'rule':'译文沿用原文编号；讲解按已登记的真实来源双向关联；没有生成的译文或讲解不伪造对应正文'}


def question_alignment(material,question):
    """问题和追问保留自己的位置，再经由原文编号连接三种内容。"""
    passages={p['id']:p for p in material.get('passages',[])}
    p=passages.get(question.get('passage_id'),{})
    target=question.get('target') or {}
    candidates=target.get('source_refs') or [question.get('passage_id')]
    if not isinstance(candidates,list):candidates=[]
    links=[]
    for pid in candidates:
        row=passages.get(pid,{})
        if row.get('content_kind')=='explanation':links.extend(material.get('alignment',{}).get('explanations',{}).get(pid,{}).get('links') or row.get('source_links',[]))
        elif row:links.append({'passage_id':pid,'sentence_id':question.get('sentence_id') if pid==question.get('passage_id') else None})
    unique={};explanation_ids=[p['id']] if p.get('content_kind')=='explanation' else []
    for l in links:
        source=passages.get(l.get('passage_id'),{})
        if not source or source.get('content_kind')=='explanation':continue
        sid=l.get('sentence_id')
        if sid and not any(s['id']==sid for s in source.get('sentences',[])):sid=None
        unique[(source['id'],sid)]={'passage_id':source['id'],'sentence_id':sid}
    return {'source_ids':list(dict.fromkeys(pid for pid,sid in unique)),'links':list(unique.values()),
            'explanation_ids':explanation_ids,'passage_id':question.get('passage_id')}
