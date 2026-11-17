"""Exercise the real Reader browser in every isolated sample and retain native receipts."""

from argparse import ArgumentParser
import asyncio
from collections import Counter
import json
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout

from manualreader.artifacts import read_json as read, write_json as write


BROWSER_STATE = """
async (material) => {
    const session=await fetch('/api/session').then(r=>r.json());
    const headers={'X-Reader-Pin':session.pin || '', 'X-Reader-Material':material};
    const get=path=>fetch(path,{headers}).then(async r=>[r.status,await r.json()]);
    const [doc,meta,state]=await Promise.all(
        ['/api/material','/api/postprocess-preview','/api/progress'].map(get));
    return {meaningful:{doc:doc[0],manifest:meta[0],progress:state[0]},
            alignment:doc[1].alignment, pages:doc[1].pages, manifest:meta[1],
            translation_images:doc[1].passages.filter(p=>p.visual_language_verified).length,
            native_quizzes:state[1].quizzes?.length};
}
"""
PROGRESS = """
async material=>{
    const session=await fetch('/api/session').then(r=>r.json());
    const resp=await fetch('/api/progress',{headers:{'X-Reader-Pin':session.pin||'','X-Reader-Material':material}});
    return resp.json();
}
"""
SIMULATE = """
async options=>{
    const session=await fetch('/api/session').then(r=>r.json());
    const headers={'X-Reader-Pin':session.pin||'','X-Reader-Material':options.material,
        'X-Reader-Finish':'isolated-test','Content-Type':'application/json'};
    const post=async(path,body)=>{
        const response=await fetch('/api/next-finish/simulate/'+path,{
          method:'POST',headers,body:JSON.stringify(body)
        });
        return [response.status,await response.json()];
    }
    const start=await post('prepare',{});
    if(start[0]!==200)return {prepare:start};
    const args={commit_id:start[1].commit_id,preview_token:start[1].preview_token};
    const rejected=await post('commit',{...args,confirmed:false});
    const first=await post('commit',{...args,confirmed:true});
    const replay=await post('commit',{...args,confirmed:true});
    delete start[1].preview_token;
    return {prepare:start,rejected,first,replay};
}
"""


def native_questions(progress):
    return [q for rd in progress.get('rounds',[]) for q in rd.get('questions',[])]


def choose_questions(root, urls):
    choices={}
    used=set()
    for name,url in urls.items():
        material=urlsplit(url).query.split('=',1)[-1]
        doc=read(root/'reader-isolated'/'library'/material/'postprocess-preview'/'manifest.json')
        for block,items in doc['resources'].items():
            for item in items:
                kind=item['kind']
                if item['status'] in {'explained','complete','verified'} and item['components']:
                    row=(name,item['resource_id'],block)
                    choices.setdefault(kind,[]).append(row)
    result={}
    for kind,rows in sorted(choices.items()):
        for sample,rid,block in rows:
            if (sample,block) not in used:
                used.add((sample,block))
                result.setdefault(sample,{})[kind]=rid
                break
    return result


async def exercise_navigation(page, alignment):
    if not alignment:
        return {'available':False}
    from_explanations=alignment.get('explanations',{})
    if not isinstance(from_explanations,dict) or not alignment.get('sources'):
        return {'available':False}
    for block, row in sorted(from_explanations.items(),key=lambda pair:pair[1].get('precision') not in ('block','quote')):
        targets=[link['passage_id'] for link in row.get('links',[])]
        if not targets or not block.startswith('postprocess-preview--block-'):
            continue
        if await page.locator(f'#explanation article[data-passage-id="{block}"]').count()==0:
            continue
        sid=next((sid for sid in targets if block in alignment.get('sources',{}).get(sid,{}).get('explanation_ids',[])),None)
        if sid is None:
            continue
        await page.evaluate("""()=> {window.__nextLinked=[];document.addEventListener('reader-source-linked',
            event=>window.__nextLinked.push(event.detail));}""")
        await page.locator(f'#explanation article[data-passage-id="{block}"] .markdown-body').first.evaluate('node=>node.click()')
        try:
            await page.wait_for_function('window.__nextLinked.length>0',timeout=5000)
        except BrowserTimeout:
            continue
        forward=await page.evaluate('window.__nextLinked.at(-1)')
        reverse=None
        original=page.locator(f'#original-reading article[data-passage-id="{sid}"]')
        translated=page.locator(f'#translation article[data-passage-id="{sid}"]')
        location=original if await original.count() else translated
        if await location.count():
            # Clicking the same native relationship again may toggle its highlight.
            count=await page.evaluate('window.__nextLinked.length')
            await location.first.evaluate('node=>node.click()')
            try:
                await page.wait_for_function('n=>window.__nextLinked.length>n',arg=count,timeout=1000)
            except BrowserTimeout:
                await location.first.evaluate('node=>node.click()')
            try:
                await page.wait_for_function('n=>window.__nextLinked.length>n',arg=count,timeout=3000)
                reverse=await page.evaluate('window.__nextLinked.at(-1)')
            except BrowserTimeout:
                pass
        return {'available':True,'source_id':sid,'block_id':block,
                'from_explanation':forward,'to_explanation':reverse,
                'source_matched':sid in forward.get('source_ids',[]),
                'reverse_matched':bool(reverse and block in reverse.get('explanation_ids',[]))}
    return {'available':True,'source_matched':False,'reverse_matched':False}


