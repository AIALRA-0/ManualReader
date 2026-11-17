"""Recover one writer transport timeout from this whiteboard run without replaying other models."""

import asyncio
from argparse import ArgumentParser
from pathlib import Path
from time import time

from manualreader.stages.finish.content import practice
from manualreader.stages.finish.formatting import layer,replace_leaves,scan_batch,sha,text_leaves,type_patches
from manualreader.stages.finish.provider import ModelFailure,frozen_prompts,invoke_sol,serialize
from manualreader.stages.step05.contracts import digest
from manualreader.stages.step05.inference import read,write
from manualreader.transport import executable


async def main(root,format_only=False):
    pending=[]
    for path in (root/'requests/F3').glob('*/receipt.json'):
        r=read(path)
        if r['status']!='complete' and r.get('error')=='TimeoutExpired' and len(r['attempts'])==1 and not format_only:
            pending.append((path,r))
        elif format_only and (path.parent/'receipt-before-technical-recovery.json').is_file():
            previous=read(path.parent/'receipt-before-technical-recovery.json')
            if previous.get('error')=='TimeoutExpired':
                pending.append((path,previous))
    for path,old in pending:
        old_input=read(path.parent/'input.json')
        payload=old_input['input']
        system=frozen_prompts()['F3']
        if old_input['prompt_sha256']!=digest(system):
            raise ValueError('练习请求与本轮冻结提示词不一致')
        sample=old['sample_id']
        if format_only:
            receipt=read(path)
            value=read(path.parent/'result.json')
            fixed=value['result'] is not None
            original_recovery=read(root/'f3-technical-retry.json')
            started=original_recovery['started_at']
        else:
            target=path.parent/'attempt-2'
            target.mkdir()
            started=time()
            value=await asyncio.to_thread(invoke_sol,target,'F3',system,payload,{},executable())
            fixed=value['result'] is not None
            receipt={**old,'attempts':old['attempts']+value['attempts'],'retries':1,'ended_at':time(),
                     'status':'complete' if fixed else 'partial','error':value.get('error'),'response':value['response']}
            write(path.parent/'receipt-before-technical-recovery.json',old)
            write(path,receipt)
            if fixed:
                write(path.parent/'result.json',value)
        body=root/'jobs'/sample
        side=body/'generated'
        card=read(root/'inputs'/sample/'task-card.json')
        structure=read(side/'structure.json')

        async def archives(stage,current,images=None):
            if stage!='F3':
                raise ModelFailure('本次只处理练习超时')
            if serialize(current)==serialize(payload):
                if fixed:
                    return value['result']
                raise ModelFailure(receipt.get('error') or '没有收到练习响应')
            for original in (root/'requests/F3').glob('*/input.json'):
                if serialize(read(original)['input'])==serialize(current):
                    proof=original.parent/'result.json'
                    if proof.is_file():
                        return read(proof)['result']
                    break
            raise ModelFailure('本轮练习回执与请求不一致')

        data=await practice(root,sample,structure,card,archives)
        previous=read(side/'formatted.json')['attachments']['questions']
        prior_ids={row.get('quiz_id') or row.get('id') for row in previous}
        added=[row for row in data['questions'] if (row.get('quiz_id') or row.get('id')) not in prior_ids]
        leaves=list(text_leaves(added))
        texts=[v for _,v in leaves]
        changes={}
        new_patches=[]
        for (key,original),scan in zip(leaves,scan_batch(texts) if texts else [],strict=True):
            edited,operations=layer(original,type_patches(original,scan))
            changes[key]=edited
            if operations:
                new_patches.append({'path':['attachments','questions',len(previous)+key[0],*key[1:]],
                    'from_sha256':sha(original),'to_sha256':sha(edited),
                    'phases':[{'name':'typography','operations':operations}]})
        all_questions=previous+replace_leaves(added,changes)
        current_patches=read(side/'formatted.json')['patches']
        # Preserve original indices so prior reversible formatting still points at the same fields.
        write(body/'format-patches.json',current_patches+new_patches)
        write(body/'quiz-candidates.json',all_questions)
        sidecar=body/'attachments.final.json'
        att=read(sidecar)
        write(sidecar,{**att,'questions':all_questions})
        run=read(body/'run.json')
        write(body/'run.json',{**run,'exercises':len(all_questions),
            'issues':{**run['issues'],'F3':data['issues']}})
        write(root/'f3-technical-retry.json',{'sample_id':sample,'request_id':old['id'],
              'started_at':started,'ended_at':receipt['ended_at'],'response_saved':fixed,'exercises_total':len(all_questions),
              'initial_error':old['error'],'error_after_retry':receipt.get('error'),
              'format_replayed_from_receipt':format_only})
        print(sample,'F3 transport retry',fixed,len(all_questions),flush=True)


if __name__=='__main__':
    p=ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    p.add_argument('--format-only',action='store_true')
    args=p.parse_args()
    asyncio.run(main(args.root.resolve(),args.format_only))
