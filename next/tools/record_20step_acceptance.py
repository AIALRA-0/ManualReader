"""Record measured browser, save, and asset results in the existing run journal."""

import argparse
from hashlib import sha256
from pathlib import Path

from manualreader.artifacts import read_json as read, write_json as write
from manualreader.steps.events import RunJournal


def main():
    app=argparse.ArgumentParser(description=__doc__)
    app.add_argument('base',type=Path)
    app.add_argument('baseline',type=Path)
    args=app.parse_args()
    root=args.base/'cold'
    run_name=next((root/'runs').iterdir()).name
    names=read(root/'whiteboard-inputs.json')['samples']
    manifest=read(root/'runs'/run_name/'run_manifest.json')
    journal=RunJournal(root,run_name,manifest['tasks'])
    browser=read(root/'browser-20step-final.json')['final']
    saved={r['sample']:r for r in read(root/'save-version-and-protection.json')['samples']}
    replay=read(args.base/'hot-replay.json')['samples']
    projection={r['sample_id']:r for r in read(root/'reader-projections.json')['samples']}
    latest={(row['task_id'],row['step_id']):row['event_id'] for row in journal.nodes.values()}
    statuses={}
    for name,meta in names.items():
        item=root/'jobs'/name
        r=read(item/'run.json')
        b=browser.get(name,{})
        s=saved.get(name,{})
        view=projection.get(name,{})
        def event(step,kind,data,status,dependencies,recorded_start=None):
            ids=[latest[name,x] for x in dependencies if (name,x) in latest]
            if (name,step) in latest:
                ids.append(latest[name,step])
            latest[name,step]=journal.emit(name,int(step[-2:]),kind,status,value=data,upstream=ids,started=recorded_start)
        facts={}
        tx=r['translation']
        facts['translation']='partial_ready' if tx.get('unresolved') or tx.get('uncertain') else 'complete' if tx.get('needed') else 'skipped'
        visual=r['visual_translations']
        event('STEP-11','visual_label_scope_recorded',visual,
              'partial_ready' if visual.get('unresolved') or visual.get('uncertain') else 'complete' if visual else 'skipped',
              ('STEP-03',))
        p2=r['p2']
        facts['p2']='partial_ready' if any(p2.get(k) for k in ('partial','pending','unreadable')) else (
            'complete' if p2.get('explained') or p2.get('covered_by_group') else 'skipped')
        event('STEP-12','resource_coverage_recorded',p2,facts['p2'],('STEP-08',))
        terms=view.get('term_receipt',{})
        facts['terms']='partial_ready' if terms.get('rejected') or not view.get('terms') and r['issues'].get('F2') else (
            'complete' if view.get('terms') else 'skipped')
        event('STEP-13','native_terms_recorded',terms,facts['terms'],('STEP-08',))
        coverage=view.get('practice_coverage') or {}
        facts['practice']='complete' if coverage.get('total') and coverage.get('covered')==coverage['total'] else 'partial_ready'
        event('STEP-14','native_practice_recorded',{'parts':r['parts'],'visible':view.get('quizzes'),
                                                'part_coverage':coverage},
              facts['practice'],('STEP-08',))
        facts['guidance']='skipped' if meta['mode']=='ANSWER' else (
            'complete' if view.get('recap_parts')==r['parts'] else 'partial_ready')
        event('STEP-15','native_chapter_guidance_recorded',
              {'parts':r['parts'],'recap_parts':view.get('recap_parts'),'chapter_recaps':view.get('recaps')},
              facts['guidance'],('STEP-08',))
        if (name,'STEP-16') not in latest:
            event('STEP-16','final_coordinates_replayed',
                  {'offset_map_sha256':sha256((item/'offset-map.json').read_bytes()).hexdigest()},'complete',('STEP-08','STEP-15'))
        if (name,'STEP-18') not in latest:
            event('STEP-18','final_alignment_replayed',
                  {'alignment_sha256':sha256((item/'alignment.json').read_bytes()).hexdigest()},'complete',('STEP-08','STEP-10','STEP-16'))
        event('STEP-17','final_preview_confirmed',facts,'partial_ready' if 'partial_ready' in facts.values() else 'complete',
              ('STEP-08','STEP-09','STEP-10','STEP-11','STEP-12','STEP-13','STEP-14','STEP-15','STEP-16','STEP-18'))
        event('STEP-19','browser_and_zero_model_replay_recorded',
              {'http':b.get('http'),'open_at':b.get('browser_opened_at'),
               'browser_seconds':b.get('wall_seconds'),'page_errors':b.get('page_errors',[]),
               'reconstruction_verified':bool(replay.get(name,{}).get('fresh_projection'))},
              'complete' if b.get('http')==200 and b.get('error') is None and not b.get('page_errors') and
              replay.get(name,{}).get('fresh_projection') else 'partial_ready',('STEP-17','STEP-18'))
        event('STEP-20','isolated_transaction_verified',
              {k:s.get(k) for k in ('first_code','stale_code','replayed','native_user_records_unchanged','updated_file_ids')},
              'complete' if s.get('status')=='applied' and s.get('stale_code')==409 and s.get('replayed') else 'partial_ready',
              ('STEP-19',))
        statuses[name]=facts
    write(args.base/'delivery-completeness.json',{'sample_statuses':statuses,
          'note':'预览已发布不等于所有附件完成；此表不改变原始模型输出或正文。'})
    workspace=args.base/'workspace'
    stagepath=Path('src/manualreader/stages')
    names=sorted(p.relative_to(workspace) for p in (workspace/stagepath).glob('step0*/*')
                 if p.is_file() and p.parent.name in {'step02','step04','step05','step06','step07'}
                 and p.suffix in {'.md','.txt'} and 'LICENSE' not in p.name)
    names.append(stagepath/'step02/reader_step02_policies.json')
    proof={}
    for path in names:
        first=(args.baseline/path).read_bytes()
        final=(workspace/path).read_bytes()
        proof[path.as_posix()]={'sha256':sha256(final).hexdigest(),'byte_identical':first==final}
    assert all(v['byte_identical'] for v in proof.values())
    write(args.base/'format-and-origin-proof.json',{'frozen_code_assets':proof,
          'entries':len(proof),'isolated_only':True,'fresh_replay_verified':sum('fresh_projection' in x for x in replay.values())})


if __name__=='__main__':
    main()
