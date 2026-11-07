"""同一次写作的开头、路线和结尾接入现有讲解块，不另外生成练习。"""
import copy,json,unicodedata
NARRATIVE_CONTRACT={'opening':'接住上一条已经讲清的认识，直接引出本条的问题；首条从具体任务开始','flow':[{'part_id':'本章讲解part编号','label':'这一步要弄明白的问题'}],'closing':'总结本条结果，再引出目录下一条的问题；末条给出完成结果'}
OBJECTIVE_CONTRACT={'id':'o1','text':'一个具体可检查的动作','expected_response':'用户做对时实际说出的判断或算出的结果'}

def validate_objectives(objectives,parts,quizzes):
    if not isinstance(objectives,list)or not objectives:raise ValueError('新讲解需要登记实际训练的目标')
    pmap={p['id']:p for p in parts};qmap={q.get('id'):q for q in quizzes};gmap={g.get('id'):g for g in objectives}
    if len(gmap)!=len(objectives)or None in gmap:raise ValueError('目标编号缺失或重复')
    for g in objectives:
        if not all(isinstance(g.get(k),str)and g[k].strip()for k in ('text','conditions','expected_response')):raise ValueError('目标需要具体动作、使用条件和预期表现')
        if not g.get('part_ids')or not g.get('quiz_ids')or not set(g['part_ids'])<=set(pmap)or not set(g['quiz_ids'])<=set(qmap):raise ValueError('目标引用的正文或题目不存在')
        for identity in g['quiz_ids']:
            q=qmap[identity]
            if g['id']not in q.get('objective_ids',[])or q.get('placement',{}).get('after_part_id')not in g['part_ids']:raise ValueError('目标和实际题目没有双向对应')
    for q in quizzes:
        assessment=q.get('assessment',{})
        if not q.get('objective_ids')or not set(q['objective_ids'])<=set(gmap)or assessment.get('objective_id')not in q['objective_ids']or not assessment.get('task')or not assessment.get('correct_evidence'):raise ValueError('题目需要交代它实际检验哪个目标和哪个判断')
    return copy.deepcopy(objectives)

def validate_narrative(narrative,parts):
    if not isinstance(narrative,dict)or not all(isinstance(narrative.get(k),str)and narrative[k].strip()for k in ('opening','closing')):raise ValueError('讲解需要自然开头和结尾')
    flow=narrative.get('flow')
    if not isinstance(flow,list)or any(not isinstance(s,dict)or not isinstance(s.get('label'),str)or not s['label'].strip()for s in flow):raise ValueError('讲解路线需要逐步写清要解决的问题')
    if [s.get('part_id')for s in flow]!=[p['id']for p in parts]:raise ValueError('讲解路线必须按顺序对应本章的每个正文部分')
    return copy.deepcopy(narrative)

def _plain_frame_blocks(data,blocks):
    narrative=data.get('narrative')
    if not narrative:return blocks
    narrative=validate_narrative(narrative,blocks)
    if any(b['id']in('opening','route','closing')for b in blocks):raise ValueError('opening、route、closing用于讲解首尾和路线，请给正文部分其他稳定编号')
    links=[];source_ids=[]
    for b in blocks:
        for link in b.get('source_ranges',[]):
            if link not in links:links.append(copy.deepcopy(link))
        source_ids.extend(b.get('explained_source_ids',[]))
    mapping={'source_ranges':links,'explained_source_ids':list(dict.fromkeys(source_ids)),'reading_heading':'','progress_exempt':True}
    lines=['flowchart TD']+[f's{i}[{json.dumps(diagram_label(step["label"]),ensure_ascii=False)}]'for i,step in enumerate(narrative['flow'])]+[f's{i} --> s{i+1}'for i in range(len(narrative['flow'])-1)]
    return [{'id':'opening','markdown':narrative['opening'],**mapping},{'id':'route','markdown':'```mermaid\n'+'\n'.join(lines)+'\n```',**mapping}]+blocks+[{'id':'closing','markdown':narrative['closing'],**mapping}]

