"""把原图地址和问题编号一起传给写作者，显式看图请求由脚本补齐实际引用。"""
import re
from collections import Counter, defaultdict

VISUAL_REQUEST = re.compile(r'看[一下这原]*图|引用.*(?:图|表|页面|截图)|显示.*(?:图|图片)|画.*(?:图|流程)|图中|图里|这张|截图|非文字|非文本|配图|流程图|diagram|figure|screenshot',re.I)
DRAW_REQUEST = re.compile(r'(?:请|帮我|给我|能否|能不能|希望|需要|要|必须|如何想象|想看|请你)[^？?。；\n]{0,16}(?:配图|画图|画一张|绘制|看原图|附图|附上图|引用原图|展示原图|示意图|流程图|截图)|(?:配图|画图|画一张图|给我看图|附上原图|引用原图|展示原图)')
NO_DRAW_REQUEST = re.compile(r'(?:不用|不必|不要|无需|不需要|不想)[^？?。；\n]{0,4}(?:配图|画图|附图|看原图|图示)')
FENCE = re.compile(r"(?ms)^ {0,3}(`{3,}|~{3,})[ \t]*([^\n]*)\n(.*?)^ {0,3}\1[ \t]*$")
COORDINATE_IMAGE = re.compile(r'!\[([^\]\n]+)\]\(coordinate:([A-Za-z0-9_-]+)\)')
IMAGE = re.compile(r'!\[[^\]\n]+\]\(([^\s]+?)\)')
INLINE_CODE = re.compile(r'`[^`\n]*`')
FLOW_DIRECTION = re.compile(r'(?im)^(\s*(?:flowchart|graph)(?:-elk)?)\b(?=[ \t]*(?:;|\r?\n|$))|^(\s*(?:flowchart|graph)(?:-elk)?[ \t]+)(?:LR|RL|BT)\b')
HORIZONTAL = re.compile(r'(?m)^[ \t]*%% reader-layout: horizontal[ \t]*$')
DIAGRAM = re.compile(r'(?m)^\s*(?:stateDiagram(?:-v2)?|classDiagram|erDiagram|gantt|timeline|xychart(?:-beta)?|mindmap|sequenceDiagram)\b[^\n]*\n\s*\S')
PIPE_TABLE = re.compile(r'(?m)^ {0,3}(\|[^\n]+\|)[ \t]*\n {0,3}(\|(?:[ \t]*:?-+:?[ \t]*\|){2,})[ \t]*\n(?: {0,3}\|[^\n]+\|[ \t]*$\n?)+')
LOG_VISUAL = re.compile(r'日志|终端|命令输出|原始输出|控制台|逐字展示|原顺序.{0,6}字段|\b(?:console|log)\b', re.I)


def normalize_flowcharts(markdown):
    """给新稿的普通流程图统一纵排，保留明确需要横向比较的图。"""
    def orient(match):
        body=match[3]
        if match[2].strip().lower()!='mermaid' or HORIZONTAL.search(body):
            return match[0]
        updated=FLOW_DIRECTION.sub(lambda direction:(direction[1]+' TD') if direction[1] else direction[2]+'TD',body,count=1)
        start=match.start(3)-match.start()
        return match[0][:start]+updated+match[0][start+len(body):]
    return FENCE.sub(orient,markdown)


def text_diagram(language,body):
    if language.strip().lower() not in ('','text','txt','plaintext'):
        return False
    rows=body.splitlines()
    box_rows=sum(bool(re.search(r'[┌┐└┘├┤┬┴┼╔╗╚╝─━│┃]{2,}',row)) for row in rows)
    ascii_boxes=sum(bool(re.fullmatch(r'\s*\+(?:[-=]{2,}\+)+\s*',row)) for row in rows)
    return box_rows>=2 or ascii_boxes>=2


def validate_generated_diagrams(parts, *, vertical=False):
    """在写回前拦住误用普通代码块画出的图；保留代码、终端输出和明确的文字图备选。"""
    for part in parts:
        if vertical:
            part['markdown']=normalize_flowcharts(part.get('markdown') or '')
        for _, language, body in FENCE.findall(part.get('markdown') or ''):
            if text_diagram(language,body):
                raise ValueError(f"正文 {part['id']} 里有用普通代码块画的图，请改用 Mermaid 或实际图片。只能用文字画图时，把代码块标为 ascii。真实终端输出标为 console。")


