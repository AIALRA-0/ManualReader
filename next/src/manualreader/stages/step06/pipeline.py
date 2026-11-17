"""Schedule independent mapping, gap and depth requests for a frozen draft."""

from collections import Counter
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from itertools import groupby
from pathlib import Path
import re
from threading import Event, RLock
from time import perf_counter, sleep, time

from ...transport import executable
from ..step03.cache import atomic
from ..step05.anchors import anchors
from ..step05.contracts import additions, consolidate, digest, links
from ..step05.inference import invoke, read, write
from .coverage import analyze
from .navigation import complete_source_navigation


def shards(rows, budget=72_000):
    """Keep adjacent original material together while bounding request size."""
    def key(source):
        pos=source['position'] or {}
        return (source['file_id'],pos['type'] if pos else None,
                pos.get('start') if pos.get('type')=='pdf_page' else source['section_id'])

    def cost(source):
        return len(source['text'].encode())+160+30*len(source['asset_refs'])

    result=[]
    current=[]
    used=0
    previous_file=None
    for group_key, group in groupby(rows,key):
        if current and group_key[0]!=previous_file:
            result.append(current)
            current=[]
            used=0
        local=list(group)
        amount=sum(cost(s) for s in local)
        if current and used+amount>budget:
            result.append(current)
            current=[]
            used=0
        for source in local:
            amount=cost(source)
            if current and used+amount>budget:
                result.append(current)
                current=[]
                used=0
            current.append(source)
            used+=amount
        previous_file=group_key[0]
    if current:
        result.append(current)
    return result


def source_view(rows):
    return [{key:row[key] for key in ('source_id','file_id','position','kind','role','section_id','text','asset_refs')}
            for row in rows]


def pairs_json(pairs):
    return [{'source_id':sid,'body_anchor_id':aid} for sid,aid in sorted(pairs)]


def summarize(calls):
    timing=[]
    token_counts=Counter()
    running=[]
    requests=Counter()
    for call in calls:
        stage=call['stage']
        reply=call['value']
        timing.append({'stage':stage,'partition':call['partition'],'queue_seconds':call['queue_seconds'],
                       'wall_seconds':call['wall_seconds'],'state':reply['state'],
                       'response_id':reply.get('response_id')})
        for req in reply.get('provider_responses',[]):
            if 'status' in req:
                requests[str(req['status'])]+=1
                if req.get('seconds') is not None:
                    running.append([req['started_at'],req['started_at']+req['seconds']])
            usage=(req.get('response') or {}).get('usage') or {}
            for field in ('input_tokens','output_tokens'):
                token_counts[field]+=usage.get(field,0)
            token_counts['cached_input_tokens']+=(usage.get('input_tokens_details') or {}).get('cached_tokens',0)
            token_counts['reasoning_tokens']+=(usage.get('output_tokens_details') or {}).get('reasoning_tokens',0)
    return {'calls':timing,'http_status':dict(requests),'token_usage':dict(token_counts),
            'provider_intervals':running,'provider_cost_usd':None,
            'complete_calls':sum(p['state']=='complete' for p in timing)}


