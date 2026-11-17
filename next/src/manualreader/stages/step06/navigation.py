"""Deterministic, provenance-preserving completion of *navigation* for all four modes.

Never calls a model or changes substantive source coverage.  Unmatched sources
can navigate to an adjacent valid explanation anchor, but the relation is always
marked navigation_only; no semantic match or lesson coverage is asserted.
STEP-05's one-pass mapping, original Source IDs, and STEP-06A/B are untouched.
"""


def _page(row):
    pos=row.get('position') or {}
    if pos.get('type') not in ('pdf_page','subtitle','subtitle_sid','subtitle_time',
                               'dom','html_dom_unit','line','text_span'):
        return None
    value=pos.get('start')
    return float(value) if isinstance(value,(int,float)) else None


def _anchor_order(anchors):
    return {a['anchor_id']:i for i,a in enumerate(anchors)}


def complete_source_navigation(merged, sources, anchors, overlays=()):
    """Give every valid source a *clickable* destination, without inventing evidence.

    Favor an actual mapped source from the same file/section/page. If a file has
    no mapped source, fall back to an existing body anchor and explicitly record
    that this is navigational context, not an explanation of the source.
    """
    if (merged.get('navigation_only_not_semantic_coverage') and
            not merged.get('unresolved_source_ids') and
            len(merged.get('source_to_explanation', {})) == len(sources)):
        return merged
    forward={k:list(v) for k,v in merged.get('source_to_explanation',{}).items()}
    anchor_rank=_anchor_order(anchors)
    verified_overlays={'overlay:'+p['patch_id'] for p in overlays
                       if isinstance(p,dict) and p.get('patch_id') and (p.get('markdown') or '').strip()}
    available=set(anchor_rank)|verified_overlays
    destination_rank={**anchor_rank,**{aid:len(anchor_rank)+i for i,aid in enumerate(sorted(verified_overlays))}}
    valid_sources={sid:row for sid,row in sources.items() if isinstance(sid,str)}
    # Use only real, already-renderable destinations, never fabricate an anchor.
    forward={sid:[a for a in aids if a in available] for sid,aids in forward.items() if sid in valid_sources}
    original=forward.copy()
    nonempty={sid:aids for sid,aids in original.items() if aids}
    rank={sid:i for i,sid in enumerate(valid_sources)}
    absorbed_by_source={row['source_id']:row for row in merged.get('absorption',[])}
    direct_sources=set(merged.get('substantively_linked_source_ids',nonempty))-absorbed_by_source.keys()
    by_file={}
    for sid in nonempty:
        by_file.setdefault(valid_sources[sid].get('file_id'),[]).append(sid)
    body_anchors=[a['anchor_id'] for a in anchors
                  if a['anchor_id'] in available and (a.get('text') or '').strip()
                  and not (a.get('text') or '').lstrip().startswith('#')]
    if not body_anchors:
        body_anchors=list(anchor_rank) or sorted(verified_overlays)
    navigation=[]
    for sid,src in valid_sources.items():
        if nonempty.get(sid):
            if sid in absorbed_by_source:
                row=absorbed_by_source[sid]
                parent=row['parent_source_id']
                navigation.extend(
                    {'source_id':sid,'parent_source_id':parent,'body_anchor_id':target,
                     'source_position':src.get('position'),'parent_position':sources[parent].get('position'),
                     'relation_type':'navigation_only','strategy':row['evidence_kind'],
                     'substantive_coverage':False,'navigation_scope':merged.get('scope')}
                    for target in nonempty[sid])
            continue
        local=by_file.get(src.get('file_id'),[])
        pid=None
        if local:
            sp=_page(src)
            def distance(other):
                parent=valid_sources[other]
                pp=_page(parent)
                same_section = src.get('section_id') == parent.get('section_id')
                return (not same_section,
                        sp is None or pp is None,
                        abs(sp-pp) if sp is not None and pp is not None else float('inf'),
                        other not in direct_sources,abs(rank[sid]-rank[other]),rank[other])
            pid=min(local,key=distance)
            targets=sorted(nonempty[pid],key=lambda a:destination_rank[a])
            target=targets[0]
            strategy='nearest_mapped_source_in_file'
        elif body_anchors:
            # No available mapped source in this file. The body remains viewable,
            # but this is only a navigational fallback, never evidence of coverage.
            target=body_anchors[0]
            strategy='body_context_fallback'
        else:
            # No real body anchor exists; do not create fictional links.
            continue
        forward[sid]=[target]
        navigation.append({'source_id':sid,'parent_source_id':pid,
                         'body_anchor_id':target,'relation_type':'navigation_only',
                         'source_position':src.get('position'),
                         'parent_position':valid_sources[pid].get('position') if pid else None,
                         'strategy':strategy,'substantive_coverage':False,
                         'navigation_scope':merged.get('scope')})
    out=dict(merged)
    out['source_to_explanation']={sid:forward[sid] for sid in valid_sources if forward.get(sid)}
    reverse={}
    for sid,aids in out['source_to_explanation'].items():
        for aid in aids:
            reverse.setdefault(aid,[]).append(sid)
    out['explanation_to_source']=reverse
    nav_by_pair={(row['source_id'],row['body_anchor_id']):row for row in navigation}
    out['links']=[nav_by_pair[(sid,aid)] if (sid,aid) in nav_by_pair else
                  {'source_id':sid,'body_anchor_id':aid,'relation_type':'direct_semantic',
                   'substantive_coverage':True}
                  for sid,aids in out['source_to_explanation'].items() for aid in aids]
    out['unresolved_source_ids']=[sid for sid in valid_sources if sid not in out['source_to_explanation']]
    out['navigation_only_links']=navigation
    out['navigation_only_source_ids']=list(dict.fromkeys(x['source_id'] for x in navigation))
    out['source_navigation']={
        'total':len(valid_sources),
        'linked':len(out['source_to_explanation']),
        'coverage_pct':round(100*len(out['source_to_explanation'])/len(valid_sources),2) if valid_sources else 100.0,
        'navigation_only_count':len(out['navigation_only_source_ids']),
        'direct_semantic_count':len(direct_sources & out['source_to_explanation'].keys()),
        'unresolved':len(out['unresolved_source_ids']),
    }
    out['navigation_only_not_semantic_coverage']=True
    return out


# Compatibility alias for clients already importing the earlier helper.
complete_normal_navigation=complete_source_navigation
