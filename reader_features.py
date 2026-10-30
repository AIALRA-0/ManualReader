"""保存可编辑问法，按已有阅读记录计算进度，并导出用户自己的问题样本。"""
from pathlib import Path
from collections import Counter
import json,re,os

QUESTION_FORMS={
 '概念':['是什么？','是什么意思？','指的是什么？','包含哪些东西？','有什么特征？','能举一个例子吗？','有什么反例？'],
 '命名':['为什么叫这个名字？','名字是什么意思？','缩写的全称是什么？','为什么用这个符号？'],
 '作用':['有什么用？','在这里负责什么？','解决什么问题？','为什么要研究它？','对谁有价值？'],
 '原因':['为什么？','为什么需要它？','为什么这样做？','不这样做会怎样？','为什么在这里提到它？'],
 '前置':['先要知道什么？','这里默认我已经知道什么？','能先解释最基础的含义吗？'],
 '关系':['和前文有什么关系？','和另一个概念有什么区别？','哪些地方相同？','依赖什么条件？','为什么能推广？'],
 '过程':['第一步做什么？','下一步为什么这样做？','输入是什么？','输出是什么？','中间发生了什么？','能按顺序走一遍吗？'],
 '公式':['每个符号分别表示什么？','这个等号是什么意思？','这个箭头是什么意思？','这个数怎样算出来？','为什么相等？','变量可以取什么值？','单位是什么？'],
 '证明':['要证明什么？','依据是什么？','这一步怎样推出？','为什么一定存在？','为什么这个例子满足条件？','这里用了哪个条件？','去掉这个条件会怎样？'],
 '范围':['什么时候成立？','什么时候不成立？','边界在哪里？','为什么需要这个限制？'],
 '代码':['这个名字是什么意思？','这个模块负责什么？','请求从哪里进入？','这一步改了什么？','什么时候返回？','出错后怎样处理？'],
 '图表':['这个位置表示什么？','横轴表示什么？','纵轴表示什么？','箭头表示什么？','颜色表示什么？','这行应该怎样读？','这里的变化说明什么？'],
 '比较':['为什么更简单？','简单在哪里？','付出了什么代价？','怎样验证？','和原来的方法相比改了什么？'],
 '背景':['是谁提出的？','当时有什么问题？','为什么到这时才出现？','原文哪里支持这个说法？']}
def default_templates():
    return [{'id':f'form-{i:03d}','category':category,'text':text,'enabled':True} for i,(category,text) in enumerate(((c,t) for c,items in QUESTION_FORMS.items() for t in items),1)]
DEFAULT_COLORS={
 'light':{'source_fill':'#e6b543','source_border':'#a46a0d','sentence_fill':'#e1ebe3','sentence_border':'#a4bfae','term_line':'#386f56'},
 'dark':{'source_fill':'#d6ae53','source_border':'#d6ae53','sentence_fill':'#363636','sentence_border':'#808080','term_line':'#b8b8b8'}}

def settings(path):
    value=json.loads(Path(path).read_text('utf8')) if Path(path).exists() else {'templates':default_templates()}
    appearance={theme:{**colors,**value.get('appearance',{}).get(theme,{})} for theme,colors in DEFAULT_COLORS.items()}
    return {**value,'appearance':appearance,'motion_speed':value.get('motion_speed',.8),'revision':Path(path).stat().st_mtime_ns if Path(path).exists() else 0}
