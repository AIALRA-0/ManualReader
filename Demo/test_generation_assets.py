"""Exercise the same term and illustration checks the writing client uses."""
import unittest

from reader_execute import result_json
from reader_generation import requires_update
from reader_media import check_visuals, normalize_flowcharts, publish_coordinate_figures, validate_generated_diagrams
from reader_prompts import WRITING_VERSION
from reader_writer_schema import output_schema


def answer_with_terms(term, markdown='根据队首位置取出最早存入的值。'):
    return {
        'writing_version': WRITING_VERSION,
        'plan': {'target': '读出最早的值', 'direct_answer': '从队首读',
                 'units': [{'id': 'u1', 'say': '队首的位置指向最早存入的值。', 'needs': [], 'source_refs': []}],
                 'order': ['u1'], 'stop_when': '读出最早的值'},
        'answers': [{'question_id': 'q1', 'question_version': 1, 'parts': [
            {'id': 'p1', 'unit_ids': ['u1'], 'question_span': '先读哪个值', 'markdown': markdown},
        ]}],
        'quizzes': [{'prompt': '依次写入 7 和 9 后，先读哪个值？', 'type': 'choice', 'options': ['7', '9'],
                     'correct_index': 0, 'explanation': '队首是先写入的 7。',
                     'option_explanations': ['7 最早写入。', '9 在 7 之后写入。'],
                     'question_ids': ['q1'], 'placement': {'kind': 'answer', 'question_id': 'q1', 'after_part_id': 'p1'}}],
        'terms': [term],
    }


