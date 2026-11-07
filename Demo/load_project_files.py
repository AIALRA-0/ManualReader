"""创建独立的文件查看演示，所有内容均在本机生成，不调用模型。"""
from pathlib import Path
from urllib.request import Request,urlopen
import argparse,base64,json
from load_walkthrough import load as load_reading

def load(base):
    result=load_reading(base);identity=result['project_id'];session=json.load(urlopen(base+'/api/session'))
    headers={'X-Reader-Pin':session['pin'],'X-Reader-Material':identity,'Content-Type':'application/json'}
    files={'.editorconfig':b'root = true\n[*]\ncharset = utf-8\n',
           'src/processor.py':b'def process(values):\n    total = sum(values)\n    return total / len(values)\n',
           'docs/data-flow.md':'# 数据怎样通过三个步骤\n\n先接收输入，再计算总和，最后求平均值；\n\n| 输入 | 总和 | 平均值 |\n| --- | --- | --- |\n| 2、4、6 | 12 | 4 |'.encode(),
           'data/inputs.csv':'名称,数值\n第一项,2\n第二项,4\n第三项,6\n'.encode(),
           'config/project.json':b'{"name":"Project Files Walkthrough","width":4}\n'}
    import fitz
    pdf=fitz.open()
    for title,text in [('INPUT','2, 4, 6'),('PROCESS','2 + 4 + 6 = 12'),('OUTPUT','12 / 3 = 4')]:
        page=pdf.new_page(width=620,height=440);page.insert_text((48,68),title,fontsize=28);page.draw_rect(fitz.Rect(48,110,570,350),color=(.5,.6,.7));page.insert_text((80,240),text,fontsize=28)
    files['docs/data-flow.pdf']=pdf.tobytes();pdf.close()
    for name,raw in files.items():
        request=Request(base+'/api/project-files/file',headers=headers,data=json.dumps({'path':name,'content_base64':base64.b64encode(raw).decode()}).encode())
        with urlopen(request)as response:json.load(response)
    return {**result,'files':len(files)}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--base',default='http://127.0.0.1:8939');args=parser.parse_args();print(json.dumps(load(args.base),ensure_ascii=False))
