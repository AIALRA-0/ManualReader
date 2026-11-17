"""Apply localized edits and retain the STEP-06 identities and coordinates."""

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
from pathlib import Path
from time import perf_counter

from ..step03.cache import atomic
from ..step05.contracts import digest
from manualreader.artifacts import read_json as read, write_json as write
from .inference import input_files
from .resources import Media, scan_batch
from .transitions import resolve_transitions



class StructuralError(ValueError):
    pass


def overlap(left, right):
    return left[0]<right[1] and right[0]<left[1]


def exact(text, quote):
    if not isinstance(quote,str) or not quote.strip():
        raise ValueError('empty_exact_quote')
    first = text.find(quote)
    if first < 0 or text.find(quote,first+1) >= 0:
        raise ValueError('quote_not_unique')
    return first, first+len(quote)


def local_edit(text, row, scan, accepted):
    original, replacement = row['original_exact'], row['replacement']
    a,b = exact(text,original)
    if not isinstance(replacement,str):
        raise ValueError('replacement_is_not_text')
    if original==replacement:
        raise ValueError('unchanged')
    if any(overlap((a,b),(op['start'],op['end'])) for op in accepted):
        raise ValueError('overlapping_edit')
    enclosed={}
    last=0
    for resource in scan['resources']:
        start,end=resource['start'],resource['end']
        if not overlap((a,b),(start,end)):
            continue
        if not a<=start<end<=b:
            raise ValueError('edit_splits_protected_'+resource['kind'])
        raw=text[start:end]
        container=next((span for span in enclosed if span[0]<=start<end<=span[1]),None)
        if container:
            new_start=enclosed[container][0]+start-container[0]
        else:
            new_start=replacement.find(raw,last)
            if new_start<0:
                raise ValueError('edit_changes_protected_'+resource['kind'])
            last=new_start+len(raw)
        new_end=new_start+len(raw)
        if replacement[new_start:new_end]!=raw:
            raise ValueError('edit_changes_protected_'+resource['kind'])
        enclosed[start,end]=(new_start,new_end)
    return {'start':a,'end':b,'old':original,'new':replacement,
            'protected_spans':[[x,y,*dest] for (x,y),dest in enclosed.items()]}


def layer(text, operations):
    parts, manifest, cursor, output = [], [], 0, 0
    for op in sorted(operations,key=lambda item:(item['start'],item['end'])):
        a,b = op['start'],op['end']
        if not cursor<=a<=b<=len(text) or text[a:b]!=op['old']:
            raise ValueError('operations do not match base bytes')
        parts.extend((text[cursor:a],op['new']))
        output += a-cursor
        manifest.append({**op,'fused_start':output,'fused_end':output+len(op['new'])})
        output += len(op['new'])
        cursor=b
    return ''.join(parts)+text[cursor:],manifest


def invert(text, operations):
    for op in reversed(operations):
        a,b = op['fused_start'],op['fused_end']
        if text[a:b]!=op['new']:
            raise ValueError('cannot undo edits against a different fused version')
        text=text[:a]+op['old']+text[b:]
    return text


def shifted(offset, manifest, *, start):
    return offset+sum(len(op['new'])-len(op['old']) for op in manifest
                      if op['end']<offset or (op['end']==offset and (start or op['old'])))


def location(start, end, *, surface='body', path='body.fused.md'):
    return {'surface':surface,'path':path,'start':start,'end':end}


def remap_span(begin,end,manifest):
    return shifted(begin,manifest,start=True),shifted(end,manifest,start=False)


def remap_resource(begin,end,manifest):
    for op in manifest:
        for old_start,old_end,new_start,new_end in op.get('protected_spans',()):
            if (begin,end)==(old_start,old_end):
                return op['fused_start']+new_start,op['fused_start']+new_end
    return remap_span(begin,end,manifest)


def remap_anchor(begin,end,manifest,original):
    start,stop=remap_span(begin,end,manifest)
    for op in manifest:
        if not op['old'] or not overlap((begin,end),(op['start'],op['end'])):
            continue
        if op['start']<=begin<end<=op['end']:
            offset=op['new'].find(original)
            if offset>=0 and op['new'].find(original,offset+1)<0:
                return op['fused_start']+offset,op['fused_start']+offset+len(original)
        if op['start']<=begin<op['end']:
            start=op['fused_start']
        if op['start']<end<op['end']:
            stop=op['fused_end']
    return start,stop


def target_for_quote(body,quote,anchors):
    a,b=exact(body,quote)
    target=next((x for x in anchors if x['start']<=a<b<=x['end']),None)
    if target is None:
        raise ValueError('quote_is_not_inside_one_anchor')
    return target


