"""检索缓存只保存各页自己的已打开正文，查询与网页摘要不冒充来源。"""
from pathlib import Path
import json,sys,tempfile,time,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_source_cache import SourceCache
from reader_web_sources import opened,pieces

class SourceCacheTests(unittest.TestCase):
    def test_separator_without_leading_newline_keeps_urls_with_their_own_text(self):
        first='资料甲 (https://example.com/a)\nSource: open({"ref_id":"a"})\nL1: '+('甲页事实与动作 '*15)
        second='资料乙 (https://example.com/b)\nSource: open({"ref_id":"b"})\nL1: '+('乙页独立事实 '*15)
        values=[opened(p)for p in pieces(first+'-'*80+'\n'+second)]
        pages=[v for v in values if v]
        self.assertEqual([(p['url'],p['lines'][0][1][:2])for p in pages],[('https://example.com/a','甲页'),('https://example.com/b','乙页')])
    def test_collect_uses_only_the_cited_page_and_preserves_user_cache_file_until_save(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);job=root/'job';job.mkdir()
            text='搜索摘要 (https://example.com/search)\nPublished: today\n摘要内容'+'-'*80+'\n'+'实际来源 (https://example.com/source)\nSource: open({"ref_id":"source"})\nL1: '+('来自实际页面的完整机制和操作 '*10)
            request={'input':[{'type':'custom_tool_call','call_id':'a','input':'text(await tools.web__run({open:[]}))'},{'type':'custom_tool_call_output','call_id':'a','output':[{'type':'input_text','text':text}]}]}
            (job/'sent-1.json').write_text(json.dumps(request),'utf8');(job/'cache-citations.json').write_text(json.dumps(['https://example.com/source']),'utf8')
            cache=SourceCache(root/'cache.json');result=cache.collect([job])
            self.assertEqual(result['added'],1);self.assertEqual(cache.read()[0]['url'],'https://example.com/source');self.assertIn('实际页面',cache.read()[0]['text'])
    def test_one_distinctive_name_can_find_a_verified_source(self):
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp)/'cache.json';path.write_text(json.dumps({'version':3,'fragments':[{'url':'https://example.com/cpu','text':'Skymont is a compact CPU core design.','observed_at':time.time(),'used_in_completed_generation':True,'line_start':1,'line_end':1}]}),'utf8')
            result=SourceCache(path).select({'questions':[{'text':'Skymont'}]})
            self.assertEqual(result['cache_hits'],1)
    def test_generic_one_word_does_not_match_every_page(self):
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp)/'cache.json';path.write_text(json.dumps({'version':3,'fragments':[{'url':'https://example.com/'+str(i),'text':'Memory architecture common page '+str(i),'observed_at':time.time(),'used_in_completed_generation':True,'line_start':1,'line_end':1}for i in range(10)]}),'utf8')
            self.assertEqual(SourceCache(path).select({'questions':[{'text':'architecture'}]})['cache_hits'],0)
    def test_old_unverified_cache_does_not_change_original_data(self):
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp)/'cache.json';text=json.dumps({'version':2,'fragments':[{'text':'旧片段','url':'https://example.com','observed_at':time.time(),'used_in_completed_generation':True}]});path.write_text(text,'utf8')
            self.assertEqual(SourceCache(path).read(),[]);self.assertEqual(path.read_text('utf8'),text)
if __name__=='__main__':unittest.main()