async def resource_questions(page, material, targets):
    questions={}
    for kind,rid in targets.items():
        panel=page.locator(f'details.postprocess-resource[data-resource-id="{rid}"]').first
        await panel.wait_for(state='attached',timeout=9000)
        if not await panel.evaluate('node=>node.open'):
            await panel.locator('summary').evaluate('node=>node.click()')
        button=panel.get_by_role('button',name='就这个元素提问').first
        await button.wait_for(state='attached',timeout=4000)
        await button.evaluate('node=>node.click()')
        focused=page.locator('textarea.reader-question-input:focus')
        if not await focused.count():
            questions[kind]={'resource_id':rid,'native_question_saved':False,'reason':'Reader 原输入框没有取得焦点'}
            continue
        target=await focused.get_attribute('data-next-resource-context')
        existing=[q for q in native_questions(await page.evaluate(PROGRESS,material))
                  if q.get('target',{}).get('source_segment_id')==target]
        if existing:
            questions[kind]={'resource_id':rid,'native_question_saved':True,'question_ids':[q['id'] for q in existing],
                             'source_segment_id':target,'reused':True}
            continue
        if not target or not target.startswith('resource:'+rid+':'):
            questions[kind]={'resource_id':rid,'native_question_saved':False,'source_segment_id':target}
            continue
        content=await panel.locator('[data-component-id] code').first.text_content()
        symbol=(content or '').strip().replace('\n',' ')[:65]
        prompt=f'图文里的「{symbol}」在这个{ {"image":"图", "table":"表", "mermaid":"流程图", "code":"代码块"}.get(kind,"公式") }里表示什么？它怎样影响这一段的结论？'
        async with page.expect_response(lambda res:'/api/questions/sync' in res.url and res.request.method=='POST',
                                        timeout=16000) as save:
            await focused.fill(prompt)
            await focused.press('Tab')
        packet=await (await save.value).json()
        known=[q for q in packet.get('questions',[]) if q.get('target',{}).get('source_segment_id')==target]
        questions[kind]={'resource_id':rid,'native_question_saved':bool(known),'question_ids':[q['id'] for q in known],
                         'source_segment_id':target,'question':prompt}
    return questions


async def exercise_quiz(page, material, total):
    if not total:
        return {'available':False}
    radios=page.locator('#explanation input[type=radio][name^="quiz:"]')
    if not await radios.count():
        return {'available':True,'submitted':False,'reason':'正文暂未显示这道题的选择框'}
    radio=radios.first
    quiz=await radio.evaluate("node=>node.closest('.reader-inline-quiz')?.dataset.inlineQuizId")
    if not quiz:
        return {'available':True,'submitted':False,'reason':'找不到选择框对应的真实题号'}
    card=page.locator(f'#explanation .reader-inline-quiz[data-inline-quiz-id="{quiz}"]').first
    old=next((q for q in (await page.evaluate(PROGRESS,material)).get('quizzes',[]) if q.get('id')==quiz),{})
    if old.get('selected') is not None:
        return {'available':True,'submitted':True,'reused':True,'quiz_id':quiz,'selected':old['selected']}
    await radio.evaluate('node=>node.click()')
    check=card.get_by_role('button',name='核对答案',exact=True)
    if await check.count():
        await check.evaluate('button=>{if(!button.hidden)button.click()}')
    for _ in range(15):
        row=next((q for q in (await page.evaluate(PROGRESS,material)).get('quizzes',[]) if q.get('id')==quiz),{})
        if row.get('selected') is not None:
            return {'available':True,'submitted':True,'quiz_id':quiz,'selected':row['selected']}
        await asyncio.sleep(.2)
    return {'available':True,'submitted':False,'quiz_id':quiz}


