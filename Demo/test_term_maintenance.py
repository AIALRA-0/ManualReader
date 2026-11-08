"""用隔离术语验证只补缺段、最多两路并行，以及恢复时不重复调用模型。"""
from pathlib import Path
from unittest.mock import patch
import copy,json,sys,tempfile,threading,time,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_term_maintenance import task,updates,maintain
from reader_terms import revise_term
from test_term_quality import DEFINITION,INTUITION
class TermMaintenanceTests(unittest.TestCase):
    def test_only_missing_half_is_requested_and_qualified_half_is_rejected(self):
        item={'id':'t1','name':'缓存（Cache）','version':1,'professional_definition':DEFINITION,'intuitive_explanation':'短'}
        row=task(item);self.assertEqual(row['write_fields'],['intuitive_explanation'])
        result=updates([row],{'terms':[{'term_id':'t1','professional_definition':None,'intuitive_explanation':INTUITION}]})
        self.assertNotIn('professional_definition',result[0])
        with self.assertRaises(ValueError):updates([row],{'terms':[{'term_id':'t1','professional_definition':DEFINITION,'intuitive_explanation':INTUITION}]})
    def test_batches_resume_without_rewriting_user_data_or_regenerating(self):
        class Bridge:
            def __init__(self):
                self.state={'terms':[{'id':'t'+str(i),'name':'缓存'+str(i)+'（Cache）','version':1,'professional_definition':DEFINITION,'intuitive_explanation':'短','question_ids':['q1']}for i in range(20)],'notes':{'q1':'保留'},'read':{'p1':True},'selected':2}
                self.lock=threading.Lock()
            def request(self,path,data=None):
                with self.lock:
                    if data is None:
                        terms=self.state['terms'];return {'material_id':'synthetic','terms':copy.deepcopy([t for t in terms if len(t['intuitive_explanation'])<100]if 'needs_revision'in path else terms)}
                    self.assert_repair=data['repair_only'];known={t['id']:t for t in self.state['terms']}
                    for proposal in data['updates']:revise_term(known[proposal['term_id']],{**proposal,'action':'revise'},lambda:'test')
                    return {'updated':len(data['updates'])}
        bridge=Bridge();before=copy.deepcopy(bridge.state);calls=[];active=0;maximum=0;lock=threading.Lock()
        def writer(folder,prompt,images,executable,**kwargs):
            nonlocal active,maximum
            with lock:active+=1;maximum=max(maximum,active);calls.append(folder.name)
            time.sleep(.03)
            payload=json.loads(prompt.rsplit('\n',1)[1]);terms=[{'term_id':t['term_id'],'professional_definition':None,'intuitive_explanation':INTUITION}for t in payload['terms']]
            raw=json.dumps({'terms':terms,'used_sources':[]});(folder/'final.txt').write_text(raw,'utf8')
            (folder/'usage.json').write_text(json.dumps({'usage':{'input_tokens':100,'cached_input_tokens':40,'output_tokens':20,'model_calls':1},'usage_complete':True}),'utf8')
            with lock:active-=1
            return raw,{},None
        with tempfile.TemporaryDirectory()as tmp,patch('reader_execute.invoke_writer',side_effect=writer),patch('reader_source_cache.for_bridge',return_value=None):
            first=maintain(bridge,tmp,executable='synthetic')
            self.assertEqual(len(calls),3);self.assertLessEqual(maximum,2);self.assertEqual(first['updated'],20)
            self.assertEqual(first['writer_usage']['input_tokens'],300);self.assertEqual(first['new_writer_usage']['input_tokens'],300)
            second=maintain(bridge,tmp,executable='synthetic');self.assertEqual(len(calls),3)
            self.assertEqual(second['writer_usage']['input_tokens'],300);self.assertEqual(second['new_writer_usage']['input_tokens'],0)
            self.assertTrue(second['usage_complete'])
            # 模拟已写回而回执落盘前中断；现有文本足够确认，不重新写作。
            (Path(tmp)/'terms-001/applied.json').unlink();maintain(bridge,tmp,executable='synthetic');self.assertEqual(len(calls),3)
        self.assertEqual({k:v for k,v in bridge.state.items()if k!='terms'},{k:v for k,v in before.items()if k!='terms'})
        for old,new in zip(before['terms'],bridge.state['terms']):
            self.assertEqual(new['id'],old['id']);self.assertEqual(new['question_ids'],old['question_ids']);self.assertEqual(new['professional_definition'],old['professional_definition']);self.assertEqual(new['version'],2)
if __name__=='__main__':unittest.main()
