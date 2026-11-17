"""Four-mode planning, material evidence assembly, and client-side result files."""
from base64 import b64decode
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
import json
from threading import RLock
from time import perf_counter
from uuid import uuid4

from .stages.step02.card import assemble,digest,link_sources,model_schema,policies,prompt,render
from .stages.step02.repair import repair_card
from .transport import PROFILE,invoke

_SHARED={}
_LOCK=RLock()
GOAL_CONTRACT='每一项只写一个独立的知识主题，优先用短名词短语。独立概念、判据和结论分别列出。允许必要的公式、合法别名或自然成对概念。已经属于目标解释、答案结论或讲述步骤的内容不要写进目标。'


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def get_original(cache,api,rid,row):
    fid=row['file_id']
    def call(action,**fields):
        return api.call('step02',action,round_id=rid,file_id=fid,**fields)
    key=(api.base,api.project,fid,row['content_sha256'],row['file_revision'])
    with _LOCK:
        future=cache.get(key)
        hit=future is not None
        if future is None:
            future=Future()
            cache[key]=future
    if hit:
        return future.result(),True
    try:
        catalog=call('catalog')
        ns=catalog['positions']
        units={}
        read_ranges=[]
        i=0
        while i<len(ns):
            start=ns[i]
            while i+1<len(ns) and ns[i+1]-start<300:
                i+=1
            end=ns[i]
            i+=1
            while start is not None:
                result=call('read',start=start,end=end)
                if result['content_sha256']!=row['content_sha256'] or not result['rows']:
                    raise RuntimeError('读取到的原件版本或位置与 M1 不一致')
                for actual in result['rows']:
                    n=actual['n']
                    text=actual['text']
                    while len(text)<actual['text_chars']:
                        ext=call('read',start=n,end=n,char_offset=len(text))
                        if ext['content_sha256']!=row['content_sha256'] or not ext['rows'][0]['text']:
                            raise RuntimeError('原件续读返回的位置或版本不完整')
                        text+=ext['rows'][0]['text']
                    if n in units:
                        raise RuntimeError('原件读取返回重复位置')
                    units[n]={**actual,'text':text}
                read_ranges.append({'start':start,'end':end,'actual_positions':[r['n'] for r in result['rows']]})
                start=result['next_start']
        if list(units)!=ns:
            raise RuntimeError('原件读回的位置与目录不完整')
        value=(catalog,units,read_ranges)
        future.set_result(value)
        return value,False
    except Exception as error:
        future.set_exception(error)
        with _LOCK:cache.pop(key,None)
        raise


