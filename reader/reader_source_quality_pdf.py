"""Recover page regions from the archived PDF, keeping Reader passage IDs intact."""
from collections import defaultdict
from pathlib import Path
from io import BytesIO
import re
import subprocess

import fitz
from PIL import Image

VISUAL = {'image','visual','figure','page_image','equation','formula','table'}


def bbox(rect):
    return [round(v, 3) for v in rect]


def area(r):
    return max(0, r.width) * max(0, r.height)


def padded(r, margin, boundary):
    return (r + (-margin,-margin,margin,margin)) & boundary


def coords(ref):
    values = ref.get('bbox')
    if not isinstance(values,(list,tuple)) or len(values)!=4:
        return fitz.Rect()
    r=fitz.Rect(values)
    return r if r.is_valid and not r.is_infinite else fitz.Rect()


def rate(a,b):
    return area(a & b)/max(area(a),1)


def combine(rects, gap):
    result=[]
    for rect in rects:
        expanded=rect+(-gap,-gap,gap,gap)
        hits=[r for r in result if expanded.intersects(r)]
        for r in hits:
            rect|=r
            result.remove(r)
        result.append(rect)
    return result


def page_sources(rows, page_id):
    return {p['id']: fitz.Rect(union) for p in rows if
            (boxes:=[coords(r) for r in p.get('refs',[]) if r.get('page_id')==page_id])
            and not (union:=join_rects(boxes)).is_empty}


def join_rects(rects):
    out=fitz.Rect()
    for rect in rects:
        out|=rect
    return out


def short_fragment(text):
    t=re.sub(r'[\s\u200b\u200c\u200d\ufeff]','',text)
    return bool(t and len(t)<=3 and re.fullmatch(r'[\w+\-−=<>≤≥(){}\[\]|.,…∑∫×÷·√∈]+',t))


def native_text(row):
    return row.get('raw_text') or row.get('en') or row.get('zh') or ''


def render(page, crop, source, number, fallback=False):
    zoom=4 if min(crop.width,crop.height)<65 else 3
    zoom=min(zoom,3400/max(crop.width,crop.height))
    if fallback:
        scale=round(zoom*72)
        # Poppler is independent of MuPDF's image and font decoding.
        command=['pdftoppm','-f',str(number),'-l',str(number),'-r',str(scale),'-cropbox','-singlefile',
                 '-x',str(max(0,int(crop.x0*scale/72))),'-y',str(max(0,int(crop.y0*scale/72))),
                 '-W',str(max(1,int(crop.width*scale/72))),'-H',str(max(1,int(crop.height*scale/72))),
                 '-png',str(source)]
        call=subprocess.run(command,capture_output=True,timeout=30,check=True)
        return call.stdout, scale
    shot=page.get_pixmap(matrix=fitz.Matrix(zoom,zoom),clip=crop,alpha=False,annots=True)
    return shot.tobytes('png'), round(72*zoom)


def exact_table(table, words):
    extracted=table.extract()
    if not extracted or len(extracted)<2 or len(extracted[0])<2 or not all(
            len(row)==len(extracted[0]) for row in extracted):
        return None
    populated=[sum(bool(v and v.strip()) for v in row) for row in extracted]
    if (sum(n>=2 for n in populated)<2 or
            sum(populated)<0.45*len(extracted)*len(extracted[0])):
        return None
    found=set(); expected=[]
    for row, cells in zip(extracted,table.rows,strict=True):
        if len(cells.cells)!=len(row) or any(c is None for c in cells.cells):
            return None
        for value,cell in zip(row,cells.cells,strict=True):
            if value is None or cell in found:
                return None
            found.add(cell)
            r=fitz.Rect(cell)
            indices={i for i,w in enumerate(words) if fitz.Point((w[0]+w[2])/2,(w[1]+w[3])/2) in r}
            if ' '.join(value.split())!=' '.join(words[i][4] for i in sorted(indices)):
                return None
            expected.extend(indices)
    outer=fitz.Rect(table.bbox)
    for i,w in enumerate(words):
        if fitz.Point((w[0]+w[2])/2,(w[1]+w[3])/2) in outer and expected.count(i)!=1:
            return None
    return {'format':'rectangular_cells','rows':extracted,'basis':'cell_text_matches_pdf_word_coordinates'}


