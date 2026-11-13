"""Check new two-paragraph terms without rewriting stored terminology."""
import difflib
import re
import unicodedata

MINIMUMS={"professional_definition":80,"intuitive_explanation":100}
from reader_task_prompts import TERM_COMPLETION, section

INSTRUCTION = section(TERM_COMPLETION, 3, 5)
INDEPENDENCE=re.compile(r"如上所述|如前所述|沿用.{0,12}(?:例子|比喻)|前文(?:所述|提到|的)|(?:本题|本章|本图)(?=中|内|里|的|所|要求|给出|提到|介绍|展示|规定|采用|使用|表示|说明|讨论|讲|[，。；：、\s]|$)|这里跟随")
NONINFORMATIVE=re.compile(r"不代表|不能说明|无法说明|不能证明|不等于|无法保证|仅供参考|类比.{0,12}(?:局限|不能)")
def length(text):
    return len(re.sub(r"\s+","",text))

def inspect(term,enforce_minimum=True):
    errors=[];warnings=[]
    text={}
    for field,floor in MINIMUMS.items():
        value=term.get(field)
        if not isinstance(value,str)or not value.strip():
            errors.append(field+": 需要完整解释");continue
        value=value.strip();text[field]=value
        if re.search(r"\n\s*\n",value)or re.search(r"(?m)^\s*(?:#{1,6}\s|[-*]\s|\d+[.)]\s)",value):
            errors.append(field+": 保持一个自然段，不加列表或小标题")
        if enforce_minimum and length(value)<floor:
            errors.append(field+": 至少"+str(floor)+"个非空白字符，当前"+str(length(value)))
        if INDEPENDENCE.search(value):errors.append(field+": 解释依赖原文章或前文")
        if NONINFORMATIVE.search(value):warnings.append(field+": 检查这句限定是否提供必要知识")
    if len(text)==2:
        values=list(text.values());clean=lambda s:re.sub(r"\W+","",s)
        if difflib.SequenceMatcher(None,*map(clean,values)).ratio()>.90:
            errors.append("定义与直观解释基本重复")
    return {"passed":not errors,"errors":errors,"warnings":warnings,
            "lengths":{field:length(value)for field,value in text.items()}}

def validate_new_terms(terms):
    problems=[]
    for index,term in enumerate(terms):
        if term.get("term_id")and term.get("action")!="revise":
            continue
        result=inspect(term)
        if result["errors"]:problems.append({"index":index,"name":term.get("name",""),"errors":result["errors"]})
    if problems:
        raise ValueError("术语需要补齐："+str(problems))
    return True

def revision_fields(item,proposal):
    """先核对当前版本与旧文本，再检查合并后的两段；不改动记录。"""
    fields={key:proposal[key]for key in MINIMUMS if key in proposal}
    if not fields:return fields
    old={"professional_definition":item.get("professional_definition")or item.get("definition")or"",
         "intuitive_explanation":item.get("intuitive_explanation")or""}
    if proposal.get("expected_version")!=item.get("version",1):
        raise ValueError("术语版本已经变化，请重新读取后再修订")
    for sent,field in (("expected_definition","professional_definition"),("expected_intuitive_explanation","intuitive_explanation")):
        if sent in proposal and str(proposal[sent]).strip()!=old[field].strip():
            raise ValueError("术语解释已经变化，请保留新内容并重新核对")
    result=inspect({**old,**fields})
    # 只补缺失字段时保留另一段的合格旧排版；新写字段仍按两段式检查。
    errors=[error for error in result["errors"] if not any(
        key not in fields and error==key+": 保持一个自然段，不加列表或小标题"
        for key in MINIMUMS)]
    if errors:raise ValueError("术语需要补齐："+str(errors))
    return fields

def repair_fields(item):
    """只登记过短、依赖前文或完全重复的字段，不因旧换行重写合格解释。"""
    values={"professional_definition":item.get("professional_definition")or item.get("definition")or"",
            "intuitive_explanation":item.get("intuitive_explanation")or""}
    fields=[key for key,value in values.items()if length(value)<MINIMUMS[key]or INDEPENDENCE.search(value)]
    if values["professional_definition"]and values["professional_definition"]==values["intuitive_explanation"]:
        fields=list(MINIMUMS)
    return fields