def save_settings(path,data):
    previous=settings(path);rows=data.get('templates',previous['templates'])
    if not isinstance(rows,list) or not 1<=len(rows)<=300:raise ValueError('请保留 1 至 300 条问法')
    clean=[];seen=set()
    for i,row in enumerate(rows):
        if not isinstance(row,dict):raise ValueError('每条问法必须是对象')
        identity=str(row.get('id') or f'custom-{i}');text=str(row.get('text','')).strip();category=str(row.get('category','自定义')).strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',identity) or identity in seen:raise ValueError('问法编号重复或格式无效')
        if not text or len(text)>500 or '\n' in text:raise ValueError('每条问法请写在一行内，最多 500 字')
        if not category or len(category)>40:raise ValueError('请填写最多 40 字的类别')
        seen.add(identity);clean.append({'id':identity,'category':category,'text':text,'enabled':row.get('enabled',True) is not False})
    appearance=data.get('appearance',previous['appearance'])
    if not isinstance(appearance,dict):raise ValueError('配色需要按浅色和深色分别提交')
    colors={}
    for theme,defaults in DEFAULT_COLORS.items():
        values=appearance.get(theme,previous['appearance'][theme])
        if not isinstance(values,dict) or set(values)-set(defaults):raise ValueError('配色字段无效')
        colors[theme]={**previous['appearance'][theme],**values}
        if any(not isinstance(c,str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}',c) for c in colors[theme].values()):raise ValueError('颜色请使用六位十六进制格式，例如 #386f56')
    speed=data.get('motion_speed',previous['motion_speed'])
    if isinstance(speed,bool) or not isinstance(speed,(int,float)) or not .5<=speed<=2:raise ValueError('动画速度请设为 0.5 至 2 倍')
    target=Path(path);target.parent.mkdir(parents=True,exist_ok=True);temporary=target.with_suffix('.next.json');temporary.write_text(json.dumps({'templates':clean,'appearance':colors,'motion_speed':speed},ensure_ascii=False,indent=2),'utf8');temporary.replace(target);return settings(target)

def reading_progress(material,state):
    passages=material['passages'];position={p['id']:i for i,p in enumerate(passages)};legacy=any(p.get('outline_id') for p in passages)
    eligible=[p for p in passages if not p.get('heading') and not p.get('duplicate_of') and p.get('role') not in ('page_furniture','metadata','whitespace') and (not legacy or p.get('outline_id') or p.get('file_id')!='primary' or p.get('content_kind')=='explanation') and ((p.get('zh') or p.get('en') or '').strip() or p.get('kind') in ('image','visual','page_image'))]
    def count(items):
        total=len(items);done=sum(bool(state.get('read_passages',{}).get(p['id'])) for p in items)
        return {'done':done,'total':total,'percent':round(done/total*100) if total else 0}
    chapters={}
    files=material.get('reading_files') or [{'id':None,'outline':material.get('outline',[]),'passage_ids':[p['id'] for p in passages]}]
    for file in files:
        outline=file['outline'];members=[p for p in passages if p['id'] in set(file['passage_ids'])];positions={p['id']:i for i,p in enumerate(members)}
        for i,row in enumerate(outline):
            start=positions.get(row.get('passage_id'),0);end=next((positions.get(n.get('passage_id'),len(members)) for n in outline[i+1:] if n.get('level',1)<=row.get('level',1)),len(members))
            chapters[row.get('id') or row['passage_id']]=count([p for p in eligible if p['id'] in positions and start<=positions[p['id']]<end])
    for explanation in material.get('explanations',[]):chapters[explanation['id']]=count([p for p in eligible if p.get('explanation_id')==explanation['id']])
    return {'overall':count(eligible),'chapters':chapters}

def samples(material,state):
    paragraphs={p['id']:p for p in material['passages']};records=[]
    for round in state.get('rounds',[]):
        for q in round['questions']:
            if q.get('deleted_at'):continue
            p=paragraphs.get(q.get('passage_id'),{});source=next((s for s in p.get('sentences',[]) if s['id']==q.get('sentence_id')),None)
            records.append({'schema_version':1,'origin':'user','material_id':material['id'],'material_title':material['title'],'question_id':q['id'],'round_id':round['id'],'passage_id':q.get('passage_id'),'sentence_id':q.get('sentence_id'),'parent_question_id':q.get('parent_question_id'),'question_original':q.get('original_text',q['text']),'question':q['text'],'source_original':(source or {}).get('en') or p.get('aligned_original') or p.get('en','') or (p.get('zh','') if material.get('reader_mode')=='authored' else ''),'source_translation':(source or {}).get('zh') or p.get('zh',''),'source_refs':(source or p).get('refs',[]),'precision':(source or p).get('precision'),'answer':q.get('answer'),'answer_deleted':bool(q.get('answer_deleted_at')),'answer_model':round.get('processing',{}).get('model'),'answer_effort':round.get('processing',{}).get('effort'),'terms':[t for t in state.get('terms',[]) if q['id'] in t.get('question_ids',[])],'practice':[z for z in state.get('quizzes',[]) if q['id'] in z.get('question_ids',[])],'bp':state.get('notes',{}).get(q['id'],''),'saved_at':round.get('created_at'),'answered_at':q.get('answered_at')})
    return records
def sample_stats(records,reading=None):
    categories={'含义':r'是什么|是啥|什么叫|啥叫|什么意思|何谓|何为','命名':r'名字|命名|得名|全称|缩写|为什么叫','原因':r'为什么|为啥|为何','作用与价值':r'有.?用|意义|作用|价值|目的|负责','证明与依据':r'证明|依据|推出|支持','步骤':r'怎么|如何|怎样|哪一步|经过|过程','比较':r'区别|相比|比较|更简单|相同','符号':r'公式|符号|等号|箭头|变量|fx|f\(x\)'}
    counts=Counter();phrases=Counter()
    for row in records:
        for label,pattern in categories.items():
            if re.search(pattern,row['question']):counts[label]+=1
        for phrase in re.findall(r'为什么|为啥|是什么|是什么意思|什么意思|有什么用|有什么意义|怎么证明|如何|怎么|啥叫|为什么叫',row['question']):phrases[phrase]+=1
    return {'questions':len(records),'answered':sum(bool(r.get('answer')) for r in records),'pending':sum(not r.get('answer') and not r.get('answer_deleted') for r in records),'deleted_answers':sum(bool(r.get('answer_deleted')) for r in records),'rounds':len({(r['material_id'],r['round_id']) for r in records}),'materials':len({r['material_id'] for r in records}),'followups':sum(bool(r.get('parent_question_id')) for r in records),'with_bp':sum(bool(r.get('bp')) for r in records),'anchored':sum(bool(r.get('sentence_id') and r.get('source_original')) for r in records),'question_types':dict(counts),'frequent_forms':phrases.most_common(15),'reading':reading or {}}
