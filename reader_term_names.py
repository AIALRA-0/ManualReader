"""统一整理术语名称；不猜测缺失全称，原始名称和编号继续保留。"""
import copy,re

HAN=re.compile('[\u3400-\u9fff]')
PROTECTED=re.compile(r'```[\s\S]*?```|`[^`]*`|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\$\$[\s\S]*?\$\$|\$[^$\n]+\$|https?://\S+|\b\w+_\w+\b')
EXCEPTIONS={'arXiv','eBPF','iOS','macOS','pH','bit','npm'}

def english_title(value,exceptions=(),preserve=False):
    if preserve or value in exceptions:return value
    allowed=EXCEPTIONS|set(exceptions)
    def part(text):
        def word(m):
            raw=m[0]
            if raw in allowed or raw.isupper():return raw
            if raw.lower()=='s' and m.start()>0 and text[m.start()-1] in "'’":return 's'
            return raw[0].upper()+raw[1:]
        return re.sub(r'\b[A-Za-z][A-Za-z0-9]*\b',word,text)
    out=[];at=0
    for m in PROTECTED.finditer(value):out.extend((part(value[at:m.start()]),m[0]));at=m.end()
    return ''.join(out)+part(value[at:])

def analyze_name(value,exceptions=(),preserve=False):
    raw=str(value or '').strip();name=raw;issues=[]
    match=re.fullmatch(r'(.*?)\s*[（(]([^（）()]*)[）)]',name)
    if not match:
        reverse=re.fullmatch(r'[（(]([^（）()]*)[）)]\s*(.+)',name)
        bare=re.fullmatch(r'(.*[\u3400-\u9fff])\s+([A-Za-z][A-Za-z0-9 \-’\']+)',name)
        if reverse:chinese,english=reverse[2].strip(),reverse[1].strip()
        elif bare:chinese,english=bare[1].strip(),bare[2].strip()
        else:
            return {'name':name,'display_name':name,'issues':['missing_english_name'] if HAN.search(name) else [],'changed':False}
    else:chinese,english=(s.strip() for s in match.groups())
    if not HAN.search(chinese) and HAN.search(english):chinese,english=english,chinese
    if chinese==english:return {'name':chinese,'display_name':chinese,'issues':[],'changed':chinese!=raw}
    # 括号里重复写了同一个中文名时，可以直接删除；其他中文含义留给一次集中补齐。
    chinese_core=re.sub(r'^[A-Za-z][A-Za-z0-9-]*\s+','',chinese).strip()
    if chinese_core and HAN.search(chinese_core):english=english.replace(chinese_core,'').strip(' ,，;；')
    if HAN.search(english):issues.append('chinese_inside_english')
    suffix=re.fullmatch(r'(.+?)[,，]\s*([A-Z][A-Z0-9-]{1,12})',english)
    if suffix:
        english,abbr=suffix.groups()
        if not chinese.startswith(abbr+' '):chinese=abbr+' '+chinese
    abbreviation=re.match(r'^([A-Z][A-Z0-9-]{1,12})\s+',chinese)
    if abbreviation and re.match(r'^'+re.escape(abbreviation[1])+r'\s+',english):
        rest=re.sub(r'^'+re.escape(abbreviation[1])+r'\s+','',english);letters=re.sub('[^A-Za-z]','',abbreviation[1]);words=re.findall(r'\b[A-Za-z][A-Za-z-]*\b',rest)
        if len(words)>=len(letters) and ''.join(w[0] for w in words[:len(letters)]).casefold()==letters.casefold():english=rest
        else:issues.append('abbreviation_not_expanded')
    for abbr in re.findall(r'\b[A-Z][A-Z0-9]{1,12}\b',chinese):
        if re.search(r'\b'+re.escape(abbr)+r'\b',english) and 'abbreviation_not_expanded' not in issues:issues.append('abbreviation_not_expanded')
    if not HAN.search(chinese):issues.append('missing_chinese_name')
    if not english or not re.search('[A-Za-z]',english):issues.append('missing_english_name')
    english=english_title(english,exceptions,preserve)
    formatted=chinese+'（'+english+'）' if english else chinese
    return {'name':formatted,'display_name':chinese if issues and HAN.search(chinese) else formatted,'issues':issues,'changed':formatted!=raw}

def term_name(term):return analyze_name(term.get('name',''),term.get('case_exceptions',[]),bool(term.get('english_case_exception')))

def term_key(value):
    name=analyze_name(value)['name'];name=re.sub(r'^[A-Z][A-Z0-9-]{1,12}\s+(?=[\u3400-\u9fff])','',name)
    return re.sub(r'\s+','',name).casefold()

def normalize_state(state,stamp):
    changed=[];primary={};redirect={};pending=[]
    for term in state.get('terms',[]):
        term.pop('duplicate_of',None)
        report=term_name(term);old=term['name']
        if report['changed'] and not report['issues']:
            term.setdefault('name_history',[]).append({'name':old,'at':stamp(),'action':'script_normalize'});term.setdefault('source_names',[]).append(old);term['name']=report['name'];changed.append(term['id'])
        term['display_name']=report['display_name']
        if report['issues']:pending.append({'term_id':term['id'],'name':old,'issues':report['issues'],'professional_definition':term.get('professional_definition') or term.get('definition',''),'intuitive_explanation':term.get('intuitive_explanation',''),'question_ids':term.get('question_ids',[]),**{k:copy.deepcopy(term[k]) for k in ('case_exceptions','english_case_exception') if k in term}})
        key=term_key(term['name'])
        if key not in primary:primary[key]=term;continue
        target=primary[key];redirect[term['id']]=target['id'];term['duplicate_of']=target['id']
        target['aliases']=list(dict.fromkeys(target.get('aliases',[])+[old]+term.get('aliases',[])+term.get('source_names',[])))
        target['alias_ids']=list(dict.fromkeys(target.get('alias_ids',[])+[term['id']]))
        for field in ('question_ids','learning_node_ids','explanation_ids'):target[field]=list(dict.fromkeys(target.get(field,[])+term.get(field,[])))
    for row in state.get('rounds',[]):
        for q in row['questions']:q['term_ids']=list(dict.fromkeys(redirect.get(i,i) for i in q.get('term_ids',[])))
    return {'changed':changed,'duplicate_redirects':redirect,'pending':pending}

def display_state(state):
    state=copy.deepcopy(state);report=normalize_state(state,lambda:'display_only')
    state['terms']=[t for t in state.get('terms',[]) if not t.get('duplicate_of')]
    return state,report
