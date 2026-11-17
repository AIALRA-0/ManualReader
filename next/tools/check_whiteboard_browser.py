"""Exercise the existing Reader controls on a fresh whiteboard run."""

import argparse
import asyncio
from collections import Counter
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from check_stepfinal_browser import (
    BROWSER_STATE, PROGRESS, exercise_navigation, exercise_quiz, native_questions,
)
from manualreader.artifacts import read_json as read, write_json as write


async def ask_resource(page,material,rid,question):
    panel=page.locator(f'[data-resource-id="{rid}"]')
    slot=page.locator(f'.reader-resource-question[data-segment-id="resource:{rid}"]')
    if not (await panel.count() and await slot.count()):
        raise ValueError('页面未显示资源对应的解释和提问框')
    await slot.locator('summary').click()
    textarea=slot.locator('textarea.reader-question-input')
    async with page.expect_response(lambda reply:'/api/questions/sync' in reply.url
                                    and reply.request.method=='POST',timeout=20000) as resp:
        await textarea.fill(question)
        await textarea.press('Tab')
    response=await (await resp.value).json()
    saved=[q for q in response.get('questions',[]) if (q.get('target') or {}).get('source_segment_id')=='resource:'+rid]
    if not saved:
        items=await page.evaluate(PROGRESS,material)
        saved=[q for q in native_questions(items) if (q.get('target') or {}).get('source_segment_id')=='resource:'+rid]
    return {'resource_id':rid,'question_id':saved[0]['id'] if saved else None,
            'target':saved[0].get('target') if saved else None,'question':question}


async def check_recaps(page, expected):
    section=page.locator('#explanation article[data-passage-id="postprocess-preview--framework-outro"]')
    if not expected:
        assert await section.count()==0, '没有章末回顾数据，但页面显示了回顾'
        return {'parts':0,'items':0}
    assert await section.count()==1, '一篇讲解只能显示一处章末回顾'
    assert await section.locator(':scope > h3').inner_text()=='把这一节串起来'
    body=section.locator(':scope > .chinese.markdown-body')
    assert await body.locator(':scope > :not(.reader-recap-list)').count()==0, '旧的回顾正文仍在显示'
    actual=await body.locator('.reader-recap-list > li').evaluate_all("""nodes=>nodes.map(node=>({
        title:node.querySelector(':scope > .reader-recap-part')?.dataset.markdownSource,
        items:[...node.querySelectorAll(':scope > .reader-recap-row')].map(row=>[
            row.querySelector('.reader-recap-icon')?.title,
            row.querySelector('.reader-recap-icon')?.getAttribute('aria-label'),
            row.querySelector('.markdown-body')?.dataset.markdownSource
        ])
    }))""")
    fields=(('context','背景'),('problem','问题'),('claim','结论'),('reason','原因'),
            ('evidence','依据'),('implication','用法'))
    wanted=[{'title':group['title'],'items':[[label,label,group['items'][key]]
             for key,label in fields if group['items'].get(key)]} for group in expected]
    assert actual==wanted, '回顾 Part 标题、内容、顺序或图标名称与原数据不一致'
    assert await body.evaluate("""async node=>{
        const source=node.dataset.markdownSource.trim();
        const selection=getSelection();
        const range=document.createRange();
        range.selectNodeContents(node);
        selection.removeAllRanges();
        selection.addRange(range);
        const copy=()=>{
            const event=new ClipboardEvent('copy',{bubbles:true,cancelable:true,clipboardData:new DataTransfer()});
            node.dispatchEvent(event);
            return event.clipboardData.getData('text/plain');
        };
        const immediate=copy();
        await new Promise(resolve=>setTimeout(resolve,120));
        const afterSelection=copy();
        selection.removeAllRanges();
        return immediate===source && afterSelection===source;
    }"""), '复制回顾应只保留一份带 Part 标题的 Markdown'
    return {'parts':len(actual),'items':sum(len(group['items']) for group in actual),'copy':True}


