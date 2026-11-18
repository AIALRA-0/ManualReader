"""Keep Markdown resources anchored while applying or undoing a local revision."""
from collections import Counter
from functools import lru_cache
import re

from reader_p0_resources import digest, scan_markdown


@lru_cache(maxsize=24)
def native(text):
    return scan_markdown(text,details=True)


def sha(text):
    return digest(text.encode('utf8'))


PROTECTED=re.compile(
    r'(?P<url>(?:https?://|mailto:|www\.)[^\s<>\x60，。！？；]+)'
    r'|(?P<tick>\x60+)[^\n]+?(?P=tick)(?!\x60)'
    r'|(?:\b[A-Za-z]:[\\/]|(?:\.{1,2}|~)[\\/]|/)[^\s，。！？；]+'
    r'|\b[\w-]+(?:[./\\][\w-]+)+(?=[\s，。！？；：,;:()\[\]）]|$)'
    r'|「[^」\n]+」|『[^』\n]+』|“[^”\n]+”|《[^》\n]+》|【[^】\n]+】'
    r'|(?<!!)\[[^\]\n]+\]\([^\n)]*\)|\[\^[^\]\n]+\]')
NUMBERS=re.compile(r'(?<![A-Za-z])\d+(?:[.,:/–—-]\d+)*(?:[ \t]*[%％]|[ \t]*[A-Za-zµμ]+\b)?|'
                   r'[零〇一二两三四五六七八九十百千万亿兆半点]+(?=(?:年|月|日|秒|分钟|小时|元|米|拍|位|端|件|条|页|层|个))')
SCOPE=re.compile(r'至少|至多|除非|当且仅当|仅当|只有|只要|不超过|不低于|不少于|不得|不等于|非负|'
                 r'并未|尚未|没有|不能(?!据此|说明|理解为)|不保证|不满足|可能|有时|一般|必然|总是|'
                 r'全部|约为|大约|估算|实测|证实')
CAUSE=re.compile(r'导致|致使|源于|因为|所以|因而|因此|归因于|证明了|证实了|取决于|使得')
TURN=re.compile(r'不是.{0,40}而是|并不意味着|并非|不能据此|而非|反而|不过')
MIXED=re.compile(r'[，、；]+[。.]|[。][，、；]+|[；，、]{2,}')
BOLD_EDGE=re.compile(r'(?m)^\*\*([^*\r\n]+)\n\*\*[ \t]+(?=\S)')
HAN=r'\u3400-\u9fff'


def paragraphs(text):
    return [b for b in native(text)['blocks'] if b['type']=='paragraph_open'
            and not any(x['type'] in ('blockquote_open','html_block','fence','code_block')
                        and x['start']<=b['start']<b['end']<=x['end'] for x in native(text)['blocks'])]


def resources(text):
    return native(text)['resources']


def protected(text):
    return [(r['start'],r['end']) for r in resources(text)]+[m.span() for m in PROTECTED.finditer(text)]


def atoms(rex,text):
    return Counter(m[0] for m in rex.finditer(text))


def mechanical(text):
    guard=protected(text)
    result=[]
    def add(a,b,value,kind):
        if not any(x<b and a<y for x,y in guard):
            result.append(dict(kind=kind,start_offset=a,end_offset=b,old=text[a:b],new=value,
                               origin='structural_rule'))
    for row in paragraphs(text):
        a,b=row['start'],row['end']; value=text[a:b]
        if not re.search(f'[{HAN}]',value):
            continue
        for m in MIXED.finditer(value):
            # A full stop beside a separator is already an explicit sentence boundary.
            after=value[m.end():]
            replacement='，'
            if '。' in m[0] or '.' in m[0]:
                replacement='\n' if after.strip() and not after.startswith('\n') else ''
            add(a+m.start(),a+m.end(),replacement,'adjacent_punctuation')
        for m in BOLD_EDGE.finditer(value):
            add(a+m.end(1),a+m.end(),'**\n','split_emphasis')
    return sorted(result,key=lambda p:p['start_offset'])


def hints(text, start,end):
    excerpt=text[start:end]
    flags=[]
    if len(TURN.findall(excerpt))>=2:
        flags.append('multiple_contrast_markers')
    if re.search(f'[{HAN}][ \t]+(?=[{HAN}]{{3}})',excerpt):
        flags.append('possible_sentence_join')
    if re.search(rf'[{HAN}]\.(?:[ \t]+|$)',excerpt.strip()):
        flags.append('possible_ascii_sentence_boundary')
    return flags


