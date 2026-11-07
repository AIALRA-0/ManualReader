"""旧正文只增加显示结构，原文段落、问题来源及用户记录保持原样。"""
from pathlib import Path
import copy,hashlib,json,re

VERSION='reader-framework-v3'
FIELDS=('context','problem','claim','reason','evidence','implication')
def fingerprint(explanation):return hashlib.sha256(json.dumps(explanation,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def heading(text):
    text=text.strip()
    return bool(re.fullmatch(r'\*\*[^\n]{1,60}\*\*|#{1,6}\s+[^\n]{1,60}',text))
def read(folder):
    path=Path(folder)/'reading-framework.json'
    return json.loads(path.read_text('utf8'))if path.exists()else {'version':VERSION,'explanations':{}}
def project(material,stored,folder):
    metadata=read(folder);byid={p['id']:p for p in material['passages']};raw={e['id']:e for e in stored.get('explanations',[])};stale=[]
    for e in material.get('explanations',[]):
        e['display_framework_version']=VERSION
        if e.get('framework_version')==VERSION and e.get('framing'):continue
        row=metadata.get('explanations',{}).get(e['id'])
        if not row:
            # Demo或尚无新框架的旧稿只复用已有小标题，不用模型补写内容。
            if not e.get('narrative'):
                steps=[]
                for pid in e.get('passage_ids',[]):
                    p=byid.get(pid,{})
                    title=p.get('display_reading_heading')or p.get('reading_heading')
                    if title:steps.append({'part_id':pid.removeprefix(e['id']+'--'),'label':title})
                if steps:e['narrative']={'flow':steps}
            continue
        if e['id']not in raw or row.get('fingerprint')!=fingerprint(raw[e['id']]):stale.append(e['id']);continue
        e['legacy_framework']=True;e['practice_links']=copy.deepcopy(row.get('practice_links',[]));e['narrative']={'flow':[]}
        for part in row['parts']:
            members=[e['id']+'--'+bid for bid in part['block_ids']];valid=[pid for pid in members if pid in byid]
            if not valid:continue
            visible=[pid for pid in valid if not byid[pid].get('display_omit')]
            anchor=next((pid for pid in visible if not heading(byid[pid].get('display_markdown',byid[pid].get('zh','')))),next(iter(visible),valid[0]))
            for pid in valid:
                p=byid[pid];p['display_reading_heading']=''
                if heading(p.get('display_markdown',p.get('zh',''))):p['presentation_omit']=True;p['display_group_leader']=anchor
                if p.get('display_omit'):p['display_group_leader']=anchor
            p=byid[anchor];p['display_reading_heading']=part['heading'];p['heading_premise']=part['premise'];p['heading_question']=part['question']
            p['display_group_ids']=list(dict.fromkeys(p.get('display_group_ids',[])+[anchor]+valid))
            e['narrative']['flow'].append({'part_id':anchor.removeprefix(e['id']+'--'),'label':part['heading'],'premise':part['premise'],'question':part['question']})
        def scaffold(identity,title,text):
            pid=e['id']+'--framework-'+identity
            p={'id':pid,'kind':'markdown','en':'','zh':text,'display_markdown':text,'raw_text':text,'content_kind':'explanation','explanation_id':e['id'],'section_id':e.get('section_id'),'content_version':raw[e['id']].get('version',1),'source_links':copy.deepcopy(e.get('source_links',[])),'sentences':[],'reading_heading':title,'display_reading_heading':title,'progress_exempt':True,'framework_scaffold':True}
            material['passages'].append(p);byid[pid]=p;return pid
        start=[]
        for i,item in enumerate(row['start']):start.append(scaffold('start-'+str(i),'接着上一节'if item['role']=='previous'else'这一节要弄明白什么',item['markdown']))
        route=scaffold('route','阅读流程','')
        recap='\n\n'.join('- **'+part['heading']+'**：'+''.join(part['recap'][k].strip().rstrip('。；;')+'；'for k in FIELDS)for part in row['parts'])
        e['narrative']['opening']='\n\n'.join(item['markdown']for item in row['start'])
        e['narrative']['closing']=recap
        end=[scaffold('recap','把这一节串起来',recap)]
        if row.get('next'):end.append(scaffold('next','接下来要解决什么',row['next']))
        e['passage_ids']=start+[route]+e['passage_ids']+end
    material['framework_migration']={'version':VERSION,'stale_explanation_ids':stale,'content_policy':'既有正文与用户记录保留，只补缺少的显示结构'}

def practice_overlay(result,material,known,successors=None):
    """旧题只增加正文展示位置，题号和同一份作答记录继续复用。"""
    passages={p['id']:p for p in material['passages']};valid=set(passages)
    successors=successors or {}
    def current(identity,visited=frozenset()):
        if identity in visited:return []
        if identity in known:return [identity]
        return list(dict.fromkeys(qid for child in successors.get(identity,[])for qid in current(child,visited|{identity})))
    remapped={qid for e in material.get('explanations',[])for link in e.get('practice_links',[])for qid in current(link.get('quiz_id'))}
    for pid,rows in result['lesson'].items():result['lesson'][pid]=[qid for qid in rows if qid not in remapped]
    for e in material.get('explanations',[]):
        for link in e.get('practice_links',[]):
            pid=link.get('after_passage_id');pid=passages.get(pid,{}).get('display_group_leader')or pid
            if pid not in valid:continue
            for qid in current(link.get('quiz_id')):
                rows=result['lesson'].setdefault(pid,[])
                if qid not in rows:rows.append(qid)