async def chapter(browser,root,sample,url,question=False,read_only=False):
    begin=perf_counter()
    row={'sample_id':sample,'url':url}
    material=urlsplit(url).query.split('=',1)[-1]
    page=await browser.new_page(viewport={'width':1700,'height':1050},reduced_motion='reduce')
    errors=[]
    page.on('pageerror',lambda err: errors.append(str(err)))
    try:
        rs=await page.goto(url,wait_until='domcontentloaded',timeout=45000)
        row['http']=rs.status
        await page.locator('#explanation[data-next-presentation-ready="true"]').wait_for(timeout=48000)
        state=await page.evaluate(BROWSER_STATE,material)
        row['recap_content']=await check_recaps(page,state['manifest'].get('chapter_recaps',[]))
        assert not await page.locator('button').evaluate_all("""buttons=>buttons.some(button=>
            getComputedStyle(button).backgroundColor==='rgb(40, 91, 69)')"""), '有按钮仍在使用旧组件的默认绿色'
        expected={item['resource_id']:item for items in state['manifest']['resources'].values() for item in items}
        details=await page.locator('#explanation details.reader-resource-details').evaluate_all("""nodes => nodes.map(d=>({
            id:d.dataset.resourceId,open:d.open,question:d.nextElementSibling?.dataset.segmentId,
            same_article:!!d.closest('article')?.querySelector('[data-next-resource-body="'+d.dataset.resourceId+'"]'),
            adjacent:!!d.previousElementSibling?.matches('[data-next-resource-body], [data-reader-media-controls], .reader-media-inline, .markdown-table-scroll,p,figure,pre') ||
                       !!d.previousElementSibling?.querySelector('[data-next-resource-body]'),
          }))""")
        recap_order=await page.get_by_role('heading',name='把这一节串起来',exact=True).evaluate_all("""nodes=>nodes.map(n=>({
            passage_id:n.closest('article')?.dataset.passageId || null,
          }))""")
        inside_recaps=await page.get_by_role('heading',name='本节回顾',exact=True).count()
        row.update(mode=state['manifest']['mode'],
                   rendered_resources=len(details), expected_resources=len(expected),
                   missing_resources=sorted(expected.keys()-{item['id'] for item in details}),
                   wrong_resource_order=[d['id'] for d in details if not d['same_article']
                                         or not d['adjacent'] or d['question']!='resource:'+d['id']],
                   initially_open=[d['id'] for d in details if d['open']],
                   inline_math_details=sum(expected[d['id']]['kind']=='formula_inline' for d in details),
                   recaps=recap_order,wrong_recap_order=[x['passage_id'] for x in recap_order[1:]],
                   in_body_recaps=inside_recaps,
                   eligible_recaps=state['manifest']['projection_receipt']['recaps'],
                   route_items=await page.locator('.reader-route-node').count(),
                   formal_terms=await page.locator('#study .term-entry, #study details').count(),
                   has_banned_ui=await page.locator('.reader-handoff-trigger, .postprocess-banner, .postprocess-terms, a[href*="/api/next-finish/download"]').count(),
                   has_legacy_bare_question_button=await page.get_by_role('button',name='就这个资源提问',exact=True).count())
        await page.locator('#explanation .markdown-body').first.wait_for()
        if any(x['kind'] in ('mermaid','formula_display') for x in expected.values()):
            await page.wait_for_timeout(500)
        row['math']=await page.locator('#explanation [data-math-display="block"] .katex').count()
        row['code']=await page.locator('#explanation pre code, #translation pre code, #original-reading pre code').count()
        row['mermaid']=await page.locator('#explanation [data-diagram-rendered="true"]').count()
        row['resource_kinds']=dict(Counter(x['kind'] for x in expected.values()))
        row['unverified_math_delimiters']=await page.locator('#explanation [data-math-error]').count()
        state_progress=await page.evaluate(PROGRESS,material)
        term_cards=page.locator('#study .term-card')
        row['formal_terms']=await term_cards.count()
        row['term_links']=await page.locator('#explanation a.term-link').count()
        if row['formal_terms']:
            first=term_cards.first
            await first.evaluate('node=>node.expandReader ? node.expandReader() : node.open=true')
            paragraphs=await first.locator('.term-body > p').all_text_contents()
            row['term_example']={'name':await first.locator('summary').inner_text(),'paragraphs':paragraphs}
        if question:
            chosen=next((item for item in expected.values() if item['kind']=='mermaid' and item.get('overview')
                         and item.get('summary') and item['resource_id'] not in row['missing_resources']),None)
            if chosen:
                item=page.locator(f'details[data-resource-id="{chosen["resource_id"]}"]')
                await item.locator('summary').click()
                row['open_details']={'resource_id':chosen['resource_id'],
                    'body_count':await item.locator('.inline-question-body > *').count(),
                    'summary_visible':chosen['summary'].strip()[:15] in (await item.inner_text())}
                row['real_question']=await ask_resource(page,material,chosen['resource_id'],
                    '这张流程图里，哪一步把前面的输入变成了后面的结果？请按图中的实际标签简洁说明。')
        row['opened']=True
        if not read_only:
            name=root/'browser'/f'{sample}.png'
            name.parent.mkdir(exist_ok=True)
            await page.screenshot(path=name,timeout=24000)
            row['screenshot']=str(name)
        if not question and not read_only:
            row['navigation']=await exercise_navigation(page,state['alignment'])
            row['quiz']=await exercise_quiz(page,material,len(state_progress.get('quizzes',[])))
    except Exception as error:
        row['error']=f'{type(error).__name__}: {error}'
    finally:
        row['page_errors']=errors
        row['wall_seconds']=perf_counter()-begin
        await page.close()
    return row


async def main(args):
    root=args.root
    ready=read(root/'reader-service-ready.json')['samples']
    if args.samples:
        ready={item:ready[item] for item in args.samples}
    start=perf_counter()
    cap=asyncio.Semaphore(args.tabs)
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path=args.edge,headless=True)
        async def show(sample,url):
            async with cap:
                row=await chapter(browser,root,sample,url,args.question,args.read_only)
                print(sample,row.get('opened',False),row.get('missing_resources',[]),row.get('error',''),flush=True)
                return row
        rows=await asyncio.gather(*(show(k,v) for k,v in ready.items()))
        await browser.close()
    result={'samples':rows,'count':len(rows),'opened':sum(bool(x.get('opened')) for x in rows),
            'wall_seconds':perf_counter()-start,'error_samples':[x['sample_id'] for x in rows if x.get('error')]}
    write(root/args.receipt,result)
    return int(bool(result['error_samples']))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    p.add_argument('--edge',default='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
    p.add_argument('--tabs',type=int,default=8)
    p.add_argument('--samples',action='append')
    p.add_argument('--question',action='store_true')
    p.add_argument('--read-only',action='store_true',help='只检查页面和复制行为，跳过答题和截图')
    p.add_argument('--receipt',default='browser-whiteboard.json')
    raise SystemExit(asyncio.run(main(p.parse_args())))
