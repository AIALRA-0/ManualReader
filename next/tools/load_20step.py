"""Measure browser load on the current isolated Reader without writing reader data."""

import argparse
import asyncio
from pathlib import Path
from time import perf_counter

from playwright.async_api import async_playwright
import psutil

from final_delivery import distribution
from manualreader.artifacts import read_json as read, write_json as write


async def main(args):
    urls=list(read(args.run/'reader-service-ready.json')['samples'].values())
    if not urls or any(not url.startswith('http://127.0.0.1:') for url in urls):
        raise ValueError('需要当前本机 Reader 的实际预览')
    report=[]
    async with async_playwright() as driver:
        browser=await driver.chromium.launch(channel='msedge',headless=True)
        for size in (4,12,24):
            peak=0
            done=False
            async def rss():
                nonlocal peak
                while not done:
                    processes=psutil.Process().children(recursive=True)
                    amount=0
                    for p in processes:
                        try:
                            amount+=p.memory_info().rss
                        except psutil.Error:
                            pass
                    peak=max(peak,amount)
                    await asyncio.sleep(.20)
            async def open_one(url):
                began=perf_counter()
                page=await browser.new_page(viewport={'width':1460,'height':960})
                try:
                    r=await page.goto(url,wait_until='domcontentloaded',timeout=60000)
                    await page.locator('#explanation[data-next-presentation-ready]').wait_for(state='attached',timeout=45000)
                    images=await page.locator('#explanation img').count()
                    resources=await page.locator('#explanation .reader-resource-details').count()
                    return {'http':r.status,'seconds':perf_counter()-began,'media':images+resources}
                except Exception as e:
                    return {'seconds':perf_counter()-began,'error':str(e)}
                finally:
                    await page.close()
            watcher=asyncio.create_task(rss())
            t=perf_counter()
            rows=await asyncio.gather(*(open_one(urls[i%len(urls)]) for i in range(size)))
            done=True
            await watcher
            report.append({'parallel_readers':size,'wall_seconds':perf_counter()-t,
                           'memory_peak_process_tree_bytes':peak,'latency':distribution(r['seconds'] for r in rows),
                           'opened':sum(r.get('http')==200 for r in rows),'rows':rows})
        await browser.close()
    write(args.run/'browser-load-20step.json',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    asyncio.run(main(parser.parse_args()))
