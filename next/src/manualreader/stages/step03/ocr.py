"""Read a locally verified scan with an optional installed OCR executable."""

from base64 import b64decode
import csv
from io import StringIO
import shutil
import subprocess
from tempfile import TemporaryDirectory
from pathlib import Path


def extract_words(tsv):
    try:
        words=[r for r in csv.DictReader(StringIO(tsv),delimiter='\t') if r.get('text','').strip()]
        return [{'text':r['text'],'confidence':float(r['conf']),
                 'bbox':[int(r[k]) for k in ('left','top','width','height')]} for r in words]
    except (ValueError,KeyError,TypeError):
        return []


def scan_text(sent, *, run=subprocess.run, executable=None):
    executable=executable or shutil.which('tesseract')
    if not executable:
        return {'status':'visual_only','reason':'本机没有可用的 OCR 工具','attempts':[]}
    raw=b64decode(sent['content']['image_url'].split(',',1)[1],validate=True)
    texts=[]
    attempts=[]
    with TemporaryDirectory() as home:
        file=Path(home)/'original.png'
        file.write_bytes(raw)
        for mode in ('6','11'):
            try:
                result=run([executable,str(file),'stdout','--psm',mode,'tsv'],capture_output=True,
                           encoding='utf-8',errors='replace',timeout=30,check=False,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                words=extract_words(result.stdout) if result.returncode==0 else []
            except (OSError, subprocess.TimeoutExpired):
                words=[]
            accepted=bool(words) and all(word['confidence']>=88 for word in words)
            attempts.append({'method':f'tesseract_psm_{mode}','word_count':len(words),'usable':accepted})
            texts.append(words if accepted else [])
    if texts[0] and [row['text'] for row in texts[0]]==[row['text'] for row in texts[1]]:
        return {'status':'available','text':' '.join(x['text'] for x in texts[0]),'words':texts[0],
                'coordinate_system':'image_pixels_xywh','origin':'local_ocr_agreement','attempts':attempts}
    return {'status':'visual_only','reason':'两种本地识别方式没有得到一致文字','attempts':attempts}
