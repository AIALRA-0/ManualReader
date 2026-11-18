"""复用术语时保留编号；明确修订时保存旧解释和版本。"""
from reader_contract import canonical_term


def revise_term(item, proposal, stamp):
    fields={key:proposal[key].strip() for key in ('professional_definition','intuitive_explanation') if isinstance(proposal.get(key),str) and proposal[key].strip()}
    if not fields:return False
    if proposal.get('action')!='revise':
        for key,value in fields.items():
            if not item.get(key):item[key]=value
        item['needs_intuitive_explanation']=not bool(item.get('intuitive_explanation'))
        return False
    version=item.get('version',1)
    if proposal.get('expected_version')!=version:raise ValueError('术语已经更新，请读取当前版本后再修订')
    if all(item.get(key)==value for key,value in fields.items()):return False
    item.setdefault('definition_history',[]).append({'version':version,'at':stamp(),'definition':item.get('definition'),'professional_definition':item.get('professional_definition'),'intuitive_explanation':item.get('intuitive_explanation')})
    item.update(fields)
    if 'professional_definition' in fields:item['definition']=fields['professional_definition']
    item['version']=version+1;item['needs_intuitive_explanation']=not bool(item.get('intuitive_explanation'))
    return True