async def sample(browser, root, identity, url, selected, save, screenshot_root):
    started=perf_counter()
    page=await browser.new_page(viewport={'width':1680,'height':1050},reduced_motion='reduce')
    issues=[]
    result={'sample_id':identity,'reader_url':url}
    page.on('pageerror',lambda error:issues.append(str(error)))
    try:
        material=urlsplit(url).query.split('=',1)[-1]
        response=await page.goto(url,wait_until='domcontentloaded',timeout=60000)
        result['status']=response.status
        await page.locator('[data-postprocess-preview]').wait_for(timeout=70000)
        view=await page.evaluate(BROWSER_STATE,material)
        result['native_endpoints']=view['meaningful']
        result['native_panes']=await page.evaluate("""()=>['original','translation','explanation']
            .map(k=>[k,!document.querySelector('[data-pane="'+k+'"]').hidden])""")
        shown=await page.locator('details.postprocess-resource[data-resource-id]').evaluate_all(
            """ds=>ds.map(d=>({id:d.dataset.resourceId,status:d.dataset.status,open:d.open}))""")
        expected={item['resource_id']:item for items in view['manifest']['resources'].values() for item in items}
        result['resources']={'expected':len(expected),'rendered':len(shown),'collapsed':sum(not q['open'] for q in shown),
            'missing_ids':sorted(set(expected)-{r['id'] for r in shown}),
            'kinds':dict(Counter(expected[q['id']]['kind'] for q in shown if q['id'] in expected))}
        result['translated_images']=view['translation_images']
        result['rendered_formulas']=await page.locator('#explanation .katex, #explanation math').count()
        result['rendered_mermaid']=await page.locator('#explanation .mermaid svg, #explanation .mermaid-rendered svg, #explanation svg[id^="mmd"]').count()
        result['reading_flow_nodes']=await page.locator('.reader-route-node').count()
        original_pages=list(view['pages'].values())
        media_urls={row.get('asset_url') for row in expected.values() if row.get('asset_url')}
        if original_pages:
            first=original_pages[0]
            media_urls.add(f"/content-files/{first['file_id']}/asset/{first['image']}?material={material}")
        result['media']=await page.evaluate("""async urls=>Promise.all(urls.map(async url=>{
           const image=new Image();image.src=url;
           try{await image.decode();return {url,width:image.naturalWidth,height:image.naturalHeight}}
           catch(e){return {url,error:String(e)}}
        }))""",sorted(media_urls))
        result['navigation']=await exercise_navigation(page,view['alignment'])
        screenshot=screenshot_root/f'{identity}.png'
        await page.screenshot(path=screenshot,animations='disabled',timeout=45000)
        result['screenshot']=screenshot.relative_to(root).as_posix()
        term_panel=page.locator('.postprocess-terms')
        if await term_panel.count():
            await term_panel.locator('summary').evaluate('node=>node.click()')
            result['term_candidates_rendered']=await term_panel.locator('[data-term-id]').count()
        else:
            result['term_candidates_rendered']=0
        result['preview_opened']=True
        result['resource_kinds']=await resource_questions(page,material,selected)
        if save:
            result['native_quiz']=await exercise_quiz(page,material,view['native_quizzes'])
            result['save_simulation']=await page.evaluate(SIMULATE,{'material':material})
    except Exception as exc:
        result['error']=str(exc)
        screenshot=screenshot_root/f'{identity}-error.png'
        try:
            await page.screenshot(path=screenshot,timeout=6000)
        except Exception:
            pass
    finally:
        result['page_errors']=issues
        result['wall_seconds']=round(perf_counter()-started,6)
        await page.close()
    return result


async def main(args):
    root=args.run.resolve()
    (root/'screenshots').mkdir(exist_ok=True)
    urls=read(root/'reader-service-ready.json')['samples']
    if args.sample:
        urls={name:urls[name] for name in args.sample}
    if any(urlsplit(url).hostname!='127.0.0.1' for url in urls.values()):
        raise ValueError('浏览器验收只允许使用本机隔离 Reader')
    chosen=choose_questions(root,urls) if args.question else {}
    rows=[]
    tag='-'+args.tag if args.tag else ''
    receipt_path=root/('browser-receipts'+tag+'.json')
    screenshot_root=root/('screenshots'+tag)
    screenshot_root.mkdir(exist_ok=True)
    slots=asyncio.Semaphore(args.tabs or len(urls))
    async def checked(browser,identity,url):
        async with slots:
            return await sample(browser,root,identity,url,chosen.get(identity,{}),args.save,screenshot_root)
    started=perf_counter()
    async with async_playwright() as driver:
        browser=await driver.chromium.launch(executable_path=str(args.browser) if args.browser else None)
        pending=[checked(browser,identity,url) for identity,url in urls.items()]
        for task in asyncio.as_completed(pending):
            row=await task
            rows.append(row)
            print(json.dumps({k:v for k,v in row.items() if k in
                ('sample_id','preview_opened','resource_kinds','error','wall_seconds')},ensure_ascii=False),flush=True)
            write(receipt_path,sorted(rows,key=lambda item:item['sample_id']))
        await browser.close()
    write(root/('browser-summary'+tag+'.json'),{'wall_seconds':round(perf_counter()-started,6),'browser_tabs':args.tabs or len(urls),
              'opened':sum(bool(row.get('preview_opened')) for row in rows),'total':len(rows),
              'exceptions':sum('error' in row for row in rows)})
    return int(any(not row.get('preview_opened') or 'error' in row for row in rows))


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--browser',type=Path)
    parser.add_argument('--sample',action='append')
    parser.add_argument('--question',action='store_true')
    parser.add_argument('--tabs',type=int)
    parser.add_argument('--tag')
    parser.add_argument('--save',action='store_true')
    raise SystemExit(asyncio.run(main(parser.parse_args())))