def validate_context(explanation,context):
    if context is None:return
    framing=explanation.get('framing',{})
    previous=any(row.get('role')=='previous'for row in framing.get('start',[]))
    following=any(row.get('role')=='next'for row in framing.get('end',[]))
    if previous!=bool(context.get('previous')):raise ValueError('章首承接与实际目录上一条不一致；首条省略前文承接')
    if following!=bool(context.get('next')):raise ValueError('章末引导与实际目录下一条不一致；末条省略下一条引导')

def reading_context(nodes,identity,stored):
    from reader_blocks import sequence_context
    context=sequence_context(nodes,identity)
    for field in ('previous','current','next'):
        node=context[field]
        if node is None:continue
        context[field]={k:copy.deepcopy(node[k])for k in ('id','number','title')if k in node}
        if field=='previous':
            rows=[e for e in stored.get('explanations',[])if e['id']in node.get('explanation_ids',[])]
            closings=[e.get('narrative',{}).get('closing','')for e in rows]
            context[field]['previous_closing']='\n'.join(c for c in closings if c)[-1000:]
            # 取真正显示的末段，让写作者知道上一节实际讲到哪一步。
            passage_map={p['id']:p for p in stored.get('passages',[])}
            bodies=[passage_map[pid] for e in rows for pid in e.get('passage_ids',[]) if pid in passage_map]
            bodies=[p for p in bodies if not(p.get('progress_exempt')or p.get('heading')or p.get('display_omit'))]
            context[field]['previous_body_tail']=[];remaining=1600
            from reader_blocks import markdown_chunks
            for passage in reversed(bodies[-2:]):
                selected=[]
                for chunk in reversed(markdown_chunks(passage.get('display_markdown',passage.get('zh')or passage.get('en')or''))):
                    if len(chunk)>remaining:continue
                    selected.insert(0,chunk);remaining-=len(chunk)
                    if len(selected)>=2:break
                if selected:context[field]['previous_body_tail'].insert(0,{'passage_id':passage['id'],'text':'\n\n'.join(selected)})
    return context


FRAMING_CONTRACT={'start': [{'role': 'previous', 'heading': '接着上一节', 'markdown': '接住目录前一条已知事实；首章省略'}, {'role': 'current', 'heading': '这一节要弄明白什么', 'markdown': '单独引出本节问题'}], 'map_heading': '阅读流程', 'end': [{'role': 'recap', 'heading': '把这一节串起来', 'bullets': [{'part_id': 'p1', 'context': '独立一句完整说明', 'problem': '独立一句完整说明', 'claim': '独立一句完整说明', 'reason': '独立一句完整说明', 'evidence': '独立一句完整说明', 'implication': '独立一句完整说明'}]}, {'role': 'next', 'heading': '接下来要解决什么', 'markdown': '接住本节结果引出目录下一条；末章省略'}]}

def prepare_explanation(explanation):
    value=copy.deepcopy(explanation);framing=value.get('framing')
    if framing and any('role'in r for r in framing.get('start',[])):
        value=__import__('reader_connected').validate_connected(value);framing=value['framing']
    if not framing:return value
    for name in ('start','end'):
        rows=framing.get(name)
        if not isinstance(rows,list)or not 1<=len(rows)<=2 or any(not isinstance(r,dict)or not all(isinstance(r.get(k),str)and r[k].strip()for k in ('heading','markdown'))for r in rows):raise ValueError('章首和章末分别需要一至两段，每段都要有自己的小标题')
    if not isinstance(framing.get('map_heading'),str)or not framing['map_heading'].strip():raise ValueError('整张讲解路线需要自己的小标题')
    parts=value.get('parts',[])
    for part in parts:
        if not all(isinstance(part.get(k),str)and part[k].strip()for k in ('premise','question')):raise ValueError('每个讲解部分需要先说明具体事实，再提出由它引出的问题')
        premise=part['premise'].strip().rstrip('。；;，,：:');question=part['question'].strip().rstrip('？?')
        part['premise']=premise;part['question']=question
        heading=str(part.get('heading')or'').strip().rstrip('？?')
        if not heading or len(heading)>24 or '\n'in heading:raise ValueError('请单独提供24字以内的短标题；事实和问题放在premise与question中，不塞进标题')
        part['heading']=heading
    value['narrative']={'opening':'\n\n'.join(r['markdown']for r in framing['start']),'flow':__import__('reader_connected').flow_steps(parts),'closing':'\n\n'.join(r['markdown']for r in framing['end'])}
    return value

