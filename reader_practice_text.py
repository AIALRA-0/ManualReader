"""识别正文中与结构化练习完全相同的题目，保留题号与作答记录。"""
import copy,re,unicodedata

def _key(text):
    text=unicodedata.normalize('NFKC',str(text)).casefold()
    return re.sub(r'[\W_]+','',text)

def quiz_index(quizzes):
    indexed={}
    for quiz in quizzes:
        if quiz.get('deleted_at') or not quiz.get('prompt') or not 2<=len(quiz.get('options',[]))<=6:continue
        indexed.setdefault((_key(quiz['prompt']),tuple(sorted(_key(o)for o in quiz['options']))),quiz)
    return indexed


def duplicate_ranges(markdown,quizzes,indexed=None):
    """题干及全部选项都对应同一条已存练习时，才省略重复文本。"""
    lines=str(markdown).splitlines(keepends=True);offsets=[];offset=0;protected=[];fence=None
    for line in lines:
        offsets.append(offset);offset+=len(line)
        mark=re.match(r'^\s*(`{3,}|~{3,})',line)
        protected.append(bool(fence or mark or re.match(r'^\s*>',line)))
        if mark:
            if fence and mark[1][0]==fence[0] and len(mark[1])>=len(fence):fence=None
            elif not fence:fence=mark[1]
    options=re.compile(r'^\s*(?:[-*+]\s+)?(?:\*\*)?([A-F])[.、·：:]\s*(.*?)(?:\*\*)?\s*$')
    if indexed is None:indexed=quiz_index(quizzes)
    result=[];i=0
    while i<len(lines):
        if protected[i] or not options.match(lines[i]):i+=1;continue
        start=i;items=[];letters=[];end=i
        while end<len(lines) and not protected[end]:
            match=options.match(lines[end])
            if match:letters.append(match[1]);items.append(_key(match[2]));end+=1
            elif not lines[end].strip():end+=1
            else:break
        if len(items)>=2 and len(set(letters))==len(letters):
            for count in (1,2,3):
                j=start-1
                while j>=0 and not lines[j].strip():j-=1
                first=j-count+1
                if first<0 or any(protected[first:start]):continue
                stem=' '.join(x.strip()for x in lines[first:j+1])
                stem=re.sub(r'^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:就地练习|练习题?|小测|检验|检查一下)\s*(?:[Qq]?\s*\d+)?(?:\*\*)?\s*[：:.、·]?\s*','',stem)
                match=indexed.get((_key(stem),tuple(sorted(items))))
                if match:
                    last=end
                    while last>start and not lines[last-1].strip():last-=1
                    result.append((offsets[first],offsets[last]if last<len(lines)else len(markdown),match.get('id')or match.get('prompt')));break
        i=max(end,i+1)
    return result

def clean(markdown,quizzes,indexed=None):
    text=str(markdown or'');ranges=duplicate_ranges(text,quizzes,indexed)
    for start,end,_ in reversed(ranges):text=text[:start]+text[end:]
    return text.strip(),[identity for _,_,identity in ranges]

def display_lesson(material,quizzes):
    """只为页面省略重复题目；旧正文、片段编号和进度都留在原记录中。"""
    groups={}
    for p in material.get('passages',[]):
        if p.get('content_kind')=='explanation' and not p.get('display_omit'):
            groups.setdefault(p.get('explanation_id')or p.get('section_id')or p['id'],[]).append(p)
    changed={};indexed=quiz_index(quizzes)
    for passages in groups.values():
        records=[];offset=0
        for p in passages:
            text=p.get('display_markdown',p.get('zh')or p.get('en')or'')
            records.append((p,text,offset,offset+len(text)));offset+=len(text)+2
        joined='\n\n'.join(r[1]for r in records);ranges=duplicate_ranges(joined,quizzes,indexed)
        for p,text,start,end in records:
            overlaps=[(max(a,start)-start,min(b,end)-start)for a,b,_ in ranges if a<end and b>start]
            if not overlaps:continue
            for a,b in reversed(overlaps):text=text[:a]+text[b:]
            changed[p['id']]={**p,'display_markdown':text.strip(),**({'display_omit':True}if not text.strip()else{})}
    return {**material,'passages':[changed.get(p['id'],p)for p in material.get('passages',[])]}if changed else material
