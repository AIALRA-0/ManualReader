"""One-time, read-only migration of the user-authorized acceptance corpus."""
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from hashlib import file_digest
import json
from pathlib import Path
import shutil
import sqlite3
import sys


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf8')


def checksum(path):
    with path.open('rb') as f:
        return file_digest(f,'sha256').hexdigest()


def state_of(path):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as cx:
        return json.loads(cx.execute('select value from reader_state where id=1').fetchone()[0])


def ranges(rows):
    final=[]
    for r in sorted(rows,key=lambda x:(x['type'],x['start'],x['end'])):
        if final and final[-1]['type']==r['type'] and r['start']<=final[-1]['end']+1:
            final[-1]['end']=max(r['end'],final[-1]['end'])
        else:
            final.append(dict(r))
    return final


def convert(row, out, source, archive, cq, packet, *, exam=False):
    from reader_workspace import Workspace
    from reader_v21 import previous_content
    from reader_learning import learning_payload
    from reader_completion import completion_payload
    from reader_step02_server import source_position, question_context
    from reader_teaching import recent_reader_questions

    store=Workspace(source).read()
    raw=read(source/'material.json')
    data=Workspace(source).project(raw)
    state=state_of(source/'state.sqlite3')
    config=read(source/'project-files.json') if (source/'project-files.json').exists() else {'version':1,'sources':{}}
    target=out/'projects'/raw['id']
    if target.exists():
        raise ValueError('测试素材目录已存在：'+target.name)
    target.mkdir(parents=True)
    files, protected, all_index, file_index={}, {}, {}, {}
    for record in store['files']:
        if record.get('purpose') in ('illustration','generated') or record.get('original') is False:
            continue
        fid=record['id']
        old=source/'reading-files'/fid/record['stored_name']
        if not old.is_file():
            raise FileNotFoundError(str(old))
        name=config['sources'].get(fid,'sources/'+record['name'])
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('旧登记的材料路径不合法：'+name)
        new=target/'originals'/relative
        new.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(old,new)
        h=checksum(old)
        assert checksum(new)==h
        protected[str(old)]=h
        locs=[]
        document=record['document']
        for p in document['passages']:
            located,timed=source_position(p,document)
            if located or timed:
                key=p['id']
                if key in all_index:
                    raise ValueError('同一项目的来源 ID 重复：'+key)
                item={'id':key,'position':located,'time':timed}
                locs.append(item)
                all_index[key]=(fid,item)
        files[fid]={'file_id':fid,'relative_path':relative.as_posix(),'sha256':h,'size_bytes':new.stat().st_size}
        file_index[fid]={'sha256':h,'rows':locs,'provenance':'已登记原件副本的 SHA-256 与原项目一致'}
    if not files:
        raise ValueError('当前项目没有可读取的已登记原件')

    plan=learning_payload(data,store,state)
    formal_ids={n['id'] for n in store['learning_outline']} if store.get('learning_outline') is not None else None
    nodes=[n for n in plan['nodes'] if formal_ids is None or n['id'] in formal_ids]
    selection=plan['selected_id']
    if selection not in {n['id'] for n in nodes}:
        raise ValueError('当前章节不在正式目录')
    shape=lambda n:{'node_id':n['id'],'parent_node_id':n.get('parent_id'),'number':n['number'],'title':n['title']}
    directory=[shape(n) for n in nodes]
    valid_plan={**plan,'nodes':nodes}
    completed=[]
    for n in nodes:
        rid=n['id']
        if (state.get('manual_completions',{}).get(rid,{}).get('origin','manual_ui')=='manual_ui'
                and completion_payload(data,store,state,plan={**valid_plan,'selected_id':rid})['completed']):
            completed.append(rid)
    prev={n['id']:previous_content(n,store,source)[0] for n in nodes if n['id']==selection}
    preferred={}
    if exam:
        gold=read(packet)
        previous_node=nodes[nodes.index(next(n for n in nodes if n['id']==selection))-1]
        prev[previous_node['id']]=previous_content(previous_node,store,source)[0]
        assert prev[previous_node['id']]==gold['previous']
        preferred[previous_node['id']]=gold['question_styles']
    def source_map(links):
        ds={}
        for key in links:
            if key not in all_index:
                continue
            fid,v=all_index[key]
            position=v['position']
            if position:
                ds.setdefault(fid,[]).append(position)
        return {fid:ranges(v) for fid,v in ds.items()}
    hints={n['id']:source_map([v['passage_id'] for v in n.get('effective_source_links',[])]) for n in nodes}
    recorded={}
    for q in (q for r in state['rounds'] for q in r['questions'] if q.get('id') and not q.get('deleted_at') and q.get('text')):
        recorded[q['id']]=q
    collapsed={}
    for qid,q in recorded.items():
        context, part=question_context(data,q,recorded)
        selected=context.get('选中的原文或讲解','')
        resource=context.get('选中资源')
        answer=q.get('answer')
        if isinstance(answer,dict):answer=answer.get('text') or answer.get('markdown')
        collapsed[qid]={'id':qid,'text':q['text'],'answer':answer,'parent_question_id':q.get('parent_question_id'),
                        'version':q.get('version',q.get('revision',1)),
                        'status':q.get('status') or ('answered' if answer else 'pending'),
                        'learning_node_id':q.get('learning_node_id') or (q.get('target') or {}).get('learning_node_id'),
                        'selected_text':selected,'resource':resource,
                        'scope_hints':source_map([part['id']]) if part.get('id') else {},
                        'explicit_requirement':q.get('custom_instruction') if isinstance(q.get('custom_instruction'),str) else None}
    recent={}
    for n in nodes:
        ids=set(n.get('passage_ids',[]))
        for other in nodes:
            if other['id']!=n['id']:
                ids.difference_update(other.get('passage_ids',[]))
        entries=recent_reader_questions(state,n['id'],ids,limit=100,char_budget=24000)
        recent[n['id']]=[q['question_id'] for q in entries if q['question_id'] in collapsed]
    saved=[]
    fixture_meta=None
    fixture_rejected=None
    index={e['id']:e for e in store['explanations']}
    if exam:
        for n in nodes:
            for eid in n.get('explanation_ids',[]):
                item=index.get(eid)
                if not item or not item.get('version'):
                    continue
                links=item.get('source_links') or []
                ids=[l.get('passage_id') for l in links if l.get('passage_id') in all_index]
                blocks=[b['markdown'] for b in item['blocks'] if b.get('markdown')]
                # A stored lesson can also cite explanation-local blocks. Only material IDs enter the source index.
                if ids and blocks and len(ids)==len(links):
                    saved.append({'node_id':n['id'],'chapter':n['title'],'explanation_id':eid,'version':item['version'],
                                  'source_ids':list(dict.fromkeys(ids)), 'blocks':blocks,'fixture_based':False})
                    if n['id'] not in prev:
                        prev[n['id']]=previous_content(n,store,source)[0]
        if not saved:
            raise ValueError('EXAM2 未找到能追溯到原件的正式讲解')
    else:
        first=archive/'samples'/row['sample_id']
        card=read(first/'task-card.json')
        body=(first/'chapter.md').read_bytes()
        protected[str(first/'chapter.md')]=checksum(first/'chapter.md')
        protected[str(first/'task-card.json')]=checksum(first/'task-card.json')
        ids=[]
        for item in card['material_plan']:
            fid=item['file_id']
            for pid in item['source_ids']:
                if pid not in all_index or all_index[pid][0]!=fid or fid not in files:
                    fixture_rejected='历史 C3 源编号未匹配：'+pid
                    break
                ids.append(pid)
        if not fixture_rejected and ids and body.strip():
            fixture_meta={'fixture_based':True,'source':'历史 C3 首稿和它的原任务卡',
                          'sample_id':row['sample_id'],'body_sha256':checksum(first/'chapter.md'),
                          'card_sha256':checksum(first/'task-card.json'),'source_id_count':len(set(ids)),
                          'file_sha256':{fid:files[fid]['sha256'] for fid in sorted({all_index[i][0] for i in ids})},
                          'trigger':'测试专用的用户主动请求阶段性收束','manual_completion_added':False}
            new=target/'saved/c3-first-draft.md'
            new.parent.mkdir(exist_ok=True)
            new.write_bytes(body)
            assert checksum(new)==fixture_meta['body_sha256']
            saved.append({'node_id':selection,'chapter':next(n['title'] for n in nodes if n['id']==selection),
                          'explanation_id':'fixture-c3-'+row['sample_id'],'version':1,
                          'source_ids':list(dict.fromkeys(ids)),'markdown_path':'saved/c3-first-draft.md',
                          'body_sha256':fixture_meta['body_sha256'],'fixture_based':True})
        else:
            fixture_rejected=fixture_rejected or '历史 C3 首稿或来源清单为空'
    prev={k:v for k,v in prev.items() if v.get('title')}
    project={'project_id':raw['id'],'name':raw['title'],'selection':selection,'directory':directory,'files':files,
             'versions':{'material_schema':data['schema_version'],'workspace_revision':store['revision'],
                         'records_revision':state['revision'],'selection_revision':state.get('learning_selection_revision',0),
                         'file_manifest_revision':config['version']},
             'progress_input':{'file_coverage':state.get('file_coverage',{}),'completed_chapter_ids':completed,
                               'total_formal_chapters':len(nodes)},'previous':prev,'approved_styles':preferred,
             'recent_questions':recent,'questions':collapsed,'scope_hints':hints,'saved_explanations':saved,
             'fixture':fixture_meta,'fixture_blocked':fixture_rejected}
    write(target/'project.json',project)
    write(target/'source-index.json',file_index)
    for file in ('material.json','reading-workspace.json','state.sqlite3','reading-framework.json','project-files.json'):
        old=source/file
        if old.is_file():protected[str(old)]=checksum(old)
    return {'sample_id':row['sample_id'],'project_id':raw['id'],'files':files,'scope_identity':selection,
            'old_manual':project['progress_input'],'recorded_questions':list(collapsed),
            'saved_chapters':len(saved),'fixture':fixture_meta,'fixture_blocked':fixture_rejected,
            'preserved':protected}


def main():
    p=ArgumentParser()
    for name in ('legacy','manifest','c3','exam','gold','out'):
        p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args()
    sys.path.insert(0,str(args.legacy))
    rows=read(args.manifest)['samples']
    args.out.mkdir(parents=True,exist_ok=True)
    jobs=[]
    for row in rows:
        sid=row['sample_id']
        exam=sid=='07-exam2'
        jobs.append((row,args.out,args.exam if exam else Path(row['real_project_dir']),args.c3,
                     sid,args.gold,exam))
    results={}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        pending={pool.submit(convert,*j[:-1],exam=j[-1]):j[0]['sample_id'] for j in jobs}
        for fut in as_completed(pending):
            sid=pending[fut]
            results[sid]=fut.result()
            print(sid,results[sid]['project_id'],results[sid]['saved_chapters'],
                  'fixture' if results[sid]['fixture'] else 'archived',flush=True)
    write(args.out/'import-report.json',results)


if __name__=='__main__':
    main()
