import copy
import json
from pathlib import Path
import tempfile
import unittest

from reader_learning import validate_outline
from reader_next_teach import VERSION, chapter_filename, prepare, save_preview, writer_prompt
from reader_workspace import Workspace
from source_parser import Document


class NextTeachTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(self.temp.name)
        document = Document('project-teach', '新的访谈', 'test', 'text')
        document.add('访谈开头介绍人物')
        document.add('中间讨论工作经历')
        document.add('结尾讨论后续选择')
        self.original = document.finish()
        self.material = self.workspace.project(self.original)
        self.records = {'revision': 0, 'learning_selection_revision': 0,
                        'terms': [], 'rounds': [], 'read_passages': {}, 'manual_completions': {}}

    def test_first_generation_creates_intro_over_all_registered_sources(self):
        result = prepare(self.workspace, self.material, self.records)
        self.assertEqual((result['mode'], result['title'], result['number']), ('INTRO', '导读', '0'))
        node = self.workspace.read()['learning_outline'][0]
        self.assertEqual({row['passage_id'] for row in node['source_links']},
                         {row['id'] for row in self.material['passages']})
        revision = self.workspace.read()['revision']
        self.assertEqual(prepare(self.workspace, self.material, self.records), result)
        self.assertEqual(self.workspace.read()['revision'], revision)

    def test_unwritten_chapter_keeps_its_identity_after_intro_is_inserted(self):
        value = self.workspace.read()
        value['learning_outline'] = validate_outline({'nodes': [
            {'id': 'chapter-original', 'title': '第一项具体问题', 'source_links': [],
             'parent_id': None}]}, self.material, defer_unassigned=True)
        self.workspace.write(value, value['revision'])
        result = prepare(self.workspace, self.material, self.records)
        nodes = self.workspace.read()['learning_outline']
        self.assertEqual([row['id'] for row in nodes], [result['learning_node_id'], 'chapter-original'])
        self.assertEqual(nodes[1]['title'], '第一项具体问题')

    def test_native_preview_is_readable_and_replay_preserves_its_identity(self):
        ready = prepare(self.workspace, self.material, self.records)
        self.records['learning_node_id'] = ready['learning_node_id']
        value = self.workspace.read()
        node = value['learning_outline'][0]
        before = copy.deepcopy(self.records)
        payload = {'workflow_version': VERSION, 'mode': 'INTRO',
                   'request_id': 'next-preview-' + 'a' * 24,
                   'expected_revision': value['revision'], 'selection_revision': 0,
                   'chapter': {'node_id': node['id'], 'parent_node_id': None,
                               'number': '0', 'title': '导读'},
                   'markdown': '## 先认识这份访谈\n\n这是一份可以在讲解栏阅读的导读\n',
                   'source_links': node['source_links'], 'archive_path': 'explanations/0. 导读.md'}
        result = save_preview(self.workspace, self.material, self.records, payload)
        replay = save_preview(self.workspace, self.material, self.records, payload)
        self.assertEqual(result['explanation_id'], replay['explanation_id'])
        stored = self.workspace.read()
        self.assertEqual(len(stored['explanations']), 1)
        self.assertEqual(stored['explanations'][0]['delivery_status'], 'preview')
        self.assertEqual(stored['presentation']['explanation_id'], result['explanation_id'])
        self.assertFalse(result['formal_saved'])
        self.assertEqual(self.records, before)
        material = self.workspace.project(self.original)
        self.assertTrue(any('可以在讲解栏阅读' in row.get('zh', '') for row in material['passages']))

    def test_writer_uses_frozen_mode_prompts_and_files_use_chapter_title(self):
        self.assertIn('第 0 章宏观导读', writer_prompt('INTRO'))
        self.assertIn('长篇讲解作者', writer_prompt('NORMAL'))
        self.assertEqual(chapter_filename('0', '导读'), '0. 导读.md')
        self.assertEqual(chapter_filename('1', '背景与实际问题'), '1. 背景与实际问题.md')
        self.assertNotIn('/', chapter_filename('1', '../资料/章节'))

    def test_navigation_can_keep_auxiliary_source_ids_without_counting_them_as_explained(self):
        from reader_coverage import validate_source_mapping, _Index
        row=self.material['passages'][0]
        row['role']='whitespace'
        links={'source_ranges':[{'passage_id':row['id']}]}
        self.assertEqual(validate_source_mapping(self.material,links)['source_ranges'][0]['passage_id'],row['id'])
        self.assertNotIn(row['id'],_Index(self.material).sources)

    def test_selecting_main_original_preserves_current_layout_and_explanation(self):
        initial=self.workspace.present({'layout':'three','columns':[['original'],['translation'],['explanation']],
                                        'ratios':[.4,.6]},self.material)
        chosen=self.workspace.present({'file_id':'primary'},self.material)
        self.assertEqual(chosen,{**initial,'file_id':'primary'})

    def test_next_chapter_reads_exact_saved_intro_and_recaps(self):
        from reader_chapter_sources import previous_content, RECAP_KEYS
        from reader_connected import recap_markdown
        fields={key:'已经保存的'+key for key in RECAP_KEYS}
        saved={'id':'next-chapter-example','generation_mode':'INTRO','blocks':[
            {'id':'framework-start-1','markdown':'已经保存的章首'},
            {'id':'framework-outro','markdown':recap_markdown([('一项主题',fields)])},
            {'id':'framework-next','markdown':'已经保存的启下'}]}
        folder=Path(self.temp.name)/'postprocess-preview'/saved['id']
        folder.mkdir(parents=True)
        (folder/'manifest.json').write_text(json.dumps({'chapter_recaps':[{'title':'一项主题','items':fields}]}),'utf8')
        previous, missing=previous_content({'title':'导读','explanation_ids':[saved['id']]},
                                           {'explanations':[saved]},Path(self.temp.name))
        self.assertFalse(missing)
        self.assertEqual(previous,{'title':'导读','bridges':['已经保存的章首'],
                                  'recaps':[{'part_title':'一项主题',**fields}],'forecast':'已经保存的启下'})


if __name__ == '__main__':
    unittest.main()
