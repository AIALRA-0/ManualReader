"""Interrupt an actual isolated save after attachment storage, then verify recovery."""

import argparse
from pathlib import Path
import sys
from unittest.mock import patch

from manualreader.stages.finish.commit import commit, prepare, user_fingerprint
from manualreader.stages.finish.preview import project_native, setup
from manualreader.artifacts import read_json as read, write_json as write


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('run',type=Path)
    cli.add_argument('legacy',type=Path)
    cli.add_argument('sample')
    cli.add_argument('scratch',type=Path)
    cli.add_argument('phase',choices=('crash','resume','file-change'))
    cli.add_argument('--publish',action='store_true',help='用正式隔离交稿路径注入中断')
    a=cli.parse_args()
    if a.scratch.resolve().is_relative_to(a.run.resolve()):
        raise ValueError('故障注入必须使用单独的 Reader 存储')
    isolated=setup(a.scratch,a.legacy,0)
    sys.path.insert(0,str(a.legacy.resolve()))
    import reader_server as backend
    backend.CONTEXT.material_id='next-final-'+a.sample
    handoff=isolated/'fault-restart-private.json'
    if a.phase=='file-change':
        project_native(a.run,a.sample,isolated,backend,a.run/'original-inputs/projects')
        original=prepare(backend,publish=a.publish)
        inventory=backend.project_files().ensure()
        file_id=original['selected_file_ids'][0]
        path=(Path(inventory['root'])/inventory['sources'][file_id]).resolve(strict=True)
        if not path.is_relative_to(isolated):
            raise ValueError('故障注入只能改动隔离目录里的原件')
        previous=path.read_bytes()
        request={k:original[k] for k in ('preview_token','commit_id')}|{'confirmed':True}
        before=backend.core.read_state()
        outcomes={}
        try:
            path.write_bytes(previous+b'\\n')
            for key,action in (('prepare',lambda:prepare(backend,publish=a.publish)),
                               ('commit',lambda:commit(backend,request,publish=a.publish))):
                try:
                    action()
                except ValueError as error:
                    outcomes[key]=str(error)
            assert set(outcomes)=={'prepare','commit'}
            current=backend.core.read_state()
            assert current.get('file_coverage',{})==before.get('file_coverage',{})
            assert current.get('next_published_deliveries',{})==before.get('next_published_deliveries',{})
        finally:
            path.write_bytes(previous)
        write(isolated/'file-change-proof.json',{'file_id':file_id,'rejected':outcomes,
            'no_progress_update':True,'no_delivery_write':True,'file_restored':path.read_bytes()==previous})
        return 0
    if a.phase=='crash':
        project_native(a.run,a.sample,isolated,backend,a.run/'original-inputs/projects')
        before=backend.core.read_state()
        p=prepare(backend,publish=a.publish)
        request={k:p[k] for k in ('preview_token','commit_id')}|{'confirmed':True}
        write(handoff,{'request':request,'previous_coverage':before.get('file_coverage',{}),
                       'old_user_records_sha256':user_fingerprint(before)})
        with patch('reader_step01.Step01.commit_progress_after_success',side_effect=SystemExit(86)):
            commit(backend,request,publish=a.publish)
        raise ValueError('没有触发保存中断')
    old=read(handoff)
    request=old['request']
    before=backend.core.read_state()
    stored=before['next_save_receipts'][request['commit_id']]
    assert stored['status']=='saved'
    assert before.get('file_coverage',{})==old['previous_coverage']
    first=commit(backend,request,publish=a.publish)
    confirmed=backend.core.read_state()
    again=commit(backend,request,publish=a.publish)
    final=backend.core.read_state()
    result={'sample':a.sample,'recovered_saved_version':first['explanation_version'],
            'progress_before_restart':before.get('file_coverage',{}),
            'progress_after_recovery':confirmed.get('file_coverage',{}),
            'updated_file_ids':first['progress']['updated_file_ids'],
            'replayed':again['replayed'],'same_coverage_after_replay':confirmed.get('file_coverage',{})==final.get('file_coverage',{}),
            'same_user_records':old['old_user_records_sha256']==user_fingerprint(final),
            'attachments_saved':bool(stored.get('saved_attachment_sha256'))}
    write(isolated/'fault-restart-proof.json',result)
    assert result['updated_file_ids'] and result['progress_before_restart']!=result['progress_after_recovery']
    assert result['replayed'] and result['same_coverage_after_replay'] and result['same_user_records']
    return 0


if __name__=='__main__':
    raise SystemExit(main())
