"""Save a confirmed isolated delivery and apply its verified file coverage."""

from copy import deepcopy
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4
import json

from .coverage import directly_explained_ranges
from .preview import EXPLANATION, digest, optional


SIMULATION_ID = 'next-simulated-save'
DELIVERY_PROGRESS_BASIS = 'verified-origin-v3'
USER_KEYS = ('rounds', 'notes', 'drafts', 'read_passages', 'manual_completions', 'quiz_answers', 'quizzes', 'practice_book')


def stable_hash(obj):
    return sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def user_fingerprint(state):
    return stable_hash({key:state.get(key) for key in USER_KEYS})


def stage01(backend):
    from reader_step01 import SQLiteLedger, Step01, manual_counts

    def manual(state):
        return manual_counts(backend.material(),backend.workspace().read(),state)

    def receipt(key):
        row=backend.core.read_state().get('next_save_receipts',{}).get(key)
        if row and row.get('status') in ('saved','applied'):
            return {**row,'status':'saved'}
        return None

    def source_inventory():
        config=backend.project_files().ensure()
        paths=config.get('sources',{})
        rows=backend.workspace().read().get('files',[])
        derived={row['id'] for row in rows if row['id'] in paths and
                 (row.get('purpose') in ('illustration','generated') or row.get('original') is False)}
        # A registered source overrides the legacy scanner's exclusions.
        return {**config,'sources':{key:path for key,path in paths.items() if key not in derived},
                'derived_paths':sorted(set(config.get('derived_paths',[]))|{paths[key] for key in derived})}

    return Step01(backend.mid(),source_inventory,SQLiteLedger(backend.core.db,backend.core.initial),
                  manual,receipt_lookup=receipt)


def read_draft(backend):
    if not backend.mid().startswith('next-final-'):
        raise ValueError('只能在隔离 Reader 中保存')
    folder=backend.mpath().parent
    manifest=optional(folder/'postprocess-preview','manifest.json',{})
    work=backend.workspace()
    state=work.read()
    draft=next((row for row in state['explanations'] if row['id']==EXPLANATION),None)
    if not draft or manifest.get('status')!='preview_ready':
        raise ValueError('找不到可用于模拟保存的隔离草稿')
    return manifest,work,state,draft


def prepare(backend, *, publish=False):
    with backend.core.LOCK:
        manifest,work,state,draft=read_draft(backend)
        if publish and not manifest.get('delivery_receipt_sha256'):
            raise ValueError('当前版本尚未封版')
        project=backend.mid()
        original_root=backend.mpath().parent/'reading-files'
        for fid,name in manifest['original_files'].items():
            path=original_root/fid/name
            if not path.is_file() or digest(path)!=manifest['read_only_source_versions'][fid]:
                raise ValueError('原件与预览使用的版本不同，请重新准备')
        step=stage01(backend)
        m1=step.prepare(manifest['mode'])
        selected=list(manifest['original_files'])
        if set(selected)!=set(manifest['read_only_source_versions']):
            raise ValueError('交稿范围缺少可核对的原件')
        verify_commit_sources(manifest,m1['manifest'])
        selection=step.select(m1['round_id'],selected)
        if selection['status']!='selected':
            raise ValueError('原件在核对期间发生变化，请重新准备')
        token=token_urlsafe(24)
        draft_sha=stable_hash(draft)
        manifest_sha=stable_hash(manifest)
        commit_id=('next-delivery-'+stable_hash([project,draft_sha,manifest_sha,DELIVERY_PROGRESS_BASIS]) if publish else
                   'next-save-'+uuid4().hex)
        row={'commit_id':commit_id,'project_id':project,'round_id':m1['round_id'],
             'draft_version':draft['version'],'workspace_revision':state['revision'],
             'draft_sha256':draft_sha,'selected_file_ids':selected,
             'manifest_file_ids':list(manifest['read_only_source_versions']),
             'manifest_sha256':manifest_sha,'mode':manifest['mode'],'created_at':backend.core.stamp(),
             'publication':publish,'progress_basis':DELIVERY_PROGRESS_BASIS}
        backend.core.mutate(lambda s:s.setdefault('next_save_previews',{}).update({token:row}))
        return {**row,'status':'preview_ready','isolation':'local_publication' if publish else 'local_simulation',
                'preview_token':token}


def verify_commit_sources(delivery, inventory):
    versions=delivery['read_only_source_versions']
    actual={row['file_id']:row['content_sha256'] for row in inventory['files']
            if row.get('status')=='active' and not row.get('identity_pending')}
    if any(actual.get(file_id)!=checksum for file_id,checksum in versions.items()):
        raise ValueError('项目原件与这份草稿的版本不同，请根据当前原件重新准备交稿')


