"""Open four live samples in Edge, capture previews and verify downloads."""

from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile
import json
import os
import sys

from manualreader.client import API
from manualreader.step56_client import run
from playwright.sync_api import sync_playwright


def read(path):
    return json.loads(path.read_bytes())


def main(root):
    base=read(root/'service-ready.json')['base']
    key=(root/'token.txt').read_text('utf8').strip()
    results={}
    for path in (root/'jobs').glob('*/status.json'):
        state=read(path)
        if state['status']!='complete':
            continue
        patches=read(path.with_name('overlays.json'))
        priority=max((3 if p['asset_refs'] else (2 if p['kind']=='formula' else 1) for p in patches),default=0)
        label=path.parent.name
        results.setdefault(state['mode'],[]).append((priority,label,patches))
    assert set(results)=={'NORMAL','INTRO','FINALE','ANSWER'}
    screenshots=root/'browser'
    screenshots.mkdir(exist_ok=True)
    executable=Path(os.environ['ProgramFiles(x86)'])/'Microsoft/Edge/Application/msedge.exe'
    evidence=[]
    api=API(base,key,'isolated')
    with sync_playwright() as browser:
        edge=browser.chromium.launch(executable_path=str(executable),headless=True)
        page=edge.new_page(viewport={'width':1440,'height':980})
        page.goto(base+'/step56')
        page.get_by_label('访问密钥').fill(key)
        page.get_by_role('button',name='打开结果').click()
        page.wait_for_url(base+'/step56')
        assert page.locator('ol li').count()==40
        for mode in ('NORMAL','INTRO','FINALE','ANSWER'):
            priority,label,patches=max(results[mode],key=lambda v:(v[0],v[1]))
            url=base+'/step56/'+label
            page.goto(url,wait_until='networkidle')
            page.locator('article.rendered').wait_for()
            coverage=read(root/'jobs'/label/'step06-goal-question-coverage.json')
            items=page.locator('#coverage-items')
            items.locator('summary').click()
            links=items.locator('a')
            expected=coverage['goal_items']+coverage['question_items']
            assert links.count()==sum(len(item['target_ids']) for item in expected)
            assert all(page.locator(f'[id="{url[1:]}"]').count()==1
                       for url in links.evaluate_all("links=>links.map(link=>link.getAttribute('href'))"))
            target_link=links.first
            if mode=='ANSWER':
                question_link=items.locator('li').filter(has_text='子问 ·').locator('a')
                assert question_link.count()
                target_link=question_link.first
            target=target_link.get_attribute('href')
            target_link.click()
            assert page.evaluate('location.hash')==target
            assert page.locator(f'[id="{target[1:]}"]').is_visible()
            ledger=read(root/'jobs'/label/'step06-merged-mapping.json')
            checked_navigation=False
            if ledger['navigation_only_links']:
                nav=ledger['navigation_only_links'][0]
                sid=nav['source_id']
                page.get_by_text('查看来源和对应位置',exact=True).click()
                row=page.locator(f'[id="{sid}"]')
                row.locator('summary').click()
                nav_target=row.get_by_role('link',name='仅导航 1',exact=True)
                anchor=nav_target.get_attribute('href')
                nav_target.click()
                assert page.evaluate('location.hash')==anchor
                landing=page.locator(f'[id="{anchor[1:]}"]')
                assert landing.is_visible()
                if anchor.startswith('#overlay:') and landing.get_attribute('open') is None:
                    landing.locator('summary').click()
                landing.get_by_role('link',name=f'{sid}（仅导航）',exact=True).click()
                assert page.evaluate('location.hash')=='#'+sid and row.is_visible()
                checked_navigation=True
            fragment_links=0
            if mode=='ANSWER':
                fragments=page.locator('#question-fragments')
                fragments.locator('summary').click()
                raw=fragments.locator('a')
                fragment_links=raw.count()
                assert fragment_links==sum(len(r['target_ids']) for r in coverage['question_raw_units'])
                if fragment_links:
                    fragment=raw.first.get_attribute('href')
                    raw.first.click()
                    assert page.evaluate('location.hash')==fragment
                    assert page.locator(f'[id="{fragment[1:]}"]').is_visible()
            shown=page.locator('details.overlay').count()
            assert shown==len(patches)
            response=run(api,label,'download',screenshots/'downloads')
            with ZipFile(screenshots/'downloads'/f'{label}.zip') as archive:
                name=read(root/'inputs'/label/'step04-run.json')['output_file']
                assert sha256(archive.read(name)).hexdigest()==read(root/'jobs'/label/'run-metrics.json')['original_sha256']
                assert archive.testzip() is None
                assert json.loads(archive.read('step06-goal-question-coverage.json'))==coverage
                assert json.loads(archive.read('step06-merged-mapping.json'))==ledger
            photos=0
            formula=0
            if priority:
                item=max(patches,key=lambda p:3 if p['asset_refs'] else (2 if p['kind']=='formula' else 1))
                block=page.locator(f'[id="overlay:{item["patch_id"]}"]')
                if block.get_attribute('open') is None:
                    block.locator('summary').click()
                an=block.get_by_role('link',name='查看原稿位置')
                link=an.get_attribute('href')
                assert page.locator(f'[id="{link[1:]}"]').count()==1
                an.click()
                assert page.evaluate('location.hash')==link
                block.scroll_into_view_if_needed()
                for image in block.locator('img').all():
                    image.scroll_into_view_if_needed()
                    page.wait_for_function('''element=>element.complete && element.naturalWidth>0''',
                                           arg=image.element_handle())
                    photos+=1
                formula=int(item['kind']=='formula')
            page.screenshot(path=screenshots/f'{mode}.png',animations='disabled',timeout=15_000)
            evidence.append({'sample_id':label,'mode':mode,'url':url,'overlays_visible':shown,
                             'goal_or_question_links_checked':links.count(),
                             'clicked_target':target,
                             'navigation_only_round_trip':checked_navigation,
                             'raw_question_links_checked':fragment_links,
                             'original_images_loaded':photos,'formula_overlay_opened':formula,
                             'downloaded_sha256':response['sha256']})
    assert any(r['original_images_loaded'] or r['formula_overlay_opened'] for r in evidence)
    (screenshots/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(evidence,ensure_ascii=False))


if __name__=='__main__':
    main(Path(sys.argv[1]))
