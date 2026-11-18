"""使用当前 Next 界面在回环地址启动 Reader，学习数据保存在独立实例目录。"""
from argparse import ArgumentParser
from pathlib import Path
import os
import sys
from tempfile import TemporaryDirectory

REPO=Path(__file__).resolve().parent.parent
READER=REPO/'reader'
sys.path[:0]=[str(READER),str(REPO/'next/src')]


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=int(os.environ.get('MANUAL_READER_PORT','8939')))
    parser.add_argument('--data',type=Path,default=REPO/'instance')
    args=parser.parse_args()
    data=args.data.resolve()
    data.mkdir(parents=True,exist_ok=True)
    defaults={'DB':data/'reader.sqlite3','LIBRARY':data/'library',
              'SETTINGS':data/'reader-settings.json','PIN_FILE':data/'connection-pin.txt',
              'SECURITY':data/'security','MATERIAL':READER/'material.json'}
    for key,value in defaults.items():
        os.environ.setdefault('MANUAL_READER_'+key,str(value))
    os.environ['MANUAL_READER_PORT']=str(args.port)
    from manualreader.production import configure_static
    from manualreader.stepfinal_server import server_handler
    with TemporaryDirectory(prefix='reader-ui-',dir=data) as temporary:
        static=Path(temporary)/'static'
        configure_static(READER,static)
        os.environ['MANUAL_READER_STATIC']=str(static)
        import reader_server as backend
        backend.core.read_state()
        script=(static/'reader-postprocess-preview.js').read_bytes()
        handler=server_handler(backend,script,production_runs=data/'next-runs')
        with backend.ReaderHTTPServer(('127.0.0.1',args.port),handler) as server:
            print('Reader http://127.0.0.1:'+str(server.server_port),flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__=='__main__':
    main()
