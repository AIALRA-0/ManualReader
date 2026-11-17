"""Verify the new isolated Reader as each chapter becomes available."""

import argparse
import asyncio
from pathlib import Path
from time import time, perf_counter

from playwright.async_api import async_playwright

from check_stepfinal_browser import BROWSER_STATE, PROGRESS, exercise_navigation
from check_whiteboard_browser import ask_resource
from manualreader.artifacts import read_json as read, write_json as write


def load(path, default=None):
    return read(path) if path.is_file() else default


async def request(page,material,path,body):
    return await page.evaluate("""
        async ({material,path,body})=>{
            const data=await fetch('/api/session').then(r=>r.json());
            const reply=await fetch(path,{method:'POST',headers:{'X-Reader-Material':material,
                'X-Reader-Pin':data.pin||'','Content-Type':'application/json'},body:JSON.stringify(body)});
            return {status:reply.status,value:await reply.json()}
        }
    """,{'material':material,'path':path,'body':body})


async def exercise_types(page,material):
    data=await page.evaluate(PROGRESS,material)
    types={}
    for q in data.get('quizzes',[]):
        kind=q.get('type','choice')
        if kind in types or q.get('selected') is not None:
            continue
        if kind=='numeric':
            try:
                from fractions import Fraction
                expected=Fraction(q['expected'])
                valid={'response':q['expected']+' '+q.get('unit','')}
                invalid={'response':str(expected+Fraction(q.get('tolerance','0') or '0')+1)}
            except (ValueError,KeyError,TypeError,ZeroDivisionError):
                continue
        elif kind=='ordering':
            if not q.get('reference_order') or not q.get('before'):
                continue
            valid={'response':q['reference_order']}
            invalid={'response':list(reversed(q['reference_order']))}
        elif kind in ('choice','true_false','recall'):
            valid={'selected':q['correct_index']}
            invalid={'selected':next(i for i in range(len(q['options'])) if i!=q['correct_index'])}
            if kind=='recall':
                valid['response']=q['reference_answer']
                invalid['response']='我还没有完成这道题'
        else:
            continue
        path='/api/quizzes/'+q['id']+'/answer'
        no=await request(page,material,path,invalid)
        yes=await request(page,material,path,valid)
        types[kind]={'id':q['id'],'false_http':no['status'],'true_http':yes['status'],
                     'false_result':no['value'].get('correct'),'true_result':yes['value'].get('correct'),
                     'option_explanations':len(q.get('option_explanations',[])),
                     'passed':no['value'].get('correct') is False and yes['value'].get('correct') is True}
    return types


async def basic(page,root,sample,url,deep,screens):
    material='next-final-'+sample
    start=perf_counter()
    failures=[]
    page.on('pageerror',lambda error:failures.append(str(error)))
    response=await page.goto(url,wait_until='domcontentloaded',timeout=45000)
    section=page.locator('#explanation[data-next-presentation-ready="true"]')
    await section.wait_for(state='attached',timeout=60000)
    data=await page.evaluate(BROWSER_STATE,material)
    folds=section.locator('details.reader-resource-details')
    ids=await folds.evaluate_all("nodes=>nodes.map(n=>[n.dataset.resourceId,n.open,n.querySelector('summary')?.textContent])")
    wanted={item['resource_id'] for items in data['manifest'].get('resources',{}).values() for item in items}
    result={'sample':sample,'url':url,'browser_opened_at':time(),'http':response.status,
            'resource_count':len(ids),'resource_misses':sorted(wanted-{r[0] for r in ids}),
            'resource_titles_correct':all(not r[1] and r[2]=='解读' for r in ids),
            'recap_headings':await section.get_by_role('heading',name='把这一节串起来',exact=True).count(),
            'pane_visible':await section.is_visible(),
            'phase': 'final' if data['manifest'].get('delivery_receipt_sha256') else 'early',
            'translation_image_groups':data['translation_images']}
    if deep:
        result['navigation']=await exercise_navigation(page,data['alignment'])
        result['grading']=await exercise_types(page,material)
        if ids:
            first=folds.first
            await first.locator('summary').click()
            result['resource_expanded']=await first.locator('.inline-question-body > *').count()>0
            sibling=section.locator(f'.reader-resource-question[data-segment-id="resource:{ids[0][0]}"]')
            if await sibling.count():
                result['resource_question']=await ask_resource(page,material,ids[0][0],
                                                               '请解释这项资源中最关键的一个关系。')
        path=screens/(sample+'.png')
        await page.screenshot(path=path)
        result['screenshot']=str(path)
    result['wall_seconds']=perf_counter()-start
    result['page_errors']=failures
    return result


async def main(args):
    root=args.run
    fixed=read(root/'whiteboard-inputs.json')
    samples=fixed['samples']
    screens=root/'acceptance-screens'
    screens.mkdir(exist_ok=True)
    first={}
    complete={}
    failed={}
    result_path=args.output or (root/'browser-20step.json')
    start=perf_counter()
    async with async_playwright() as driver:
        browser=await driver.chromium.launch(channel='msedge',headless=True)
        limit=asyncio.Semaphore(12)
        async def inspect(sample,url,deep=False):
            async with limit:
                p=await browser.new_page(viewport={'width':1450,'height':960},reduced_motion='reduce')
                try:
                    return await basic(p,root,sample,url,deep,screens)
                except Exception as error:
                    return {'sample':sample,'error':str(error)}
                finally:
                    await p.close()
        scheduled={}
        while len(complete)+len(failed)<len(samples):
            state=load(root/'reader-service-ready.json',{})
            urls=state.get('samples',{})
            model=load(root/'whiteboard-result.json',{})
            resuming=set(load(root/'resume-20step.json',{}).get('previously_unstarted_finales',[]))
            for name,error in model.get('failures',{}).items():
                if name not in complete and name not in urls and name not in resuming:
                    failed.setdefault(name,error)
            for name,url in urls.items():
                if name not in samples or name in failed:
                    continue
                m=root/'reader-isolated'/'library'/('next-final-'+name)/'postprocess-preview/manifest.json'
                final=(load(m,{}) or {}).get('delivery_receipt_sha256')
                phase='final' if final else 'early'
                if (name,phase) in scheduled or (phase=='early' and name in complete):
                    continue
                scheduled[name,phase]=asyncio.create_task(inspect(name,url,phase=='final' and name in fixed['deep_browser_samples']))
            for (name,phase),task in list(scheduled.items()):
                target=complete if phase=='final' else first
                if task.done() and name not in target:
                    target[name]=task.result()
                    write(result_path,{'early':first,'final':complete,'missing':failed,
                                                      'elapsed_seconds':perf_counter()-start})
                    print(name,phase,'error' if 'error' in target[name] else 'ready',flush=True)
            if perf_counter()-start>args.timeout:
                failed.update({name:'等待隔离预览超时' for name in samples if name not in complete and name not in failed})
                break
            await asyncio.sleep(.7)
        await browser.close()
    write(result_path,{'early':first,'final':complete,'missing':failed,
                                      'elapsed_seconds':perf_counter()-start,'deep_samples':fixed['deep_browser_samples'],
                                      'opened':sum(row.get('http')==200 for row in complete.values())})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--timeout',type=int,default=5400)
    asyncio.run(main(parser.parse_args()))
