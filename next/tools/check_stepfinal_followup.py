"""Verify that a real resource answer accepts a native follow-up and a BP note."""

from argparse import ArgumentParser
import asyncio
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

from check_stepfinal_browser import PROGRESS, native_questions
from manualreader.artifacts import write_json as write
from playwright.async_api import async_playwright


async def verify(args):
    started=perf_counter()
    target=urlsplit(args.url)
    material=target.query.split('=',1)[-1]
    if target.hostname!='127.0.0.1' or not material.startswith('next-final-'):
        raise ValueError('只能在本机隔离的 Reader 里验证追问')
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path=str(args.browser) if args.browser else None)
        try:
            page=await browser.new_page(viewport={'width':1680,'height':1050})
            await page.goto(args.url,wait_until='domcontentloaded')
            await page.locator('#explanation[data-next-presentation-ready]').wait_for()
            q=next((q for q in native_questions(await page.evaluate(PROGRESS,material)) if q['id']==args.question),None)
            if not q or not q.get('answer'):
                raise ValueError('实际生成的资源回答尚未保存')
            card=page.locator('.question-card[id="'+args.question+'"]').first
            if not await card.count():
                await page.locator('#question-project-scope[aria-pressed="false"]').evaluate('node=>node.click()')
                await card.wait_for(state='attached')
            # Native menu and question card open behavior stays in the reused Reader.
            if not await card.evaluate('node=>node.open'):
                await card.locator(':scope > summary').click()
            follow=card.locator('.answer-followup').first
            await follow.wait_for(state='attached')
            await follow.locator(':scope > summary').evaluate('node=>node.click()')
            followup='这个解释在哪一步用到了原文给出的条件？'
            existing=next((item for item in native_questions(await page.evaluate(PROGRESS,material))
                           if item.get('parent_question_id')==args.question and item['text']==followup),None)
            if not existing:
                async with page.expect_response(lambda res:'/api/questions/sync' in res.url and res.request.method=='POST') as save:
                    await follow.locator('textarea').fill(followup)
                    await follow.locator('textarea').press('Tab')
                packet=await (await save.value).json()
                existing=next(item for item in packet['questions'] if item.get('parent_question_id')==args.question)
            bp=card.locator(':scope > .bp')
            await bp.locator('summary').evaluate('node=>{if(!node.parentElement.open)node.click()}')
            note='隔离验收：这条笔记挂在原资源问题下。'
            reading=await page.evaluate(PROGRESS,material)
            if reading.get('notes',{}).get(args.question)!=note:
                async with page.expect_response(lambda res:'/api/notes' in res.url and res.request.method=='POST') as save:
                    await bp.locator('textarea').fill(note)
                    await bp.locator('textarea').press('Tab')
                await save.value
            state=await page.evaluate(PROGRESS,material)
            result={'question_id':args.question,'resource_context':q.get('target',{}).get('source_segment_id'),
                    'followup_id':existing['id'],'followup_context':existing.get('target',{}).get('source_segment_id'),
                    'followup_parent_id':existing.get('parent_question_id'),'note_saved':state.get('notes',{}).get(args.question)==note,
                    'wall_seconds':round(perf_counter()-started,6)}
            write(args.out,result)
            await page.screenshot(path=args.out.with_suffix('.png'),animations='disabled')
            print(result)
        finally:
            await browser.close()


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--question',required=True)
    parser.add_argument('--browser',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    asyncio.run(verify(parser.parse_args()))
