"""Measure concurrent real Reader page loads against two or more isolated materials."""

import argparse
import asyncio
from math import ceil
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from manualreader.artifacts import write_json


def percentile(values, share):
    if not values:
        return None
    return round(sorted(values)[ceil(len(values)*share)-1],3)


async def measure(browser, urls, readers):
    began=perf_counter()

    async def one(index):
        url=urls[index%len(urls)]
        start=perf_counter()
        page=await browser.new_page(viewport={'width':1440,'height':920})
        row={'url':url,'page_errors':[]}
        page.on('pageerror',lambda error:row['page_errors'].append(str(error)))
        try:
            response=await page.goto(url,wait_until='domcontentloaded',timeout=90000)
            await page.locator('#explanation[data-next-presentation-ready="true"]').wait_for(timeout=90000)
            row.update(status=response.status,resource_details=await page.locator('details.reader-resource-details').count(),
                       first_view_seconds=perf_counter()-start)
        except Exception as error:
            row['error']=str(error)
        finally:
            await page.close()
        return row

    rows=await asyncio.gather(*(one(index) for index in range(readers)))
    good=[row for row in rows if row.get('status')==200 and row.get('resource_details',0)>0 and not row['page_errors']]
    values=[row['first_view_seconds'] for row in good]
    return {'readers':readers,'passed':len(good),'p50_seconds':percentile(values,.5),
            'p95_seconds':percentile(values,.95),'wall_seconds':round(perf_counter()-began,3),'results':rows}


async def run(urls, out):
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(channel='msedge')
        try:
            rounds=[await measure(browser,urls,concurrency) for concurrency in (4,12,24)]
        finally:
            await browser.close()
    write_json(out,{'rounds':rounds})
    for row in rounds:
        print({k:v for k,v in row.items() if k!='results'},flush=True)
    return int(any(row['passed']!=row['readers'] for row in rounds))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('urls',nargs='+')
    args=parser.parse_args()
    if any(urlsplit(url).scheme!='http' or urlsplit(url).hostname!='127.0.0.1' for url in args.urls):
        parser.error('压测仅接受隔离 Reader 的本机地址')
    return asyncio.run(run(args.urls,args.output))


if __name__=='__main__':
    raise SystemExit(main())
