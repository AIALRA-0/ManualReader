"""Submit a task card and download its original Markdown Writer draft."""

from argparse import ArgumentParser
from base64 import b64decode, b64encode
from hashlib import sha256
import json
from pathlib import Path
from time import sleep

from .client import API


ACTIVE=('queued','preparing','writing')


def submit(api, card, round_id, mode, *, confirm_finale=False, revise=False, resume=False):
    return api.call('step04','submit',round_id=round_id,mode=mode,task_card=b64encode(card).decode(),
                    explicit_finale=confirm_finale,revise_existing_answer=revise,resume_before_dispatch=resume)


def wait(api, run, interval=2):
    while run['status'] in ACTIVE:
        sleep(interval)
        run=api.call('step04','status',task_id=run['task_id'])
    return run


def download(api, run, out):
    packet=api.call('step04','download',task_id=run['task_id'])
    body=b64decode(packet['markdown'],validate=True)
    if packet['run']['output_hash']!=sha256(body).hexdigest():
        raise ValueError('下载首稿与运行回执的哈希不同')
    out=Path(out)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_bytes(body)
    return body


def main():
    p=ArgumentParser(prog='manualreader-step04')
    p.add_argument('--base',required=True)
    p.add_argument('--token-file',required=True,type=Path)
    p.add_argument('--project',required=True)
    p.add_argument('--round',required=True)
    p.add_argument('--mode',required=True,choices=('NORMAL','INTRO','FINALE','ANSWER'))
    p.add_argument('--card',required=True,type=Path)
    p.add_argument('--out',type=Path)
    p.add_argument('--confirm-finale',action='store_true')
    p.add_argument('--revise-existing-answer',action='store_true')
    p.add_argument('--resume-before-dispatch',action='store_true')
    args=p.parse_args()
    api=API(args.base,args.token_file.read_text('utf8'),args.project)
    run=submit(api,args.card.read_bytes(),args.round,args.mode,confirm_finale=args.confirm_finale,
               revise=args.revise_existing_answer,resume=args.resume_before_dispatch)
    if args.out:
        run=wait(api,run)
        if run.get('output_file'):
            download(api,run,args.out)
    print(json.dumps(run,ensure_ascii=False))
    return 0 if run['status']=='complete' or run['status'] in ACTIVE else 2


if __name__=='__main__':
    raise SystemExit(main())