def coordinate_table(figure):
    """把用坐标排成的规则表格还原为可选中、可缩放的 Markdown 表格。"""
    panels=figure.get('panels',[])
    if len(panels)!=1:
        return ''
    panel=panels[0]
    if any(line.get('label') or
           (len({p[0] for p in line['points']})>1 and len({p[1] for p in line['points']})>1)
           for line in panel.get('lines',[])):
        return ''
    positions=defaultdict(dict)
    for point in panel.get('points',[]):
        x,y=point['at']
        label=point['label'].strip()
        if x in positions[y] or not label or '\n' in label or '|' in label or '<' in label or '>' in label:
            return ''
        positions[y][x]=label
    rows=sorted(positions.items(),reverse=True)
    grid=[sorted(row.items()) for _,row in rows if len(row)>1]
    if len(grid)<2:
        return ''
    columns=[x for x,_ in grid[0]]
    if any([x for x,_ in row]!=columns for row in grid):
        return ''
    if any(len(row)==1 for _,row in rows[:len(grid)]):
        return ''
    def cells(values):
        return '| '+' | '.join(values)+' |'
    header=cells(label for _,label in grid[0])
    body=[cells(label for _,label in row) for row in grid[1:]]
    footnotes=[next(iter(row.values())) for _,row in rows[len(grid):]]
    return '\n'.join([header,cells(['---']*len(columns)),*body])+''.join('\n\n'+note for note in footnotes)


def markdown_images(pattern,markdown):
    protected=[*FENCE.finditer(markdown),*INLINE_CODE.finditer(markdown)]
    return (match for match in pattern.finditer(markdown)
            if not any(block.start()<=match.start()<block.end() for block in protected))


def rewrite_coordinate_images(markdown, replace):
    cursor=0
    chunks=[]
    for match in markdown_images(COORDINATE_IMAGE,markdown):
        chunks.extend((markdown[cursor:match.start()],replace(match)))
        cursor=match.end()
    return ''.join(chunks)+markdown[cursor:]


