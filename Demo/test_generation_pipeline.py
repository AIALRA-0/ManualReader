"""回归真实生成中出现过的缺漏和格式故障；只用合成资料，不调用模型。"""
from pathlib import Path
import copy,json,sys,tempfile,time,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_generation import normalize_output,validate_plan,source_groups
from reader_writer_format import normalize
from reader_writer_input import compact_packet,restore_output
from reader_writer_patch import apply
from reader_writer_schema import output_schema
from reader_writer_transport import measured_usage,Gate
from reader_source_cache import SourceCache,for_bridge
from reader_sequence import frame_blocks
from objective_grading import calculation

class GenerationRegression(unittest.TestCase):
    def test_moving_first_chapter_preserves_opening_anchor(self):
        current={'role':'current','heading':'这一节要弄明白什么','markdown':'本节问题'}
        previous={'role':'previous','heading':'接着上一节','markdown':'上一节结果'}
        frame={'framing':{'start':[current],'end':[],'map_heading':'阅读流程'},'narrative':{'flow':[]}}
        original=frame_blocks(frame,[])
        self.assertEqual(original[0]['id'],'opening')
        frame['framing']['start']=[previous,current]
        moved=frame_blocks(frame,[],original)
        self.assertEqual([(b['id'],b['markdown'])for b in moved[:2]],[('opening-previous','上一节结果'),('opening','本节问题')])
        self.assertEqual(frame_blocks(frame,[],moved),moved)

    def test_invalid_internal_id_updates_only_internal_references(self):
        old={'answers':[{'question_id':'original-question','parts':[{'id':'ap space','markdown':'原话 ap space'}]}],'quizzes':[{'placement':{'after_part_id':'ap space'}}]}
        new,changes=normalize(old)
        self.assertEqual(new['answers'][0]['parts'][0]['id'],new['quizzes'][0]['placement']['after_part_id'])
        self.assertEqual(new['answers'][0]['question_id'],'original-question')
        self.assertEqual(new['answers'][0]['parts'][0]['markdown'],'原话 ap space')
        self.assertEqual(old['answers'][0]['parts'][0]['id'],'ap space')
        self.assertTrue(changes)

    def test_normalization_is_idempotent_and_avoids_collision(self):
        old={'answers':[{'parts':[{'id':'ap x'},{'id':'ap-x'}]}]}
        first,_=normalize(old);second,changes=normalize(first)
        self.assertEqual(first,second);self.assertFalse(changes)
        self.assertNotEqual(first['answers'][0]['parts'][0]['id'],'ap-x')

    def test_annotated_dependency_uses_real_registered_unit(self):
        old={'plan':{'units':[{'id':'u1','needs':[]},{'id':'u2','needs':['u1：已经解释的动作']} ]}}
        new,_=normalize(old);self.assertEqual(new['plan']['units'][1]['needs'],['u1'])

    def test_unknown_dependency_is_not_silently_removed(self):
        old={'plan':{'units':[{'id':'u1','needs':['missing：不存在']} ]}}
        new,_=normalize(old);self.assertEqual(new,old)

    def test_cycle_still_fails_after_format_repair(self):
        old={'plan':{'target':'解释过程','direct_answer':'说明结果','order':['u1','u2'],'units':[{'id':'u1','say':'第一步','needs':['u2：后一步']},{'id':'u2','say':'第二步','needs':['u1：前一步']}]}}
        new,_=normalize(old)
        with self.assertRaises(ValueError):validate_plan(new['plan'])

    def test_short_aliases_preserve_source_code_and_question_ids(self):
        packet={'sources':[{'id':'actual-source-abc','original':'src1 是代码中的变量'}],'questions':[{'id':'q-original','text':'src1是什么'}]}
        short,mapping=compact_packet(packet)
        self.assertEqual(short['sources'][0]['original'],packet['sources'][0]['original'])
        output=restore_output({'answers':[{'question_id':'q-original','parts':[{'id':'src1','source_ids':['src1'],'markdown':'src1'}]}]},mapping)
        self.assertEqual(output['answers'][0]['parts'][0]['source_ids'],['actual-source-abc'])
        self.assertEqual(output['answers'][0]['parts'][0]['id'],'src1')

    def test_project_file_alias_in_markdown_keeps_page_target_and_code(self):
        original = '[原件](/content-files/doc1/original#page=3)\n![图](source:src1)\n`[示例](/content-files/doc1/original)`'
        result = restore_output({'markdown': original}, {'doc1': 'file-actual', 'src1': 'passage-actual'})
        self.assertEqual(result['markdown'], '[原件](/content-files/file-actual/original#page=3)\n![图](source:passage-actual)\n`[示例](/content-files/doc1/original)`')

    def test_writer_input_drops_only_exact_copies_and_retains_source_ids(self):
        packet={'sources':[{'id':'s','original':'完整原句','excerpt':'原句','translation':'完整原句'}],
                'reference_blocks':[{'id':'s','text':'完整原句','for_source_ids':['s']},
                                    {'id':'neighbor','text':'相关的另一句','for_source_ids':['s']}]}
        packed,aliases=compact_packet(packet)
        self.assertEqual(packed['sources'],[{'id':'src1','original':'完整原句','excerpt':'原句'}])
        self.assertEqual(packed['reference_blocks'],[{'id':'src1','for_source_ids':['src1']},
                                                      {'id':'src2','text':'相关的另一句','for_source_ids':['src1']}])
        self.assertEqual(aliases,{'src1':'s','src2':'neighbor'})
        self.assertEqual(packet['sources'][0]['translation'],'完整原句')

    def test_schema_names_do_not_restrict_body_strings(self):
        schema=output_schema({'mode':'answer','sources':[{'id':'src1'}],'questions':[{'id':'q1'}]})
        part=schema['properties']['answers']['items']['properties']['parts']['items']['properties']
        self.assertIn('pattern',part['id']);self.assertNotIn('enum',part['markdown'])
        self.assertEqual(schema['properties']['answers']['items']['properties']['question_id']['enum'],['q1'])

    def test_one_field_patch_keeps_the_correct_body(self):
        old={'explanation':{'parts':[{'id':'p1','markdown':'正确正文'}]},'quizzes':[{'calculation':'2×3'}]}
        result=apply(old,{'changes':[{'path':'/quizzes/0/calculation','value_json':'"2*3"'}]})
        self.assertEqual(result['explanation'],old['explanation']);self.assertEqual(result['quizzes'][0]['calculation'],'2*3')

    def test_patch_cannot_replace_whole_body(self):
        for path in ['/explanation','/explanation/parts','/answers']:
            with self.assertRaises(ValueError):apply({'explanation':{'parts':[]}}, {'changes':[{'path':path,'value_json':'[]'}]})

    def test_partial_usage_is_a_known_lower_bound(self):
        rows=[{'started_at':1,'seconds':1,'response':{'model':'gpt-6.1-sol','service_tier':'default','usage':{'input_tokens':100,'input_tokens_details':{'cached_tokens':60},'output_tokens':10}}},{'started_at':2,'phase':'transport_error'}]
        report=measured_usage(rows,{'model':'gpt-6.1-sol','effort':'high'},'synthetic')
        self.assertFalse(report['usage_complete']);self.assertEqual(report['usage']['input_tokens'],100)
        self.assertEqual(report['http_requests'],2)

    def test_unknown_image_charge_is_not_zero(self):
        row={'started_at':1,'tool_calls':[{'input':'await tools.image_gen__imagegen({prompt:"synthetic"})'}],'response':{'model':'gpt-6.1-sol','usage':{'input_tokens':1,'output_tokens':1}}}
        report=measured_usage([row],{'model':'gpt-6.1-sol','effort':'high'},'synthetic')
        self.assertIsNone(report['image_tools'][0]['actual_cost_usd'])

    def test_source_groups_keep_non_text_and_file_boundaries(self):
        sources=[{'id':'s1','file_id':'a','original':'短句','kind':'paragraph'},{'id':'s2','file_id':'a','kind':'image','src':'/actual.png'},{'id':'s3','file_id':'b','original':'代码','kind':'code'}]
        self.assertEqual([g['source_ids']for g in source_groups(sources)],[['s1'],['s2'],['s3']])

    def test_arithmetic_checks_every_equality(self):
        self.assertEqual(calculation('结果：2×3=6'),6)
        with self.assertRaises(ValueError):calculation('2*3=6=7')

    def test_cache_ignores_expired_and_uncited_fragments(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'cache.json';path.write_text(json.dumps({'fragments':[{'url':'https://example.com','text':'old','observed_at':0,'used_in_completed_generation':True},{'url':'https://example.com','text':'uncited','observed_at':time.time(),'used_in_completed_generation':False}]}),'utf8')
            self.assertEqual(SourceCache(path).read(),[])

    def test_gate_removes_maintenance_messages_keeps_real_tool_history(self):
        gate=Gate({'model':'gpt-6.1-sol','effort':'high'},'this task','writer','.',False,False)
        value={'model':'gpt-6.1-sol','reasoning':{'effort':'high'},'input':[{'role':'developer','content':[{'type':'input_text','text':'unrelated maintenance'}]},{'role':'user','content':[{'type':'input_text','text':'this task'}]},{'type':'custom_tool_call_output','call_id':'real','output':'actual evidence'}]}
        sent=gate.prepare(value)
        self.assertNotIn('unrelated maintenance',json.dumps(sent));self.assertEqual(sent['input'][-1]['call_id'],'real')

if __name__=='__main__':unittest.main()