def add_resources(rows, text, origin, inventory, sample, media, convert):
    for resource in inventory:
        a,b = resource['start'],resource['end']
        raw = text[a:b]
        row = {**convert(a,b),'origin':origin,'original_start':a,'original_end':b,'raw_content':raw,
               'resource_id':'res-'+digest(f'{sample}\0{origin}\0{resource["kind"]}\0{a}\0{b}\0{raw}')[:24],
               'kind':resource['kind'],'heading':resource['heading'],'status':resource['status'],
               'content_sha256':digest(raw)}
        if resource['kind']=='image' and resource['status']=='ok':
            row.update(media.resolve(resource.get('media_path')))
        rows.append(row)


def existing_targets(mapping, coverage):
    edges = {row['body_anchor_id'] for row in mapping['links']}
    edges.update(target for targets in mapping['source_to_explanation'].values() for target in targets)
    edges.update(target for key in ('goal_items','question_items','question_raw_units')
                 for item in coverage[key] for target in item['target_ids'])
    return edges


def overlay_decisions(body, anchors, patches, reply, base, scans, media, invalid):
    positions = {a['anchor_id']:i for i,a in enumerate(anchors)}
    models = defaultdict(list)
    for decision in reply.get('supplement_decisions',[]):
        models[decision['overlay_id']].append(decision)
    identifiers = {p['patch_id'] for p in patches}
    invalid.extend({'scope':'model','reason':'unknown_overlay_id','proposal':r}
                   for ident,rows in models.items() if ident not in identifiers for r in rows)
    drafts = []
    for patch,scan in zip(patches,scans,strict=True):
        identifier='overlay:'+patch['patch_id']
        options=models.get(patch['patch_id'],[])
        proposed = options[0] if len(options)==1 else None
        original=positions[patch['anchor_id']]
        base_anchor = anchors[original]
        state=dict(patch=patch,scan=scan,anchor=base_anchor,side=patch['placement'],
                   action='keep_inline',proposed=proposed,edits=[],id=identifier,
                   markdown=patch['markdown'],original_anchor=base_anchor,rejections=[])
        drafts.append(state)
        if not proposed:
            state['rejections'].append('missing_model_decision' if not options else 'duplicate_model_decision')
        if not media.patch_assets_readable(patch,scan['resources']):
            state['action']='unavailable_media'
            state['rejections'].append('overlay_original_media_cannot_be_verified')
            continue
        if not proposed:
            continue
        try:
            action=proposed['action']
            if action=='move_inline':
                destination=target_for_quote(body,proposed['new_anchor_quote'],anchors)
                side=proposed['insert_side']
                if side not in ('before','after'):
                    raise ValueError('invalid_insert_side')
                if destination!=base_anchor or side!=patch['placement']:
                    state.update(anchor=destination,side=side,action=action)
            elif action=='defer':
                if patch['origin']=='06A':
                    raise ValueError('required_06A_cannot_be_deferred')
                state['action']=action
            elif action=='omit_duplicate':
                state['requested_duplicate']=proposed['new_anchor_quote']
            elif action!='keep_inline':
                raise ValueError('unknown_action')
        except ValueError as exc:
            state['rejections'].append(str(exc))
            continue
        for change in proposed['text_revisions']:
            try:
                state['edits'].append(local_edit(patch['markdown'],change,scan,state['edits']))
            except ValueError as exc:
                invalid.append({'scope':identifier,'reason':str(exc),'proposal':change})
        state['markdown'],state['edits']=layer(patch['markdown'],state['edits'])

    lookup={x['id']:x for x in drafts}
    for draft in drafts:
        witness=draft.get('requested_duplicate')
        if witness is None:
            continue
        patch=draft['patch']
        other=lookup.get(witness) or lookup.get('overlay:'+witness)
        protected=bool(draft['scan']['resources'] or draft['scan']['links'] or
                       patch['asset_refs'] or patch['origin']=='06A')
        verified=None
        if (other and other is not draft and 'requested_duplicate' not in other
            and other['action'] in ('keep_inline','move_inline')
            and (not protected or (other['markdown']==patch['markdown']
                 and set(other['patch']['asset_refs'])==set(patch['asset_refs'])))):
            verified=other['id']
        else:
            try:
                anchor=target_for_quote(body,witness,anchors)
                if not protected or (anchor['text']==patch['markdown']==witness and not patch['asset_refs']):
                    verified=anchor['anchor_id']
            except ValueError:
                pass
        if verified:
            draft.update(action='omit_duplicate',alias=verified,edits=[])
        else:
            draft['rejections'].append('no_verifiable_duplicate_target')
            draft.update(markdown=patch['markdown'],edits=[])
    return drafts


