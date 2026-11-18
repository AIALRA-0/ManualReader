"""在本机或 VPS 上转写短语音，录音只留在请求内存中。"""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_SECONDS = 45


def backend_url():
    return os.environ.get('MANUAL_READER_SPEECH_URL', '').rstrip('/')


def proxy(path, data=None):
    base = backend_url()
    if not base:
        return {'enabled': False, 'message': '本机语音服务尚未启用'}
    from urllib.parse import urlsplit
    if urlsplit(base).hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('离线语音服务必须运行在本机回环地址')
    body = None if data is None else json.dumps(data).encode()
    request = Request(base + path, data=body, headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=35 if data else 2) as response:
            return json.load(response)
    except HTTPError as error:
        try: message = json.load(error).get('error')
        except (ValueError, OSError): message = '语音服务暂时忙，请保留录音后重试'
        raise ValueError(message) from error
    except (URLError, TimeoutError) as error:
        if data is None:return {'enabled': False, 'message': '本机语音服务暂未就绪'}
        raise ValueError('语音服务暂时无法连接，录音仍保留在当前页面，可以重试') from error


class Engine:
    def __init__(self, model_dir, threads=2):
        import sherpa_onnx
        model = Path(model_dir)
        started = time.perf_counter()
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model/'model.int8.onnx'), tokens=str(model/'tokens.txt'),
            num_threads=threads, use_itn=True, language='auto', provider='cpu')
        self.load_seconds = round(time.perf_counter()-started, 3)
        self.lock = threading.Lock()

    def transcribe(self, data):
        import numpy as np
        raw = data.get('audio_base64')
        if not isinstance(raw, str) or len(raw)>MAX_AUDIO_BYTES*4//3+8:
            raise ValueError('一次录音最多 45 秒、4 MB，请分段提问')
        try: audio = base64.b64decode(raw, validate=True)
        except ValueError as error: raise ValueError('录音数据格式不正确') from error
        if not audio or len(audio)>MAX_AUDIO_BYTES:raise ValueError('录音为空或超过 4 MB')
        if not self.lock.acquire(blocking=False):raise ValueError('正在转写另一段录音，请稍后重试')
        started = time.perf_counter()
        try:
            decoded = subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-protocol_whitelist','pipe,crypto,data','-i','pipe:0','-t',str(MAX_SECONDS+1),'-f','f32le','-ac','1','-ar','16000','pipe:1'],
                input=audio, capture_output=True, timeout=12,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if decoded.returncode:raise ValueError('无法读取这段录音，请重新录制')
            samples=np.frombuffer(decoded.stdout,dtype=np.float32)
            duration=len(samples)/16000
            if not .25<=duration<=MAX_SECONDS:raise ValueError('请录制 0.25 至 45 秒的语音')
            if not np.isfinite(samples).all() or np.sqrt(np.mean(samples*samples))<.0008:
                return {'text':'','audio_seconds':round(duration,3),'seconds':round(time.perf_counter()-started,3),'message':'没有听到清楚的语音，请重新录制'}
            stream=self.recognizer.create_stream();stream.accept_waveform(16000,samples)
            self.recognizer.decode_stream(stream)
            return {'text':stream.result.text.strip(),'audio_seconds':round(duration,3),
                    'seconds':round(time.perf_counter()-started,3),'engine':'sensevoice-int8','cloud':False}
        finally:self.lock.release()


def serve(model_dir, port, threads):
    engine=Engine(model_dir,threads)
    class Handler(BaseHTTPRequestHandler):
        def reply(self,value,status=200):
            body=json.dumps(value,ensure_ascii=False).encode();self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.path!='/status':return self.reply({'error':'没有这个语音接口'},404)
            self.reply({'enabled':True,'engine':'sensevoice-int8','cloud':False,'max_seconds':MAX_SECONDS,'load_seconds':engine.load_seconds})
        def do_POST(self):
            if self.path!='/transcribe':return self.reply({'error':'没有这个语音接口'},404)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<MAX_AUDIO_BYTES*4//3+2048:raise ValueError('录音请求过大')
                value=json.loads(self.rfile.read(size))
                if not isinstance(value,dict):raise ValueError('录音请求需要使用对象')
                self.reply(engine.transcribe(value))
            except (ValueError,subprocess.SubprocessError) as error:self.reply({'error':str(error)},400)
        def log_message(self,*args):pass
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--model-dir',required=True);parser.add_argument('--port',type=int,default=18972);parser.add_argument('--threads',type=int,default=2)
    args=parser.parse_args();serve(args.model_dir,args.port,max(1,min(4,args.threads)))