def inspect_page(path, number, page_id, rows, repair, target):
    from reader_source_quality import asset_record, digest, put, store_visual

    with fitz.open(path) as doc:
        page=doc[number-1]
        bounds=page.rect
        transform=page.rotation_matrix
        raw=page.get_text('dict',flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
        lines=[(fitz.Rect(l['bbox'])*transform,l) for b in raw['blocks'] for l in b.get('lines',[])]
        raw_bitmap=[(fitz.Rect(i['bbox'])*transform,i) for i in page.get_image_info(xrefs=True)]
        drawings=page.get_drawings()
        rects=[]
        ignored=[]
        for d in drawings:
            rect=fitz.Rect(d['rect'])*transform
            if rect.width<1 or rect.height<1:
                rect=padded(rect,1,bounds)
            if area(rect)>=6 and rate(bounds,rect)<0.92:
                rects.append(rect & bounds)
            else:
                ignored.append({'bounds':bbox(rect),'reason':'small_path_or_page_background',
                                'items':len(d['items'])})
        vectors=[v for v in combine(rects,9) if area(v)>100]
        for v in combine(rects,9):
            if area(v)<=100:
                ignored.append({'bounds':bbox(v),'reason':'small_isolated_path'})
        layout=[]
        for r,info in raw_bitmap:
            clipped=r & bounds
            if area(clipped):
                layout.append(('image',clipped,'pdf_image_occurrence',None,
                    {'xref':info['xref'],'native_width':info['width'],'native_height':info['height']}))
        for r in vectors:
            enriched=join_rects([r]+[l for l,_ in lines if rate(l,padded(r,12,bounds))>=0.65])
            layout.append(('vector',enriched,'pdf_paths_and_nearby_labels',None,{}))

        registry={x['id']:x for x in rows}
        row_boxes={k:r*transform for k,r in page_sources(rows,page_id).items()}
        page_numbers={k for k,r in row_boxes.items() if registry[k].get('role')=='page_furniture'
                      and native_text(registry[k]).strip()==str(number)
                      and (r.y0<bounds.height*0.08 or r.y1>bounds.height*0.90)}
        formula_rows=[(k,r) for k,r in row_boxes.items() if registry[k]['kind'] in ('equation','formula')]
        for r,l in lines:
            spans=l.get('spans',[])
            text=''.join(s['text'] for s in spans).strip()
            math_glyph=any(ch in text for ch in ('∫','∑','∏','√','∈','≤','≥','⩽','⩾','↔','→'))
            math_font=any(re.search(r'(?:CMSY|STIX|Math|CMMI|CMEX)',s['font'],re.I) for s in spans)
            if r.width>3 and (math_glyph or math_font) and (len(text)<110 or '=' in text):
                formula_rows.append((None,r))
        formula_groups=combine([r for _,r in formula_rows],8)
        layout.extend(('formula',r,'pdf_formula_layout',None,{}) for r in formula_groups if area(r)>10)
        table_error=None
        rejected_grids=[]
        if len(drawings)>=5 or any(r['kind']=='table' for r in rows):
            try:
                tables=page.find_tables(strategy='lines').tables
                words=page.get_text('words',sort=True)
                for t in tables:
                    bounds_table=fitz.Rect(t.bbox)*transform
                    matrix=exact_table(t,words) if not page.rotation else None
                    labeled=any(r['kind']=='table' and r['id'] in row_boxes and
                                rate(row_boxes[r['id']],bounds_table)>0.6 for r in rows)
                    detail={'rows':t.row_count,'columns':t.col_count}
                    if matrix or labeled:
                        layout.append(('table',bounds_table,'pdf_grid_and_word_alignment',matrix,detail))
                    else:
                        rejected_grids.append({'bbox':bbox(bounds_table),**detail,
                            'reason':'grid_has_no_verified_cell_structure'})
            except (RuntimeError,ValueError,TypeError,AttributeError) as exc:
                table_error=type(exc).__name__
        if not lines:
            layout.append(('page_image',bounds,'scanned_or_vector_only_page',None,{}))

        direct={}; assigned=defaultdict(list); candidates=[]
        for k,r in row_boxes.items():
            if k in page_numbers:
                continue
            row=registry[k]; kind=row['kind']
            if kind not in VISUAL and not short_fragment(native_text(row)):
                continue
            if kind in ('formula','equation'):
                wanted={'formula'}
            elif kind=='table':
                wanted={'table'}
            elif kind in VISUAL:
                wanted={'image','vector','page_image'}
            else:
                wanted={'formula','vector','table','image','page_image'}
            scored=[(rate(r,x[1]),-area(x[1]),i) for i,x in enumerate(layout)
                    if x[0] in wanted and rate(r,x[1])>=0.65]
            if scored:
                idx=max(scored)[-1]; assigned[idx].append(k)
            else:
                region=join_rects([r]+[b for b,_ in lines if rate(b,padded(r,13,bounds))>=0.55])
                layout.append(('formula' if kind in ('equation','formula') else
                               kind if kind in VISUAL else 'fragment',region,
                               'native_source_bbox_and_original_page',None,{}))
                assigned[len(layout)-1].append(k)

        for i,(kind,reg,method,structure,detail) in enumerate(layout):
            ids=assigned[i]
            if kind in ('formula','fragment'):
                # Keep line context above and below a displaced summation limit or subscript.
                reg=join_rects([reg]+[row_boxes[k] for k in ids])
                if kind=='fragment':
                    reg=padded(reg,9,bounds)
            crop=padded(reg,5,bounds)
            if crop.is_empty:
                continue
            identity=asset_record(repair,page_id,kind,bbox(crop),ids,method,detail)
            key=target/'regions'/f"{identity['asset_id']}.json"
            if key.is_file():
                import json
                prior=json.loads(key.read_bytes())
                from reader_source_quality import checked_asset
                if prior.get('structure')==structure and checked_asset(prior,target):
                    candidates.append(prior|{'cache_hit':True,'member_source_ids':identity['member_source_ids']})
                    continue
            for fallback in (False,True):
                tool='poppler_render' if fallback else 'mupdf_render'
                try:
                    pixels,dpi=render(page,crop,path,number,fallback)
                    carrier=store_visual(pixels,target)
                    identity.update(carrier=carrier, dpi=dpi,structure=structure,
                                    status='verified_structured' if structure else 'verified_visual')
                    identity['attempts'].append({'method':tool,'status':'ok'})
                    break
                except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
                    identity['attempts'].append({'method':tool,'status':'failed','error':type(exc).__name__,
                                                 'detail':str(exc)[:180]})
            if not identity.get('carrier'):
                if all(x.get('error')=='ValueError' for x in identity['attempts'] if 'error' in x) and any(
                        'blank' in x.get('detail','') for x in identity['attempts']):
                    identity.update(status='blocked_user_input',reason='两个渲染器都没有在原页区域读到有效像素',
                                    failure_kind='unreadable_original')
                else:
                    identity.update(status='retryable_infrastructure_failure',reason='原页区域未能渲染，请重试提取')
            put(key,identity)
            candidates.append(identity)
        return {'page_id':page_id,'page':number,'rotation':page.rotation,'bounds':bbox(bounds),
                'counts':{'text_lines':len(lines),'bitmap_occurrences':len(raw_bitmap),'vectors':len(drawings),
                          'table_candidates':sum(t[0]=='table' for t in layout), 'ignored_paths':len(ignored)},
                'table_detection_error':table_error,'unverified_grid_candidates':rejected_grids,
                'visible_text_geometry':[{'bbox':bbox(r),'spans':len(l['spans'])} for r,l in lines],
                'ignored_paths':ignored,'verified_page_numbers':sorted(page_numbers),'regions':candidates,
                'reader_source_ids':[x['id'] for x in rows]}
