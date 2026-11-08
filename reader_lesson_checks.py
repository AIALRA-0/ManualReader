"""核对新讲解的显式证据，防止参数只出现在结构字段而没有写进正文。"""
import re
def compact(text):return re.sub(r'\s+|[`*]','',str(text))
def requirements(sources):
    rows=[];seen=set()
    for source in sources:
        text=source.get('original')or source.get('en')or source.get('translation')or source.get('zh')or''
        found=[(m[0],m[0])for m in re.finditer(r'\b\d+(?:\.\d+)?\s*(?:GHz|MHz|KiB|MiB|KB|MB|GB|TOPS)\b',text)]
        found += [(m[1],m[2])for m in re.finditer(r'parameter\s+(?:int\s+)?(\w+)\s*=\s*(\d+)',text)]
        found += [(m[1],m[2])for m in re.finditer(r'\.([A-Z_]\w*)\s*\(\s*(\d+)\s*\)',text)]
        for label,value in found:
            key=(source['id'],label,value)
            if key in seen:continue
            seen.add(key);rows.append({'id':'fact-'+str(len(rows)+1),'source_id':source['id'],'label':label,'value':value})
    return rows
EVIDENCE_CONTRACT={'prerequisites':[{'term':'正文实际出现的一个新概念或符号原词；多个概念分别登记，可共用同一句quote','part_id':'解释它的part编号','quote':'正文首次把它讲清的连续原句；实际解释必须在正文'}],'facts':[{'ids':['输入required_facts中的编号'],'part_id':'p1','quote':'正文说明这个数值属于谁、单位和作用的连续原句'}],'transitions':[{'part_id':'从第二个part开始，每个part一项','previous_part_id':'实际紧接的前一个part编号','quote':'本part承接的前一part正文连续原句，不引用标题或导语；只登记依据，不重复展示'}]}
def validate(explanation,sources,require_transitions=False):
    parts={p['id']:p for p in explanation['parts']};facts={f['id']:f for f in requirements(sources)};covered=set();expanded=0
    for row in explanation.get('evidence',{}).get('facts',[]):
        part=parts.get(row.get('part_id'));quote=compact(row.get('quote',''))
        if not part or not quote or quote not in compact(part['markdown']):raise ValueError('参数证据没有逐字落在正文中')
        for identity in row.get('ids',[]):
            fact=facts.get(identity)
            if not fact:raise ValueError('参数证据引用未知编号：'+str(identity))
            numeric=lambda value:re.findall(r'\d+(?:\.\d+)?',value)
            if not all(number in numeric(quote)for number in numeric(fact['value'])):raise ValueError('正文参数数值与原材料不一致：'+identity)
            units=re.findall(r'GHz|MHz|KiB|MiB|KB|MB|GB|TOPS',fact['value'])
            if any(unit not in quote for unit in units):raise ValueError('正文参数没有保留单位：'+identity)
            if re.fullmatch(r'[A-Za-z_]\w*',fact['label'])and fact['label']not in quote:raise ValueError('正文参数没有说明对应的标识：'+identity)
            covered.add(identity)
    missing=set(facts)-covered
    if missing:raise ValueError('下列原始参数尚未在正文讲明白：'+','.join(sorted(missing)))
    for row in explanation.get('evidence',{}).get('prerequisites',[]):
        part=parts.get(row.get('part_id'));quote=compact(row.get('quote',''));term=compact(row.get('term',''))
        if not part or len(quote)<8 or quote not in compact(part['markdown'])or not term:raise ValueError('前置解释需要逐字对应正文中实际讲清的句子')
        # term是索引名称；“与或逻辑”等名称可以对应讲清&和|的真实正文，不要求正文机械重复索引文字。
        if term not in quote:expanded+=1
    transitions=explanation.get('evidence',{}).get('transitions',[])
    if require_transitions or transitions:
        order=list(parts);expected={identity:order[i-1]for i,identity in enumerate(order)if i};found=set()
        for row in transitions:
            identity=row.get('part_id');previous=row.get('previous_part_id');quote=compact(row.get('quote',''))
            if identity in found or identity not in expected or expected[identity]!=previous:raise ValueError('承接依据必须逐项对应实际相邻的两个讲解部分')
            if len(quote)<8 or quote not in compact(parts[previous]['markdown']):raise ValueError('承接依据没有对应前一部分的实际正文，请保留原稿并核对这一处')
            found.add(identity)
        if found!=set(expected):raise ValueError('下列讲解部分尚未登记前一段实际接住的内容：'+','.join(sorted(set(expected)-found)))
    return {'required_facts':len(facts),'covered_facts':len(covered),'transitions':len(transitions),'prerequisite_labels_without_literal_match':expanded}
