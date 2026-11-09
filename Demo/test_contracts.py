"""用合成资料检查格式、图像引用和语音边界，不连接官网。"""
import base64,json,sys,unittest,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from reader_questions import answer_body,decorate_question
from reader_media import annotate_questions,ensure_visible_sources,ensure_result_media
from reader_prompts import TEACHING_METHOD,writing_method
from reader_contract import writing_guide,canonical_term
from reader_speech import Engine
from reader_execute import store_generated_assets,source_images

class ReaderContracts(unittest.TestCase):
    def setUp(self):
        self.material={'passages':[{'id':'p1','en':'Original sentence','zh':'对应原句','sentences':[{'id':'s1','en':'Original sentence','zh':'对应原句'}]}]}
        self.question={'id':'q1','passage_id':'p1','sentence_id':'s1','answer':'原文：对应原句\n\n回答：完整解释','text':'是什么'}
    def test_old_answer_has_same_new_display(self):
        row=decorate_question(self.material,self.question)
        self.assertEqual(row['display_answer'],'原文：\n\n对应原句\n\n回答：\n\n完整解释')
    def test_new_answer_does_not_duplicate_headers(self):
        self.assertEqual(answer_body('原文：\n\n对应原句\n\n回答：\n\n完整解释',{'text':'对应原句'}),'完整解释')
    def test_markdown_headers_supported(self):
        self.assertEqual(answer_body('**原文：**\n\n对应原句\n\n**回答：**\n\n完整解释',{'text':'对应原句'}),'完整解释')
    def test_explicit_image_request_gets_actual_source(self):
        packet=annotate_questions({'sources':[{'id':'s1','media_items':[{'src':'/content-files/f/asset/figure.png','source_id':'figure-1','alt':'图 1'}]}],'questions':[{'id':'q1','source_id':'s1','text':'请显示原图解释图中的连线'}]})
        result={'answers':[{'question_id':'q1','text':'解释'}]};changes=ensure_visible_sources(result,packet)
        self.assertEqual(len(changes),1);self.assertIn('![图 1](/content-files/f/asset/figure.png)',result['answers'][0]['text'])
        self.assertEqual(ensure_visible_sources(result,packet),[])
    def test_ordinary_definition_does_not_get_decorative_image(self):
        packet=annotate_questions({'sources':[{'id':'s1','media_items':[{'src':'/figure.png'}]}],'questions':[{'id':'q1','source_id':'s1','text':'缓存是什么意思？'}]})
        result={'answers':[{'question_id':'q1','text':'解释'}]};self.assertEqual(ensure_visible_sources(result,packet),[])
    def test_missing_picture_never_gets_invented_url(self):
        packet=annotate_questions({'sources':[{'id':'s1'}],'questions':[{'id':'q1','source_id':'s1','text':'请看图'}]});result={'answers':[{'question_id':'q1','text':'缺少图 1 原件'}]}
        self.assertEqual(ensure_visible_sources(result,packet),[]);self.assertNotIn('![',result['answers'][0]['text'])
    def test_media_is_enforced_at_api_boundary(self):
        material={'passages':[{'id':'p1','kind':'image','src':'/figure.png','alt':'电路图'}]};state={'rounds':[{'questions':[{'id':'q1','passage_id':'p1','text':'这张图是什么'}]}]};result={'answers':[{'question_id':'q1','text':'解释'}]}
        self.assertTrue(ensure_result_media(material,state,result));self.assertIn('/figure.png',result['answers'][0]['text'])
    def test_prompt_uses_one_writing_call(self):
        self.assertIn('同一次写作',TEACHING_METHOD);self.assertEqual(writing_guide()['instructions'].count(writing_method('answer')),1);self.assertEqual(writing_guide('lesson')['instructions'].count(writing_method('lesson')),1)
    def test_visual_rule_is_shared_by_guide_and_both_writers(self):
        from reader_prompts import VISUAL_METHOD,WRITING_VERSION
        for role in ('answer','lesson'):
            guide=writing_guide(role)
            self.assertEqual(guide['version'],WRITING_VERSION)
            self.assertEqual(guide['instructions'].count(writing_method(role)),1)
            self.assertEqual(writing_method(role).count(VISUAL_METHOD),1)
        self.assertIn('Mermaid',VISUAL_METHOD)

    def test_capitalization_and_chinese_english_order(self):
        self.assertEqual(canonical_term('矩形剖分（rectangle dissection）'),'矩形剖分（Rectangle Dissection）')
    def test_ordinary_pdf_text_does_not_attach_the_whole_page(self):
        payload={'sources':[{'id':'s1','source_kind':'text','media_items':[{'src':'/page/one.png'}]}],'questions':[{'source_id':'s1','text':'这个词是什么意思'}]}
        self.assertEqual(source_images(None,payload,ROOT),[])
    def test_generated_image_upload_uses_real_file_and_strips_metadata(self):
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo
        class Bridge:
            def request(self,path,data):
                self.path,self.data=path,data
                return {'original_url':'/content-files/synthetic/original'}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            folder=Path(temporary);image=Image.new('RGB',(32,32),'white');meta=PngInfo();meta.add_text('private-note','not public');image.save(folder/'figure.png',pnginfo=meta)
            result={'answers':[{'question_id':'q1','text':'解释'}],'generated_assets':[{'path':'figure.png','question_ids':['q1'],'alt':'结构图'}]};bridge=Bridge()
            self.assertEqual(store_generated_assets(bridge,result,folder),[])
            self.assertEqual(bridge.path,'/api/content/files');self.assertIn('/content-files/synthetic/original',result['answers'][0]['text'])
            self.assertNotIn(b'private-note',base64.b64decode(bridge.data['content_base64']))
            result['generated_assets'][0]['path']='../outside.png'
            self.assertTrue(store_generated_assets(bridge,result,folder))

if __name__=='__main__':unittest.main(verbosity=2)
