"""用仓库已有的 Mermaid 绘制文档图，不访问远程绘图服务。"""
from pathlib import Path
import asyncio

REPO=Path(__file__).resolve().parent.parent


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(channel='msedge')
        page=await browser.new_page()
        await page.add_script_tag(path=str(REPO/'reader/vendor/media-vendor/mermaid.min.js'))
        await page.evaluate("""mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'base',
            fontFamily:'Segoe UI, Microsoft YaHei, sans-serif',
            themeVariables:{primaryColor:'#eff4fa',primaryTextColor:'#202d40',primaryBorderColor:'#7089a8',
                lineColor:'#7089a8',secondaryColor:'#f5f7fa',tertiaryColor:'#ffffff',fontSize:'17px'},
            flowchart:{htmlLabels:false,curve:'basis',padding:20,nodeSpacing:24,rankSpacing:38}})""")
        destination=REPO/'docs/assets/readme/next'
        destination.mkdir(parents=True,exist_ok=True)
        for path in sorted((REPO/'docs/diagrams').glob('*.mmd')):
            svg=await page.evaluate("async source=>(await mermaid.render('readmeDiagram',source)).svg",path.read_text('utf8'))
            assert 'foreignObject' not in svg
            name={'workflow':'章节生成的步骤与依赖 / Chapter workflow',
                  'source-relations':'原文译文与讲解关联 / Source relationships',
                  'permissions':'项目文件权限 / Project permissions'}[path.stem]
            position=svg.index('>')+1
            svg=svg[:position]+'<title>'+name+'</title><rect width="100%" height="100%" fill="#fff"/>'+svg[position:]
            (destination/(path.stem+'.svg')).write_text(svg,'utf8')
            print(path.stem,flush=True)
        await browser.close()


if __name__=='__main__':
    asyncio.run(main())
