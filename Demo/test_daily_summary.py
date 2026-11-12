"""Check that the local command waits for one execution before reporting its usage."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_execute import daily, summarize_daily, WRITER_ROLE


class DailySummaryTests(unittest.TestCase):
    def test_model_gets_only_the_finished_summary_and_no_tools(self):
        with TemporaryDirectory() as folder:
            stats = {'question_count':2,'state':'completed','usage':{'input_tokens':100}}
            def writer(path,prompt,images,program,**options):
                self.assertEqual(WRITER_ROLE.get(),'coordinator')
                self.assertEqual(json.loads(prompt),stats)
                self.assertFalse(options['research'])
                self.assertFalse(options['allow_image_generation'])
                self.assertEqual(images,[])
                return '两题均已保存',{'usage':{'input_tokens':37}},'test'
            with patch('reader_execute.invoke_writer',side_effect=writer) as call:
                measured = summarize_daily(folder,stats,'codex')
            call.assert_called_once()
            self.assertEqual(measured['usage']['input_tokens'],37)
            self.assertEqual(Path(folder,'reply.md').read_text('utf8'),'两题均已保存')

    def test_daily_adds_report_tokens_after_the_single_local_run(self):
        with TemporaryDirectory() as folder:
            run = {'id':'run-test','question_ids':['q1'],'state':'completed','usage':{'input_tokens':100},
                   'complete_usage':{'input_tokens':137},'complete_estimated_token_cost_usd':0.002}
            report = {'run':run,'coordinator':{'total_seconds':5},'issues':[]}
            bridge = Mock()
            bridge.request.side_effect = [{},run]
            with patch('reader_execute.execute',return_value=report) as execute, \
                 patch('reader_execute.summarize_daily',return_value={'usage':{'input_tokens':37}}) as summary, \
                 patch('reader_execute.codex_executable',return_value='codex'):
                result = daily(bridge,folder)
            execute.assert_called_once_with(bridge,Path(folder).resolve(),False,None,observe_caller=False)
            summary.assert_called_once()
            sent = bridge.request.call_args_list[0].args[1]
            self.assertEqual(sent['caller']['usage']['input_tokens'],37)
            self.assertEqual(result['usage']['input_tokens'],137)


if __name__=='__main__':
    unittest.main()
