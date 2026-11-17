"""Verify fused preview navigation, actual rendering and client downloads in Edge."""

import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import sys
import traceback

from playwright.async_api import async_playwright

from manualreader.client import API
from manualreader.step07_client import download


def read(path):
    return json.loads(path.read_bytes())


async def verify(root):
    base=read(root/'service-ready.json')['base']
    token=(root/'token.txt').read_text('utf8').strip()
    api=API(base,token,'isolated')
    names=api.call('step07','list')['samples']
    assert len(names)==40
    output=root/'browser'
    output.mkdir(exist_ok=True)
    chosen={}
    for name in names:
        row=read(root/'jobs'/name/'run.json')
        kinds=Counter(r['kind'] for r in read(root/'jobs'/name/'resources.fused.json')['items']
                      if r['surface']=='body')
        score=(min(kinds['image'],2)+bool(kinds['formula_display'])+bool(kinds['table']),
               sum(kinds.values()))
        if row['mode'] not in chosen or chosen[row['mode']][0]<score:
            chosen[row['mode']]=score,name
    screenshots={sample:mode for mode,(_,sample) in chosen.items()}
    edge_exe=Path(os.environ['ProgramFiles(x86)'])/'Microsoft/Edge/Application/msedge.exe'
    async with async_playwright() as tools:
        browser=await tools.chromium.launch(executable_path=str(edge_exe),headless=True)
        context=await browser.new_context(viewport={'width':1440,'height':1000})
        login=await context.new_page()
        await login.goto(base+'/step07',wait_until='networkidle')
        await login.locator('input[name=token]').fill(token)
        await login.get_by_role('button',name='打开结果').click()
        await login.wait_for_url(base+'/step07')
        assert await login.locator('li').count()==40
        sdk_response=await context.request.get(base+'/v1/client')
        assert sdk_response.ok
        (output/'manualreader-step07-client.zip').write_bytes(await sdk_response.body())
        await login.close()
        concurrency=asyncio.Semaphore(10)

        async def one(sample):
            async with concurrency:
                page=await context.new_page()
                errors=[]
                page.on('pageerror',lambda err:errors.append(str(err)))
                current=root/'jobs'/sample
                run=read(current/'run.json')
                remap=read(current/'anchor-remap.json')
                res=read(current/'resources.fused.json')['items']
                try:
                    response=await page.goto(f'{base}/step07/{sample}',wait_until='domcontentloaded')
                    assert response.ok
                    await page.locator('#draft.rendered').wait_for()
                    anchors=sorted(remap['anchors'])
                    missing=await page.evaluate('(targets)=>targets.filter(x=>!document.getElementById(x))',anchors)
                    assert not missing,(sample,missing)
                    malformed=await page.evaluate('''() => [...document.querySelectorAll(
                        '#sources a[href^="#"],#coverage a,#question-fragments a')]
                        .map(a=>a.hash.slice(1)).filter(id=>!document.getElementById(id))''')
                    assert not malformed,(sample,malformed)
                    if await page.locator('#draft .reader-diagram').count():
                        await page.wait_for_function('''() => [...document.querySelectorAll('#draft .reader-diagram')]
                          .every(el=>el.dataset.rendered)''',timeout=25000)
                    images=await page.evaluate('''async () => {
                       const all=[...document.querySelectorAll('#draft img')];
                       for (const item of all) item.loading='eager';
                       await Promise.allSettled(all.map(item=>item.decode()));
                       return {count:all.length,loaded:all.filter(x=>x.complete&&x.naturalWidth>0).length};
                    }''')
                    assert images['count']==images['loaded'],(sample,images)
                    counts={}
                    for kind, selector in (
                        ('math','#draft .katex'),('math_unrendered','#draft .math-unrendered'),
                        ('table','#draft table'),('code','#draft pre > code'),
                        ('mermaid','#draft .reader-diagram[data-rendered="true"]'),
                        ('mermaid_unrendered','#draft .reader-diagram[data-rendered="false"]'),
                        ('headings','#draft :is(h1,h2,h3,h4,h5,h6)'),('paragraphs','#draft p')):
                        counts[kind]=await page.locator(selector).count()
                    expected=Counter(kind for kind,_,_ in {(r['kind'],r['start'],r['end']) for r in res
                                                               if r['surface']=='body' and r['status']=='ok'})
                    assert expected['formula_inline']+expected['formula_display']==counts['math'],(sample,expected,counts)
                    assert expected['table']==counts['table'] and expected['code']==counts['code'],(sample,expected,counts)
                    assert expected['mermaid']==counts['mermaid'],(sample,expected,counts)
                    if sample in screenshots:
                        await page.screenshot(path=str(output/(screenshots[sample]+'.png')))
                    if await page.locator('#coverage li a').count():
                        await page.locator('#coverage summary').click()
                        item=page.locator('#coverage li a').first
                        if run['mode']=='ANSWER' and await page.locator('#coverage li').filter(has_text='子问 ·').count():
                            item=page.locator('#coverage li').filter(has_text='子问 ·').first.locator('a').first
                        target=(await item.get_attribute('href'))[1:]
                        await item.click()
                        await page.wait_for_function('(id)=>decodeURIComponent(location.hash.slice(1))===id',arg=target)
                        await page.wait_for_function('(id)=>document.getElementById(id).classList.contains("active")',arg=target)
                    sides=remap['mapping']['links']
                    nav=next((r for r in sides if r['relation_type']=='navigation_only'),None)
                    if not nav and sides:
                        nav=sides[0]
                    if nav:
                        sid, target=nav['source_id'],nav['body_anchor_id']
                        await page.evaluate('''(id)=>{
                            document.querySelector('#sources').open=true;
                            document.getElementById(id).open=true;
                        }''',sid)
                        await page.locator(f'[id="{sid}"] a[href="#{target}"]').click()
                        await page.wait_for_function('(id)=>decodeURIComponent(location.hash.slice(1))===id',arg=target)
                        backlink=page.locator(f'[id="{target}"] a[href="#{sid}"]')
                        await backlink.first.click()
                        await page.wait_for_function('(id)=>decodeURIComponent(location.hash.slice(1))===id && document.getElementById(id).open',arg=sid)
                    expected_image_count=len({(r['start'],r['end']) for r in res
                                              if r['surface']=='body' and r['kind']=='image' and r['status']=='ok'})
                    assert expected_image_count==images['loaded'],(sample,expected_image_count,images,errors)
                    deferred=next((k for k,v in remap['anchors'].items() if v['surface']=='deferred'),None)
                    if deferred:
                        await page.evaluate('(key)=>{location.hash=key}',deferred)
                        await page.wait_for_function('(key)=>document.getElementById(key).open',arg=deferred)
                    assert not errors,(sample,errors)
                    receipt=await asyncio.to_thread(download,API(base,token,'isolated'),sample,output/'downloads')
                    return {'sample_id':sample,'mode':run['mode'],'targets':len(anchors),
                            'fixture_based':run['fixture_based'],'images':images,'rendered':counts,
                            'checked_source_roundtrip':bool(nav),'checked_deferred':bool(deferred),
                            'download':receipt,'screenshot':screenshots.get(sample)}
                finally:
                    await page.close()

        gathered=await asyncio.gather(*(one(sample) for sample in names),return_exceptions=True)
        await browser.close()
    errors={sample:''.join(traceback.format_exception(result)) for sample,result in zip(names,gathered) if isinstance(result,Exception)}
    rows=[r for r in gathered if isinstance(r,dict)]
    report={'browser':'Microsoft Edge','samples':rows,'errors':errors}
    (output/'evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'passed':len(rows),'errors':errors},ensure_ascii=False))
    return int(bool(errors))


if __name__=='__main__':
    raise SystemExit(asyncio.run(verify(Path(sys.argv[1]))))