def apply_decisions(sample, body, anchors, patches, mapping, coverage, reply, media, base, scans):
    for a in anchors:
        if body[a['start']:a['end']]!=a['text'] or digest(a['text'])!=a['content_sha256']:
            raise ValueError('STEP-06 anchor does not match STEP-04 draft')
    if len({p['patch_id'] for p in patches})!=len(patches):
        raise ValueError('duplicate overlay identity in frozen input')
    invalid,original_edits=[],[]
    drafts=overlay_decisions(body,anchors,patches,reply,base,scans,media,invalid)
    known_sources=set(mapping['source_to_explanation'])
    known_sources.update(sid for patch in patches for sid in patch['source_ids'])
    for row in reply.get('c3_edits',[]):
        try:
            if any(s not in known_sources for s in row['source_ids']):
                raise ValueError('unknown_source_identity')
            op=local_edit(body,row,base,original_edits)
            original_edits.append({**op,'kind':'c3_edit','proposal':row})
        except ValueError as exc:
            invalid.append({'scope':'C3','reason':str(exc),'proposal':row})

    for draft in drafts:
        draft['planned_edits']=[dict(op) for op in draft['edits']]
    groups,bridges=resolve_transitions(body,anchors,drafts,reply.get('transition_edits',[]),
                                        base,original_edits,local_edit,invalid)
    deferred, positions_by_id, resources = {}, {}, []
    for draft in drafts:
        if draft['action'] not in ('omit_duplicate','unavailable_media'):
            draft['markdown'],draft['edits']=layer(draft['patch']['markdown'],draft['planned_edits'])
        if draft['action'] in ('defer','unavailable_media'):
            text=draft['markdown']
            deferred[draft['id']]={'markdown':text,'status':draft['action'],
                                    'source_ids':draft['patch']['source_ids'],'asset_refs':draft['patch']['asset_refs']}
            positions_by_id[draft['id']]=location(0,len(text),surface='deferred',path='deferred.json')

    assembly=[]
    offsets=sorted(groups.keys()|bridges.keys())
    for op in original_edits:
        if any(op['start']<offset<op['end'] for offset in offsets):
            invalid.append({'scope':'transition' if op['kind']=='transition_replace' else 'C3',
                            'proposal':op['proposal'],'reason':'edit_crosses_insertion_boundary'})
        else:
            assembly.append(op)
    gap=('\r\n' if '\r\n' in body else '\n')*2
    for offset in offsets:
        buffer=''
        spans={}
        transition_spans={}
        group=groups.get(offset,[])
        slots=defaultdict(list)
        for bridge in bridges.get(offset,[]):
            slots[bridge['slot']].append(bridge)
        for slot in range(len(group)+1):
            for bridge in slots[slot]:
                buffer+=gap
                transition_spans[bridge['id']]={'start':len(buffer),'end':len(buffer)+len(bridge['text']),
                                                 'proposal':bridge['proposal']}
                buffer+=bridge['text']
            if slot<len(group):
                draft=group[slot]
                buffer+=gap
                spans[draft['id']]=(len(buffer),len(buffer)+len(draft['markdown']))
                buffer+=draft['markdown']
        assembly.append({'start':offset,'end':offset,'old':'','new':buffer+gap,
                         'kind':'overlay_insertion','overlay_spans':spans,'transition_spans':transition_spans})
    fused, manifest=layer(body,assembly)
    if invert(fused,manifest)!=body:
        raise ValueError('fused output does not reverse to the original bytes')
    for anchor in anchors:
        positions_by_id[anchor['anchor_id']]=location(*remap_anchor(
            anchor['start'],anchor['end'],manifest,anchor['text']))
    for op in manifest:
        for identifier,(a,b) in op.get('overlay_spans',{}).items():
            positions_by_id[identifier]=location(a+op['fused_start'],b+op['fused_start'])
    for draft in drafts:
        if alias:=draft.get('alias'):
            positions_by_id[draft['id']]={**positions_by_id[alias],'alias_of':alias}
    if existing_targets(mapping,coverage)-positions_by_id.keys():
        raise ValueError('a STEP-06 target was lost in fusion')

    add_resources(resources,body,'C3',base['resources'],sample,media,
                  lambda a,b:location(*remap_resource(a,b,manifest)))
    changes=[]
    for draft in drafts:
        identifier=draft['id']
        patch=draft['patch']
        target=positions_by_id[identifier]
        def convert(a,b):
            a,b=remap_resource(a,b,draft['edits'])
            return {**target,'start':target['start']+a,'end':target['start']+b}
        add_resources(resources,patch['markdown'],identifier,draft['scan']['resources'],sample,media,convert)
        changes.append({'overlay_id':patch['patch_id'],'origin':patch['origin'],'original_anchor_id':patch['anchor_id'],
                        'original_placement':patch['placement'],'placement':draft['side'],
                        'anchor_id':draft['anchor']['anchor_id'],'target':target,
                        'action':draft['action'],'proposal':draft['proposed'],'rejections':draft['rejections'],
                        'text_edits':draft['edits'],'original_markdown':patch['markdown'],
                        'source_ids':patch['source_ids'],'asset_refs':patch['asset_refs']})
    transition_log=[]
    for op in manifest:
        for bridge_id,row in op.get('transition_spans',{}).items():
            transition_log.append({'id':bridge_id,'proposal':row['proposal'],
                                   'target':location(op['fused_start']+row['start'],
                                                     op['fused_start']+row['end'])})
        if op['kind']=='transition_replace':
            transition_log.append({'id':op['bridge_id'],'proposal':op['proposal'],
                                   'target':location(op['fused_start'],op['fused_end'])})
    for draft in drafts:
        target=positions_by_id[draft['id']]
        for op in draft['edits']:
            if 'bridge_id' in op:
                a=target['start']+op['fused_start']
                b=target['start']+op['fused_end']
                transition_log.append({'id':op['bridge_id'],'proposal':op['proposal'],
                                       'target':{**target,'start':a,'end':b}})

    for row in resources:
        carrier=fused if row['surface']=='body' else deferred[row['origin']]['markdown']
        if carrier[row['start']:row['end']]!=row['raw_content']:
            raise StructuralError(f'protected_resource_bytes_changed:{row["resource_id"]}')
    for identifier,target in positions_by_id.items():
        carrier=fused if target['surface']=='body' else deferred[identifier]['markdown']
        if (not 0<=target['start']<target['end']<=len(carrier) or
            not carrier[target['start']:target['end']].strip()):
            raise StructuralError(f'empty_or_invalid_anchor:{identifier}')

    parsed,*bank_scans=scan_batch([fused]+[row['markdown'] for row in deferred.values()])
    baseline_links=Counter(base['links'])
    for draft in drafts:
        if draft['action']!='omit_duplicate':
            baseline_links.update(draft['scan']['links'])
    final_links=Counter(parsed['links'])
    for scan in bank_scans:
        final_links.update(scan['links'])
    if final_links!=baseline_links:
        raise StructuralError('markdown_link_targets_or_link_count_changed')

    inventories={'body':(fused,parsed)}
    inventories.update({k:(v['markdown'],scan) for (k,v),scan in zip(deferred.items(),bank_scans)})
    indexed=defaultdict(set)
    for row in resources:
        key='body' if row['surface']=='body' else row['origin']
        indexed[key].add((row['kind'],row['start'],row['end']))
    for key,(carrier,scan) in inventories.items():
        actual={(row['kind'],row['start'],row['end']):row for row in scan['resources']}
        if indexed[key]-actual.keys():
            raise StructuralError('existing_resource_no_longer_parses_after_assembly')
        new=[row for k,row in actual.items() if k not in indexed[key]]
        for row in new:
            if row['status']!='ok':
                raise StructuralError('invalid_new_markdown_resource:'+row['kind'])
            if row['kind']=='image' and media.resolve(row.get('media_path'))['status']!='ok':
                raise StructuralError('new_image_has_no_verified_media')
        origin='STEP07' if key=='body' else key
        dest=lambda a,b: location(a,b,surface='body' if key=='body' else 'deferred',
                                  path='body.fused.md' if key=='body' else 'deferred.json')
        add_resources(resources,carrier,origin,new,sample,media,dest)
    resources.sort(key=lambda r:(r['surface'],r['start'],r['end'],r['resource_id']))
    metrics={'overlays':len(patches),'actions':dict(Counter(x['action'] for x in changes)),
             'c3_edits':sum(op['kind']=='c3_edit' for op in manifest),
             'overlay_text_edits':sum('bridge_id' not in op for x in changes for op in x['text_edits']),
             'transition_edits':dict(Counter(x['proposal']['side'] for x in transition_log)),
             'transition_rejected':sum(x['scope']=='transition' for x in invalid),
             'rejected_edits':len(invalid),'overlays_with_fallback':sum(bool(x['rejections']) for x in changes),
             'source_navigation':mapping['source_navigation'],'source_edges':len(mapping['links']),
             'goals':coverage['goal_coverage'],'question_items':coverage['question_item_coverage'],
             'question_raw_units':coverage['question_raw_unit_coverage'],
             'resources':len(resources),'unique_visible_resources':len({
                 (r['kind'],r['start'],r['end']) for r in resources if r['surface']=='body'}),
             'deferred_resources':sum(r['surface']=='deferred' for r in resources),
             'resource_issues':sum(r['status']!='ok' for r in resources)}
    return fused, {'operations':manifest,'overlays':changes,'rejections':invalid,'transitions':transition_log}, {
        'anchors':positions_by_id,'mapping':mapping,'goal_question_coverage':coverage,
        'offset_unit':'UTF-8 Unicode code points, zero-based, end exclusive'}, resources,deferred,metrics


