"""Read real PDF page objects and crop evidence in unrotated PDF coordinates."""

from io import BytesIO
from pathlib import Path
import re
from time import perf_counter, process_time

import fitz
from PIL import Image, ImageDraw

from .cache import Cache, digest
from .media import original_text, unavailable, visual


MATH = re.compile(r'[∑∫∏√≤≥≠≈∈∂∞∀∃]|(?:[=<>±]\s*[\w(\[])')
CAPTION = re.compile(r'^\s*(?:fig(?:ure)?\.?\s*\d|table\s*\d|[图表]\s*\d)', re.I)
CONTINUED = re.compile(r'(?:continued|续表|接上页)', re.I)


def rectangle(value):
    return [round(float(x),3) for x in value]


def locator(box):
    return {'type':'pdf_bbox','bbox':rectangle(box),'coordinate_system':'pdf_points_unrotated'}


class Page:
    def __init__(self,page,cache):
        self.page = page
        self.cache = cache
        self.frame = page.rect
        self.last = {}

    def png(self, box, scale):
        rect = fitz.Rect(box)&self.frame
        if rect.is_empty or rect.width < 1 or rect.height < 1:
            raise ValueError('原页中的区域没有有效尺寸')
        scale = min(scale, (8_000_000/(rect.width*rect.height))**0.5)
        key = tuple(rect),scale
        if key not in self.last:
            self.last[key] = self.page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=rect,alpha=False).tobytes('png')
        return self.last[key]

    def crop(self, kind, box, scale=1.75, **fields):
        try:
            clip=fitz.Rect(box)&self.frame
            rendered=clip
            if not clip.is_empty and (clip.width<1 or clip.height<1):
                rendered=padded(self.page,clip,2)
                fields['rendered_bbox']=rectangle(rendered)
            return visual(self.cache,kind,locator(clip),self.png(rendered,scale),**fields)
        except (RuntimeError, ValueError, OSError) as error:
            return unavailable(kind,locator(box),str(error),**fields)


def merge_drawings(page,drawings):
    try:
        return page.cluster_drawings(drawings=drawings,x_tolerance=9,y_tolerance=9)
    except (AttributeError,RuntimeError,ValueError):
        return [d['rect'] for d in drawings]


def lines_in_order(page,blocks):
    # Preserve source blocks and their line positions. Split clear columns at the middle gutter.
    meaningful=[b for b in blocks if b['type']==0 and b.get('lines')]
    middle=page.rect.width/2
    left_lines=lambda lines:[l for l in lines if l['bbox'][2]<=middle-8]
    right_lines=lambda lines:[l for l in lines if l['bbox'][0]>=middle+8]
    native_lines=[l for b in meaningful for l in b['lines']]
    if len(left_lines(native_lines))>=2 and len(right_lines(native_lines))>=2:
        divided=[]
        for b in meaningful:
            lhs=left_lines(b['lines']); rhs=right_lines(b['lines'])
            if lhs and rhs and len(lhs)+len(rhs)==len(b['lines']):
                for side in (lhs,rhs):
                    bbox=fitz.Rect(side[0]['bbox'])
                    for line in side[1:]:
                        bbox |= fitz.Rect(line['bbox'])
                    divided.append({**b,'bbox':tuple(bbox),'lines':side})
            else:
                divided.append(b)
        meaningful=divided
    left=[b for b in meaningful if b['bbox'][2]<=middle-8]
    right=[b for b in meaningful if b['bbox'][0]>=middle+8]
    if len(left)>=2 and len(right)>=2:
        others=[b for b in meaningful if b not in left and b not in right]
        key=lambda b:(round(b['bbox'][1],1),b['bbox'][0])
        result=[]
        local=left+right
        for block in sorted(others,key=key):
            before=[b for b in local if b['bbox'][1]<block['bbox'][1]]
            result.extend(sorted(before,key=lambda b:(b in right,key(b))))
            local=[b for b in local if b not in before]
            result.append(block)
        return result+sorted(local,key=lambda b:(b in right,key(b))), 'columns'
    return meaningful,'native_position_order'


def observed_tables(page,drawings):
    if not drawings or not any(d.get('type') in ('s','fs') for d in drawings):
        return []
    try:
        found=page.find_tables(paths=drawings).tables
    except (RuntimeError,ValueError,TypeError,IndexError):
        return []
    tables=[]
    for table in found:
        rows=table.extract()
        if table.row_count<2 or table.col_count<2 or not rows:
            continue
        row_density=[sum(bool(x and x.strip()) for x in r) for r in rows]
        if row_density[0]<2 or sum(x>=2 for x in row_density[1:]) < 1 or sum(row_density)<max(4,len(rows)):
            continue
        # Keep actual cells and merged holes as returned by the original PDF geometry.
        cells=[[rectangle(cell) if cell else None for cell in row.cells] for row in table.rows]
        tables.append((table.bbox,{'rows':rows,'cells':cells,'header_names':table.header.names,
                                  'header_external':table.header.external}))
    return tables


