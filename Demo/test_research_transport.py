"""验证搜索策略保留原文范围，断行与中文分片不会丢掉用量回执。"""
from pathlib import Path
import json,sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_research_transport import compact_history, prepare,events
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
    def test_open_normalizes_an_integer_line_alias_without_overwriting_a_valid_line(self):
        actual,changes=self.send({'open':[{'ref_id':'a','line':120},{'ref_id':'b','line':30,'lineno':35}]})
        self.assertEqual(actual['open'],[{'ref_id':'a','lineno':120},{'ref_id':'b','lineno':35}])
        self.assertEqual(changes['open_line_aliases_normalized'],2)
    def test_other_routes_and_disabled_policy_are_untouched(self):
        raw=b'{"commands":{"search_query":[],"response_length":"long"}}'
        self.assertEqual(prepare('/alpha/image',raw),(raw,{}));self.assertEqual(prepare('/alpha/search',raw,False),(raw,{}))
    def test_sse_final_receipt_without_newline(self):
        raw='data: '+json.dumps({'type':'response.completed','text':'中文回执'},ensure_ascii=False)
        chunks=[raw.encode()[i:i+3]for i in range(0,len(raw.encode()),3)]
        self.assertEqual(list(events(chunks)),[{'type':'response.completed','text':'中文回执'}])
    def test_sse_ignores_incomplete_json_and_comments(self):
        self.assertEqual(list(events([b': ping\n\ndata: {broken}\ndata: {"type":"ok"}\n\n'])),[{'type':'ok'}])
    def test_recovers_characters_and_keeps_truncation_warning(self):
        source='Primary source (https://example.test/paper)\nSource: open({})\nL1: '+('Exact text and values 123. ' * 16)
        raw='\n'.join(source)
        cut=raw.index('L\n1')
        raw='Warning: truncated output (original token count: 1000)\nTotal output lines: 1000\n\n'+raw[:cut]+'…10 tokens truncated…\n'+raw[cut:]
        rows=[{'type':'custom_tool_call','call_id':'a','input':'text(await tools.web__run({open:[]}))'},
              {'type':'custom_tool_call_output','call_id':'a','output':[{'type':'input_text','text':raw}]}]
        clean=compact_history(rows)[1]['output'][0]['text']
        self.assertIn(source.splitlines()[0],clean)
        self.assertIn(source.splitlines()[-1],clean)
        self.assertIn('…10 tokens truncated…',clean)
        self.assertIn('The tool output was truncated.',clean)

    def test_deduplicates_only_exact_lines_on_the_same_page(self):
        original='Evidence from the numbered line, with its value 17.'
        image={'type':'input_image','image_url':'data:image/png;base64,example'}
        def tool(key,url,line):
            return [{'type':'custom_tool_call','call_id':key,'input':'text(await tools.web__run({find:[]}))'},
                    {'type':'custom_tool_call_output','call_id':key,'output':[{'type':'input_text','text':f'Paper (https://example.test/{url})\nref-{key}\nSource: find({{}})\nL20: {line}\n'},image]}]
        rows=tool('first','a',original)+tool('repeat','a',original)+tool('other','b',original)+tool('changed','a',original.replace('17','29'))
        after=compact_history(rows)
        self.assertEqual(after[1],rows[1])
        self.assertEqual(after[5:],rows[5:])
        self.assertIn('ref-repeat',after[3]['output'][0]['text'])
        self.assertIn('present above: 20',after[3]['output'][0]['text'])
        self.assertNotIn(original,after[3]['output'][0]['text'])
        self.assertEqual(after[3]['output'][1],image)
        self.assertEqual(compact_history(after),after)

if __name__=='__main__':unittest.main()
