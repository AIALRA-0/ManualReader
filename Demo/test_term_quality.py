"""用合成术语检查两段式规则、版本冲突与旧内容保留，不连接官网。"""
import copy,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from reader_term_quality import INSTRUCTION,inspect,revision_fields,validate_new_terms,repair_fields
from reader_terms import revise_term

DEFINITION=('缓存把近期使用的结果放在容易取得的位置，程序再次需要相同结果时，先检查是否已经保存；'
            '找到就直接复用，省去重新计算或远程读取的等待；找不到才执行原来的取数过程，并按容量选择保留哪些结果。')
INTUITION=('可以想象书桌旁有一个小书架：你把最近常查的书放在手边，下次找同一本时先看小书架；'
           '找到就直接拿，没找到才去大书柜，回来后再决定是否放进小书架；小书架对应容量有限的缓存，'
           '找书对应检查已有结果，走到大书柜对应较慢的原始读取，腾位置则对应缓存满时选择移走哪条记录。')

class TermQualityTests(unittest.TestCase):
    def term(self):
        return {'id':'term-synthetic','name':'缓存（Cache）','version':3,'professional_definition':DEFINITION,
                'definition':DEFINITION,'intuitive_explanation':INTUITION,'question_ids':['q1'],'learning_node_ids':['lesson1']}
    def test_two_complete_independent_paragraphs_pass(self):
        self.assertTrue(inspect(self.term())['passed'])
    def test_short_or_article_dependent_explanation_is_rejected(self):
        for field,value in [('professional_definition','保存结果'),('intuitive_explanation','本章的这个例子'+INTUITION)]:
            row=self.term();row[field]=value
            self.assertFalse(inspect(row)['passed'])
    def test_existing_term_can_be_reused_without_rewriting(self):
        self.assertTrue(validate_new_terms([{'term_id':'registered'}]))
    def test_explicit_revision_cannot_bypass_quality_check(self):
        with self.assertRaises(ValueError):validate_new_terms([{'term_id':'registered','action':'revise'}])
    def test_revision_requires_current_version_and_old_text(self):
        row=self.term()
        for proposal in [{'expected_version':2},{'expected_version':3,'expected_definition':'changed'}]:
            with self.assertRaises(ValueError):revision_fields(row,{**proposal,'intuitive_explanation':INTUITION})
    def test_only_short_half_is_selected(self):
        row=self.term();row['intuitive_explanation']='小书架'
        self.assertEqual(repair_fields(row),['intuitive_explanation'])
    def test_book_counts_and_normal_word_parts_are_not_article_references(self):
        for prefix in ('找回五本图书；','在样本图片中观察颜色；','按照基本章程分工；','练习基本题型；'):
            row=self.term();row['intuitive_explanation']=prefix+INTUITION
            self.assertTrue(inspect(row)['passed'],prefix)
            self.assertEqual(repair_fields(row),[])
    def test_legacy_new_terms_cannot_bypass_quality_but_reuse_still_works(self):
        from reader_term_quality import validate_asset_terms
        with self.assertRaises(ValueError):validate_asset_terms([{'name':'新概念（New Concept）','definition':'太短'}],[])
        self.assertTrue(validate_asset_terms([{'term_id':'old'}],[{'id':'old','name':'旧概念（Old Concept）','definition':'历史短解释'}]))
    def test_unchanged_qualified_legacy_paragraphs_are_preserved(self):
        row=self.term();row['professional_definition']=DEFINITION+'\n\n'+DEFINITION
        fields=revision_fields(row,{'expected_version':3,'intuitive_explanation':INTUITION})
        self.assertEqual(set(fields),{'intuitive_explanation'})
        with self.assertRaises(ValueError):revision_fields(row,{'expected_version':3,'professional_definition':row['professional_definition']})
    def test_revision_preserves_identity_links_and_history(self):
        row=self.term();row['intuitive_explanation']='旧解释';before=copy.deepcopy(row)
        fields=revision_fields(row,{'expected_version':3,'expected_intuitive_explanation':'旧解释','intuitive_explanation':INTUITION})
        self.assertTrue(revise_term(row,{**fields,'action':'revise','expected_version':3},lambda:'test-time'))
        self.assertEqual(row['version'],4)
        self.assertEqual(row['definition_history'][-1]['intuitive_explanation'],'旧解释')
        for key in ('id','name','question_ids','learning_node_ids','professional_definition'):self.assertEqual(row[key],before[key])
    def test_unchanged_repeated_write_does_not_add_history(self):
        row=self.term();before=copy.deepcopy(row)
        self.assertFalse(revise_term(row,{**row,'action':'revise','expected_version':3},lambda:'test-time'))
        self.assertEqual(row,before)
    def test_writing_method_contains_the_same_instruction_once(self):
        from reader_prompts import writing_method
        from reader_agent_contract import CLIENT_MODULES
        for mode in ('lesson','answer'):self.assertEqual(writing_method(mode).count(INSTRUCTION),1)
        self.assertIn('reader_term_quality.py',CLIENT_MODULES)

    def test_new_rule_version_keeps_source_group_coverage(self):
        from unittest.mock import patch
        from reader_generation import normalize_output
        value={'writing_version':'test-term-format','framing':True,
               'plan':{'target':'解释机制','direct_answer':'先给结果','units':[{'id':'u1','say':'具体动作','needs':[]}],'order':['u1']},
               'explanation':{'parts':[{'id':'p1','heading':'怎样操作','markdown':'实际正文与动作','unit_ids':['u1']}]}}
        with patch('reader_prompts.WRITING_VERSION','test-term-format'),patch('reader_sequence.prepare_output',side_effect=copy.deepcopy),patch('reader_generation.apply_group_coverage',side_effect=RuntimeError('coverage_reached')):
            with self.assertRaisesRegex(RuntimeError,'coverage_reached'):normalize_output(value,lesson=True)

    def test_actual_http_update_is_atomic_and_keeps_user_records(self):
        code=r'''
import copy,json,os,sys,threading,urllib.request,urllib.error
from pathlib import Path
root=Path(sys.argv[1]);folder=Path(sys.argv[2]);sys.path.insert(0,str(root))
data=json.loads(sys.argv[3]);material={'id':'synthetic','title':'合成测试','passages':[{'id':'p1','en':'Source','zh':'原句','sentences':[]}],'outline':[]}
(folder/'material.json').write_text(json.dumps(material),'utf8');(folder/'pin.txt').write_text('123456','utf8')
os.environ.update(MANUAL_READER_MATERIAL=str(folder/'material.json'),MANUAL_READER_DB=str(folder/'state.sqlite3'),MANUAL_READER_PIN_FILE=str(folder/'pin.txt'),MANUAL_READER_LIBRARY=str(folder/'library'),MANUAL_READER_SETTINGS=str(folder/'settings.json'))
import reader_server as app
server=app.ReaderHTTPServer(('127.0.0.1',0),app.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
state=app.core.initial();state.update(terms=[data],notes={'q1':'用户笔记'},read_passages={'p1':True},quizzes=[{'id':'quiz1','selected':1}],rounds=[{'id':'r1','questions':[{'id':'q1','text':'问题','answer':'已有回答'}]}])
def replace(current):current.clear();current.update(copy.deepcopy(state))
app.core.mutate(replace);original=app.core.read_state()
base='http://127.0.0.1:'+str(server.server_port)
def post(updates):
 request=urllib.request.Request(base+'/api/terms',data=json.dumps({'updates':updates}).encode(),headers={'Content-Type':'application/json','X-Reader-Pin':'123456','X-Reader-Material':'synthetic','Origin':base})
 try:
  with urllib.request.urlopen(request)as response:return response.status,json.load(response)
 except urllib.error.HTTPError as error:return error.code,json.load(error)
good={'term_id':data['id'],'expected_version':3,'expected_intuitive_explanation':data['intuitive_explanation'],'intuitive_explanation':data['intuitive_explanation']+' 书架上的书用完仍按保存规则归位，方便下一次查找。'}
status,_=post([good,{'term_id':'missing','expected_version':1,'intuitive_explanation':'bad'}]);assert status==400,status
assert app.core.read_state()==original,'失败批次产生了部分写入'
status,_=post([good]);assert status==200,status
after=app.core.read_state()
for key in original:
 if key not in ('terms','revision'):assert after[key]==original[key],key
term=after['terms'][0];assert term['version']==4 and term['id']==data['id'];assert term['question_ids']==data['question_ids'];assert term['definition_history'][-1]['intuitive_explanation']==data['intuitive_explanation']
status,_=post([good]);assert status==400,status
assert app.core.read_state()==after,'冲突请求改变了当前内容'
server.shutdown();print('HTTP_OK')
'''
        with tempfile.TemporaryDirectory()as temporary:
            result=subprocess.run([sys.executable,'-X','utf8','-c',code,str(ROOT),temporary,json.dumps(self.term(),ensure_ascii=False)],capture_output=True,text=True,encoding='utf8',timeout=45)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('HTTP_OK',result.stdout)

if __name__=='__main__':unittest.main(verbosity=2)
