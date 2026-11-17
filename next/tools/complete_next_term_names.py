"""Apply the mature Reader's name-only repair contract to the isolated corpus."""
import argparse
import asyncio
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from time import perf_counter
from urllib.parse import urlsplit

import requests
from manualreader.stages.finish.provider import Provider, ModelFailure
from manualreader.stages.step05.inference import read,write


def entries(f):
    with sqlite3.connect(f.as_uri()+'?mode=ro',uri=True) as conn:
        row=conn.execute('SELECT value FROM reader_state WHERE id=1').fetchone()
    return json.loads(row[0])['terms'] if row else []


async def main(root,out,legacy,url):
    if urlsplit(url).hostname!='127.0.0.1':
        raise ValueError('这次修改术语名称只在隔离 Reader 运行')
    sys.path.insert(0,str(legacy))
    from reader_term_names import analyze_name, term_key
    from reader_term_quality import planned_term_keys
    start=perf_counter()
    provider=Provider(out,sol=40,vision=40)
    saving=defaultdict(list)
    known=defaultdict(lambda:defaultdict(set))
    needing=[]
    open_items={}
    candidate_targets=defaultdict(list)
    for f in sorted((root/'reader-isolated'/'library').glob('next-final-*/state.sqlite3')):
        sample=f.parent.name.removeprefix('next-final-')
        project=read(root/'inputs'/sample/'step04-run.json')['project_id']
        for row in entries(f):
            entry={**row,'sample_id':sample,'project_id':project}
            config=analyze_name(row['name'],row.get('case_exceptions',[]),bool(row.get('english_case_exception')))
            if set(config['issues']) & {'missing_english_name','missing_chinese_name','chinese_inside_english'}:
                needing.append(entry)
            elif not config['issues']:
                for a in (row['name'],*row.get('aliases',[])):
                    for key in planned_term_keys(a):
                        known[project][key].add(config['name'])

    def append(row,name,method):
        saving[row['sample_id']].append({'term_id':row['id'],'expected_name':row['name'],'name':name,
                                          'expected_version':row.get('version',1),'based_on':method})
    for row in needing:
        names=known[row['project_id']][term_key(row['name'])]
        if len(names)==1:
            append(row,next(iter(names)),'同项目已有词条')
            continue
        identity=sha256((row['project_id']+'\0'+term_key(row['name'])).encode()).hexdigest()[:24]
        candidate_targets[identity].append(row)
        open_items.setdefault(identity,{'identity':identity,'project':row['project_id'],'name':row['name'],
                                        'definition':row.get('professional_definition','')[:240],
                                        'aliases':row.get('aliases',[])})
    grouped=defaultdict(list)
    for row in open_items.values():
        grouped[row['project']].append(row)
    batches=[rows[i:i+24] for rows in grouped.values() for i in range(0,len(rows),24)]
    failures=[]
    async def model(batch):
        try:
            return await provider.ask(batch[0]['project'],'TERM_NAME',{'items':batch})
        except ModelFailure as exc:
            failures.append(str(exc))
            return {}
    for response in await asyncio.gather(*(model(rows) for rows in batches)):
        for t in response.get('updates',[]):
            c=analyze_name(t['name'])
            if not c['issues']:
                for row in candidate_targets.pop(t['identity'],[]):
                    append(row,c['name'],'按既有术语解释补齐中英文名称')
    with requests.Session() as request:
        r=request.get(url+'/api/session',timeout=20);r.raise_for_status()
        pin=r.json()['pin']

    def publish(sample,updates):
        safe={'X-Reader-Pin':pin,'X-Reader-Material':'next-final-'+sample}
        with requests.Session() as http:
            res=http.post(url+'/api/terms',headers=safe,json={'updates':updates,'repair_only':True},timeout=30)
            if res.ok:
                return len(updates),[]
            result=[]
            count=0
            for row in updates:
                resp=http.post(url+'/api/terms',headers=safe,json={'updates':[row],'repair_only':True},timeout=20)
                if resp.ok:count+=1
                else:result.append({'term_id':row['term_id'],'reason':resp.json().get('error')})
            return count,result
    outcomes=await asyncio.gather(*(asyncio.to_thread(publish,sample,updates)
                for sample,updates in saving.items()),return_exceptions=True)
    items=[{'sample_id':s,'updated':value[0],'errors':value[1]} if not isinstance(value,Exception)
           else {'sample_id':s,'updated':0,'errors':[str(value)]}
           for s,value in zip(saving,outcomes)]
    proof={'originally_hidden_entries':len(needing),'unresolved_names':[
            {'sample_id':row['sample_id'],'name':row['name'],'id':row['id']}
            for rows in candidate_targets.values() for row in rows],
            'requests':provider.log,'items':items,'failed_batches':failures,'wall_seconds':round(perf_counter()-start,3)}
    write(out/'step-13-name-repair.json',proof)
    print('names',len(needing),sum(r['updated'] for r in items),'unresolved',len(proof['unresolved_names']),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('root','output','legacy'):
        p.add_argument(name,type=Path)
    p.add_argument('url')
    a=p.parse_args()
    asyncio.run(main(a.root,a.output,a.legacy,a.url))