def fuse(sample,body,anchors,patches,mapping,coverage,reply,media):
    base,*scans=scan_batch([body]+[p['markdown'] for p in patches])
    args=(sample,body,anchors,patches,mapping,coverage)
    try:
        return apply_decisions(*args,reply,media,base,scans)
    except StructuralError:
        # Split conflicting edit groups until the actual broken proposal is isolated.
        pass
    groups=('supplement_decisions','c3_edits','transition_edits')
    all_items=[(k,row) for k in groups for row in reply.get(k,[])]
    accepted={key:[] for key in groups}
    rejected=[]
    working=apply_decisions(*args,accepted,media,base,scans)

    def add(items):
        nonlocal accepted,working
        combined={k:rows+[item for group,item in items if group==k] for k,rows in accepted.items()}
        try:
            working=apply_decisions(*args,combined,media,base,scans)
            accepted=combined
        except StructuralError as error:
            middle=len(items)//2
            if middle:
                add(items[:middle])
                add(items[middle:])
            elif items:
                kind,proposal=items[0]
                rejected.append({'scope':'transition' if kind=='transition_edits' else kind,
                                 'proposal':proposal,'reason':str(error)})

    add(all_items)
    for row in rejected:
        if row['scope']=='supplement_decisions':
            for overlay in working[1]['overlays']:
                if overlay['overlay_id']==row['proposal']['overlay_id']:
                    overlay['proposal']=row['proposal']
                    overlay['rejections']=[row['reason']]
    working[1]['rejections'].extend(rejected)
    working[5]['rejected_edits']+=len(rejected)
    working[5]['transition_rejected']+=sum(row['scope']=='transition' for row in rejected)
    return working


