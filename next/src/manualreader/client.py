"""Standalone client for planning on a local ManualReader Next service."""
from argparse import ArgumentParser
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError
import json


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*_args,**_kwargs):
        return None


class API:
    def __init__(self,base,token,project):
        target=urlsplit(base)
        if (target.scheme!='http' or target.hostname not in ('127.0.0.1','localhost')
                or target.username or target.password or target.path not in ('','/')
                or target.query or target.fragment or target.port is None):
            raise ValueError('请提供独立服务的本机地址和端口')
        self.base=base.rstrip('/')
        self.key=token.strip()
        self.project=project
        self.opener=build_opener(NoRedirect())
    def call(self,step,action,**fields):
        body=json.dumps({**fields,'action':action,'project_id':self.project}).encode()
        request=Request(self.base+'/v1/'+step,data=body,
                        headers={'Authorization':'Reader '+self.key,'Content-Type':'application/json'})
        try:
            with self.opener.open(request,timeout=90) as response:
                return json.load(response)
        except HTTPError as error:
            try: detail=json.load(error)
            except ValueError:detail={}
            raise RuntimeError(detail.get('error') or f'HTTP {error.code}') from error


def main():
    from .planning import plan
    p=ArgumentParser(prog='manualreader-next')
    p.add_argument('--base',required=True)
    p.add_argument('--token-file',type=Path,required=True)
    p.add_argument('--project',required=True)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--cache',type=Path)
    p.add_argument('--mode',required=True,choices=('NORMAL','INTRO','FINALE','ANSWER'))
    p.add_argument('--question')
    p.add_argument('--confirm-finale',action='store_true')
    p.add_argument('--revise-existing-answer',action='store_true')
    args=p.parse_args()
    if bool(args.question)!=(args.mode=='ANSWER'):
        p.error('只有 ANSWER 需要 --question')
    if args.revise_existing_answer and args.mode!='ANSWER':
        p.error('复答选项只用于 ANSWER')
    api=API(args.base,args.token_file.read_text('utf8'),args.project)
    res=plan(api,args.out,args.mode,question_id=args.question,explicit_finale=args.confirm_finale,
             revise_existing_answer=args.revise_existing_answer,cache=args.cache)
    print(json.dumps(res,ensure_ascii=False,indent=2))
    return 0 if res['status']=='planned' else 2


if __name__=='__main__':
    raise SystemExit(main())
