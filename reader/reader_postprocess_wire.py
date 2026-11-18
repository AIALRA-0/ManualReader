"""Recover mechanically equivalent wire fields without rewriting model content."""
from copy import deepcopy

CONTRACTS={'A1':'resource-explanation-v2','A2':'resource-explanation-v2','B1':'translation-stage-v1',
           'E':'source-map-candidates-v2','F1':'chapter-guidance-v1','F2':'terms-candidates-v1','F3':'quiz-candidates-v1'}


def contract_for(spec,name):
    if name=='B2':
        return None
    key=CONTRACTS.get(name)
    if key not in spec.schemas:
        return None
    schema=spec.schemas[key]
    return schema['properties']['groups']['items'] if name in {'A1','A2'} else schema


def compatible(name, raw, payload):
    result=deepcopy(raw)
    changes=[]
    def alias(row,key,values):
        before=row.get(key)
        if isinstance(before,str) and before in values and values[before]!=before:
            row[key]=values[before]
            changes.append({'field':key,'from':before,'to':row[key]})
    if name=='B1':
        for group in result.get('groups',[]):
            if 'kind' in group:
                group.pop('kind')
                changes.append({'field':'group.kind','action':'omit_redundant'})
            for seg in group.get('segments',[]):
                alias(seg,'status',{'ok':'complete','translated':'complete','success':'complete','needs_verification':'needs_review'})
    elif name in {'A1','A2'}:
        target=result.get('group',result)
        offered=set(payload.get('source_excerpts',{}))
        for comp in target.get('components',[]):
            alias(comp,'certainty',{'certain':'grounded','high':'grounded','confirmed':'grounded',
                                    'medium':'uncertain','low':'uncertain','inferred':'uncertain'})
            provided=comp.get('evidence_ids',[])
            if isinstance(provided,list) and any(x not in offered for x in provided):
                comp['evidence_ids']=[x for x in provided if x in offered]
                comp['certainty']='uncertain'
                changes.append({'field':'component.evidence_ids','action':'leave_unverified',
                                'component_id':comp.get('component_id')})
        alias(target,'status',{'ok':'complete','needs_review':'partial'})
    elif name=='E':
        for item in result.get('links',[]):
            alias(item,'confidence',{'candidate':'tentative','high':'tentative','medium':'tentative','low':'tentative'})
    elif name=='F1':
        notes=result.get('uncertainties')
        if isinstance(notes,list) and any(isinstance(n,str) for n in notes):
            for n in notes:
                if isinstance(n,str):
                    changes.append({'field':'uncertainties','action':'preserve_chapter_level_note_in_log','text':n})
            result['uncertainties']=[n for n in notes if not isinstance(n,str)]
        for item in result.get('part_recaps',[]):
            alias(item,'status',{'ok':'complete','supported':'complete','partial':'needs_review'})
    elif name in {'F2','F3'}:
        expected='terms-candidates-v1' if name=='F2' else 'quiz-candidates-v1'
        if result.get('schema_version')==expected:
            result.pop('schema_version')
            changes.append({'field':'schema_version','action':'remove_duplicate_wire_version'})
        if name=='F2':
            result.setdefault('uncertainties',[])
            if isinstance(result.get('uncertainties'),list):
                for note in result['uncertainties']:
                    if isinstance(note,str):
                        changes.append({'field':'uncertainties','action':'preserve_chapter_level_note_in_log','text':note})
                result['uncertainties']=[n for n in result['uncertainties'] if not isinstance(n,str)]
        if name=='F3' and result.get('uncertainties')==[]:
            result.pop('uncertainties')
        for row in result.get('items',[]):
            alias(row,'status',{'ok':'complete','ready':'complete','draft':'needs_review',
                               'pending_review':'needs_review','pending_verification':'needs_review','partial':'needs_review'})
            if name=='F2':
                alias(row,'action',{'create_new':'new','create':'new','reuse':'reuse_existing'})
    return result, changes
