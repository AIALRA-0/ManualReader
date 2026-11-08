"""Check new two-paragraph terms without rewriting stored terminology."""
import difflib
import re

MINIMUMS={"professional_definition":80,"intuitive_explanation":100}
INSTRUCTION="""每个术语仍只有定义和直观解释两个自然段，不新增小标题或展示字段。第一段讲清是什么、人为什么需要它、它怎样起作用；按对象、动作、变化与结果接清关键过程，必要前置在使用处说明；只预设普通中文与四则运算，新专业词先说含义再使用。第二段按解释对象选择场景：抽象机制用题材之外、容易想象的生活类比，明确对象、动作与结果分别对应真实机制的什么，类比中的新安排先交代目的；生物、物质等具体实体优先直接描写实际外形、行为和变化，只有解释抽象作用过程确实需要时才加类比，不把外形特征硬换成另一套物品，也不给分类对象强造用途。两段相互补充，每词脱离原文章仍能读懂，文章只确定词义，不把本项目的例子当成定义；不依赖前文，不用陌生词替换解释，不添加无用边界说明。定义至少80个非空白字符，直观解释至少100个非空白字符；用机制、前置和例子的对应关系满足下限，不重复或堆背景凑字数；用“参与”“部分”等准确措辞交代事实后，不再补一遍泛泛的“还有其他因素或过程”；每句继续提供具体知识。复杂概念可自然写长。例子里用到的数字和符号须有来由；若计算与理解这个词无关，就直接用动作讲清机制，不硬塞公式。旧术语继续复用，只有明确修订才按当前版本检查并保留历史。"""
INDEPENDENCE=re.compile(r"如上所述|如前所述|沿用.{0,12}(?:例子|比喻)|前文(?:所述|提到|的)|(?:这个|本|该)项目|本题|本章|本图|这里跟随")
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
    if proposal.get("expected_version")!=item.get("version",1):
        raise ValueError("术语版本已经变化，请重新读取后再修订")
    old={"professional_definition":item.get("professional_definition")or item.get("definition")or"",
         "intuitive_explanation":item.get("intuitive_explanation")or""}
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
