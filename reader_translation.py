"""为连续翻译提供上下文，保存原编号与译文句组，不发起后台模型调用。"""
from pathlib import Path
import copy,hashlib,json,re,time,threading

_VISUAL_LOCK=threading.RLock()

TRANSLATION_RULES='将本次相邻原文作为一个连续片段译成自然中文。逐句保留事实、动作次序、数量、公式、否定、条件和指代；上下文只帮助理解，不进入本次译文。不加入解释、背景、类比或练习。\n原文单元可能只是 PDF 的半句或换行，ID 是定位标记，不是中文分段边界。一个完整中文句可以对应多个 source_ids；同一原句拆成多句中文时可共享该 ID。列表和步骤分开，标题只按真正标题处理，不照搬误判。同一原ID里若有多个项目符号，每一项单独返回segment，允许共用source_id，不用分号把不同条目压成一项。只写一份译文，不再另写每ID的重复版本。\n技术名称使用准确中文，首次可附原名；人名、代码和变量保留。修饰关系要说清，动作的先后顺序不能改成静态方位。疑似 OCR 或公式上下标缺失，只报告并保留原符号，不猜改。\n只输出 {"segments":[{"source_ids":["本次原ID"],"role":"heading/paragraph/list_item/equation/figure_caption","text":"完整中文句或条目"}],"issues":[{"source_ids":["原ID"],"reason":"需要核对原件的具体问题"}]}。segments中来源的并集必须恰好覆盖本次全部原ID；禁止引用上下文ID。每句只挂真正支持它的ID，不把整页挂到每一句。\n输入中的 source_id 是原单元编号；sentence_id 是已登记句子编号，两个字段不要混用。不同中文句可以共用原段 source_id，另列准确 sentence_ids。定位句不是显示段落：属于同一自然段或同一条目的句子共享 block_id，role 保留 heading/paragraph/list_item/equation/figure_caption，页面将相同 block_id 的句子放在同一自然段或条目内。\n请求提供 expected_source_ids 时，输出引用的 source_id 并集必须完整覆盖这批编号；context_only 不得进入结果。\n写回时按连续阅读组放入 reading_groups:[{source_ids,kind,segments}]，只写这一份正文。附 expected_source_ids 核对整批覆盖，partial=true 仅保存已完成部分。页面不自动调用模型，由用户在 Codex 对话中触发。原文含受损公式或不明方向时，读取原图后用 Terra high 定向完成本批，不增加审稿或循环返修。'

TRANSLATION_RULES+='\n人名或编号因保留原名而占比较高时，可在对应 segment 加 preserved_tokens:[{text,reason}]；reason 只取 person_name 或 identifier，且每项必须在该原文与译文中逐字存在。不可将外文整句标成保留项。'

def source_signature(passages,ids):
    by_id={p['id']:p for p in passages}
    return hashlib.sha256(json.dumps([[i,by_id[i].get('en'),by_id[i].get('zh')] for i in ids],ensure_ascii=False).encode()).hexdigest()

