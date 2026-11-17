"""Resolve bridge proposals against the actual order of body and supplements."""

from collections import defaultdict


def gap_for(draft,anchors,body):
    anchor=draft['anchor']
    if draft['side']=='after':
        return anchor['end']
    before,_=body_neighbors(anchors,anchor['start'])
    if before and not body[before['end']:anchor['start']].strip():
        return before['end']
    return anchor['start']


def inline_groups(drafts,anchors,body):
    groups=defaultdict(list)
    for draft in drafts:
        if draft['action'] in ('keep_inline','move_inline'):
            groups[gap_for(draft,anchors,body)].append(draft)
    return groups


def unique_quote(text, quote):
    if not isinstance(quote,str) or not quote.strip():
        return []
    start=text.find(quote)
    if start<0:
        return []
    second=text.find(quote,start+1)
    return [start] if second<0 else [start,second]


def body_neighbors(anchors,offset):
    before=next((a for a in reversed(anchors) if a['end']<=offset),None)
    after=next((a for a in anchors if a['start']>=offset),None)
    return before,after


def quote_in(anchor,text,quote):
    return anchor and quote in text[anchor['start']:anchor['end']]


def transition_quote(body, patches, proposal, draft=None):
    quote=proposal['anchor_quote']
    # Stable overlay identity disambiguates text copied from the original manuscript.
    if draft:
        matches=unique_quote(draft['patch']['markdown'],quote)
        if len(matches)==1:
            return draft['id'],matches[0]
        if len(matches)>1:
            raise ValueError('bridge_quote_not_unique_in_overlay')
    anchors=unique_quote(body,quote)
    matching=[(d['id'],offset) for d in patches if d is not draft
              for offset in unique_quote(d['patch']['markdown'],quote)]
    if len(anchors)==1:
        return 'C3',anchors[0]
    if not anchors and len(matching)==1:
        return matching[0]
    raise ValueError('bridge_quote_missing_or_ambiguous')


def resolve_transitions(body, anchors, drafts, raw, base, body_ops, editor, invalid):
    groups=inline_groups(drafts,anchors,body)
    lookup={d['id']:d for d in drafts}
    insertions=defaultdict(list)
    for index,row in enumerate(raw):
        ident=f'bridge-{index+1}'
        try:
            key=row['overlay_id']
            draft=lookup.get('overlay:'+key) if key else None
            if key and draft is None:
                raise ValueError('unknown_overlay_id')
            if draft and draft['action'] not in ('keep_inline','move_inline'):
                raise ValueError('bridge_overlay_not_inline')
            quote_scope, quote_start=transition_quote(body,drafts,row,draft)
            side=row['side']
            quote=row['anchor_quote']
            if draft is None and quote_scope!='C3':
                draft=lookup[quote_scope]
                if draft['action'] not in ('keep_inline','move_inline'):
                    raise ValueError('bridge_overlay_not_inline')
            quote_anchor=next((a for a in anchors if a['start']<=quote_start and
                               quote_start+len(quote)<=a['end']),None) if quote_scope=='C3' else None

            if draft:
                offset=gap_for(draft,anchors,body)
                peer=groups[offset]
                at=peer.index(draft)
                prev,next_=body_neighbors(anchors,offset)
                adjacent=(prev,next_,draft['original_anchor'])
                if quote_scope=='C3' and not any(quote_in(a,body,quote) for a in adjacent):
                    raise ValueError('bridge_quote_does_not_identify_overlay_boundary')
                if quote_scope!='C3' and lookup[quote_scope] not in peer:
                    raise ValueError('bridge_quote_points_to_different_overlay_boundary')
                if side=='between':
                    if len(peer)<2:
                        raise ValueError('no_adjacent_inline_supplement')
                    other=peer.index(lookup[quote_scope]) if quote_scope!='C3' else at
                    if other!=at and abs(other-at)!=1:
                        raise ValueError('supplements_not_adjacent')
                    slot=(max(at,other) if other!=at else at+1 if at+1<len(peer) else at)
                else:
                    slot=at+(side=='after')
            else:
                if not quote_anchor:
                    raise ValueError('bridge_quote_not_in_body_anchor')
                if side=='between':
                    left=gap_for({'anchor':quote_anchor,'side':'before'},anchors,body)
                    offset=next((i for i in (left,quote_anchor['end'])
                                 if len(groups.get(i,[]))>=2),None)
                    if offset is None:
                        raise ValueError('no_adjacent_inline_supplement')
                    slot=1
                else:
                    offset=quote_anchor['start' if side=='before' else 'end']
                    slot=0 if side=='before' else len(groups.get(offset,[]))

            if row['operation']=='insert':
                replacement=row['replacement']
                if row['original_exact'] is not None or not replacement.strip():
                    raise ValueError('invalid_bridge_insertion')
                if any(b['slot']==slot and b['text']==replacement for b in insertions[offset]):
                    raise ValueError('duplicate_insert_at_same_position')
                insertions[offset].append({'id':ident,'slot':slot,'text':replacement,'proposal':row})
                continue

            if row['operation']!='replace' or not isinstance(row['original_exact'],str):
                raise ValueError('invalid_bridge_replacement')
            if quote_scope=='C3':
                op=editor(body,row,base,body_ops)
                if quote_anchor is None or not quote_anchor['start']<=op['start']<op['end']<=quote_anchor['end']:
                    raise ValueError('bridge_replacement_and_quote_are_in_different_passages')
                if draft:
                    prev,next_=body_neighbors(anchors,offset)
                    if side=='between' or quote_anchor!=(prev if side=='before' else next_):
                        raise ValueError('bridge_replacement_not_at_final_side')
                body_ops.append({**op,'kind':'transition_replace','bridge_id':ident,'proposal':row})
            else:
                selected=lookup[quote_scope]
                op=editor(selected['patch']['markdown'],row,selected['scan'],selected['planned_edits'])
                selected['planned_edits'].append({**op,'bridge_id':ident,'kind':'transition_replace','proposal':row})
        except ValueError as exc:
            invalid.append({'scope':'transition','proposal':row,'transition_id':ident,'reason':str(exc)})
    return groups,insertions