class JointJobs:
    def __init__(self, root, model_workers=36, *, infer=invoke, cli=None):
        self.root=Path(root).resolve()
        self.work=ThreadPoolExecutor(max_workers=40,thread_name_prefix='task56')
        self.models=ThreadPoolExecutor(max_workers=model_workers,thread_name_prefix='model56')
        self.lock=RLock()
        self.cancelled={}
        self.infer=infer
        self.cli=cli or executable()

    def close(self):
        self.work.shutdown()
        self.models.shutdown()

    def folder(self, sample):
        if not isinstance(sample,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}',sample):
            raise ValueError('任务编号格式不符')
        return self.root/'jobs'/sample

    def input(self, sample):
        self.folder(sample)
        return self.root/'inputs'/sample

    def status(self, sample):
        with self.lock:
            return read(self.folder(sample)/'status.json')

    def put(self, sample, view):
        with self.lock:
            path=self.folder(sample)/'status.json'
            for delay in (.02,.1,.3):
                try:
                    write(path,view)
                    return
                except PermissionError:
                    sleep(delay)
            write(path,view)

    def submit(self, sample, resume=False):
        src=self.input(sample)
        record=read(src/'step04-run.json')
        old=self.folder(sample)/'status.json'
        with self.lock:
            event=self.cancelled.get(sample)
            if old.exists():
                state=read(old)
                if event is not None or state['status']=='complete' or not resume:
                    return state
            event=Event()
            self.cancelled[sample]=event
            view={'sample_id':sample,'mode':record['mode'],'fixture_based':record['fixture_based'],
                  'status':'queued','submitted_at':time()}
            self.put(sample,view)
            self.work.submit(self.finish,sample,event,view)
            return view

    def cancel(self, sample):
        with self.lock:
            event=self.cancelled.get(sample)
            if event:
                event.set()
            return self.status(sample)

    @lru_cache(maxsize=1024)
    def image(self, sha):
        if not re.fullmatch(r'[0-9a-f]{64}',sha):
            raise ValueError('图片哈希格式不符')
        data=(self.root/'media'/f'{sha}.png').read_bytes()
        if digest(data)!=sha:
            raise ValueError('图片内容与原件记录不一致')
        return data

    def dispatch(self, sample, stage, number, packet, assets, event):
        queued=perf_counter()

        def process():
            start=perf_counter()
            folder=self.folder(sample)/'model'/f'{stage}-{number:02}'
            if event.is_set() and not (folder/'outcome.json').exists():
                value={'state':'cancelled','reply':{}}
            else:
                try:
                    images={asset['sha256']:self.image(asset['sha256']) for asset in assets.values() if asset.get('sha256')}
                    value=self.infer(folder,stage,packet,images,self.cli)
                except Exception as exc:
                    folder.mkdir(parents=True,exist_ok=True)
                    value={'state':'local_error','reply':{},'error':str(exc)}
                    write(folder/'local-error.json',value)
            return {'stage':stage,'partition':number,'value':value,'queue_seconds':start-queued,
                    'wall_seconds':perf_counter()-start,'asset_ids':set(assets)}
        return self.models.submit(process)

    def finish(self, sample, event, start):
        view={**start,'status':'running'}
        self.put(sample,view)
        try:
            metrics=self.run(sample,event,start)
        except Exception as error:
            self.put(sample,{**view,'status':'failed','error':str(error),'ended_at':time()})
        else:
            state='cancelled' if event.is_set() else ('partial' if metrics['failed_calls'] else 'complete')
            self.put(sample,{**view,'status':state,'metrics':metrics,'ended_at':time()})
        finally:
            with self.lock:
                self.cancelled.pop(sample,None)

    def run(self, sample, event, start):
        source=self.input(sample)
        dest=self.folder(sample)
        saved=read(source/'step04-run.json')
        raw=(source/saved['output_file']).read_bytes()
        card_bytes=(source/'task-card.json').read_bytes()
        if digest(raw)!=saved['output_hash'] or digest(card_bytes)!=saved['task_card_sha256']:
            raise ValueError('冻结首稿或任务卡哈希不一致')
        mode=saved['mode']
        if os.environ.get("MANUALREADER_STEP56_FORCE_FRESH") == "1" and (dest/"model").exists():
            if any((dest/"model").rglob("outcome.json")):
                raise ValueError("Fresh STEP-05/06 run must use a new output directory, not existing model outcomes")
        card=read(source/'task-card.json')
        data=read(source/'source-index.json')
        all_rows=data['sources']
        selected=[sid for entry in card['material_plan'] for sid in entry['source_ids']]
        source_pool={s['source_id']:s for s in all_rows}
        if selected!=list(source_pool) or len(all_rows)!=len(source_pool):
            raise ValueError('来源编号与冻结任务卡不一致')
        blocks=anchors(raw.decode())
        block_ids={a['anchor_id'] for a in blocks}
        media=data['assets']
        for asset in media.values():
            if sha:=asset.get('sha256'):
                asset['media_path']=f'assets/{sha}.png'
        base={'mode':mode,'draft_markdown':raw.decode(),'anchor_offset_unit':'unicode_codepoint',
              'temporary_anchors':[{k:v for k,v in a.items() if k not in ('text','content_sha256')} for a in blocks],
              'question':card.get('question'),'question_context':card.get('context'),
              'chapter':card.get('chapter'),'saved_chapters':data['saved_chapters'],
              'scope':[{k:v for k,v in entry.items() if k!='source_ids'} for entry in card['material_plan']]}

        def images(rows):
            ids={a for row in rows for a in row['asset_refs']}
            return {aid:media[aid] for aid in sorted(ids) if aid in media}

        def packet(rows, assets):
            return {**base,'sources':source_view(rows),'assets':list(assets.values()),
                    'image_order':list(dict.fromkeys(a['sha256'] for a in assets.values() if a.get('sha256')))}

        depth=self.dispatch(sample,'06B',0,{**packet(all_rows,media),'goals':card['goals']},media,event)
        replay=source/'cached-step05-06a'
        force_fresh = os.environ.get("MANUALREADER_STEP56_FORCE_FRESH") == "1"
        cached=replay.is_dir() and not force_fresh
        batches=[] if cached else shards(all_rows)
        pending={}
        for number,rows in enumerate(batches):
            chosen=images(rows)
            future=self.dispatch(sample,'05',number,packet(rows,chosen),chosen,event)
            pending[future]=(number,rows)
        mapping=read(replay/'step05-mapping.json') if cached else {'links':[]}
        gap=read(replay/'step06a-additions.json') if cached else {'links':[], 'additions':[], 'absorb_candidates':[]}
        initial={(link['source_id'],link['body_anchor_id']) for link in mapping['links']}
        extra={(link['source_id'],link['body_anchor_id']) for link in gap['links']}
        patches_a=gap['additions']
        patches_b=[]
        depth_reply={}
        depth_patch_by_index={}
        previous_depth=source/'previous-step06b-additions.json'
        legacy_patches=read(previous_depth).get('additions',[]) if previous_depth.exists() and not force_fresh else []
        suggestions=gap['absorb_candidates']
        rejected=[]
        logged=[]
        gaps={}
        mapped_batches=[]
        for future in as_completed(pending):
            number,rows=pending[future]
            call=future.result()
            logged.append(call)
            local={r['source_id'] for r in rows}
            mapped=links(call['value']['reply'].get('links',[]),local,block_ids,rejected)
            initial.update(mapped)
            missing=[r for r in rows if r['source_id'] not in {s for s,_ in mapped}]
            mapped_batches.append({'partition':number,'source_ids':[s['source_id'] for s in rows],
                                   'state':call['value']['state']})
            if missing and call['value']['state']=='complete' and not event.is_set():
                chosen=images(missing)
                context={**packet(missing,chosen),'initial_links':pairs_json(mapped),
                         'adjacent_context_source_ids':[r['source_id'] for r in rows]}
                result=self.dispatch(sample,'06A',number,context,chosen,event)
                gaps[result]=(number,{r['source_id'] for r in missing})
        missing_ids=[sid for sid in selected if sid not in {s for s,_ in initial}]
        if cached:
            for filename in ('step05-mapping.json','step05-unmapped.json','step06a-additions.json'):
                atomic(dest/filename,(replay/filename).read_bytes())
        else:
            write(dest/'step05-mapping.json',{'links':pairs_json(initial),
                                             'batches':sorted(mapped_batches,key=lambda a:a['partition'])})
            write(dest/'step05-unmapped.json',{'source_ids':missing_ids})
        for future in as_completed([*gaps,depth]):
            call=future.result()
            logged.append(call)
            reply=call['value']['reply']
            assets={a:media[a] for a in call['asset_ids']}
            if future in gaps:
                _,allowed=gaps[future]
                extra.update(links(reply.get('additional_links',[]),allowed,block_ids,rejected))
                patches_a.extend(additions(reply.get('additions',[]),'06A',allowed,block_ids,assets,rejected))
                suggestions.extend(r for r in reply.get('absorb_candidates',[])
                                   if isinstance(r,dict) and r.get('source_id') in allowed)
            else:
                depth_reply=reply
                patches_b.extend(additions(reply.get('additions',[]),'06B',source_pool,block_ids,assets,rejected))
                for index,item in enumerate(reply.get('additions',[])):
                    validated=additions([item],'06B',source_pool,block_ids,assets,[])
                    if validated:
                        depth_patch_by_index[index]=validated[0]['patch_id']
        overlays=sorted({p['patch_id']:p.copy() for p in patches_a+legacy_patches+patches_b}.values(),
                        key=lambda p:p['patch_id'])
        merged=consolidate(source_pool,blocks,initial,extra,overlays,suggestions,mode)
        merged=complete_source_navigation(merged,source_pool,blocks,overlays)
        if not cached:
            write(dest/'step06a-additions.json',{'links':pairs_json(extra),'additions':patches_a,
                                                 'absorb_candidates':suggestions})
        coverage=analyze(card,mode,blocks,depth_reply,depth_patch_by_index)
        write(dest/'step06-goal-question-coverage.json',coverage)
        write(dest/'step06b-additions.json',{'additions':legacy_patches+patches_b,
                                          'goal_links':depth_reply.get('goal_links',[]),
                                          'question_links':depth_reply.get('question_links',[])})
        write(dest/'step06-merged-mapping.json',merged)
        write(dest/'overlays.json',overlays)
        for overlay in overlays:
            atomic(dest/'overlays'/f'{overlay["patch_id"]}.md',overlay['markdown'].encode())
        write(dest/'anchors.json',blocks)
        if rejected:
            write(dest/'rejected-model-items.json',rejected)
        metrics={**summarize(logged),'mode':mode,'fixture_based':saved['fixture_based'],
                 'wall_seconds':time()-start['submitted_at'],'source_total':len(selected),
                 'initial_mapped':len(set(s for s,_ in initial)),'extra_mapped':len(set(s for s,_ in extra)),
                 'patch_sources':len({sid for p in overlays for sid in p['source_ids']}),
                 'absorbed':len(merged['absorption']),'unresolved':len(merged['unresolved_source_ids']),
                 'body_dispositions':dict(Counter(merged['explanation_dispositions'].values())),
                 'patches_a':len(patches_a),'patches_b':len(patches_b),
                 'prior_06b_patches':len(legacy_patches),'local_media_cache':self.image.cache_info()._asdict(),
                 'reused_calls':read(replay/'receipts-index.json') if cached else {},
                 'force_fresh_from_step05':force_fresh,
                 'source_navigation':merged['source_navigation'],
                 'goal_coverage':coverage['goal_coverage'],
                 'question_coverage':coverage['question_item_coverage'],
                 'failed_calls':sum(p['value']['state']!='complete' for p in logged),
                 'rejected_items':len(rejected),'original_sha256':digest(raw),'card_sha256':digest(card_bytes)}
        write(dest/'run-metrics.json',metrics)
        return metrics
