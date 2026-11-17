"""Send one already-validated original visual region to the existing model adapter."""

from argparse import ArgumentParser
from base64 import b64decode
from hashlib import sha256
from pathlib import Path
from subprocess import TimeoutExpired
from time import perf_counter
import json

from manualreader.client import API
from manualreader.transport import PROFILE, invoke


SCHEMA={'type':'object','properties':{
    'issue_kind':{'type':'string'},
    'decision':{'type':'string','enum':['supported','unsupported','uncertain']},
    'candidate_structure':{'type':['string','null']},
    'visible_evidence':{'type':'array','items':{'type':'string'}},
    'disputed_elements':{'type':'array','items':{'type':'string'}},
    'need_more_source':{'type':'boolean'}},
    'required':['issue_kind','decision','candidate_structure','visible_evidence','disputed_elements','need_more_source'],
    'additionalProperties':False}

def transport_evidence(folder, checksum):
    path=folder/'transport.json'
    receipts=json.loads(path.read_bytes()) if path.is_file() else []
    accepted=[r for r in receipts if checksum in r.get('image_sha256',[]) and (r.get('response') or {}).get('usage')]
    codes=[r.get('status') for r in receipts]
    return {'status':'read' if accepted else 'provider_failure','source_status':'visual_available',
            'receipt_statuses':codes,'request_count':len(receipts),'receipts_with_image':accepted}


def main():
    p=ArgumentParser()
    for name in ('base','project','round','file','asset','question'):
        p.add_argument('--'+name,required=True)
    for name in ('token-file','prompt-file','out'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--page',type=int,required=True)
    a=p.parse_args()
    cli=API(a.base,a.token_file.read_text('utf8'),a.project)
    eligible=cli.call('step03','asset',round_id=a.round,file_id=a.file,asset_id=a.asset,
                      capability='inspect_visual',page=a.page)
    media=eligible['content']['image_url']
    raw=b64decode(media.split(',',1)[1],validate=True)
    checksum=sha256(raw).hexdigest()
    if checksum!=eligible['carrier']['sha256']:
        raise ValueError('送给模型的图像与已登记的实际字节不同')
    a.out.mkdir(parents=True,exist_ok=False)
    def save(name,value):
        (a.out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    save('output-schema.json',SCHEMA)
    payload={'issue_kind':'symbol_ambiguity','locator':eligible['locator'],
             'image_sha256':checksum,'question':a.question,'original_resource_only':True}
    save('request.json',payload)
    t=perf_counter()
    try:
        result=invoke(a.out,a.prompt_file.read_bytes(),payload,[(a.file,a.page,raw)],{}, {})
    except (OSError,ValueError,RuntimeError,TimeoutExpired) as error:
        save('provider-failure.json',{**transport_evidence(a.out,checksum),'failure':str(error)})
        raise
    evidence=transport_evidence(a.out,checksum)
    if evidence['status']!='read':
        raise RuntimeError('服务回执没有记录实际发送的原件图像')
    save('model-message-evidence.json',{'profile':PROFILE,'image_input_sha256':checksum,'mime':'image/png',
                                      'bytes':len(raw),'usage':evidence['receipts_with_image'],'wall_seconds':perf_counter()-t,
                                      'result':result,'retained_as':'visual_candidate'})
if __name__=='__main__':main()
