"""Check the isolated Reader adapter without a provider or a running service."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from manualreader.stages.finish.commit import stable_hash, user_fingerprint
from manualreader.stages.finish.preview import records, render_id
from manualreader.stages.finish.native_ui import ASSETS, substitute
from manualreader.stages.finish.provider import DS_CREDENTIAL, invoke_ds


class IsolatedProjectionTests(unittest.TestCase):
    def test_existing_ascii_ids_and_unicode_ids_round_trip_in_sidecar(self):
        self.assertEqual(render_id('block-0123'), 'block-0123')
        first=render_id('小节一')
        self.assertRegex(first, r'^next-[a-f0-9]{40}$')
        self.assertEqual(first,render_id('小节一'))
        self.assertNotEqual(first,render_id('小节二'))

    def test_optional_records_wrapper_is_supported(self):
        self.assertEqual(records({'translations':[{'label':'轴'}]},'translations'),[{'label':'轴'}])
        self.assertEqual(records({'translations':None},'translations'),[])

    def test_save_receipts_and_progress_do_not_change_user_records(self):
        before={'rounds':[{'id':'q-123'}],'notes':{'q-123':'笔记'},'revision':1,'file_coverage':{}}
        after={**before,'revision':2,'file_coverage':{'source':1},'next_save_receipts':{'r1':'saved'}}
        self.assertEqual(user_fingerprint(before),user_fingerprint(after))
        after['notes']={}
        self.assertNotEqual(user_fingerprint(before),user_fingerprint(after))
        self.assertEqual(stable_hash({'a':1,'b':2}),stable_hash({'b':2,'a':1}))

    def test_original_question_form_receives_resource_id(self):
        script=ASSETS.joinpath("reader-finish.js").read_text("utf8")
        self.assertIn("slot.dataset.segmentId='resource:'+item.resource_id;",script)
        self.assertIn("ctx.inlineQuestions?.(root)",script)
        self.assertNotIn("就这个资源提问",script)
        self.assertNotIn("已核对原文编号",script)
        with self.assertRaises(ValueError):
            substitute("not the established Reader template","questionUnit","newHook")

    def test_ds_403_retains_provider_reason_without_recording_raw_body(self):
        response=Mock(status_code=403,headers={'X-Request-ID':'provider-403'})
        response.json.return_value={'error':{'code':'INSUFFICIENT_BALANCE','message':'not for the receipt'}}
        context=Mock()
        context.__enter__=Mock(return_value=response)
        context.__exit__=Mock(return_value=False)
        with TemporaryDirectory() as path, patch.dict('os.environ',{DS_CREDENTIAL:'test-value'}), \
                patch('manualreader.stages.finish.provider.requests.post',return_value=context):
            got=invoke_ds(Path(path),'B1',b'translate',{})
        self.assertEqual(got['error'],'HTTP 403 (INSUFFICIENT_BALANCE)')
        self.assertEqual(got['attempts'][0]['provider_request_id'],'provider-403')
        self.assertNotIn('not for the receipt',repr(got))



if __name__=='__main__':
    unittest.main()
