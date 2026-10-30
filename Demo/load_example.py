"""把一次实际模型回答写入合成演示项目，不修改其他项目或已读进度。"""
import argparse,json,urllib.request
from pathlib import Path
from urllib.parse import urlsplit

parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--base',default='http://127.0.0.1:8939');args=parser.parse_args()
base=args.base.rstrip('/');url=urlsplit(base)
if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost') or url.path:raise SystemExit('演示只允许写入本机阅读器')
session=json.load(urllib.request.urlopen(base+'/api/session'));headers={'X-Reader-Pin':session['pin'],'X-Reader-Material':'demo-reader','Content-Type':'application/json'}
def request(path,data=None):
    with urllib.request.urlopen(urllib.request.Request(base+path,data=None if data is None else json.dumps(data,ensure_ascii=False).encode(),headers=headers)) as response:return json.load(response)
material=request('/api/material')
if material['id']!='demo-reader' or not all(p['id'].startswith('demo-') for p in material['passages']):raise SystemExit('当前资料不是仓库自带的合成演示，已停止写入')
value=json.loads(Path(__file__).with_name('example-answer.json').read_text('utf8'))
batch=request('/api/question-batches',{'request_id':'public-example-lru-v1','questions':[{'passage_id':value['passage_id'],'sentence_id':value['sentence_id'],'text':value['question']}]})
qid=batch['questions'][0]['id'];result={k:value[k] for k in ('answers','terms','quizzes')}
for row in result['answers']:row['question_id']=qid
for row in result['terms']+result['quizzes']:row['question_ids']=[qid]
request('/api/rounds/'+batch['rounds'][0]['id']+'/result',result)
print('合成演示的回答、术语和练习已写入；已读进度保持不变')