def check_visuals(value, analysis, sources=()):
    """核对规划图确实出现在它要解释的章节或回答中。"""
    lesson=analysis['mode']=='lesson'
    owners={a['question_id']:a.get('parts') or a.get('answer_parts',[]) for a in value.get('answers',[])}
    if lesson:
        owners={analysis['items'][0]['task_id']:value.get('explanation',{}).get('parts',[])}
    plans={visual['id']:(item['task_id'],visual) for item in analysis['items'] for visual in item.get('visuals',[])}
    parts={(task,p['id']):p for task,rows in owners.items() for p in rows}
    figures={item['id']:item for item in value.get('coordinate_figures',[])}
    known_sources={s['id']:s for s in sources}
    if len(figures)!=len(value.get('coordinate_figures',[])):
        raise ValueError('坐标图编号重复，请给每幅图一个独立编号')
    issues=[]
    needs={}
    missing=set(plans)
    checked=set()
    destinations=defaultdict(set)
    for entry in value.get('visual_checks',[]):
        identity=entry['visual_id']
        if identity not in plans:
            issues.append('配图检查需要引用本轮规划中的图号：'+identity)
            continue
        task,plan=plans[identity]
        key=(task,entry['part_id'])
        if key not in parts:
            issues.append('配图需要放在这道题或这章的实际正文里：'+identity)
            continue
        if (identity,key) in checked:
            continue
        checked.add((identity,key))
        needs.setdefault(key,[]).append((identity,plan))
        destinations[identity].add(key)
        missing.discard(identity)
    if missing:
        issues.append('正文缺少规划中的图：'+', '.join(f'{i}（{plans[i][1]["show"]}）' for i in sorted(missing)))
    current_references=defaultdict(set)
    for key,part in parts.items():
        for match in markdown_images(COORDINATE_IMAGE,part['markdown']):
            current_references[match[2]].add(key)
    for identity,figure in tuple(figures.items()):
        targets=destinations[identity]
        refs=current_references[identity]
        if len(targets)!=1 or refs and refs!=targets:
            continue
        key=next(iter(targets))
        part=parts[key]
        plan=plans[identity][1]
        if plan['kind']=='table' and (table:=coordinate_table(figure)):
            if refs:
                part['markdown']=rewrite_coordinate_images(part['markdown'],lambda match:
                    '\n\n'+match[1]+'\n\n'+table+'\n\n' if match[2]==identity else match[0])
            else:
                part['markdown']+='\n\n'+table
            del figures[identity]
            continue
        if plan['kind']=='table' and not refs:
            prose=INLINE_CODE.sub('',FENCE.sub('\n\n',part['markdown']))
            if PIPE_TABLE.search(prose):
                # 补齐时已按规划提交了实际表格，旧的未引用图不再单独上传。
                del figures[identity]
                continue
        if not refs and plan['kind']!='table':
            alt=re.sub(r'[\[\]`\r\n]',' ',plan['show']).strip()
            if not alt:
                continue
            image=f'![{alt}](coordinate:{identity})'
            if plan['kind']!='original':
                inserted=False
                def attach(match):
                    nonlocal inserted
                    if inserted or not text_diagram(match[2],match[3]):
                        return match[0]
                    inserted=True
                    # 正式图挂在原先的图位，保留文字图里可复制的完整标注。
                    return image+'\n\n'+match[1]+'ascii\n'+match[3]+match[1]
                part['markdown']=FENCE.sub(attach,part['markdown'])
                if inserted:
                    continue
            part['markdown']+='\n\n'+image
    if len(figures)!=len(value.get('coordinate_figures',[])):
        value['coordinate_figures']=list(figures.values())
    task_sources={row['task_id']:row.get('source_ids',[]) for row in analysis['items']}
    source_units={u['id']:{ref['source_id'] for ref in u.get('source_refs',[])} for u in value.get('plan',{}).get('units',[])}
    used=set()
    for key, part in parts.items():
        markdown=part['markdown']
        fences=FENCE.findall(markdown)
        diagrams={body.strip() for _,language,body in fences
                  if language.strip().lower()=='mermaid' and
                  (re.search(r'(?:-->|---|-.->|==>|->>|-->>|->|<\|--|--o|~~~)',body)
                   or DIAGRAM.search(body))}
        prose=INLINE_CODE.sub('',FENCE.sub('\n\n',markdown))
        tables={m[0].strip() for m in PIPE_TABLE.finditer(prose) if len(re.split(r'(?<!\\)\|',m[1]))==m[2].count('|')+1}
        coordinates={match[2] for match in markdown_images(COORDINATE_IMAGE,markdown)}
        if coordinates-figures.keys():
            issues.append('正文引用了没有坐标数据的图：'+', '.join(sorted(coordinates-figures.keys())))
        used.update(coordinates)
        images={match[1] for match in markdown_images(IMAGE,markdown)
                if re.match(r'^(?:https?://|source:|/(?:content-files|page|asset)/)',match[1])}
        bad_sources={url for url in images if url.startswith('source:') and
                     not (known_sources.get(url[7:],{}).get('src') or (known_sources.get(url[7:],{}).get('media') or {}).get('src'))}
        if bad_sources:
            issues.append('原图引用需要指向这次材料里实际有图的编号：'+', '.join(sorted(bad_sources)))
        generated={row['path'] for row in value.get('generated_assets',[]) if row.get('after_part_id')==key[1]
                   and (lesson or key[0] in row.get('question_ids',[])) and row.get('path')}
        assigned=needs.get(key,[])
        tally=Counter(item['kind'] for _,item in assigned)
        original_text=set()
        log_count=sum(plan['kind']=='original' and bool(LOG_VISUAL.search(plan['show'])) for _,plan in assigned)
        if log_count:
            # 日志或代码可以逐字展示。只接受本问题关联来源里的实际行。
            from reader_generation import source_texts
            ids=set(task_sources[key[0]])
            for unit in part.get('unit_ids',[]):
                ids.update(source_units.get(unit,()))
            originals=[text for sid in ids for text in source_texts(known_sources.get(sid,{})) if text]
            for _,language,body in fences:
                if language.strip().lower() not in ('console','text','txt','plaintext','log'):
                    continue
                excerpt=[line.strip() for line in body.splitlines() if line.strip()]
                if not excerpt or len(body.strip())<16:
                    continue
                # 连续日志必须顺序、字段一致。只摘一条诊断时，允许原文在叙述中逐字引述它。
                block='\n'.join(excerpt)
                if any((block in text if len(excerpt)==1 else
                        '\n'+block+'\n' in '\n'+'\n'.join(
                            line.strip() for line in text.splitlines() if line.strip())+'\n')
                       for text in originals):
                    original_text.add(body.strip())
        original_count=min(log_count,len(original_text))
        if tally['coordinates']>len(coordinates)+len(images):
            issues.append('请在 '+key[1]+' 放入实际坐标图，画出这一步的对象和改动')
        if tally['original']>len(images)+original_count:
            issues.append('请在 '+key[1]+' 放入实际原图、原图截图或经原件核对的日志')
        if tally['original']+tally['coordinates']>len(images)+original_count+len(coordinates):
            issues.append('请在 '+key[1]+' 分别展示原件和坐标图')
        if tally.pop('table',0)>len(tables):
            issues.append('请在 '+key[1]+' 放入能渲染的 Markdown 对照表，保留表头和分隔行')
        if sum(tally.values())>len(diagrams)+len(images)+original_count+len(coordinates)+len(generated):
            pending='；'.join(f"{identity} 要展示{item['show']}" for identity,item in assigned)
            issues.append('请在 '+key[1]+' 放入计划里的每幅图，或将共同的动作登记成一幅多联图。'+pending)
    if figures.keys()-used:
        issues.append('请在对应正文用 ![图注](coordinate:图号) 引用已经登记的坐标图')
    if issues:
        raise ValueError('\n'.join(issues))


