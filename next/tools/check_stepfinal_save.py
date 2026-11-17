"""Run the isolated save twice, reject its stale sibling, and compare native user records."""

from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from manualreader.stages.finish.commit import user_fingerprint
from manualreader.artifacts import read_json as read, write_json as write


def examine(base,pin,sample,url):
    material=urlsplit(url).query.split('=',1)[-1]
    headers={'Content-Type':'application/json','X-Reader-Finish':'isolated-test',
             'X-Reader-Material':material,'X-Reader-Pin':pin}

    def request(path,body=None):
        packet=Request(base+path,data=json.dumps(body).encode('utf-8') if body is not None else None,
                       headers=headers)
        try:
            with urlopen(packet,timeout=50) as response:
                return response.status,json.load(response)
        except HTTPError as error:
            return error.code,json.load(error)

    def begin():
        code,created=request('/api/next-finish/simulate/prepare',{})
        if code!=200:
            raise RuntimeError(created.get('error','未建立模拟保存预览'))
        return created

    def commit(preview):
        return request('/api/next-finish/simulate/commit',{'commit_id':preview['commit_id'],
                       'preview_token':preview['preview_token'],'confirmed':True})

    before=request('/api/progress')[1]
    first=begin()
    stale=begin()
    code,receipt=commit(first)
    after=request('/api/progress')[1]
    stale_code, stale_result=commit(stale)
    replay_code,replay=commit(first)
    return {
        'sample':sample,'first_code':code,'status':receipt.get('status'),
        'stale_code':stale_code,'stale_result':stale_result,'replay_code':replay_code,'replayed':replay.get('replayed'),
        'version':receipt.get('explanation_version'),
        'native_quiz_selections_before':{q['id']:q.get('selected') for q in before.get('quizzes',[])},
        'native_quiz_selections_after':{q['id']:q.get('selected') for q in after.get('quizzes',[])},
        'quizzes_unchanged':before.get('quizzes')==after.get('quizzes'),
        'native_user_records_unchanged':user_fingerprint(before)==user_fingerprint(after),
        'receipt_user_records_unchanged':bool(receipt.get('user_state_sha256_before')) and
            receipt.get('user_state_sha256_before')==receipt.get('user_state_sha256_after'),
        'progress_authoritative':receipt.get('progress',{}).get('manifest',{}).get('progress'),
        'updated_file_ids':receipt.get('progress',{}).get('updated_file_ids',[]),
    }


def main(args):
    state=read(args.run/'reader-service-ready.json')
    base=state['url']
    if urlsplit(base).hostname!='127.0.0.1':
        raise ValueError('版本验证只能执行于本机隔离 Reader')
    with urlopen(base+'/api/session') as response:
        pin=json.load(response)['pin']
    samples=sorted(state['samples'].items())
    with ThreadPoolExecutor(max_workers=args.clients or len(samples)) as pool:
        rows=list(pool.map(lambda pair:examine(base,pin,*pair),samples))
    metrics={'samples':rows,
             'stale_version_rejections':sum(r['stale_code']==409 for r in rows),
             'idempotent':sum(r['replayed'] is True for r in rows),
             'unchanged_quizzes':sum(r['quizzes_unchanged'] for r in rows),
             'unchanged_user_records':sum(r['native_user_records_unchanged'] for r in rows),
             'unchanged_user_records_receipt':sum(r['receipt_user_records_unchanged'] for r in rows),
             'progress_updated_samples':sum(bool(r['updated_file_ids']) for r in rows)}
    write(args.run/'save-version-and-protection.json',metrics)
    print({k:v for k,v in metrics.items() if k!='samples'})
    return int(any(row['status']!='applied' or row['stale_code']!=409 or not row['replayed'] or
                   not row['native_user_records_unchanged'] or not row['receipt_user_records_unchanged'] for row in rows))


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--clients',type=int)
    raise SystemExit(main(parser.parse_args()))
