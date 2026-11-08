"""用真实本地 HTTP 连接验证末尾回执、客户端断开和上游中断，不调用模型。"""
from pathlib import Path
from unittest.mock import patch
import http.client,json,socket,struct,sys,tempfile,time,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_writer_transport import Gate,measured_usage

SPEC={'model':'gpt-6.1-sol','effort':'high','service_tier':'default'}
def event(value):return ('data: '+json.dumps(value,ensure_ascii=False)+'\n\n').encode()
RECEIPT={'type':'response.completed','response':{'model':SPEC['model'],'status':'completed','service_tier':'default','usage':{'input_tokens':120,'input_tokens_details':{'cached_tokens':32},'output_tokens':25,'output_tokens_details':{'reasoning_tokens':8}}}}
class Response:
    status_code=200
    headers={'Content-Type':'text/event-stream'}
    def __init__(self,disconnect=False,broken=False):self.closed=False;self.disconnect=disconnect;self.broken=broken
    def iter_content(self,chunk_size):
        yield event({'type':'response.output_item.added','item':{'id':'m1','type':'message','role':'assistant','channel':'final'}})
        if self.disconnect:
            for _ in range(10):time.sleep(.01);yield b':'+b'x'*8192+b'\n\n'
        yield event({'type':'response.output_text.delta','item_id':'m1','delta':'完整回答'})
        if self.broken:raise OSError('synthetic upstream break')
        yield event(RECEIPT).rstrip(b'\n')
    def close(self):self.closed=True

class WriterHTTPTests(unittest.TestCase):
    def run_request(self,disconnect=False,broken=False):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        response=Response(disconnect,broken)
        with patch('reader_writer_transport.requests.post',return_value=response):
            gate=Gate(SPEC,'当前任务','只处理当前任务',temp.name,research=False).start()
            try:
                raw=json.dumps({'model':SPEC['model'],'reasoning':{'effort':'high'},'input':[{'role':'user','content':[{'type':'input_text','text':'当前任务'}]}]}).encode()
                client=http.client.HTTPConnection('127.0.0.1',gate.port,timeout=3)
                client.request('POST','/responses',raw,{'Content-Type':'application/json'})
                connection=client.sock
                received=client.getresponse();self.assertEqual(received.status,200)
                if disconnect:
                    connection.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('hh',1,0))
                    received.close();client.close()
                else:received.read();client.close()
                for _ in range(100):
                    if gate.receipts and 'seconds'in gate.receipts[0]:break
                    time.sleep(.01)
                row=gate.receipts[0];draft=Path(temp.name,'partial-output-1.txt').read_text('utf8')
                self.assertEqual(draft,'完整回答');self.assertTrue(response.closed)
                self.assertEqual(json.loads(Path(temp.name,'transport.json').read_text('utf8'))[0]['phase'],row['phase'])
                return row
            finally:gate.stop()
    def test_unterminated_final_sse_line_has_full_usage(self):
        row=self.run_request();self.assertEqual(row['phase'],'complete')
        usage=measured_usage([row],SPEC,'test');self.assertTrue(usage['usage_complete'])
        self.assertEqual(usage['usage']['input_tokens'],120);self.assertEqual(usage['usage']['output_tokens'],25)
    def test_client_disconnect_keeps_draft_and_usage(self):
        row=self.run_request(disconnect=True);self.assertTrue(row.get('client_disconnected'))
        self.assertEqual(row['response']['usage']['input_tokens'],120)
    def test_upstream_break_closes_response_without_fake_completion(self):
        row=self.run_request(broken=True);self.assertEqual(row['phase'],'transport_error')
        self.assertNotIn('response',row);self.assertIsNone(measured_usage([row],SPEC,'test'))
if __name__=='__main__':unittest.main()