def audit(terms):
    entries=[]
    for term in terms:
        if term.get("duplicate_of"):continue
        value={**term,"professional_definition":term.get("professional_definition")or term.get("definition")or""}
        report=inspect(value)
        entries.append({"term_id":term["id"],"name":term["name"],"version":term.get("version",1),
                        "repair_fields":repair_fields(term),"lengths":report["lengths"],
                        "format_errors":report["errors"],"warnings":report["warnings"]})
    return {"version":"terms-two-paragraph-v1","minimums":dict(MINIMUMS),
            "needs_revision":sum(bool(row["repair_fields"])for row in entries),"entries":entries}

def term_labels(item):
    """取登记全称、缩写和明确别名，供查找正文和核对复用。"""
    import re
    name = str(item.get('name') or '')
    labels = {name, *(alias for alias in item.get('aliases', []) if isinstance(alias, str))}
    bilingual = re.fullmatch(r'(.+?)[（(]([^()（）]+)[）)]', name)
    if bilingual:
        prefix, english = bilingual.groups()
        labels.update((prefix, english))
        abbreviated = re.fullmatch(r'([A-Za-z][A-Za-z0-9.+/-]*(?:[ -][A-Za-z0-9.+/-]+)*)\s+([\u3400-\u9fff].*)', prefix.strip())
        if abbreviated:
            labels.update(abbreviated.groups())
    for label in tuple(labels):
        if '或' in label and re.fullmatch(r'[\u3400-\u9fff]{2,}(?:或[\u3400-\u9fff]{2,})+',label):
            labels.update(label.split('或'))
    return {label.strip() for label in labels if len(label.strip()) > 1}

def term_pattern(label):
    label=unicodedata.normalize('NFKC',label).casefold()
    left=r'(?<![a-z0-9_])' if label[0].isascii() and label[0].isalnum() else ''
    right=r'(?![a-z0-9_])' if label[-1].isascii() and label[-1].isalnum() else ''
    return re.compile(left+re.escape(label)+right)


def term_prose(markdown):
    prose=re.sub(r'\]\([^\n)]*\)|https?://\S+', '', markdown)
    return unicodedata.normalize('NFKC',prose).casefold()


def term_matcher(terms):
    """为同一项目的多段正文准备一次名称索引。"""
    labels = {}
    for term in terms:
        if term.get('duplicate_of'):
            continue
        for label in term_labels(term):
            key = unicodedata.normalize('NFKC', label).casefold()
            labels.setdefault(key, {})[term['id']] = term
    patterns = []
    for label, candidates in labels.items():
        if len(candidates) != 1:
            continue
        patterns.append((term_pattern(label), candidates))

    def find(markdown):
        prose = term_prose(markdown)
        matches = {}
        for pattern, candidates in patterns:
            if pattern.search(prose):
                matches.update(candidates)
        return list(matches.values())
    return find

def mentioned_terms(markdown, terms):
    """按原词找唯一匹配；短缩写需要单独出现。"""
    return term_matcher(terms)(markdown)

