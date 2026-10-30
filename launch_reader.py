"""启动这一份阅读器；端口已被别的程序占用时换一个端口，保留对方进程。"""
from pathlib import Path
import argparse,json,os,socket,subprocess,sys,time,urllib.request,webbrowser
ROOT=Path(__file__).resolve().parent

def session(port):
    try:return json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/api/session',timeout=.4))
    except (OSError,ValueError):return None

def launch(preferred=8939):
    for port in range(preferred,preferred+100):
        running=session(port)
        if running and running.get('application')=='codex-manual-reader' and Path(running.get('instance_path','')).resolve()==ROOT:return running['api_base']
        probe=socket.socket()
        try:
            if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            probe.bind(('127.0.0.1',port))
        except OSError:probe.close();continue
        probe.close()
        env={**os.environ,'MANUAL_READER_PORT':str(port),'MANUAL_READER_DB':str(ROOT/'reader.sqlite3'),
             'MANUAL_READER_MATERIAL':str(ROOT/'material.json'),'MANUAL_READER_STATIC':str(ROOT/'static'),
             'MANUAL_READER_LIBRARY':str(ROOT/'library'),'MANUAL_READER_SETTINGS':str(ROOT/'reader-settings.json'),'PYTHONIOENCODING':'utf-8'}
        if (ROOT/'vendor').exists():env['MANUAL_READER_VENDOR']=str(ROOT/'vendor')
        process=subprocess.Popen([sys.executable,str(ROOT/'reader_server.py')],cwd=ROOT,env=env,
                    stdout=(ROOT/'reader-server.stdout.log').open('a',encoding='utf8'),
                    stderr=(ROOT/'reader-server.stderr.log').open('a',encoding='utf8'),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),start_new_session=os.name!='nt')
        for _ in range(100):
            ready=session(port)
            if ready and ready.get('application')=='codex-manual-reader' and Path(ready.get('instance_path','')).resolve()==ROOT:
                (ROOT/'reader-url.txt').write_text(ready['api_base'],'utf8');return ready['api_base']
            if process.poll() is not None:raise RuntimeError('阅读器没有启动，请查看 reader-server.stderr.log')
            time.sleep(.1)
        raise RuntimeError('阅读器仍在启动，请查看 reader-server.stderr.log')
    raise RuntimeError('没有找到空闲端口，请关闭不需要的本地服务后再试')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8939);parser.add_argument('--no-browser',action='store_true');args=parser.parse_args();url=launch(args.port)
    if not args.no_browser:webbrowser.open(url)
    print('阅读器已就绪：'+url)