def validate_local(text, a,b,new):
    old=text[a:b]
    p=next((v for v in paragraphs(text) if v['start']<=a<b<=v['end']),None)
    if not p or len(old)>360 or not new.strip() or len(new)>min(480,len(old)+100):
        raise ValueError('改动超出当前段落内的局部文字')
    if re.search(r'(?m)^\s*(?:#{1,6} |>|\x60{3}|~{3}|<[/!A-Za-z]|[-*+] |\d+[.)] )|\n[ \t]*\n',new):
        raise ValueError('改句加入了新的段落或 Markdown 结构')
    if any(x<b and a<y and not a<=x<y<=b for x,y in protected(text)):
        raise ValueError('改句边界切开了受保护的内容')
    for rex,label in ((PROTECTED,'引文、链接、路径或行内代码'),(NUMBERS,'数值或单位'),(SCOPE,'数值范围、条件或事实强度')):
        if atoms(rex,old)!=atoms(rex,new):
            raise ValueError(f'改句改变了{label}')
    for left,right in (('（','）'),('【','】'),('「','」'),('『','』'),('“','”'),('(',')')):
        if old.count(left)-old.count(right)!=new.count(left)-new.count(right):
            raise ValueError('括号或引号配对发生变化')
    return p


def invert(text,applied):
    for p in reversed(applied):
        a,b=p['view_start'],p['view_end']
        if text[a:b]!=p['new']:
            raise ValueError('不能在当前版本上撤销这处改动')
        text=text[:a]+p['old']+text[b:]
    return text


def layer(text,index,patches,*,coordinate='preview.md'):
    parts=[]; logged=[]; cursor=0; at=0
    for p in sorted(patches,key=lambda x:(x['start_offset'],x['end_offset'])):
        a,b=p['start_offset'],p['end_offset']
        if cursor>a or not 0<=a<=b<=len(text) or text[a:b]!=p['old']:
            raise ValueError('补丁交叠或原句与当前版本不一致')
        parts.extend((text[cursor:a],p['new']))
        at+=a-cursor
        logged.append(p|{'view_start':at,'view_end':at+len(p['new'])})
        cursor=b
        at+=len(p['new'])
    preview=''.join(parts+[text[cursor:]])
    if invert(preview,logged)!=text:
        raise ValueError('不能逐字符还原改动前的正文')
    actual={(r['kind'],r['start'],r['end']):r for r in resources(preview)}
    remap={}
    for old in resources(text):
        a,b=old['start'],old['end']; raw=text[a:b]
        touching=[p for p in logged if p['start_offset']<b and a<p['end_offset']]
        if touching:
            if len(touching)!=1 or not touching[0]['start_offset']<=a<b<=touching[0]['end_offset']:
                raise ValueError('改动切断了正文中的资源')
            p=touching[0]
            move=next((x for x in logged if x.get('move_from')==[p['start_offset'],p['end_offset']]),None)
            if move:
                x=move['view_start']+move.get('insert_prefix',0)+a-p['start_offset']
            else:
                if p['new'].count(raw)!=1:
                    raise ValueError('改句没有逐字保留原资源')
                x=p['view_start']+p['new'].index(raw)
        else:
            x=a+sum(len(p['new'])-len(p['old']) for p in logged if p['end_offset']<=a)
        y=x+len(raw)
        row=actual.pop((old['kind'],x,y),None)
        if not row or preview[x:y]!=raw or row.get('status')!=old.get('status'):
            raise ValueError('资源已变化，或在新的坐标无法定位')
        remap[(old['kind'],a,b)]=(x,y)
    if actual:
        raise ValueError('改动新增了没有登记的资源')
    rows=[]
    for row in index['resources']:
        pos=row.get('preview_location')
        if pos is not None:
            x,y=remap[row['kind'],pos['start_offset'],pos['end_offset']]
            pos=pos|{'start_offset':x,'end_offset':y,'start_line':preview.count('\n',0,x)+1,
                     'end_line':preview.count('\n',0,y-1)+1}
        rows.append(row|{'preview_location':pos})
    return preview,index|{'resources':rows,'preview_sha256':sha(preview),
                          'preview_coordinate_space':coordinate},logged
