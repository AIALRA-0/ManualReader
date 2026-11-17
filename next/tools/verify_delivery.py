"""Verify a finished chapter through the isolated Reader and record actual handoff receipts."""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter
from urllib.parse import urlencode, urlsplit

from playwright.async_api import async_playwright

from check_whiteboard_browser import chapter, PROGRESS
from manualreader.artifacts import read_json, write_json
from manualreader.steps.events import RunJournal


async def verify(root, sample):
    ready = await await_preview(root, sample)
    address = urlsplit(ready)
    if address.scheme != 'http' or address.hostname != '127.0.0.1':
        raise ValueError('隔离交稿只能在本机进行')
    base = f'{address.scheme}://{address.netloc}'
    material = 'next-final-'+sample
    manifest = read_json(root/'whiteboard-inputs.json')
    journal = RunJournal(root, manifest['run_id'], manifest['samples'])
    latest = {row['step_id']:row['event_id'] for row in journal.nodes.values() if row['task_id']==sample}
    started = perf_counter()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel='msedge')
        try:
            checked = await chapter(browser,root,sample,ready)
            if not checked.get('opened') or checked['missing_resources'] or checked['page_errors']:
                raise RuntimeError(f'浏览器没有完整打开成稿：{checked}')
            browser_event = journal.emit(sample,19,'final_chapter_verified',
                                         value=checked,upstream=[latest['STEP-17']])
            page = await browser.new_page(viewport={'width':1750,'height':1040})
            page.on('dialog',lambda dialog:dialog.accept())
            await page.goto(ready,wait_until='domcontentloaded',timeout=60000)
            await page.locator('#explanation[data-next-presentation-ready="true"]').wait_for(timeout=60000)
            before = await page.evaluate(PROGRESS,material)
            button = page.get_by_role('button',name='交稿',exact=True)
            if await button.count():
                async with page.expect_response(lambda item:item.url.endswith('/delivery/commit')) as waiting:
                    await button.click()
                response = await waiting.value
                receipt = await response.json()
                if not response.ok or receipt.get('status')!='applied':
                    raise RuntimeError(f'浏览器交稿未完成：{receipt}')
            else:
                raise RuntimeError('请在尚未交稿的隔离运行中记录首次提交')

            pin = (await (await page.request.get(base+'/api/session')).json())['pin']
            headers={'X-Reader-Material':material,'X-Reader-Pin':pin,'X-Reader-Finish':'isolated-test'}
            draft=await (await page.request.post(base+'/api/next-finish/delivery/prepare',
                                                headers=headers,data={})).json()
            replay=await (await page.request.post(base+'/api/next-finish/delivery/commit',
                                                  headers=headers,data={'preview_token':draft['preview_token'],
                                                  'commit_id':draft['commit_id'],'confirmed':True})).json()
            assert replay['replayed'] and replay['commit_id']==receipt['commit_id']
            assert receipt['user_state_sha256_before']==receipt['user_state_sha256_after']
            after=await page.evaluate(PROGRESS,material)
            assert before.get('rounds')==after.get('rounds')
            assert before.get('manual_completions')==after.get('manual_completions')
            progress=receipt['progress']['manifest']['progress']
            assert progress['files']['coverage_pct']>0
            journal.emit(sample,20,'delivery_committed',value={
                'commit_id':receipt['commit_id'],'progress':progress,'updated_file_ids':receipt['progress']['updated_file_ids'],
                'replayed':True},upstream=[browser_event])

            await page.reload(wait_until='domcontentloaded')
            await page.locator('.reader-next-delivery').wait_for(state='hidden',timeout=30000)
            query=urlencode({'material':material})
            await page.goto(base+'/quality-review?'+query,wait_until='domcontentloaded')
            await page.get_by_text('Next 运行记录',exact=True).first.wait_for(timeout=30000)
            await page.screenshot(path=root/'quality-review.png')
            review=await (await page.request.get(base+'/api/next-finish/review',headers=headers)).json()
            calls=await (await page.request.get(base+'/api/quality-review/calls',headers=headers)).json()
            assert review['delivery']['progress']==progress
            assert calls['calls'] and len(review['stages'])==20
            proof={'url':ready,'browser':checked,'receipt':receipt,'stages':review['stages'],
                   'receipt_rows':len(calls['calls']),'usage':calls['usage'],
                   'replayed':True,'wall_seconds':perf_counter()-started}
            write_json(root/'full-delivery-proof.json',proof)
            print(json.dumps({'url':ready,'resource_count':checked['rendered_resources'],
                              'progress':progress,'calls':len(calls['calls'])},ensure_ascii=False),flush=True)
        finally:
            await browser.close()


async def await_preview(root, sample):
    path=root/'reader-service-ready.json'
    for _ in range(60):
        if path.is_file():
            url=read_json(path).get('samples',{}).get(sample)
            if url:
                return url
        await asyncio.sleep(.5)
    raise RuntimeError('等待隔离 Reader 发布预览超时')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--sample')
    args=parser.parse_args()
    sample=args.sample
    if sample is None:
        samples=read_json(args.root/'whiteboard-inputs.json')['samples']
        if len(samples)!=1:
            parser.error('运行目录包含多个项目，请提供 --sample')
        sample=next(iter(samples))
    asyncio.run(verify(args.root,sample))


if __name__=='__main__':
    main()