def assemble(root,sample):
    began=perf_counter()
    _,previous,run,body,_,source=input_files(root,sample)
    output=root/'jobs'/sample
    model=read(output/'model/outcome.json') if (output/'model/outcome.json').is_file() else {'state':'not_run'}
    inputs={p.name:digest(p.read_bytes()) for p in previous.glob('*.json')}
    args=(sample,body,read(previous/'anchors.json'),read(previous/'overlays.json'),
          read(previous/'step06-merged-mapping.json'),read(previous/'step06-goal-question-coverage.json'))
    media=Media(root/'media',run,source)
    result=fuse(*args,model.get('reply',{}),media)
    state=model['state']
    fused,edits,remap,resources,deferred,metrics=result
    output.mkdir(parents=True,exist_ok=True)
    before,after=digest(body),digest(fused)
    atomic(output/'body.fused.md',fused.encode('utf8'))
    for name,value in (('fusion-edits',edits),('anchor-remap',remap),
                       ('resources.fused',{'items':resources}),('deferred',deferred)):
        write(output/(name+'.json'),{'input_sha256':before,'fused_sha256':after,**value}
              if name!='deferred' else value)
    state_record={'sample_id':sample,'task_id':run['task_id'],'mode':run['mode'],
                  'fixture_based':run['fixture_based'],'state':state,'input_sha256':before,
                  'fused_sha256':after,'baseline':inputs,'assembly_seconds':perf_counter()-began,**metrics}
    write(output/'run.json',state_record)
    return state_record


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('root',type=Path)
    parser.add_argument('--parallel',type=int,default=20)
    args=parser.parse_args()
    root=args.root.resolve()
    began=perf_counter()
    failed={}
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        work={pool.submit(assemble,root,p.name):p.name for p in sorted((root/'previous').iterdir())}
        for job in as_completed(work):
            try:
                result=job.result()
                print(result['sample_id'],result['state'],result['actions'],flush=True)
            except (OSError,ValueError,RuntimeError,KeyError) as exc:
                failed[work[job]]=str(exc)
                print(work[job],str(exc),flush=True)
    write(root/'assembly-batch.json',{'wall_seconds':perf_counter()-began,'failures':failed})
    return int(bool(failed))


if __name__=='__main__':
    raise SystemExit(main())