def coverage_residual(png,boxes,frame):
    with Image.open(BytesIO(png)) as im:
        raster=im.convert('L')
    raster.thumbnail((380,380))
    sx,sy=raster.width/frame.width,raster.height/frame.height
    active=raster.point(lambda value: 255 if value<214 else 0)
    unaccounted=active.copy()
    painter=ImageDraw.Draw(unaccounted)
    for box in boxes:
        rect=fitz.Rect(box)&frame
        if not rect.is_empty:
            painter.rectangle((rect.x0*sx-2,rect.y0*sy-2,rect.x1*sx+2,rect.y1*sy+2),fill=0)
    area=unaccounted.histogram()[255]
    if area<max(10,raster.width*raster.height*0.0007):
        return None
    box=unaccounted.getbbox()
    return {'bbox':rectangle([box[0]/sx,box[1]/sy,box[2]/sx,box[3]/sy]),'ink_pixels':area,
            'raster_size':list(raster.size)}


def padded(page,box,extra=3):
    b=fitz.Rect(box)
    return fitz.Rect(b.x0-extra,b.y0-extra,b.x1+extra,b.y1+extra)&page.rect


def extract(page,cache,preview,geometry):
    painter=Page(page,cache)
    blocks=page.get_text('dict',flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES,sort=True)['blocks']
    text_blocks, reading_order=lines_in_order(page,blocks)
    assets=[]
    covered=[]
    captions=[]
    numbers=0
    for b in text_blocks:
        lines=[]
        for line in b['lines']:
            spans=[{'text':s['text'],'bbox':rectangle(s['bbox']),'font':s['font'],
                    'size':round(s['size'],3),'flags':s['flags']} for s in line['spans']]
            if not spans:
                continue
            line_text=''.join(s['text'] for s in spans)
            numbers+=1
            loc={**locator(line['bbox']),'line':numbers}
            kind='caption' if CAPTION.search(line_text) else 'text'
            row=original_text(kind,loc,line_text,spans=spans)
            if kind=='caption':
                captions.append(row)
            assets.append(row)
            lines.append(row)
            covered.append(line['bbox'])
        # Join spatial fragments without replacing the native text or claiming numeric LaTeX.
        math_lines=[v for v in lines if MATH.search(v['text'])]
        if math_lines:
            box=fitz.Rect(b['bbox'])
            formula=painter.crop('formula',padded(page,box),scale=2.3,origin='native_glyphs_and_page_pixels',
                                 members=[i['locator']['line'] for i in lines],
                                 extracted_glyphs='\n'.join(i['text'] for i in lines))
            assets.append(formula)
    images=page.get_image_info(hashes=True,xrefs=True)
    visual_boxes=[]
    for image in images:
        box=fitz.Rect(image['bbox'])&page.rect
        if box.is_empty:
            continue
        covered.append(box)
        visual_boxes.append(box)
        assets.append(painter.crop('image',box,origin='embedded_pdf_image',
                                   original_size=[image['width'],image['height']]))
    drawings=page.get_drawings()
    for box in merge_drawings(page,drawings) if drawings else []:
        box=fitz.Rect(box)&page.rect
        if box.is_empty:
            continue
        covered.append(box)
        if box.width<10 or box.height<9:
            continue
        # Keep the complete vector region plus a nearby caption and axis labels.
        nearby=[c for c in captions if 0 <= c['locator']['bbox'][1]-box.y1 <= 38 or
                0 <= box.y0-c['locator']['bbox'][3] <= 26]
        for v in nearby:
            box |= fitz.Rect(v['locator']['bbox'])
        assets.append(painter.crop('vector',padded(page,box,9),origin='page_vector_paths'))
    for box,structure in observed_tables(page,drawings):
        asset=painter.crop('table',padded(page,box),origin='pdf_cell_geometry')
        asset['structure']=structure
        asset['capabilities'].append('compute_structure')
        # Native cells remain usable even if a separate image renderer failed.
        if asset['status']=='unavailable':
            asset.update(status='available',visual_error=asset.pop('reason'))
        assets.append(asset)
    residual=coverage_residual(preview,covered,page.rect)
    if residual:
        assets.append(painter.crop('unclassified_region',padded(page,residual['bbox'],3),origin='raster_coverage'))
    scan=not any(a.get('text','').strip() for a in assets)
    assets.append(visual(cache,'page_visual',locator(page.rect),preview,origin='rendered_original_page',
                         scanned_page=scan))
    return {'geometry':geometry,'assets':assets,'reading_order':reading_order,
            'observed':{'text_lines':numbers,'embedded_images':len(images),'vector_paths':len(drawings),
                        'residual_ink':residual,'scan_candidate':scan}}


def page_job(raw_path,storage,index,revision,refresh=False,submitted=None):
    # Each worker opens just its requested page from the shared version-pinned PDF.
    began=perf_counter()
    cpu=process_time()
    cache=Cache(storage)
    with fitz.open(Path(raw_path)) as doc:
        page=doc[index-1]
        rotation=page.rotation
        page.set_rotation(0)
        geometry={'width':round(page.rect.width,3),'height':round(page.rect.height,3),
                  'rotation':rotation,'coordinate_system':'pdf_points_unrotated','unit':'pt'}
        raster=Page(page,cache).png(page.rect,1.35)
        native=page.get_text('dict',flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES,sort=True)
        key=[revision,digest(raster),geometry,native]
        result,hit,_=cache.calculate('pages',key,lambda:extract(page,cache,raster,geometry),refresh=refresh)
        return {'number':index,**result,'cache':hit,'wall_ms':(perf_counter()-began)*1000,
                'queue_ms':(began-submitted)*1000 if submitted is not None else 0,
                'cpu_ms':(process_time()-cpu)*1000}
