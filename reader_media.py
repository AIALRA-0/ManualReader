"""把原图地址和问题编号一起传给写作者，显式看图请求由脚本补齐实际引用。"""
import re

VISUAL_REQUEST = re.compile(r'看[一下这原]*图|引用.*(?:图|表|页面|截图)|显示.*(?:图|图片)|画.*(?:图|流程)|图中|图里|这张|截图|非文字|非文本|配图|流程图|diagram|figure|screenshot',re.I)


def source_media(material, passage):
    result=[]
    def add(src,alt,identity):
        if src and src not in {x['src'] for x in result}:
            result.append({'src':src,'alt':alt or '原文图片','source_id':identity})
    def original(p):
        src=p.get('src') or ('/asset/'+p['asset'] if p.get('asset') else '')
        if src:add(src,p.get('alt') or p.get('path'),p['id'])
    original(passage)
    by_id={p['id']:p for p in material.get('passages',[])}
    for link in passage.get('source_links',[]):
        p=by_id.get(link.get('passage_id'))
        if p and p.get('kind') in ('image','visual','page_image','table','formula','equation'):original(p)
    if not result:
        for ref in passage.get('refs',[])[:2]:
            page=material.get('pages',{}).get(ref.get('page_id'),{})
            if not page.get('image'):continue
            name=page['image'].replace('\\','/').split('/')[-1]
            src=('/content-files/'+page['file_id']+'/asset/'+name) if page.get('file_id') else '/page/'+name
            add(src,'原文第 '+str(page.get('number',''))+' 页',passage['id'])
    return result


def annotate_questions(packet):
    sources={s['id']:s for s in packet.get('sources',[])}
    for q in packet.get('questions',[]):
        source=sources.get(q.get('source_id'),{})
        media=source.get('media_items') or ([source['media']] if source.get('media',{}).get('src') else [])
        if VISUAL_REQUEST.search(q.get('text','')) or source.get('source_kind') in ('image','visual','page_image','table','formula','equation'):
            q['visual_requirement']={'explicit':True,'available_source_ids':[m.get('source_id') for m in media],
                'instruction':'实际查看并在回答中显示对应原图；没有图片时说明缺少哪份原件，不编造引用'}
    return packet


def ensure_visible_sources(result,packet):
    questions={q['id']:q for q in packet.get('questions',[])};sources={s['id']:s for s in packet.get('sources',[])};changes=[]
    for answer in result.get('answers',[]):
        q=questions.get(answer.get('question_id'),{});source=sources.get(q.get('source_id'),{})
        if not q.get('visual_requirement',{}).get('explicit'):continue
        media=source.get('media_items') or ([source['media']] if source.get('media',{}).get('src') else [])
        text=answer.get('text','')
        # 地址来自解析后的原件目录；不接受模型猜出来的文件名。
        for item in media[:2]:
            src=item.get('src','')
            if not src or src in text:continue
            alt=re.sub(r'[\[\]\r\n]',' ',item.get('alt') or '对应原图')
            text+='\n\n!['+alt+']('+src+')'
            changes.append({'question_id':q['id'],'source_id':item.get('source_id'),'action':'补入实际原图引用'})
        answer['text']=text
    return changes


def ensure_result_media(material,state,result):
    wanted={a.get('question_id') for a in result.get('answers',[])}
    questions=[q for r in state.get('rounds',[]) for q in r.get('questions',[]) if q['id'] in wanted]
    passages={p['id']:p for p in material.get('passages',[])};sources=[]
    for q in questions:
        p=passages.get(q.get('passage_id'),{})
        sources.append({'id':q['id'],'source_kind':p.get('kind'),'media_items':source_media(material,p)})
    packet=annotate_questions({'questions':[{**q,'source_id':q['id']} for q in questions],'sources':sources})
    return ensure_visible_sources(result,packet)