def prepare_output(data):
    value=copy.deepcopy(data)
    if value.get('framing'):value.setdefault('explanation',{})['framing']=value['framing']
    explanation=value.get('explanation',{})
    if not explanation.get('framing'):return value
    explanation=prepare_explanation(explanation);value['explanation']=explanation;value['narrative']=explanation['narrative']
    quizzes=value.get('quizzes',[]);parts={p['id']:p for p in explanation['parts']};goals=value.get('objectives',[]);gmap={g['id']:g for g in goals}
    for goal in goals:
        own=[q for q in quizzes if goal['id']in q.get('objective_ids',[])];goal['quiz_ids']=[q['id']for q in own]
        goal['part_ids']=list(dict.fromkeys(q.get('placement',{}).get('after_part_id')for q in own));goal['conditions']='；'.join(q['prompt'].split('\n')[0]for q in own)
        goal['source_ids']=list(dict.fromkeys(s for pid in goal['part_ids']if pid in parts for s in parts[pid].get('source_ids',[])))
    for quiz in quizzes:
        owner=parts.get(quiz.get('placement',{}).get('after_part_id'),{});quiz.setdefault('source_ids',owner.get('source_ids',[]))
        keys=quiz.get('objective_ids',[])
        if keys and keys[0]in gmap:quiz['assessment']={'objective_id':keys[0],'task':quiz['prompt'],'correct_evidence':'；'.join(gmap[k]['expected_response']for k in keys if k in gmap)}
    explanation['objectives']=copy.deepcopy(goals);return value

def diagram_label(label):
    # 按显示宽度换行，让长事实和问题在窄栏里仍能读清。
    lines=[]
    for paragraph in label.split('<br/>'):
        row='';width=0
        for char in paragraph:
            step=2 if unicodedata.east_asian_width(char)in('F','W')else 1
            if width+step>34 and row:lines.append(row);row='';width=0
            row+=char;width+=step
        if row:lines.append(row)
    return '<br/>'.join(lines)

def frame_blocks(data,blocks):
    framing=data.get('framing')
    if not framing:return _plain_frame_blocks(data,blocks)
    mapping={'source_ranges':[],'explained_source_ids':[],'progress_exempt':True}
    for block in blocks:
        for link in block.get('source_ranges',[]):
            if link not in mapping['source_ranges']:mapping['source_ranges'].append(copy.deepcopy(link))
        mapping['explained_source_ids'].extend(block.get('explained_source_ids',[]))
    mapping['explained_source_ids']=list(dict.fromkeys(mapping['explained_source_ids']))
    def entry(row,identity):return {'id':identity,'markdown':row['markdown'],'reading_heading':row['heading'].strip().rstrip('？?'),**copy.deepcopy(mapping)}
    opening=[entry(row,'opening'if i==0 else'opening-question')for i,row in enumerate(framing['start'])]
    closing=[entry(row,'closing'if i==0 else'closing-question')for i,row in enumerate(framing['end'])]
    flow=data['narrative']['flow'];lines=['flowchart TD']+[f's{i}[{json.dumps(diagram_label(step["label"]),ensure_ascii=False)}]'for i,step in enumerate(flow)]+[f's{i} --> s{i+1}'for i in range(len(flow)-1)]
    route={'id':'route','markdown':'```mermaid\n'+'\n'.join(lines)+'\n```','reading_heading':framing['map_heading'].strip().rstrip('？?'),**copy.deepcopy(mapping)}
    return opening+[route]+blocks+closing
