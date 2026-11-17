"""Download all finished Markdown drafts via the isolated Reader's browser controls."""

from argparse import ArgumentParser
import asyncio
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

from manualreader.artifacts import read_json as read, write_json as write
from playwright.async_api import async_playwright


async def visit(browser,root,sample,url,slots):
    async with slots:
        page=await browser.new_page(accept_downloads=True)
        started=perf_counter()
        result={'sample_id':sample,'url':url}
        try:
            await page.goto(url,wait_until='domcontentloaded',timeout=30000)
            link=page.get_by_role('link',name='下载讲解',exact=True)
            await link.wait_for(timeout=20000)
            async with page.expect_download(timeout=15000) as response:
                await link.click()
            download=await response.value
            body=(await download.path()).read_bytes()
            expected=(root/'jobs'/sample/'body.final.md').read_bytes()
            result.update(filename=download.suggested_filename,bytes=len(body),
                          sha256=sha256(body).hexdigest(),exact_bytes=body==expected)
        except Exception as error:
            result['error']=str(error)
        finally:
            await page.close()
        return {**result,'wall_seconds':round(perf_counter()-started,6)}


async def main(args):
    root=args.run.resolve()
    urls=read(root/'reader-service-ready.json')['samples']
    if any(urlsplit(url).hostname!='127.0.0.1' for url in urls.values()):
        raise ValueError('下载验收仅在本机隔离 Reader 中执行')
    slots=asyncio.Semaphore(args.tabs or len(urls))
    started=perf_counter()
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path=str(args.browser) if args.browser else None)
        try:
            rows=await asyncio.gather(*(visit(browser,root,sample,url,slots) for sample,url in urls.items()))
        finally:
            await browser.close()
    result={'samples':rows,'browser_tabs':args.tabs or len(urls),'total':len(rows),
            'exact_downloads':sum(row.get('exact_bytes') is True for row in rows),
            'wall_seconds':round(perf_counter()-started,6)}
    write(root/'browser-downloads.json',result)
    print({k:v for k,v in result.items() if k!='samples'})
    return int(result['exact_downloads']!=len(rows))


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--browser',type=Path)
    parser.add_argument('--tabs',type=int)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