class GeneratedAssetTests(unittest.TestCase):
    def validate_answer(self, term, known=()):
        import json
        packet={'questions': [{'id': 'q1', 'text': '先读哪个值', 'version': 1}], 'registered_terms': known}
        return result_json(json.dumps(answer_with_terms(term)), {'question_ids': ['q1']}, packet)

    def test_known_term_does_not_relax_new_term_schema(self):
        items=output_schema({'mode': 'answer', 'questions': [{'id': 'q1'}],
                             'registered_terms': [{'id': 'fifo'}]})['properties']['terms']['items']['anyOf']
        new,reuse=[x['properties'] for x in items]
        self.assertEqual(new['professional_definition']['minLength'],80)
        self.assertEqual(new['intuitive_explanation']['minLength'],100)
        self.assertEqual(reuse['term_id']['enum'],['fifo'])
        self.assertEqual(reuse['professional_definition']['enum'],[''])
        self.assertEqual(reuse['name']['type'],'null')
        self.assertEqual(new['question_ids']['minItems'],1)

    def test_invalid_or_unbound_terms_fail_before_send(self):
        known=[{'id': 'fifo', 'name': '先进先出（First In First Out）', 'definition': '保留旧定义'}]
        with self.assertRaisesRegex(ValueError,'question_ids'):
            self.validate_answer({'term_id': 'fifo', 'question_ids': []},known)
        with self.assertRaisesRegex(ValueError,'question_ids'):
            self.validate_answer({'term_id': 'fifo', 'question_ids': ['another']},known)
        short={'name': '队首（Head）','professional_definition': '首项','intuitive_explanation': '排在最前面',
               'question_ids': ['q1']}
        with self.assertRaisesRegex(ValueError,'术语需要补齐'):
            self.validate_answer(short,known)
        self.assertEqual(self.validate_answer({'term_id': 'fifo','question_ids': ['q1']},known)['terms'][0]['term_id'],'fifo')
        self.assertEqual(known[0]['definition'],'保留旧定义')

    def test_one_paragraph_revision_keeps_the_other_saved_paragraph(self):
        from Demo.test_term_quality import DEFINITION,INTUITION
        known=[{'id':'fifo','name':'先进先出（First In First Out）','version':4,
                'professional_definition':DEFINITION,'intuitive_explanation':INTUITION}]
        term={'term_id':'fifo','action':'revise','expected_version':4,'question_ids':['q1'],
              'intuitive_explanation':INTUITION+' 这样还能看出哪一步比较慢。'}
        for untouched in (None,' \n\t '*80):
            checked=self.validate_answer({**term,'professional_definition':untouched},known)['terms'][0]
            self.assertNotIn('professional_definition',checked)
            self.assertEqual(checked['intuitive_explanation'],term['intuitive_explanation'])
        self.assertEqual(known[0]['professional_definition'],DEFINITION)

    def test_implicit_reuse_requires_matching_content_in_each_answer(self):
        from reader_term_quality import cover_planned_terms
        known=[{'id':'fifo','name':'先进先出（First In First Out）','aliases':['FIFO']},
               {'id':'other','name':'寄存器（Register）'}]
        value={'terms':[{'term_id':'fifo','name':None,'question_ids':['q1','q2'],'aliases':['缓存']},
                        {'term_id':'other','name':None,'question_ids':['q1'],'aliases':[]}],
               'answers':[{'question_id':'q1','parts':[{'markdown':'FIFO 按写入顺序读出。'}]},
                          {'question_id':'q2','parts':[{'markdown':'环形地址回到开头。'}]}]}
        analysis={'mode':'answer','items':[{'task_id':'q1','essential_terms':['FIFO']},{'task_id':'q2'}]}
        self.assertEqual(cover_planned_terms(value,analysis,known),[])
        self.assertEqual(value['terms'],[{'term_id':'fifo','name':known[0]['name'],'question_ids':['q1'],'aliases':['FIFO']}])
        value['terms'].append({'term_id':'fifo','name':'寄存器','question_ids':['q2']})
        self.assertIn('/terms/1/term_id',cover_planned_terms(value,analysis,known)[0])

    def test_reused_term_can_pick_up_a_planned_alias_that_the_answer_explains(self):
        from reader_term_quality import cover_planned_terms
        known=[{'id':'g1','name':'确认或提交（Commitment）','aliases':[]}]
        value={'terms':[{'term_id':'g1','name':None,'question_ids':['q1'],'aliases':['Commit']}],
               'answers':[{'question_id':'q1','parts':[{'markdown':'图中的 Commit 是按程序顺序提交结果。'}]}]}
        analysis={'mode':'answer','items':[{'task_id':'q1','essential_terms':['Commit']}]}
        self.assertEqual(cover_planned_terms(value,analysis,known),[])
        self.assertEqual(value['terms'][0]['aliases'],['Commit'])

    def test_markdown_diagram_and_explicit_fallback(self):
        box='┌──┐\n│A │\n└──┘'
        part={'id': 'first', 'markdown': f'```text\n{box}\n```'}
        with self.assertRaisesRegex(ValueError,'first.*Mermaid'):
            validate_generated_diagrams([part])
        for language in ('ascii','console'):
            validate_generated_diagrams([{**part,'markdown': f'```{language}\n{box}\n```'}])
        validate_generated_diagrams([{**part,'markdown': '```mermaid\nflowchart LR\nA --> B\n```'}])
        validate_generated_diagrams([{**part,'markdown': '```text\nrows=4\nresult=42\n```'}])

    def test_new_mermaid_is_vertical_unless_marked_for_comparison(self):
        plain='```mermaid\nflowchart LR\nA --> B\n```'
        marked=plain.replace('flowchart LR','%% reader-layout: horizontal\nflowchart LR')
        code=plain.replace('mermaid','text')
        self.assertIn('flowchart TD',normalize_flowcharts(plain))
        self.assertEqual(normalize_flowcharts(marked),marked)
        self.assertEqual(normalize_flowcharts(code),code)

    def test_planned_geometry_requires_a_coordinate_image_in_the_right_answer(self):
        plan={'mode':'answer','items':[{'task_id':'q1','visuals':[{'id':'projection','kind':'coordinates','show':'找出接点'}]}]}
        result={'answers':[{'question_id':'q1','parts':[{'id':'p1','markdown':'```mermaid\nflowchart TD\nA --> B\n```'}]}],
                'visual_checks':[{'visual_id':'projection','part_id':'p1'}]}
        with self.assertRaisesRegex(ValueError,'实际坐标图'):
            check_visuals(result,plan)
        result['answers'][0]['parts'][0]['markdown']='![在同一网格找接点](coordinate:projection)'
        with self.assertRaisesRegex(ValueError,'没有坐标数据'):
            check_visuals(result,plan)
        point={'at':[1,4],'label':'p','style':'new'}
        line={'points':[[1,1],[1,4]],'label':'3','style':'guide'}
        result['coordinate_figures']=[{'id':'projection','panels':[{'points':[point],'lines':[line]}]*3}]
        check_visuals(result,plan)
        from unittest.mock import Mock
        from base64 import b64decode
        from PIL import Image
        from io import BytesIO
        bridge=Mock()
        bridge.request.return_value={'original_url':'/content-files/diagram/original'}
        publish_coordinate_figures(bridge,result)
        request=bridge.request.call_args.args[1]
        with Image.open(BytesIO(b64decode(request['content_base64']))) as image:
            self.assertEqual(image.size,(860,1290))
        self.assertIn('/content-files/diagram/original',result['answers'][0]['parts'][0]['markdown'])

    def test_diagram_in_another_question_cannot_fill_a_missing_visual(self):
        plan={'mode':'answer','items':[{'task_id':'q1','visuals':[{'id':'v','kind':'diagram','show':'同一对象前后的状态'}]}]}
        result={'answers':[{'question_id':'q1','parts':[{'id':'p1','markdown':'这里只有文字'}]},
                           {'question_id':'q2','parts':[{'id':'p1','markdown':'```mermaid\nflowchart TD\nA --> B\n```'}]}],
                'visual_checks':[{'visual_id':'v','part_id':'p1'}]}
        with self.assertRaisesRegex(ValueError,'计划里的每幅图'):
            check_visuals(result,plan)
        result['answers'][0]['parts'][0]['markdown']='```mermaid\nflowchart TD\nA[乙线全部] ~~~ B[全部划痕]\n```'
        check_visuals(result,plan)

    def test_planned_coordinate_image_can_attach_only_to_its_owner(self):
        plan={'mode':'answer','items':[{'task_id':'q1','visuals':[{'id':'timing','kind':'diagram','show':'读出时序'}]}]}
        wave='~~~text\n┌──┐\n│A │\n└──┘\n~~~'
        target={'id':'p','markdown':wave+'\n\n核对结果'}
        other={'id':'p','markdown':'别的问题'}
        figure={'id':'timing','panels':[]}
        result={'answers':[{'question_id':'q1','parts':[target]},{'question_id':'q2','parts':[other]}],
                'coordinate_figures':[figure],'visual_checks':[{'visual_id':'timing','part_id':'p'}]}
        check_visuals(result,plan)
        self.assertTrue(target['markdown'].startswith('![读出时序](coordinate:timing)'))
        self.assertIn(wave.replace('~~~text','~~~ascii'),target['markdown'])
        self.assertEqual(other['markdown'],'别的问题')
        validate_generated_diagrams([target])
        check_visuals(result,plan)
        self.assertEqual(target['markdown'].count('(coordinate:timing)'),1)

    def test_log_as_original_requires_a_literal_line_from_the_current_source(self):
        plan={'mode':'answer','items':[{'task_id':'q1','source_ids':['log'],'visuals':[
            {'id':'out','kind':'original','show':'读取实际日志'}]}]}
        line='END amount=42 status=success'
        part={'id':'p','markdown':f'```console\n{line}\n```'}
        result={'answers':[{'question_id':'q1','parts':[part]}],
                'visual_checks':[{'visual_id':'out','part_id':'p'}]}
        check_visuals(result,plan,[{'id':'log','text':'输出如下\n'+line}])
        for sources in ([{'id':'another','text':line}],[{'id':'log','text':line.replace('42','41')}]):
            with self.assertRaisesRegex(ValueError,'经原件核对的日志'):
                check_visuals(result,plan,sources)

    def test_a_coordinate_grid_becomes_a_table_without_losing_its_labels(self):
        plan={'mode':'answer','items':[{'task_id':'q1','visuals':[{'id':'v','kind':'table','show':'逐项核对'}]}]}
        marker='![统计原件](coordinate:v)'
        part={'id':'p','markdown':'```markdown\n'+marker+'\n```\n\n'+marker}
        points=[{'label':label,'at':at} for at,label in (
            ([0,2],'类型'),([1,2],'结果'),([0,1],'原件'),([1,1],'42'),([0,0],'结果来自实际记录'))]
        value={'answers':[{'question_id':'q1','parts':[part]}], 'visual_checks':[{'visual_id':'v','part_id':'p'}],
               'coordinate_figures':[{'id':'v','panels':[{'points':points,'lines':[]}]}]}
        check_visuals(value,plan)
        self.assertEqual(value['coordinate_figures'],[])
        self.assertIn('| 原件 | 42 |',part['markdown'])
        self.assertIn('结果来自实际记录',part['markdown'])
        self.assertEqual(part['markdown'].count(marker),1)
        self.assertTrue(part['markdown'].startswith('```markdown\n'+marker))

    def test_publishes_a_coordinate_image_with_code_words_in_the_caption(self):
        from unittest.mock import Mock
        from reader_media import require_published_figures
        first='![采样后查看 `data_out`](coordinate:sample)'
        literal='`![普通代码](coordinate:sample)`'
        part={'id':'p','markdown':first+'\n\n'+literal}
        panel={'points':[{'at':[0,0],'label':'A','style':'base'}],'lines':[]}
        value={'answers':[{'question_id':'q1','parts':[part]}],
               'coordinate_figures':[{'id':'sample','panels':[panel]}]}
        with self.assertRaisesRegex(ValueError,'尚未绘制'):
            require_published_figures(value)
        bridge=Mock()
        bridge.request.return_value={'original_url':'/content-files/sample/original'}
        publish_coordinate_figures(bridge,value)
        self.assertEqual(part['markdown'],first.replace('coordinate:sample','/content-files/sample/original')+'\n\n'+literal)
        require_published_figures(value)

    def test_running_previous_jobs_keep_their_version(self):
        old='reader-writing-connected-v12-verified-contract'
        self.assertFalse(requires_update({'writing_version': old},{'writing_version': old}))
        self.assertFalse(requires_update({'writing_version': old},{'writing_version': WRITING_VERSION}))
        self.assertTrue(requires_update({'writing_version': WRITING_VERSION},{'writing_version': old}))

    def test_planned_table_needs_a_renderable_table_in_the_matching_part(self):
        plan = {'mode':'answer','items':[{'task_id':'q1','visuals':[{'id':'v','kind':'table','show':'逐项核对'}]}]}
        table = '| 产线 | 划痕 |\n| --- | ---: |\n| 甲线 | `18` |'
        part = {'id':'p1','markdown':table}
        result = {'answers':[{'question_id':'q1','parts':[part]}],
                  'visual_checks':[{'visual_id':'v','part_id':'p1'}]}
        check_visuals(result,plan)
        for invalid in (table.replace('| --- | ---: |\n',''), '```text\n'+table+'\n```',
                        table.replace('| --- | ---: |','| --- | ---: | --- |')):
            part['markdown'] = invalid
            with self.assertRaisesRegex(ValueError,'能渲染的 Markdown 对照表'):
                check_visuals(result,plan)