def sample(positions, chosen, count):
    ordered=sorted(chosen) or positions
    if len(ordered)<=count:return ordered
    return [ordered[j*(len(ordered)-1)//max(1,count-1)] for j in range(count)]


def select_scope(context, fid, catalog):
    positions=catalog['positions']
    return {n for n in positions if any(s['type']==catalog['type'] and s['start']<=n<=s['end']
                                        for s in context['scope_hints'].get(fid,[]))}


def excerpt(text,limit):
    if len(text)<=limit:return text
    return text[:limit*2//3]+'\n[…原件可续读…]\n'+text[-limit//3:]


def make_payload(context,catalog,units):
    mode=context['mode']
    relevant=[fid for fid in sorted(catalog) if select_scope(context,fid,catalog[fid])]
    budget=14000
    out=[]
    inline={}
    for fid in sorted(catalog,key=lambda f:(f not in relevant,f)):
        cat=catalog[fid]
        ids=cat['positions']
        focus=select_scope(context,fid,cat)
        groups=len(relevant) if relevant and mode!='INTRO' else len(catalog)
        quota=max(450,budget//max(1,groups))
        if mode=='ANSWER' and not focus:
            ns=[]
        elif relevant and mode!='INTRO' and not focus:
            ns=[]
        else:
            expanded=set(focus)
            if focus and mode=='NORMAL':
                width=1 if cat['type']=='pdf_page' else 5
                expanded.update(n for k in (min(focus),max(focus)) for n in ids if k-width<=n<=k+width)
            if cat['type']=='subtitle_sid':
                ns=sample(ids,expanded, min(len(ids), max(24,min(100,quota//50))))
            else:
                ns=sample(ids,expanded, min(len(ids),max(4,min(24,quota//400))))
        chosen=[]
        limit=max(25,min(1400,quota//max(1,len(ns))))
        for n in ns:
            u=units[fid][n]
            txt=excerpt(u['text'],limit)
            if txt==u['text']:
                inline.setdefault(fid,[]).append(n)
            chosen.append({'n':n,'text':txt,**({'time':u['time']} if 'time' in u else {})})
        out.append({'file_id':fid,'type':cat['type'],'first_position':ids[0],'last_position':ids[-1],
                    'position_count':len(ids),'scope_hints':context['scope_hints'].get(fid,[]),
                    'time_bounds':cat.get('time_bounds'),'original_excerpts':chosen,
                    'headings':[{**r,'text':excerpt(r['text'],120)} for r in cat.get('headings',[])[:24]]})
    data={'original_files':out,'knowledge_goal_contract':GOAL_CONTRACT,
          'read_original':'需要更多原件时，请在同一次工具调用里批量提交不超过 8 个互补范围；已提供的位置无需重复读取。',
          'unprocessed_previous_questions':[{'ref':q['ref'],'text':q['text']} for q in context['unprocessed_questions']],
          'inherited_question_styles':[{k:q[k] for k in ('pattern','explanation_need')} for q in context['approved_styles']]}
    fixed=context['fixed']
    if fixed['custom_instruction']:
        data['custom_instruction']=fixed['custom_instruction']
    if mode=='ANSWER':
        data.update(question=fixed['question']['text'],context=fixed['context'])
    else:
        data.update(current_chapter={k:fixed['chapter'][k] for k in ('number','title')},
                    neighboring_chapters=[{k:n[k] for k in ('number','title')} for n in context['directory']],
                    previous=fixed['previous'])
        if mode=='FINALE':
            saved=context['saved_explanations']
            share=max(800,9500//len(saved))
            data.update(saved_explanations=[{'chapter':x['chapter'],'excerpts':excerpt('\n\n'.join(x['blocks']),share)}
                                           for x in saved],
                        saved_source_ranges=context['allowed_ranges'],
                        independent_manual_completion=fixed['progress']['lessons'])
    return data,inline


def plan(api,out,mode,*,question_id=None,explicit_finale=False,revise_existing_answer=False,
         custom_instruction=None,cache=None,backend=invoke,original_cache=None):
    out=Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('请使用新的空目录，原始结果会保留')
    out.mkdir(parents=True,exist_ok=True)
    began=perf_counter()
    sys_prompt=prompt(mode)
    opts={'question_id':question_id,'explicit_finale':explicit_finale,
          'revise_existing_answer':revise_existing_answer,'custom_instruction':custom_instruction}
    stats={'step':'STEP-02','mode':mode,'project_id':api.project,'status':'preparing',
           'question_id':question_id,'explicit_finale':explicit_finale,
           'model':PROFILE,'prompt_sha256':digest(sys_prompt),'fixture_based':False,'model_dispatches':0}
    values=policies()
    trace=[]
    memory=_SHARED if original_cache is None else original_cache
    def write_trace():
        (out/'original_read_trace.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in trace),encoding='utf8')
    try:
        m1=api.call('step01','prepare',mode=mode)
        save(out/'step01-start.json',m1)
        rid=m1['round_id']
        def call(action,**fields):
            return api.call('step02',action,round_id=rid,**fields)
        context=call('context',**opts)
        save(out/'context-fixed.json',context)
        if context['fixture']:
            stats['fixture_based']=True
            stats['fixture']=context['fixture']
        active=[x for x in context['files'] if context['allowed_selection'] is None
                or x['file_id'] in context['allowed_selection']]
        catalogs,units,image_list,model_store,blocked={}, {}, [], {}, {}
        stats['verified_original_cache_hits']=0
        with ThreadPoolExecutor(max_workers=max(1,len(active))) as pool:
            futures={pool.submit(get_original,memory,api,rid,x):x for x in active}
            for fut in as_completed(futures):
                entry=futures[fut]
                fid=entry['file_id']
                try:
                    (cat,rows,read_ranges),hit=fut.result()
                except Exception as error:
                    blocked[fid]=str(error)
                    trace.append({'file_id':fid,'status':'access_blocked','detail':str(error)})
                    continue
                stats['verified_original_cache_hits']+=int(hit)
                trace.extend({'file_id':fid,'content_sha256':entry['content_sha256'],
                              'file_revision':entry['file_revision'],'status':'read_from_original',
                              'via':'verified_snapshot_cache' if hit else 'reader_http',**rg} for rg in read_ranges)
                catalogs[fid]=cat
                units[fid]=rows
                model_store[fid]={'type':cat['type'],'rows':rows,'images':{},
                                  'content_sha256':entry['content_sha256'],'file_revision':entry['file_revision']}
        if mode=='INTRO' and (blocked or m1['manifest']['inventory'].get('errors') or any(
                f['status']=='unavailable' for f in m1['manifest']['files'])):
            raise RuntimeError('导读要求能够读取全部有效原件；存在无法读取或核验的文件')
        if mode!='ANSWER' and not catalogs:
            raise RuntimeError('没有可供规划使用的真实原件')
        payload,inline=make_payload(context,catalogs,units)
        candidates=[]
        for fid in sorted(catalogs):
            cap=catalogs[fid]
            if not cap['has_page_image']:continue
            eligible=select_scope(context,fid,cap)
            if eligible:
                candidates.append((fid,sorted(eligible)[len(eligible)//2]))
            elif mode=='INTRO':
                candidates.append((fid,cap['positions'][0]))
        for fid,n in candidates[:2]:
            pic=call('read',file_id=fid,start=n,end=n,visual=True)
            raw=b64decode(pic['image'],validate=True)
            model_store[fid]['images'][str(n)]=pic['image']
            image_list.append((fid,n,raw))
            trace.append({'file_id':fid,'start':n,'end':n,'content_sha256':pic['content_sha256'],
                          'status':'read_original_image','image_sha256':digest(raw)})
        payload['visible_images']=[{'file_id':fid,'position':n,'image_sha256':digest(b)} for fid,n,b in image_list]
        save(out/'model-input.json',payload)
        save(out/'original-catalog.json',catalogs)
        folder=out/'model'
        folder.mkdir()
        schema=model_schema(sorted(catalogs),[row['ref'] for row in context['unprocessed_questions']],mode)
        save(folder/'output-schema.json',schema)
        state_key=digest({**context,'files':[{k:v for k,v in row.items() if k not in
                                           ('last_updated_round','manifest_version','modified_ns')}
                                           for row in context['files']]})
        cfg=digest([state_key,digest(sys_prompt),digest(values),PROFILE,payload,schema])
        stats.update(cache_key=cfg,cache_hit=False,blocked_originals=blocked,
                     original_units=sum(len(x) for x in units.values()),
                     inline_characters=len(json.dumps(payload,ensure_ascii=False,separators=(',',':'))),
                     images_sent=[digest(b) for _,_,b in image_list])
        saved=Path(cache)/f'{cfg}.json' if cache else None
        model_started=perf_counter()
        if saved and saved.is_file():
            draft=json.loads(saved.read_bytes())
            stats['cache_hit']=True
        else:
            stats['model_dispatches']=1
            draft=backend(folder,sys_prompt,payload,image_list,model_store,inline)
        stats['model_seconds']=perf_counter()-model_started
        save(out/'first-semantic-result.json',draft)
        card,notes=assemble(context,catalogs,draft)
        save(out/'task-card-before-source-link.json',card)
        card,stats['local_repairs']=repair_card(card,context,units)
        selected=[v['file_id'] for v in card['material_plan']]
        if mode=='ANSWER':
            selected=sorted(set(selected).union(f for f in context['scope_hints'] if f in catalogs))
        bound=api.call('step01','select',round_id=rid,file_ids=selected)
        save(out/'step01-selection.json',bound)
        if bound['status']!='selected':
            raise RuntimeError('本轮所选文件在规划期间已经变化')
        indexes={}
        with ThreadPoolExecutor(max_workers=max(1,len(selected))) as pool:
            reads={pool.submit(call,'index',file_id=fid):fid for fid in selected}
            for future in as_completed(reads):
                indexes[reads[future]]=future.result()
        card,bindings=link_sources(card,indexes,catalogs)
        save(out/'source-link-report.json',bindings)
        final=api.call('step01','reconcile',round_id=rid)
        save(out/'step01-reconcile.json',final)
        if final['status']!='ready':
            raise RuntimeError('M2 未确认规划使用的原件')
        latest=call('context',**opts)
        if latest['state_fingerprint']!=context['state_fingerprint']:
            raise RuntimeError('项目目录、问题或已保存讲解在本轮变化')
        (out/'task-card.md').write_text(render(card,values),encoding='utf8')
        save(out/'task-card.json',card)
        if saved and not stats['cache_hit']:
            temporary=saved.with_name(saved.name+'.'+uuid4().hex+'.tmp')
            save(temporary,draft)
            temporary.replace(saved)
        stats.update(status='planned',source_links=bindings,diagnostics=notes+context['diagnostics'])
    except Exception as error:
        stats.update(status='failed' if stats['model_dispatches'] else 'blocked',error=str(error))
    finally:
        stats['wall_seconds']=perf_counter()-began
        write_trace()
        save(out/'run.json',stats)
    return stats
