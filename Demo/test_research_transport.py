"""验证搜索策略保留原文范围，断行与中文分片不会丢掉用量回执。"""
from pathlib import Path
import json,sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_research_transport import prepare,events
class ResearchTransportTests(unittest.TestCase):
    def send(self,commands,path='/alpha/search'):
        raw=json.dumps({'id':'task','input':[{'content':'原始问题保留'}],'commands':commands}).encode()
        result,changes=prepare(path,raw)
        self.assertEqual(json.loads(result)['input'],json.loads(raw)['input'])
        return json.loads(result)['commands'],changes
    def test_discovery_search_returns_short_candidates(self):
        commands,changes=self.send({'search_query':[{'q':'事实'}],'response_length':'long'})
        self.assertEqual(commands['response_length'],'short');self.assertTrue(changes)
    def test_four_queries_keep_the_supported_minimum_response_size(self):
        commands,changes=self.send({'search_query':[{'q':str(i)}for i in range(4)],'response_length':'long'})
        self.assertEqual(commands['response_length'],'medium')
    def test_open_find_images_and_combined_requests_keep_their_range(self):
        for commands in [{'open':[{'ref_id':'url','lineno':200}],'response_length':'long'},{'find':[{'ref_id':'url','pattern':'证据'}],'response_length':'long'},{'screenshot':[{'ref_id':'pdf','pageno':3}]},{'search_query':[{'q':'事实'}],'open':[{'ref_id':'url'}],'response_length':'long'}]:
            actual,changes=self.send(commands);self.assertEqual(actual,commands);self.assertEqual(changes,{})
    def test_known_unsupported_parameter_is_removed_without_losing_start_line(self):
        actual,changes=self.send({'open':[{'ref_id':'url','lineno':30,'num_lines':20}],'response_length':'long'})
        self.assertEqual(actual['open'],[{'ref_id':'url','lineno':30}]);self.assertEqual(changes['unsupported_num_lines_removed'],1)
    def test_other_routes_and_disabled_policy_are_untouched(self):
        raw=b'{"commands":{"search_query":[],"response_length":"long"}}'
        self.assertEqual(prepare('/alpha/image',raw),(raw,{}));self.assertEqual(prepare('/alpha/search',raw,False),(raw,{}))
    def test_sse_final_receipt_without_newline(self):
        raw='data: '+json.dumps({'type':'response.completed','text':'中文回执'},ensure_ascii=False)
        chunks=[raw.encode()[i:i+3]for i in range(0,len(raw.encode()),3)]
        self.assertEqual(list(events(chunks)),[{'type':'response.completed','text':'中文回执'}])
    def test_sse_ignores_incomplete_json_and_comments(self):
        self.assertEqual(list(events([b': ping\n\ndata: {broken}\ndata: {"type":"ok"}\n\n'])),[{'type':'ok'}])
if __name__=='__main__':unittest.main()
