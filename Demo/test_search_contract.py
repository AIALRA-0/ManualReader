from pathlib import Path
import sys,tempfile,unittest,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_contract import writing_guide
from reader_quality import public_rules
from reader_execute import command,build_prompt
from reader_prompts import NAMING_RULE,WRITING_VERSION

class SearchContract(unittest.TestCase):
    def test_published_rules_and_agent_guide_use_the_same_search_contract(self):
        guide=writing_guide();rules=public_rules()
        self.assertEqual(guide['version'],WRITING_VERSION)
        self.assertEqual(rules['qa']['naming'],NAMING_RULE)
        self.assertIn(NAMING_RULE,guide['instructions'])
        prompt=build_prompt({'sources':[],'instructions':[],'result_contract':{'terms':[]}},{'passages':[]})
        self.assertIn(NAMING_RULE,json.loads(prompt.split('任务与原材料：\n',1)[1])['instructions'])

    def test_writer_keeps_live_search_and_normal_service(self):
        with tempfile.TemporaryDirectory()as folder:
            args=command('codex',folder,Path(folder)/'final.txt',[])
            self.assertIn('web_search="live"',args)
            self.assertIn('service_tier="default"',args)
            self.assertIn('model_reasoning_effort="high"',args)
            self.assertEqual(args[args.index('-m')+1],'gpt-6.1-terra')
            self.assertNotIn('web_search', [args[i+1]for i,x in enumerate(args)if x=='--disable'])

if __name__=='__main__':unittest.main()