def resolve_asset_term(row, known, index=0):
    """复用时，名称和编号必须指向同一个已登记概念。"""
    from reader_term_names import term_key
    path=f'/terms/{index}'
    identity=row.get('term_id')
    name=row.get('name') or ''
    if not isinstance(name,str) or identity is not None and not isinstance(identity,str):
        raise ValueError(f'{path} 的名称和编号需要是文字')
    key=term_key(name)
    by_id={item['id']:item for item in known}
    active=[item for item in known if not item.get('duplicate_of')]
    canonical=[item for item in active if key and term_key(item.get('name',''))==key]

    proposed=planned_term_keys(name,include_full=False) if key else set()
    matches=canonical or [item for item in active if proposed and
                          proposed <= {term_key(alias) for alias in term_labels(item)}]
    if not identity:
        if len(matches)>1:
            raise ValueError(f'{path}/name「{name}」对应多个登记条目，请指定正确编号')
        return next(iter(matches),None)
    existing=by_id.get(identity)
    if existing and existing.get('duplicate_of'):
        existing=by_id.get(existing['duplicate_of'])
    if not existing:
        existing=next((item for item in active if identity in item.get('alias_ids',[])),None)
    if not existing:
        raise ValueError(f'{path}/term_id「{identity}」不在当前项目的术语登记表')
    same_name=key==term_key(existing.get('name',''))
    if key and not same_name and (len(matches)!=1 or matches[0]['id']!=existing['id']):
        raise ValueError(f'{path}/term_id「{identity}」登记的是「{existing["name"]}」，提交的是「{name}」')
    return existing

def planned_term_keys(name,*,include_full=True):
    """计划只写简短术语名；括号里的缩写和文中的代码记号用同一拼写核对。"""
    import re, unicodedata
    from reader_term_names import term_key
    text=unicodedata.normalize('NFKC',name).replace('`','').strip()
    if match:=re.fullmatch(r'(.+?)\s*\(([^()]+)\)',text):
        keys={term_key(value) for value in match.groups() if value.strip()}
        return keys|({term_key(text)} if include_full else set())
    return {term_key(text)}


def validate_asset_term(row,known,index=0):
    """核对一条术语，交还实际应复用的登记条目。"""
    if not isinstance(row,dict):raise ValueError(f'/terms/{index} 需要是对象')
    aliases=row.get('aliases',[])
    if not isinstance(aliases,list) or any(not isinstance(alias,str) or not alias.strip() or len(alias)>500 for alias in aliases):
        raise ValueError(f'/terms/{index}/aliases 需要是实际使用的文字别名')
    existing=resolve_asset_term(row,known,index)
    try:
        if existing is not None:
            if row.get('action')=='revise':revision_fields(existing,row)
        else:
            result=inspect({**row,'professional_definition':row.get('professional_definition')or row.get('definition')})
            if result['errors']:
                raise ValueError('术语需要补齐：'+'；'.join(result['errors']))
    except ValueError as error:
        raise ValueError(f'/terms/{index}「{row.get("name") or row.get("term_id") or ""}」{error}') from error
    return existing


def validate_asset_terms(terms,known):
    """写入前核对术语身份和新写解释；旧解释随原编号保留。"""
    if not isinstance(terms,list):raise ValueError('术语需要使用列表')
    for index,row in enumerate(terms):
        validate_asset_term(row,known,index)
    return True

def link_draft_terms(draft,registered):
    """组内先按实际正文补上同一术语的多个引用，再各自校验回答。"""
    rows=draft.get('terms',[])
    if not isinstance(rows,list):
        return
    proposed={}
    links={}
    for index,row in enumerate(rows):
        if not isinstance(row,dict) or not isinstance(row.get('question_ids'),list):
            continue
        try:
            known=validate_asset_term(row,registered,index)
        except ValueError:
            continue
        names=known or row
        identity=names.get('id') or 'new-'+str(index)
        proposed[identity]={**names,'id':identity}
        links[identity]=index
    mentions=term_matcher(proposed.values())
    for answer in draft.get('answers',[]):
        if not isinstance(answer,dict) or not isinstance(answer.get('question_id'),str):
            continue
        body='\n\n'.join(part.get('markdown') or '' for part in answer.get('parts',[]) if isinstance(part,dict))
        for match in mentions(body):
            row=rows[links[match['id']]]
            qid=answer['question_id']
            if qid not in row['question_ids']:
                row['question_ids'].append(qid)