def publish_coordinate_figures(bridge,value):
    import base64
    import hashlib
    from urllib.parse import urlsplit
    figures={item['id']:item['panels'] for item in value.get('coordinate_figures',[])}
    if not figures:
        return
    urls={}
    def image(match):
        alt,identity=match.groups()
        if identity not in urls:
            png=coordinate_png(figures[identity])
            file_id='illustration-'+hashlib.sha256(png).hexdigest()[:20]
            receipt=bridge.request('/api/content/files',{'file_id':file_id,'filename':file_id+'.png','purpose':'illustration',
                                   'content_base64':base64.b64encode(png).decode('ascii')})
            url=urlsplit(receipt['original_url'])
            urls[identity]=url.path+('?' +url.query if url.query else '')
        return '!['+alt.replace('从左到右','从上到下').replace('由左至右','由上至下')+']('+urls[identity]+')'
    owners=[value.get('explanation',{}),*value.get('answers',[])]
    for owner in owners:
        for part in owner.get('parts',[]):
            part['markdown']=rewrite_coordinate_images(part['markdown'],image)
    value.pop('coordinate_figures',None)


def require_published_figures(value):
    """服务器只保存浏览器能读取的图，不保存客户端尚未绘制的坐标占位。"""
    for owner in [value.get('explanation') or {},*(value.get('answers') or [])]:
        if not isinstance(owner,dict):
            continue
        for part in owner.get('parts') or owner.get('answer_parts') or []:
            if isinstance(part,dict) and next(markdown_images(COORDINATE_IMAGE,str(part.get('markdown') or '')),None):
                raise ValueError('坐标图尚未绘制并上传，请用最新客户端提交这份原稿')