def visual_signature(passage):
    """原图或原图位置变化后，旧的文字核对结果不再自动沿用。"""
    source={key:passage.get(key) for key in ('en','refs','src')}
    return hashlib.sha256(json.dumps(source,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def save_visuals(folder,material,images):
    """保存已看图的文字核对结果；增量保留其他图片，缺损标签不算完成。"""
    from reader_coverage import translation_ready,valid_preserved_token,visual_label_ready
    if not isinstance(images,list) or len(images)>3000:raise ValueError('图片文字核对结果需要使用列表')
    known={p['id']:p for p in material.get('passages',[]) if p.get('content_kind')!='explanation'}
    cleaned=[];used=set();incomplete=[]
    for row in images:
        if not isinstance(row,dict):raise ValueError('每张图片的核对结果需要使用对象')
        identity=row.get('image_id')
        if not isinstance(identity,str):raise ValueError('图片编号需要使用文字')
        original=known.get(identity)
        if not original or original.get('kind') not in ('image','visual','page_image','figure','scan') or identity in used:raise ValueError('图片编号不存在、重复，或不属于当前项目的原图')
        if row.get('original_signature')!=visual_signature(original):raise ValueError('原图内容或坐标已变化，请读取当前原图后再提交')
        status=row.get('status');refs=row.get('source_ids',[]);labels=row.get('labels',[]);unreadable=row.get('unreadable',[]);evidence=row.get('evidence',{})
        if status not in ('no_text','text_covered_by_ids','translated','needs_review'):raise ValueError('图片文字状态不正确')
        if not isinstance(refs,list) or any(not isinstance(i,str) or i not in known for i in refs) or len(refs)!=len(set(refs)):raise ValueError('图片标签引用了重复或不属于当前项目的原文编号')
        if any(known[i].get('file_id','primary')!=original.get('file_id','primary') for i in refs):raise ValueError('图片的标签需要属于同一个原文件')
        if not isinstance(labels,list) or len(labels)>3000 or not isinstance(unreadable,list) or not all(isinstance(x,(str,dict)) for x in unreadable):raise ValueError('图中文字与未辨认项需要使用列表')
        if not isinstance(evidence,dict):raise ValueError('请用对象记录核对这张图片的依据')
        checked=[]
        for label in labels:
            if not isinstance(label,dict) or not isinstance(label.get('original'),str) or not label['original'].strip() or not isinstance(label.get('translation'),str) or not label['translation'].strip():raise ValueError('每个图片标签需要原文和对应的成品中文')
            if 'placeholder' in translation_flags('',label['translation']):raise ValueError('图片标签不能用待翻译占位符代替成品')
            item={'original':label['original'],'translation':label['translation']}
            if label.get('preserve'):
                if label.get('preserve_reason') not in ('symbol','product_name','unit','numeric','identifier') or label['original']!=label['translation']:raise ValueError('原样保留只用于同文的符号、产品名、单位、数字或标识，并需要声明原因')
                item.update(preserve=True,preserve_reason=label['preserve_reason'])
            if label.get('preserved_tokens') is not None:
                preserved=label['preserved_tokens']
                if not isinstance(preserved,list) or len(preserved)>30 or any(not valid_preserved_token(t,allow_product_names=True) or t['text'] not in label['original'] or t['text'] not in label['translation'] for t in preserved):raise ValueError('图内保留项需要是原文与译文中逐字存在的短名称或编号')
                item['preserved_tokens']=[{'text':t['text'],'reason':t['reason']} for t in preserved]
            checked.append(item)
        if status=='no_text' and (checked or refs):raise ValueError('声明图中没有文字时，不能同时提交标签或文字来源')
        if status=='text_covered_by_ids' and not refs:raise ValueError('复用文字译文时需要提供对应的原文编号')
        if status=='translated' and not checked:raise ValueError('声明图片已翻译时需要提交实际标签译文')
        clean={'image_id':identity,'original_signature':row['original_signature'],'status':status,'source_ids':list(refs),'labels':checked,'unreadable':copy.deepcopy(unreadable),'evidence':copy.deepcopy(evidence)}
        if row.get('translation_markdown') is not None:
            if not isinstance(row['translation_markdown'],str):raise ValueError('图表的整段译文需要使用文字')
            if 'placeholder' in translation_flags('',row['translation_markdown']):raise ValueError('图表不能提交待翻译占位符')
            clean['translation_markdown']=row['translation_markdown']
        complete=bool(evidence) and not unreadable
        if status=='text_covered_by_ids':complete=complete and all(translation_ready(known[i]) for i in refs)
        elif status=='translated':complete=complete and all(visual_label_ready(x) for x in checked)
        elif status=='needs_review':complete=False
        if not complete:incomplete.append(identity)
        cleaned.append(clean);used.add(identity)
    path=Path(folder)/'reading-visual-language.json'
    with _VISUAL_LOCK:
        old=json.loads(path.read_text('utf8')) if path.exists() else {'version':1,'revision':0,'images':[]}
        remaining=[r for r in old.get('images',[]) if r.get('image_id') not in used]
        if path.exists():
            history=Path(folder)/'translation-history';history.mkdir(exist_ok=True);(history/(str(time.time_ns())+'-visual.json')).write_bytes(path.read_bytes())
        value={'version':1,'revision':old.get('revision',0)+1,'images':remaining+cleaned};path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.next.json');temporary.write_text(json.dumps(value,ensure_ascii=False),'utf8');temporary.replace(path)
    return {'saved':len(cleaned),'revision':value['revision'],'incomplete':len(incomplete),'incomplete_image_ids':incomplete,'complete_for_submitted_images':not incomplete}

def packets(material,file_id='primary',wanted=None,max_chars=9000):
    passages=[p for p in material['passages'] if p.get('file_id','primary')==file_id and p.get('content_kind')!='explanation']
    ids={p['id'] for p in passages};chosen=set(wanted) if wanted is not None else ids
    if not chosen<=ids:raise ValueError('翻译任务含有不属于当前原文文件的编号')
    batches=[];current=[];size=0;last_page=None
    def flush():
        nonlocal current,size
        if current:batches.append(current);current=[];size=0
    for p in passages:
        if p['id'] not in chosen or p.get('translatable') is False:continue
        page=next((r.get('page_id') for r in p.get('refs',[]) if r.get('page_id')),None)
        # 页与段落是上下文边界；页面特别长时只在完整原单元之间分批。
        if current and (page and last_page and page!=last_page or size+len(p.get('en',''))>max_chars):flush()
        current.append(p);size+=len(p.get('en',''));last_page=page
    flush();positions={p['id']:i for i,p in enumerate(passages)}
    result=[]
    for index,batch in enumerate(batches):
        before=positions[batch[0]['id']];after=positions[batch[-1]['id']]
        units=[{'source_id':p['id'],'original':p.get('en',''),'translation':p.get('zh',''),'kind':p.get('kind'),'refs':p.get('refs',[]),'sentences':[{'sentence_id':s['id'],'original':s.get('en','')} for s in p.get('sentences',[]) if not s.get('historical')]} for p in batch]
        context=[{'source_id':p['id'],'original':p.get('en',''),'translation':p.get('zh','')} for p in passages[max(0,before-1):before]+passages[after+1:after+2]]
        result.append({'id':'translation-'+str(index+1),'source_ids':[p['id'] for p in batch],'units':units,'context_only':context})
    visuals=[{'source_id':p['id'],'refs':p.get('refs',[]),'src':p.get('src',''),'alt':p.get('alt',''),'ocr_text':p.get('ocr_text',''),'existing_translation':p.get('zh',''),'original_signature':visual_signature(p)} for p in passages if p['id'] in chosen and p.get('kind') in ('image','visual','page_image','figure','scan')]
    return {'material_id':material['id'],'file_id':file_id,'instructions':TRANSLATION_RULES,'batches':result,'visual_units':visuals,
        'visual_instructions':'图片的 OCR 为空不代表没有文字。先读取原图；图内已有可定位文字与译文时复用其编号，否则补译可辨认标签、图例和表格文字，并保留整图来源。无法看清时如实记录，不猜译，不标为已完成。原图不需要重画。',
        'model_trigger':'用户在Codex对话中手动触发，网页不调用模型'}

def apply_sentence_alignment(p,row):
    sentences=[s for s in p.get('sentences',[]) if not s.get('historical')];submitted=row.get('sentences')
    if submitted is not None:
        if not isinstance(submitted,list) or any(not isinstance(x,dict) for x in submitted):raise ValueError('句级译文需要是对象列表')
        known={s['id']:s for s in sentences};received=[s.get('sentence_id') for s in submitted]
        if set(received)!=set(known) or len(received)!=len(set(received)):raise ValueError('句级译文需要恰好覆盖本原单元的全部句子编号')
        for s in submitted:
            if not isinstance(s.get('text'),str) or not s['text'].strip():raise ValueError('已提交的句级译文不能为空')
        for s in submitted:known[s['sentence_id']]['zh']=s['text'].strip();known[s['sentence_id']]['translation_precision']='sentence'
        p['translation_alignment']='sentence'
    elif len(sentences)==1:
        sentences[0]['zh']=row['text'].strip();sentences[0]['translation_precision']='unit';p['translation_alignment']='unit'
    else:
        # 未提供逐句译文时仍保存完整段落；不把整段冒充第一句，也不保留已过时的句译文。
        for s in sentences:s['zh']='';s['translation_precision']='paragraph'
        p['translation_alignment']='paragraph'

def translation_flags(original,translated):
    flags=[]
    if re.fullmatch(r'\s*(?:待翻译|未翻译|翻译中|pending|TODO)\s*[。.!！]?\s*',translated,re.I):flags.append('placeholder')
    numbers=lambda text:set(re.findall(r'(?<![A-Za-z])\d+(?:\.\d+)?',text))
    missing=numbers(original)-numbers(translated)
    if missing:flags.append({'code':'numbers_need_check','missing':sorted(missing)})
    for token in re.findall(r'`[^`]+`|https?://[^\s)]+|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]',original):
        if token not in translated:flags.append({'code':'protected_item_needs_check','item':token})
    return flags


def legacy_segments(p,block_id=None):
    """让已有成品译文带上明确来源；保留正文，不把整段假装成第一句。"""
    from reader_coverage import translation_ready
    text=p.get('zh') or ''
    if not text.strip() or 'placeholder' in translation_flags('',text) or not translation_ready(p,text):return []
    role='heading' if p.get('heading') else 'list_item' if p.get('bullet') or p.get('kind')=='list' else p.get('kind') if p.get('kind') in ('table','code','equation') else 'paragraph'
    sentences=[s for s in p.get('sentences',[]) if not s.get('historical')]
    compact=lambda value:re.sub(r'\s+','',value)
    aligned=bool(sentences and all(s.get('zh','').strip() for s in sentences) and compact(''.join(s['zh'] for s in sentences))==compact(text))
    if aligned:
        result=[];cursor=0
        for index,s in enumerate(sentences):
            value=s['zh'];position=text.find(value,cursor)
            if position<0:return [{'id':p['id']+'-legacy-translated','source_ids':[p['id']],'text':text,'role':role,'block_id':block_id or p['id'],'precision':'unit'}]
            # 把原来的分隔空白保留在下一句里，避免无损映射改变用户已有译文。
            part={'id':p['id']+'-legacy-translated-'+str(index+1),'source_ids':[p['id']],'sentence_ids':[s['id']],'text':text[cursor:position+len(value)],'role':role,'block_id':block_id or p['id'],'precision':'sentence'}
            cursor=position+len(value);result.append(part)
        result[-1]['text']+=text[cursor:]
        return result
    return [{'id':p['id']+'-legacy-translated','source_ids':[p['id']],'text':text,'role':role,'block_id':block_id or p['id'],'precision':'unit'}]

def save_groups(folder,material,rows,expected_source_ids=None,partial=False):
    if rows is None:return {'saved':0}
    if not isinstance(rows,list) or len(rows)>3000:raise ValueError('连贯译文组需要使用列表')
    known={p['id']:p for p in material['passages'] if p.get('content_kind')!='explanation'};used=set();clean=[]
    child={s['id']:p['id'] for p in known.values() for s in p.get('sentences',[]) if not s.get('historical')}
    def parents(values):
        if not isinstance(values,list) or any(not isinstance(i,str) for i in values):raise ValueError('原文编号需要是字符串列表')
        return list(dict.fromkeys(child.get(i,i) for i in values))
    for row in rows:
        raw_ids=row.get('source_ids') if isinstance(row,dict) else None;ids=parents(raw_ids);segments=row.get('segments') if isinstance(row,dict) else None
        if len(raw_ids)!=len(set(raw_ids)):raise ValueError('同一个原文编号不能在组内重复登记')
        if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)) or any(i not in known or i in used for i in ids):raise ValueError('译文组引用了重复或不存在的原文编号')
        if len({known[i].get('file_id','primary') for i in ids})!=1:raise ValueError('一个译文句组必须属于同一原文件')
        if not isinstance(segments,list) or not segments:raise ValueError('译文组需要完整中文句或条目')
        union=set();parts=[]
        for index,s in enumerate(segments):
            raw_refs=s.get('source_ids',[]) if isinstance(s,dict) else [];refs=parents(raw_refs);text=s.get('text') if isinstance(s,dict) else None
            if not isinstance(refs,list) or not refs or any(i not in ids for i in refs) or not isinstance(text,str) or not text.strip():raise ValueError('译文句需要内容及本组真实原文编号')
            if 'placeholder' in translation_flags('',text):raise ValueError('译文必须是成品，不能提交待翻译占位符')
            union.update(refs);part={'id':ids[0]+'-translated-'+str(index+1),'source_ids':refs,'text':text.strip()}
            if s.get('preserved_tokens') is not None:
                from reader_coverage import valid_preserved_token
                preserved=s['preserved_tokens'];originals=[known[i].get('en','') for i in refs]
                if not isinstance(preserved,list) or len(preserved)>30 or any(not valid_preserved_token(t) or t['text'] not in text or not any(t['text'] in original for original in originals) for t in preserved):raise ValueError('原样保留项只允许原文与译文中均逐字存在的短人名或编号，不能标记整句外文')
                if len({t['text'] for t in preserved})!=len(preserved):raise ValueError('原样保留的人名或编号不能重复登记')
                part['preserved_tokens']=[{'text':t['text'],'reason':t['reason']} for t in preserved]
            submitted_sentences=s.get('sentence_ids',[])
            if not isinstance(submitted_sentences,list) or any(not isinstance(i,str) or i not in child or child[i] not in refs for i in submitted_sentences):raise ValueError('译文句子编号必须属于这句话对应的原文单元')
            sentence_ids=list(dict.fromkeys([i for i in raw_refs if i in child]+submitted_sentences))
            if sentence_ids:part['sentence_ids']=sentence_ids
            role=s.get('role')
            if role is not None:
                if role not in ('heading','paragraph','list_item','equation','figure_caption','code','table'):raise ValueError('译文句的排版类型不正确')
                part['role']=role
            if s.get('level') is not None:part['level']=max(1,min(6,int(s['level'])))
            if s.get('list_order') is not None:part['list_order']=max(1,int(s['list_order']))
            if s.get('block_id') is not None:
                if not isinstance(s['block_id'],str) or len(s['block_id'])>100:raise ValueError('译文段落编号需要是最多100字的文字')
                part['block_id']=s['block_id']
            parts.append(part)
        if union!=set(ids):raise ValueError('译文句组遗漏了部分原文编号')
        used.update(ids);kind=row.get('kind','paragraph')
        if kind not in ('paragraph','list','heading','table','figure','markdown','code','equation'):raise ValueError('译文句组类型不正确')
        clean.append({'id':ids[0]+'-continuous','source_ids':ids,'kind':kind,'segments':parts,'signature':source_signature(material['passages'],ids)})
    expected=set(parents(expected_source_ids)) if expected_source_ids is not None else used
    if any(i not in known for i in expected):raise ValueError('本批预期翻译编号不属于当前项目')
    if used-expected or (not partial and expected-used):raise ValueError('译文组未完整对应本批预期原文编号，请补齐缺项或明确 partial=true 保存部分成品')
    path=Path(folder)/'translation-groups.json';old=json.loads(path.read_text('utf8')) if path.exists() else {'revision':0,'groups':[]}
    for g in old['groups']:
        if used.intersection(g['source_ids']) and not set(g['source_ids'])<=used:raise ValueError('修改连贯译文时需要一并提交原组的全部编号，避免拆掉同一句中的其他来源：'+','.join(g['source_ids']))
    remaining=[g for g in old['groups'] if not used.intersection(g['source_ids'])]
    if path.exists():
        history=Path(folder)/'translation-history';history.mkdir(exist_ok=True);(history/(str(time.time_ns())+'.json')).write_bytes(path.read_bytes())
    value={'revision':old['revision']+1,'groups':remaining+clean};tmp=path.with_suffix('.next.json');tmp.write_text(json.dumps(value,ensure_ascii=False),'utf8');tmp.replace(path)
    return {'saved':len(clean),'revision':value['revision'],'source_ids':sorted(used),'missing_source_ids':sorted(expected-used),'complete_for_requested_ids':not bool(expected-used)}

def active_groups(folder,material):
    path=Path(folder)/'translation-groups.json'
    if not path.exists():return []
    rows=json.loads(path.read_text('utf8')).get('groups',[]);ids={p['id'] for p in material['passages']}
    return [g for g in rows if set(g['source_ids'])<=ids and g.get('signature')==source_signature(material['passages'],g['source_ids'])]
