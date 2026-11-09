"""页面与后台分开运行时，接口授权、错误响应和流式内容仍按原协议传递。"""
import gzip,http.client,sys,threading,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_web_worker import needs_worker,forward

class WebWorker(unittest.TestCase):
    def test_read_requests_stay_independent_and_mutations_use_worker(self):
        for url in('/api/material?material=x','/api/progress','/api/settings','/api/library/tree?view=names','/api/coverage?summary=true','/static/app.js'):
            self.assertFalse(needs_worker('GET',url),url)
        for url in('/api/content/presentation','/api/progress','/machine/v1/projects/x/result'):
            self.assertTrue(needs_worker('POST',url),url)
        for url in('/api/work?mode=lesson','/api/learning','/api/project-files/events','/api/library/tree','/machine/v1/guide'):
            self.assertTrue(needs_worker('GET',url),url)

    def test_authorization_body_compression_and_error_status_are_preserved(self):
        observed=[];payload=gzip.compress(b'full response')
        class Worker(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                observed.append((self.path,dict(self.headers),self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(403);self.send_header('Content-Encoding','gzip');self.send_header('Content-Length',str(len(payload)));self.send_header('X-Test','unchanged');self.end_headers();self.wfile.write(payload)
        worker=ThreadingHTTPServer(('127.0.0.1',0),Worker)
        class Web(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):forward(self,worker.server_port)
        web=ThreadingHTTPServer(('127.0.0.1',0),Web)
        threads=[threading.Thread(target=s.serve_forever)for s in(worker,web)]
        for t in threads:t.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',web.server_port,timeout=3)
            conn.request('POST','/api/result?material=project-test',b'{"question_id":"q1"}',{'Authorization':'Bearer test-only','X-Reader-Material':'project-test','Origin':'https://reader.example','Cookie':'test=1'})
            response=conn.getresponse();self.assertEqual(response.status,403);self.assertEqual(response.getheader('X-Test'),'unchanged');self.assertEqual(gzip.decompress(response.read()),b'full response');conn.close()
            path,headers,body=observed[0]
            self.assertEqual(path,'/api/result?material=project-test');self.assertEqual(body,b'{"question_id":"q1"}')
            for key,value in [('Authorization','Bearer test-only'),('X-Reader-Material','project-test'),('Origin','https://reader.example'),('Cookie','test=1')]:self.assertEqual(headers[key],value)
        finally:
            for s in(web,worker):s.shutdown();s.server_close()
            for t in threads:t.join(3)

    def test_event_stream_delivers_before_worker_closes(self):
        release=threading.Event()
        class Worker(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers();self.wfile.write(b'data: ready\n\n');self.wfile.flush();release.wait(3)
        worker=ThreadingHTTPServer(('127.0.0.1',0),Worker)
        class Web(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):forward(self,worker.server_port)
        web=ThreadingHTTPServer(('127.0.0.1',0),Web)
        threads=[threading.Thread(target=s.serve_forever)for s in(worker,web)]
        for t in threads:t.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',web.server_port,timeout=2);conn.request('GET','/api/project-files/events')
            response=conn.getresponse();self.assertEqual(response.read(13),b'data: ready\n\n');release.set();conn.close()
        finally:
            release.set()
            for s in(web,worker):s.shutdown();s.server_close()
            for t in threads:t.join(3)

if __name__=='__main__':unittest.main()
