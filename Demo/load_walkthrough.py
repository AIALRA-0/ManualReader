"""把完整的合成阅读场景写入新 Demo 项目，不覆盖已有项目。"""
import argparse,base64,json,uuid
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urlsplit

def load(base):
    base=base.rstrip('/');url=urlsplit(base)
    if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost') or url.path:raise ValueError('演示只写入本机阅读器')
    session=json.load(urlopen(base+'/api/session'));headers={'X-Reader-Pin':session['pin'],'Content-Type':'application/json'}
    def request(path,data=None):
        with urlopen(Request(base+path,data=json.dumps(data,ensure_ascii=False).encode()if data is not None else None,headers=headers),timeout=60)as response:return json.load(response)
    data=json.loads(Path(__file__).with_name('walkthrough.json').read_text('utf8'))
    project=request('/api/projects',{'title':data['title']});identity=project['id'];headers['X-Reader-Material']=identity
    tree=request('/api/library/tree');folder=next((f for f in tree['folders']if f['name']=='Demo'and f.get('parent_id')=='root'and not f.get('deleted')),None)
    if not folder:folder=request('/api/folders',{'name':'Demo','parent_id':'root'})
    request('/api/library/material/'+identity+'/move',{'parent_id':folder['id']})
    paragraphs=[p for c in data['chapters']for p in c['paragraphs']]
    source='\n\n'.join(('# 'if p.get('heading')else'')+p['en'] for p in paragraphs)
    request('/api/content/files',{'file_id':'walkthrough-source','filename':'cache-reading.md','content_base64':base64.b64encode(source.encode()).decode()})
    m=request('/api/material')
    normalize=lambda s:' '.join(s.lstrip('# ').split())
    file_ids=set(next(f for f in m['reading_files']if f['id']=='walkthrough-source')['passage_ids'])
    known={normalize(p['en']):p for p in m['passages']if p['id']in file_ids}
    assert all(normalize(p['en'])in known for p in paragraphs),'原件分段与演示内容不一致，未写入其他项目'
    request('/api/translations',{'file_id':'walkthrough-source','complete':True,'translations':[{'source_id':known[normalize(p['en'])]['id'],'text':p['zh']}for p in paragraphs]})
    nodes=[];questions=[]
    for i,chapter in enumerate(data['chapters']):
        pids=[known[normalize(p['en'])]['id']for p in chapter['paragraphs']]
        sections={known[normalize(p['en'])]['section_id']for p in chapter['paragraphs']}
        pids=list(dict.fromkeys(pids+[p['id']for p in m['passages']if p['id']in file_ids and p.get('section_id')in sections]))
        eid='demo-lesson-'+str(i)
        request('/api/content/explanations',{'id':eid,'title':chapter['title'],'parts':[{'source_id':pid,'source_span':p['en'],'heading':p.get('part_title',''),'markdown':p['explanation']}for pid,p in zip(pids,chapter['paragraphs'])if not p.get('heading')]})
        nodes.append({'id':'demo-chapter-'+str(i),'title':chapter['title'],'source_links':[{'passage_id':pid}for pid in pids],'explanation_ids':[eid],'depends_on':['demo-chapter-'+str(i-1)]if i else[]})
    request('/api/learning/outline',{'nodes':nodes})
    for chapter in data['chapters']:
        for p in chapter['paragraphs']:
            pid=known[normalize(p['en'])]['id']
            for q in p.get('questions',[]):
                batch=request('/api/question-batches',{'request_id':uuid.uuid4().hex,'questions':[{'passage_id':pid,'text':q['text']}]})
                row=batch['questions'][0];qid=row['id'];rid=batch['rounds'][0]['id']
                result={'answers':[{'question_id':qid,'parts':[{'question_span':q['text'],'markdown':q['answer']}]}],'terms':[{**t,'question_ids':[qid]}for t in q.get('terms',[])],'quizzes':[{**z,'question_ids':[qid]}for z in q.get('quizzes',[])]}
                request('/api/rounds/'+rid+'/result',result);questions.append(qid)
                if q.get('followup'):
                    f=q['followup'];batch=request('/api/question-batches',{'request_id':uuid.uuid4().hex,'questions':[{'passage_id':pid,'parent_question_id':qid,'text':f['text']}]});child=batch['questions'][0]['id']
                    request('/api/rounds/'+batch['rounds'][0]['id']+'/result',{'answers':[{'question_id':child,'parts':[{'question_span':f['text'],'markdown':f['answer']}]}],'terms':[],'quizzes':[]});questions.append(child)
    request('/api/content/presentation',{'layout':'three','columns':[['original'],['translation'],['explanation']],'file_id':'walkthrough-source','explanation_id':'demo-lesson-1'})
    request('/api/learning/select',{'node_id':'demo-chapter-1'})
    return {'project_id':identity,'url':base+'/?material='+identity,'chapters':len(nodes),'questions':len(questions),'source':'synthetic','model_calls':0}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--base',default='http://127.0.0.1:8939');args=parser.parse_args()
    print(json.dumps(load(args.base),ensure_ascii=False))
