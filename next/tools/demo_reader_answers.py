"""Submit real resource questions through the local Reader UI and let its writer answer them."""

from argparse import ArgumentParser
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import patch
from time import perf_counter
from urllib.parse import urlencode, urlsplit

from playwright.async_api import async_playwright
from check_whiteboard_browser import ask_resource
from manualreader.artifacts import read_json as read, write_json as write
from manualreader.answer_repair import targeted_supplement


async def run(args):
    base=args.base.rstrip('/')
    url=base+'/?'+urlencode({'material':args.material})
    sys.path.insert(0,str(args.legacy.resolve(strict=True)))
    from reader_bridge import Bridge
    import reader_execute

    bridge=Bridge(base,args.material)
    clock=perf_counter()
    doc=bridge.request('/api/postprocess-preview')
    resources=[r for v in doc['resources'].values() for r in v if r.get('overview')]
    submitted=[]
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(channel='msedge')
        page=await browser.new_page(viewport={'width':1800,'height':1000})
        await page.goto(url,wait_until='domcontentloaded')
        await page.locator('#explanation[data-next-presentation-ready="true"]').wait_for(timeout=60000)
        for kind,question in args.question:
            resource=next(row for row in resources if row['kind']==kind)
            row=await ask_resource(page,args.material,resource['resource_id'],question)
            if not row.get('question_id'):
                raise ValueError('浏览器未成功登记资源提问')
            submitted.append(row)
        await browser.close()

    sample=doc['sample_id']
    output=args.run/'native-qa'/sample
    output.mkdir(parents=True,exist_ok=False)
    await asyncio.to_thread(bridge.prepare,output,observe=False,
                            question_ids=[q['question_id'] for q in submitted])
    ordered=read(output/'run.json')
    references=read(output/'reference.json')
    cli=reader_execute.codex_executable()

    async def answer(group):
        identity=group['id']
        packet=(output/('prompt-'+identity+'.txt')).read_text('utf-8').split('\n',1)[1]
        await asyncio.to_thread(reader_execute.run_group,bridge,output,group,json.loads(packet),references,cli)
        receipt=output/('usage-'+identity+'.json')
        usage=read(receipt) if receipt.is_file() else {}
        return {qid:{'model':usage.get('model'),'usage':usage.get('usage',{}),
                     'seconds':usage.get('seconds'),'group_id':identity}
                for qid in group['question_ids']}

    usages={}
    with patch.object(reader_execute,'supplemental_patch',targeted_supplement(reader_execute)):
        for row in await asyncio.gather(*(answer(group) for group in ordered['groups'])):
            usages.update(row)
    state=bridge.request('/api/progress')
    stored={q['id']:q for rnd in state['rounds'] for q in rnd['questions']}
    answers=[{**q,'sample_id':sample,'answer':stored[q['question_id']].get('answer'),
              'metrics':usages.get(q['question_id'],{})} for q in submitted]
    report={'url':url,'wall_seconds':perf_counter()-clock,'answers':answers}
    write(args.run/'native-answer-demo.json',report)
    if not all(row['answer'] for row in answers):
        raise ValueError('有问题还没有写入 Reader 回答')
    print([(row['question_id'],len(row['answer'])) for row in answers],flush=True)


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    parser.add_argument('--legacy',type=Path,required=True)
    parser.add_argument('--base',required=True)
    parser.add_argument('--material',required=True)
    parser.add_argument('--question',nargs=2,action='append',required=True,metavar=('RESOURCE_KIND','TEXT'))
    params=parser.parse_args()
    endpoint=urlsplit(params.base)
    if endpoint.scheme!='http' or endpoint.hostname!='127.0.0.1' or not endpoint.port:
        parser.error('问答演示需要本机隔离地址')
    asyncio.run(run(params))