def cover_planned_terms(value, analysis, registered):
    """复用正文中唯一匹配的术语，并检查分析器登记的概念。"""
    if not isinstance(value.get('terms'), list):
        return ['terms 需要使用列表']
    rows = value['terms']
    issues = []
    existing_rows = {}
    known_mentions=term_matcher(registered)
    answer_parts = {a['question_id']: a.get('parts') or a.get('answer_parts', [])
                    for a in value.get('answers', [])}
    lesson = analysis['mode'] == 'lesson'
    lesson_parts=value.get('explanation', {}).get('parts', [])
    bodies={item['task_id']:'\n\n'.join(str(part.get('markdown') or '') for part in
                                         (lesson_parts if lesson else answer_parts.get(item['task_id'], [])))
            for item in analysis['items']}
    references={task:{term['id']:term for term in known_mentions(body)} for task,body in bodies.items()}
    from reader_analysis import TECHNICAL_NAME
    required={item['task_id']:{key for name in item.get('essential_terms',[]) if TECHNICAL_NAME.search(name)
                              for key in planned_term_keys(name)}
              for item in analysis['items']}
    from reader_term_names import term_key
    registered_labels={term_key(label) for term in registered for label in term_labels(term)}
    accepted = []
    for index, row in enumerate(rows):
        try:
            if isinstance(row,dict) and row.get('term_id') and not row.get('name'):
                candidate=resolve_asset_term(row,registered,index)
                qids=row.get('question_ids',[])
                if not isinstance(qids,list) or not lesson and any(qid not in bodies for qid in qids):
                    raise ValueError(f'/terms/{index}/question_ids 关联到本组以外的问题')
                labels={key for label in term_labels(candidate) for key in planned_term_keys(label)}
                uses=[task for task in (bodies if lesson else qids)
                      if candidate['id'] in references[task] or labels & required[task]]
                if row.get('action')!='revise':
                    if not uses:
                        continue
                    if not lesson:
                        row['question_ids']=uses
                    original=row.get('aliases',[])
                    if not isinstance(original,list) or any(not isinstance(alias,str) or not alias.strip() or len(alias)>500 for alias in original):
                        raise ValueError(f'/terms/{index}/aliases 需要是实际使用的文字别名')
                    # 同一回答既使用已登记名称，也使用计划里要求的别名时才补别名。
                    new_aliases=[alias for alias in original if term_key(alias) not in registered_labels and any(
                        term_key(alias) in required[task] and candidate['id'] in references[task] and
                        term_pattern(alias.strip()).search(term_prose(bodies[task])) for task in uses)]
                    row['aliases']=list(dict.fromkeys([*candidate.get('aliases',[]),*new_aliases]))
            existing = validate_asset_term(row, registered, index)
        except ValueError as error:
            issues.append(str(error))
            accepted.append(row)
            continue
        if existing:
            row.update(term_id=existing['id'], name=existing['name'])
            row['aliases']=list(dict.fromkeys([*existing.get('aliases',[]),*row.get('aliases',[])]))
            existing_rows[existing['id']] = row
        accepted.append(row)
    rows[:]=accepted
    proposed=[{**row,'id':'proposal-'+str(index)} for index,row in enumerate(rows)
              if isinstance(row,dict) and not row.get('term_id') and row.get('name')]
    new_mentions=term_matcher(proposed)
    for item in analysis['items']:
        task = item['task_id']
        body = bodies[task]
        for term in references[task].values():
            row = existing_rows.get(term['id'])
            if row is None:
                row = {'term_id':term['id'], 'name':term['name'], 'question_ids':[], 'aliases':term.get('aliases',[])}
                existing_rows[term['id']] = row
                rows.append(row)
            if not lesson and task not in row.get('question_ids',[]):
                row.setdefault('question_ids',[]).append(task)
        if not lesson:
            for term in new_mentions(body):
                row=rows[int(term['id'].rsplit('-',1)[1])]
                if task not in row.get('question_ids',[]):
                    row.setdefault('question_ids',[]).append(task)
        names = {key for row in rows if isinstance(row, dict) and (lesson or task in (row.get('question_ids') or []))
                 for name in term_labels(row) for key in planned_term_keys(name)}
        for name in item.get('essential_terms', []):
            if TECHNICAL_NAME.search(name) and not planned_term_keys(name) & names:
                issues.append(f'{task}/terms 缺少「{name}」的登记或关联')
    return issues