def coordinate_png(panels):
    """按坐标画 PNG；同一张图的各幅使用相同刻度与可见范围。"""
    from io import BytesIO
    from math import hypot, isfinite
    import os
    from pathlib import Path
    from PIL import Image, ImageDraw, ImageFont

    if not 1<=len(panels)<=12:
        raise ValueError('一张坐标图请提供 1 到 12 幅图')
    positions=[]
    for panel in panels:
        if not panel['points'] or len(panel['points'])>100 or len(panel['lines'])>180:
            raise ValueError('坐标图需要标出主要对象，复杂的图可拆为多幅')
        if any(len(line['points'])<2 or len(line['points'])>100 for line in panel['lines']):
            raise ValueError('每条线需要 2 到 100 个坐标点')
        for xy in [p['at'] for p in panel['points']]+[xy for line in panel['lines'] for xy in line['points']]:
            if len(xy)!=2 or not all(type(x) in (int,float) and isfinite(x) and abs(x)<=1e12 for x in xy):
                raise ValueError('坐标必须是一对有限数字')
            positions.append(xy)
    xmin,ymin=map(min,zip(*positions))
    xmax,ymax=map(max,zip(*positions))
    dx=max(xmax-xmin,1)
    dy=max(ymax-ymin,1)
    width,height=860,430
    scale=min(660/dx,285/dy)
    image=Image.new('RGB',(width,len(panels)*height),'#ffffff')
    draw=ImageDraw.Draw(image)
    font_file=next((path for path in (
        Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/msyh.ttc',
        Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
        Path('/System/Library/Fonts/PingFang.ttc'),
    ) if path.is_file()),None)
    def make_font(size):
        return ImageFont.truetype(str(font_file),size) if font_file else ImageFont.load_default(size=size)
    font=make_font(26)
    small=make_font(21)
    colors={'base':'#374151','new':'#075aba','remove':'#b63434','guide':'#768293'}
    def grid(lo,hi):
        from math import ceil, floor, log10
        if lo==hi:
            return [lo]
        estimate=max(hi-lo,1e-9)/8
        magnitude=10**floor(log10(estimate))
        step=next(mult*magnitude for mult in (1,2,5,10) if mult*magnitude>=estimate)
        if float(lo).is_integer() and float(hi).is_integer():
            step=max(step,1)
        return [i*step for i in range(ceil(lo/step),floor(hi/step)+1)]
    def label(at,text,fill,font=font):
        if text:
            text=text if font_file else re.sub(r'[^\x20-\x7e]','',text.replace('–','-').replace('→','>'))
            draw.text(at,text[:70],font=font,fill=fill,stroke_width=2,stroke_fill='white')
    for i,panel in enumerate(panels):
        y0=i*height
        left=(width-(xmax-xmin)*scale)/2
        bottom=y0+(height+(ymax-ymin)*scale)/2
        def point(xy):
            return left+(xy[0]-xmin)*scale,bottom-(xy[1]-ymin)*scale
        draw.rectangle((52,y0+39,width-44,y0+height-44),outline='#dde1e7',width=2)
        label((54,y0+10),str(i+1),'#27303f')
        for x in grid(xmin,xmax):
            xpixel,_=point((x,0))
            draw.line((xpixel,y0+40,xpixel,y0+height-45),fill='#e1e6ee',width=1)
            label((xpixel-8,y0+height-37),f'{x:.8g}','#364153',small)
        for y in grid(ymin,ymax):
            _,ypixel=point((0,y))
            draw.line((53,ypixel,width-45,ypixel),fill='#e1e6ee',width=1)
            label((10,ypixel-7),f'{y:.8g}','#364153',small)
        labels=[]
        for line in panel['lines']:
            coords=[point(xy) for xy in line['points']]
            color=colors[line['style']]
            if line['style'] in ('guide','remove'):
                for start,end in zip(coords,coords[1:]):
                    vx,vy=end[0]-start[0],end[1]-start[1]
                    distance=hypot(vx,vy)
                    for offset in range(0,int(distance),14):
                        first=offset/distance;last=min(offset+8,distance)/distance
                        draw.line((start[0]+vx*first,start[1]+vy*first,start[0]+vx*last,start[1]+vy*last),
                                  fill=color,width=3)
            else:
                draw.line(coords,fill=color,width=5 if line['style']=='new' else 3,joint='curve')
            a,b=max(zip(coords,coords[1:]),key=lambda pair:(pair[0][0]-pair[1][0])**2+(pair[0][1]-pair[1][1])**2)
            midpoint=((a[0]+b[0])/2,(a[1]+b[1])/2)
            labels.append((midpoint,line['label'],color,small))
        for p in panel['points']:
            x,y=point(p['at']); color=colors[p['style']]
            draw.ellipse((x-5,y-5,x+5,y+5),fill=color,outline='white',width=1)
            labels.insert(0,((x,y),p['label'],color,font))
        occupied=[]
        for (x,y),text,color,face in labels:
            if not text:
                continue
            bounds=draw.textbbox((0,0),text[:70],font=face,stroke_width=2)
            w=bounds[2]-bounds[0]
            h=bounds[3]-bounds[1]
            candidates=[]
            for shift in (0,30,60,90):
                candidates.extend(((x+10,y-h-9-shift),(x-w-10,y-h-9-shift),
                                   (x+10,y+8+shift),(x-w-10,y+8+shift)))
            for px,py in candidates:
                px=min(max(px,56),max(56,width-48-w))
                py=min(max(py,y0+43),max(y0+43,y0+height-48-h))
                rect=(px-3,py-3,px+w+3,py+h+3)
                if all(rect[2]<a or rect[0]>c or rect[3]<b or rect[1]>d for a,b,c,d in occupied):
                    break
            occupied.append(rect)
            label((px-bounds[0],py-bounds[1]),text,color,face)
    result=BytesIO()
    image.save(result,format='PNG')
    return result.getvalue()


def source_media(material, passage, *, by_id=None):
    result=[]
    def add(src,alt,identity):
        if src and src not in {x['src'] for x in result}:
            result.append({'src':src,'alt':alt or '原文图片','source_id':identity})
    def original(p):
        src=p.get('src') or ('/asset/'+p['asset'] if p.get('asset') else '')
        if src:add(src,p.get('alt') or p.get('path'),p['id'])
    original(passage)
    if by_id is None:
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
                'instruction':'你先查看对应原图，再把它放进回答，拿不到图时说明缺少哪份原件'}
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
