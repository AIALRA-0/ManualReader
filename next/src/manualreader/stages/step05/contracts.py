from hashlib import sha256
import json
import re


KINDS=('text','formula','table','reuse_original','mermaid','diagram','code')
EVIDENCE=('verified_layout_duplicate','verified_parent_formula','verified_caption_member',
          'verified_source_group_member')


def obj(**fields):
    return {'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}


def arr(item):
    return {'type':'array','items':item}


def enum(values):
    return {'type':'string','enum':list(values)}


TEXT={'type':'string'}
LINK=obj(source_ids=arr(TEXT),body_anchor_ids=arr(TEXT))
PATCH=obj(anchor_id=TEXT,placement=enum(('before','after')),kind=enum(KINDS),markdown=TEXT,
          source_ids=arr(TEXT),asset_refs=arr(TEXT))
CANDIDATE=obj(source_id=TEXT,parent_source_id=TEXT,evidence_kind=enum(EVIDENCE))
SCHEMAS={'05': obj(links=arr(LINK)), '06A':obj(additional_links=arr(LINK),additions=arr(PATCH),
                                             absorb_candidates=arr(CANDIDATE)),
         '06B':obj(additions=arr(PATCH),
                   goal_links=arr(obj(role=enum(('reader','teacher')),goal_index={'type':'integer'},
                                      body_anchor_ids=arr(TEXT),addition_indices=arr({'type':'integer'}))),
                   question_links=arr(obj(question_quote=TEXT,body_anchor_ids=arr(TEXT),
                                          addition_indices=arr({'type':'integer'}))))}


def digest(value):
    return sha256(value if isinstance(value,bytes) else value.encode()).hexdigest()


def links(groups, sources, anchors, rejected):
    pairs = set()
    for group in groups:
        if not isinstance(group,dict):
            rejected.append({'type':'link','reason':'invalid_group'})
            continue
        source_ids = group.get('source_ids')
        anchor_ids = group.get('body_anchor_ids')
        if not isinstance(source_ids,list) or not isinstance(anchor_ids,list):
            rejected.append({'type':'link','reason':'invalid_list'})
            continue
        for sid in source_ids:
            for aid in anchor_ids:
                if isinstance(sid,str) and isinstance(aid,str) and sid in sources and aid in anchors:
                    pairs.add((sid,aid))
                else:
                    rejected.append({'type':'link','reason':'unknown_id','source_id':sid,'anchor_id':aid})
    return pairs


def additions(rows, stage, allowed_sources, anchor_ids, assets, rejected):
    result={}
    for item in rows:
        if not isinstance(item,dict):
            rejected.append({'type':'overlay','reason':'invalid_shape'})
            continue
        aid=item.get('anchor_id')
        text=item.get('markdown')
        src=item.get('source_ids')
        media=item.get('asset_refs')
        placement=item.get('placement')
        kind=item.get('kind')
        valid = (isinstance(aid,str) and aid in anchor_ids and isinstance(text,str) and text.strip() and
                 placement in ('before','after') and kind in KINDS and isinstance(src,list) and
                 all(isinstance(x,str) and x in allowed_sources for x in src) and isinstance(media,list) and
                 all(isinstance(x,str) and x in assets for x in media) and
                 (stage!='06A' or bool(src)))
        if not valid:
            rejected.append({'type':'overlay','reason':'invalid_fields','item':item})
            continue
        references=set(media)
        for path in re.findall(r'!\[[^\]\n]*\]\(<?([^\s)>]+)',text):
            target=path.removeprefix('asset:')
            if target in references:
                continue
            if not any(path==assets[asset].get('media_path') for asset in references):
                rejected.append({'type':'overlay','reason':'unverified_image','path':path,'item':item})
                break
        else:
            src=sorted(set(src))
            media=sorted(references)
            identifier=digest(json.dumps([stage,aid,placement,kind,text,src,media],ensure_ascii=False))
            pid=f'{stage}-{identifier[:20]}'
            result[pid]={'patch_id':pid,'origin':stage,'anchor_id':aid,'placement':placement,
                         'kind':kind,'markdown':text,'source_ids':src,'asset_refs':media}
    return list(result.values())


def _same_page(left, right):
    a, b = left.get('position') or {}, right.get('position') or {}
    return bool(a and b and a.get('type') == b.get('type') and
                a.get('start') == b.get('start') and a.get('end') == b.get('end'))


def _overlap_box(left, right):
    """Use actual source coordinates, not the page preview image as evidence."""
    for a in left.get('refs') or []:
        for b in right.get('refs') or []:
            ax, bx = a.get('bbox'), b.get('bbox')
            if a.get('page_id') != b.get('page_id') or not ax or not bx:
                continue
            x = max(0.0, min(ax[2], bx[2]) - max(ax[0], bx[0]))
            y = max(0.0, min(ax[3], bx[3]) - max(ax[1], bx[1]))
            amin = min(max(1.0,(ax[2]-ax[0])*(ax[3]-ax[1])),
                       max(1.0,(bx[2]-bx[0])*(bx[3]-bx[1])))
            if x * y / amin >= 0.2:
                return True
    return False


def absorption_is_verified(child, parent, kind):
    """One evidence-based NORMAL navigation absorb, never semantic coverage."""
    if (child['source_id'] == parent['source_id'] or
            child['file_id'] != parent['file_id'] or
            child.get('section_id') != parent.get('section_id')):
        return False
    a, b = (child.get('text') or '').strip(), (parent.get('text') or '').strip()
    atom_a = set(child.get('registered_unit_ids') or [])
    atom_b = set(parent.get('registered_unit_ids') or [])
    same_atom = bool(atom_a and atom_a <= atom_b)
    same_page = _same_page(child, parent)
    if kind == 'verified_layout_duplicate':
        return bool(a and a == b and child.get('role') == parent.get('role') == 'page_furniture')
    if not same_page:
        return False
    if kind == 'verified_parent_formula':
        return bool(same_atom or (a and len(a) >= 2 and a in b and
                                  (child.get('kind') == 'formula' or parent.get('kind') == 'formula')))
    if kind == 'verified_caption_member':
        return bool(same_atom or (_overlap_box(child,parent) and
                                  (child.get('kind')=='image' or parent.get('kind')=='image')))
    if kind == 'verified_source_group_member':
        return bool(same_atom or _overlap_box(child,parent) or
                    (a and len(a) >= 3 and a in b) or
                    child.get('role')=='page_furniture')
    return False


def consolidate(sources, anchors, initial, extra, overlays, suggestions, mode):
    targets={}
    substantive=set(initial)|set(extra)
    previous={}
    for patch in overlays:
        key=(patch['anchor_id'],digest(patch['markdown'].strip()))
        if key in previous:
            patch['duplicate_of']=previous[key]
        else:
            previous[key]=patch['patch_id']
        virtual='overlay:'+patch['patch_id']
        substantive.update((sid,virtual) for sid in patch['source_ids'])
    for sid,aid in sorted(substantive):
        targets.setdefault(sid,set()).add(aid)
    absorbed=[]
    direct={sid:set(aids) for sid,aids in targets.items()}
    for row in (suggestions if mode == 'NORMAL' else []):
        child=row.get('source_id')
        parent=row.get('parent_source_id')
        kind=row.get('evidence_kind')
        if (isinstance(child,str) and isinstance(parent,str) and child in sources and parent in direct
                and child not in targets and absorption_is_verified(sources[child],sources[parent],kind)):
            targets[child]=set(direct[parent])
            absorbed.append(row)
    forward={sid:sorted(aids) for sid,aids in sorted(targets.items())}
    relations=[{'source_id':sid,'body_anchor_id':aid} for sid,aids in forward.items() for aid in aids]
    reverse={}
    for row in relations:
        reverse.setdefault(row['body_anchor_id'],[]).append(row['source_id'])
    dispositions={}
    for anchor in anchors:
        aid=anchor['anchor_id']
        if aid in reverse:
            status='source_linked'
        elif anchor['text'].lstrip().startswith('#'):
            status='heading'
        elif mode=='ANSWER' and not sources:
            status='question_context'
        elif re.search(r'(?:示例|假设|作为说明).{0,8}[：:]|例如[，：:]',anchor['text']):
            status='illustrative_or_hypothetical'
        else:
            status='unattributed'
        dispositions[aid]=status
    for p in overlays:
        aid='overlay:'+p['patch_id']
        dispositions[aid]='source_linked' if aid in reverse else 'teaching_explanation'
    return {'links':relations,'source_to_explanation':forward,'explanation_to_source':reverse,
            'explanation_dispositions':dispositions,'absorption':absorbed,
            'unresolved_source_ids':[sid for sid in sources if sid not in targets],
            'substantively_linked_source_ids': sorted(direct),
            'scope':mode,'links_are_navigation':True}
