from pathlib import Path
from unittest.mock import patch
import sys,tempfile,unittest,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_contract import writing_guide
from reader_bridge import Bridge
from reader_quality import public_rules
from reader_execute import command,build_prompt,build_lesson_prompt,source_images
from reader_generation import answer_evidence_sources,validate_plan
from reader_prompts import NAMING_RULE,WRITING_VERSION,WRITER_INSTRUCTIONS

class SearchContract(unittest.TestCase):
    def test_published_rules_and_agent_guide_use_the_same_search_contract(self):
        guide=writing_guide();rules=public_rules()
        self.assertEqual(guide['version'],WRITING_VERSION)
        self.assertEqual(rules['qa']['naming'],NAMING_RULE)
        self.assertEqual(guide['instructions'], [WRITER_INSTRUCTIONS])
        self.assertIn(NAMING_RULE, rules['qa']['writer_system'])

    def test_direct_writers_send_the_full_instructions_once(self):
        task = {'sources': [], 'instructions': [WRITER_INSTRUCTIONS, '本题范围'],
                'result_contract': {'terms': []}}
        prompt = build_prompt(task, {'passages': []})
        self.assertEqual(json.loads(prompt.split('\n\n', 1)[1])['instructions'][0], '本题范围')
        self.assertNotIn('## 3.2', prompt)
        lesson = build_lesson_prompt({**task, 'writing': writing_guide('lesson'),
                                      'translation_queue': {'current_source_ids': [], 'units': ['other-chapter']},
                                      'result_contract': {'translations': [], 'terms': [], 'explanation': {'id': 'lesson'}}})
        self.assertNotIn('## 3.2', lesson)
        self.assertNotIn('"writing":', lesson)
        lesson_input = json.loads(lesson.split('\n\n', 1)[1])
        self.assertNotIn('translation_queue', lesson_input)
        self.assertNotIn('result_contract', lesson_input)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            command('codex', path, path / 'final.txt', [])
            self.assertEqual((path / 'writer-instructions.txt').read_bytes(), WRITER_INSTRUCTIONS.encode('utf8'))
            command('codex', path, path / 'final.txt', [], system_instructions='只补缺项')
            self.assertEqual((path / 'writer-instructions.txt').read_text('utf8'), '只补缺项')

    def test_writer_keeps_live_search_and_normal_service(self):
        with tempfile.TemporaryDirectory()as folder:
            args=command('codex',folder,Path(folder)/'final.txt',[])
            self.assertIn('web_search="live"',args)
            self.assertIn('service_tier="default"',args)
            self.assertIn('model_reasoning_effort="high"',args)
            self.assertEqual(args[args.index('-m')+1],'gpt-6.1-sol')
            self.assertNotIn('web_search', [args[i+1]for i,x in enumerate(args)if x=='--disable'])

    def test_prepared_answer_prompt_has_json_after_the_first_line(self):
        bridge = object.__new__(Bridge)
        bridge.base = 'http://127.0.0.1:8939'
        bridge.remote = False
        bridge.headers = {'X-Reader-Material': 'demo'}
        manifest = {'id': 'run-demo', 'question_count': 1,
                    'groups': [{'id': '1', 'input_url': '/group'}],
                    'reference_url': '/reference'}
        payloads = {'/api/runs': manifest, '/group': {'questions': [{'id': 'q1'}]}, '/reference': {}}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            bridge, 'request', side_effect=lambda path, data=None: payloads[path]
        ):
            bridge.prepare(folder, observe=False)
            prompt = (Path(folder) / 'prompt-1.txt').read_text('utf8')
            header, payload = prompt.split('\n', 1)
            self.assertIn('你用 Sol high 回答本组问题', header)
            self.assertEqual(json.loads(payload)['questions'], [{'id': 'q1'}])

    def test_run_can_check_citations_without_downloading_the_full_reference(self):
        bridge = object.__new__(Bridge)
        bridge.base = 'http://127.0.0.1:8939'
        bridge.remote = False
        bridge.headers = {'X-Reader-Material': 'demo'}
        group = {'questions': [{'id':'q1'}], 'sources':[{'id':'src1','excerpt':'提问所在原句'}],
                 'reference_blocks':[{'id':'src2','text':'相关原件的完整句子'}]}
        manifest = {'id':'run-demo','material_id':'demo','question_count':1,'reference_mode':'group_snapshot',
                    'groups':[{'id':'1','input_url':'/group'}],'reference_url':'/reference'}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            bridge, 'request', side_effect=lambda path, data=None: {'/api/runs':manifest,'/group':group}[path]
        ):
            bridge.prepare(folder, observe=False)
            saved = json.loads((Path(folder) / 'reference.json').read_text('utf8'))
            self.assertEqual(saved['passages'], group['reference_blocks'])
        plan = {'target':'本题','direct_answer':'直接回答','order':['u1'],'units':[
            {'id':'u1','say':'解释原件','needs':[],'source_refs':[{'source_id':'src2','quote':'原件的完整句子'}]}]}
        sources = answer_evidence_sources(group,[{'id':'other','text':'整篇中不在任务内的文字'}])
        self.assertEqual(validate_plan(plan,sources),plan)
        plan['units'][0]['source_refs'][0] = {'source_id':'other','quote':'整篇中不在任务内的文字'}
        with self.assertRaisesRegex(ValueError,'没有对应到输入原句'):
            validate_plan(plan,sources)

    def test_uses_available_context_figure_without_blocking_on_missing_optional_figure(self):
        from email.message import Message
        from io import BytesIO
        bridge = object.__new__(Bridge)
        bridge.base = 'http://127.0.0.1:8939'
        bridge.remote = False
        bridge.headers = {}
        payload = {'sources':[], 'reference_blocks':[{'src':'https://example.org/missing.png'},
                                                     {'src':'https://example.org/figure.png'}]}
        response = BytesIO(b'available original figure')
        response.headers = Message()
        response.headers['Content-Type'] = 'image/png'
        with tempfile.TemporaryDirectory() as folder, patch('reader_execute.urlopen',side_effect=[OSError(),response]) as open_image:
            files = source_images(bridge,payload,Path(folder))
            self.assertEqual([f.read_bytes() for f in files],[b'available original figure'])
            self.assertEqual([call.kwargs['timeout'] for call in open_image.call_args_list],[8,8])

if __name__=='__main__':unittest.main()