def coverage_evidence(backend,manifest,draft,finalized):
    if manifest['mode']!='NORMAL':
        return {},[]
    # A navigation edge does not establish delivery of raw material.
    semantic=manifest.get('direct_sources_by_block',{})
    source={v['id']:v for v in backend.material()['passages'] if v.get('content_kind')=='source'}
    files={r['file_id']:r for r in finalized['manifest']['files']}
    original_root=backend.mpath().parent/'reading-files'
    evidence,proposals={},[]
    seen={sid for block in draft['blocks'] for sid in semantic.get(block['id'],[])}
    by_file={}
    for sid in seen:
        row=source.get(sid)
        if row:
            by_file.setdefault(row['file_id'],[]).append(row)
    for fid,rows in sorted(by_file.items()):
        original=manifest['original_files'].get(fid)
        file_row=files.get(fid,{})
        if not original or file_row.get('content_sha256')!=manifest['read_only_source_versions'].get(fid):
            continue
        path=original_root/fid/original
        blob=path.read_bytes()
        if sha256(blob).hexdigest()!=file_row['content_sha256']:
            raise ValueError('计算进度时原件版本已变化，请重新准备交稿')
        ranges=directly_explained_ranges(blob,path.suffix.lower(),rows)
        bounds={sid:[list(span) for span in sorted(spans)] for sid,spans in ranges.items() if spans}
        if not bounds:
            continue
        match={'file_revision':file_row['file_revision'],'content_sha256':file_row['content_sha256']}
        evidence[fid]={**match,'source_ranges':bounds}
        proposals.append({'file_id':fid,**match,'source_ids':sorted(bounds),
                          'byte_ranges':[span for key in sorted(bounds) for span in bounds[key]]})
    return evidence,proposals


def commit(backend,data,*,publish=False):
    token=data.get('preview_token')
    if not isinstance(token,str) or data.get('confirmed') is not True:
        raise ValueError('请确认要模拟保存的具体草稿')
    with backend.core.LOCK:
        manifest,work,state,draft=read_draft(backend)
        records=backend.core.read_state()
        pending=records.get('next_save_previews',{}).get(token)
        if (not pending or pending['project_id']!=backend.mid() or pending['commit_id']!=data.get('commit_id')
                or pending.get('publication',False)!=publish):
            raise ValueError('保存预览编号与当前项目不符')
        if stable_hash(draft)!=pending['draft_sha256'] or stable_hash(manifest)!=pending['manifest_sha256']:
            raise ValueError('草稿在确认前发生变化，请重新核对')
        key=pending['commit_id']
        receipt=records.get('next_save_receipts',{}).get(key)
        if receipt and receipt['status']=='applied':
            return {**receipt,'replayed':True}
        step=stage01(backend)
        saved=next((r for r in state['explanations']
                    if r['id']==SIMULATION_ID and r.get('handoff_digest')==key),None)
        if not receipt:
            if not publish and not saved and state['revision']!=pending['workspace_revision']:
                raise ValueError('阅读工作区版本已变化，请重新准备保存预览')
            finalized=step.reconcile(pending['round_id'])
            if finalized['status']!='ready':
                raise ValueError('提交前发现原件版本变更')
            verify_commit_sources(manifest,finalized['manifest'])
            if publish:
                saved=draft
            elif not saved:
                old=next((r for r in state['explanations'] if r['id']==SIMULATION_ID),None)
                request={field:deepcopy(draft[field]) for field in
                         ('title','blocks','source_links','writing_version','narrative') if field in draft}
                request.update(id=SIMULATION_ID,handoff_digest=key,expected_revision=state['revision'])
                if old:
                    request['expected_version']=old['version']
                saved=work.explain(request,backend.material())
            evidence,proposals=coverage_evidence(backend,manifest,draft,finalized)
            current_assets=backend.core.read_state()
            native_assets={'terms':[r['id'] for r in current_assets['terms'] if EXPLANATION in r.get('explanation_ids',[])],
                           'quizzes':[r['id'] for r in current_assets['quizzes'] if EXPLANATION in r.get('explanation_ids',[])],
                           'resources':deepcopy(manifest.get('resources',{}))}
            receipt={**pending,'status':'saved','isolation':'local_publication' if publish else 'local_simulation',
                     'explanation_id':saved['id'],'explanation_version':saved['version'],
                     'saved_attachment_sha256':stable_hash(native_assets),
                     'expected_manifest_version':finalized['pending_progress_update']['expected_manifest_version'],
                     'coverage_by_file':evidence,'proposals':proposals,
                     'user_state_sha256_before':user_fingerprint(records),'saved_at':backend.core.stamp()}
            def record(s):
                s.setdefault('next_saved_attachments',{})[key]=native_assets
                if publish:
                    s.setdefault('next_published_deliveries',{})[key]={
                        'explanation':deepcopy(saved),'attachments':native_assets,'body_sha256':manifest['body_sha256']}
                s.setdefault('next_save_receipts',{})[key]=receipt
            backend.core.mutate(record)
        progress=step.commit_progress_after_success(receipt['round_id'],key,
                    receipt['expected_manifest_version'],receipt['proposals'])
        if progress['status']=='applied':
            receipt={**receipt,'status':'applied','progress':progress,
                     'user_state_sha256_after':user_fingerprint(backend.core.read_state())}
            def finalize(s):
                s.setdefault('next_save_receipts',{})[key]=receipt
                if publish:
                    s['next_current_delivery']=key
            backend.core.mutate(finalize)
        return {**receipt,'progress':progress,'replayed':False}
