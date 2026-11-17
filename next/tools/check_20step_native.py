from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from hashlib import sha256
import json,sys,threading
from urllib.request import urlopen
from tempfile import TemporaryDirectory
from io import BytesIO
from zipfile import ZipFile
from manualreader.server import handler
from manualreader.stages.step05.inference import read,write
from manualreader.steps.step_14_practice import native_question
root=Path(sys.argv[1])
sys.path.insert(0,sys.argv[2])
from objective_grading import grade
def grades():
    number=native_question({'type':'numeric','prompt':'把 1 ms 换成微秒','explanation':'一毫秒等于一千微秒','expected':'0.001','unit':'s','tolerance':'0.000001','unit_aliases':[{'unit':'ms','multiplier':'0.001'},{'unit':'us','multiplier':'0.000001'}]})
    fix={'type':'numeric','provenance':'验收测试夹具，使用线上原生判分器','correct':grade(number,'1000 us'),'inside_tolerance':grade(number,'1000.5 us'),'wrong':grade(number,'1100 us')}
    assert fix['correct']['correct'] and fix['inside_tolerance']['correct'] and not fix['wrong']['correct']
    rows=[fix]
    for file in sorted((root/'jobs').glob('*/quiz-candidates.json')):
        for row in read(file):
            if row['type']=='ordering':
                q=native_question(row)
                correct=grade(q,q['reference_order'])
                incorrect=grade(q,q['reference_order'][::-1])
                assert correct['correct'] and not incorrect['correct']
                rows.append({'type':'ordering','sample':file.parent.name,'id':q['id'],'correct':correct,'wrong':incorrect})
    write(root/'quiz-type-runtime.json',{'rows':rows})
def sdk():
    with TemporaryDirectory() as folder:
        with ThreadingHTTPServer(('127.0.0.1',0),handler(Path(folder),'ephemeral_local_test_key_01234')) as server:
            worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base+'/v1/client-hashes') as r: manifest=json.load(r)
                with urlopen(base+'/v1/client') as r: archive=r.read()
                with ZipFile(BytesIO(archive)) as z:
                    assert z.testzip() is None
                    for file,want in manifest.items():
                        b=z.read(file)
                        assert {'sha256':sha256(b).hexdigest(),'bytes':len(b)}==want
                write(root/'native-download-proof.json',{'official_endpoint':'/v1/client','sdk_entries':len(manifest),'archive_sha256':sha256(archive).hexdigest(),'code_matches_manifest':True,'model_calls':0,'scope':'真实 Next SDK 下载接口的隔离实例；没有恢复页面中已删除的临时下载按钮'})
            finally:
                server.shutdown(); worker.join()
with ThreadPoolExecutor() as pool:
    results=[pool.submit(op) for op in (grades,sdk)]
    for f in results: f.result()
