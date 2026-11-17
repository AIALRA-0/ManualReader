"""Verify formula, code and image rendering in the Reader's actual Edge page."""

from argparse import ArgumentParser
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from manualreader.stages.step05.inference import read,write
from manualreader.stages.finish.content import code_source


async def run(root):
    links=read(root/'reader-service-ready.json')['samples']
    results={}
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(
            executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
        async def probe(name):
            page=await browser.new_page(viewport={'width':1700,'height':950})
            await page.goto(links[name],wait_until='domcontentloaded')
            await page.locator('#explanation[data-next-presentation-ready]').wait_for(timeout=55000)
            await page.wait_for_timeout(300)
            counts=await page.evaluate("""()=>['#explanation','#translation','#original-reading','#interaction'].map(
                id=>({pane:id,actual_math:document.querySelectorAll(id+' .katex').length,
                    actual_code:document.querySelectorAll(id+' pre code,'+id+' .reader-code').length,
                    math_errors:document.querySelectorAll(id+' [data-math-error]').length}))""")
            findings={'p2_images':await page.locator('img[data-next-resource-body]').count(),'panes':counts,
                      'term_links':await page.locator('#explanation .term-link').count()}
            if name=='07-exam2-normal':
                sections=read(root/'inputs'/name/'source-index.json')['sources']
                actual=next((r for r in sections if code_source(r.get('text',''))),None)
                if not actual:
                    big=read(root/'inputs/07-exam2-intro/source-index.json')['sources']
                    actual=next((r for r in big if code_source(r.get('text',''))),None)
                code=actual['text'] if actual else 'Q = diag([1, 2]);'
                raw=r"\\[\\lambda_1 + \\lambda_2 = 3\\]"
                findings['renderer_fixture']=await page.evaluate("""([raw,code])=>{
                    const stub=document.createElement('div');stub.id='renderer-fixture';
                    document.body.append(stub);
                    ReaderMarkdown.render(stub,raw+'\\n\\n'+String.fromCharCode(96).repeat(3)+
                        'matlab\\n'+code+'\\n'+String.fromCharCode(96).repeat(3));
                    return {source:raw,normalized:ReaderMarkdown.normalizeMathSource(raw),
                      language:stub.querySelector('code')?.className||null};
                }""",[raw,code])
                await page.locator('#renderer-fixture .katex').wait_for(state='attached',timeout=8500)
                findings['renderer_fixture']['rendered_katex']=True
                findings['renderer_fixture']['code_source_id']=actual['source_id'] if actual else None
                await page.locator('#renderer-fixture').screenshot(path=root/'renderer-fixture.png')
                await page.locator('#renderer-fixture').evaluate('node=>node.remove()')
                sequence=await page.locator('img[data-next-resource-body]').evaluate_all("""nodes=>nodes.map(img=>{
                    const id=img.dataset.nextResourceBody;const target=img.closest('article');
                    const explain=target?.querySelector('details[data-resource-id="'+id+'"]');
                    return {id,after:!!(img.compareDocumentPosition(explain)&Node.DOCUMENT_POSITION_FOLLOWING),
                      resource:img.alt,loaded:img.complete&&img.naturalWidth>0,placement:explain?.nextElementSibling?.dataset.segmentId};
                })""")
                findings['actual_images']=sequence
            await page.close()
            return name,findings
        for name,proof in await asyncio.gather(*(probe(n) for n in
                    ('06-paper-normal','07-exam2-normal','05-zen6-intro'))):
            results[name]=proof
        await browser.close()
    write(root/'renderer-check.json',results)
    print({name:{k:v for k,v in entry.items() if k in ('p2_images','renderer_fixture','term_links')}
           for name,entry in results.items()})


if __name__=='__main__':
    p=ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    asyncio.run(run(p.parse_args().root))
