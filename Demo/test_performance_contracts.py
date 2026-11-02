"""验证减少重复计算后，章节覆盖和手动已读的结果仍与直接计数一致。"""
import copy,random,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_features import reading_progress
from reader_completion import completion_payload,completion_scope

def direct_progress(material,state):
    passages=material['passages'];legacy=any(p.get('outline_id')for p in passages)
    eligible=[p for p in passages if not p.get('heading')and not p.get('duplicate_of')and p.get('role')not in ('page_furniture','metadata','whitespace')and(not legacy or p.get('outline_id')or p.get('file_id')!='primary'or p.get('content_kind')=='explanation')and((p.get('zh')or p.get('en')or'').strip()or p.get('kind')in ('image','visual','page_image'))]
    def count(rows):
        total=len(rows);done=sum(bool(state.get('read_passages',{}).get(p['id']))for p in rows)
        return {'done':done,'total':total,'percent':round(done/total*100)if total else 0}
    chapters={}
    for file in material.get('reading_files')or[{'outline':material.get('outline',[]),'passage_ids':[p['id']for p in passages]}]:
        positions={p['id']:i for i,p in enumerate(p for p in passages if p['id']in file['passage_ids'])}
        for i,row in enumerate(file['outline']):
            start=positions.get(row.get('passage_id'),0);end=next((positions.get(n.get('passage_id'),len(positions))for n in file['outline'][i+1:]if n.get('level',1)<=row.get('level',1)),len(positions))
            chapters[row['id']]=count([p for p in eligible if p['id']in positions and start<=positions[p['id']]<end])
    for e in material.get('explanations',[]):chapters[e['id']]=count([p for p in eligible if p.get('explanation_id')==e['id']])
    return {'overall':count(eligible),'chapters':chapters}

class PerformanceContracts(unittest.TestCase):
    def test_mixed_hierarchies_equal_direct_counts(self):
        rng=random.Random(841)
        for iteration in range(120):
            passages=[{'id':'p'+str(i),'en':'内容'if i%9 else'','kind':'image'if i%11==0 else'paragraph','file_id':'source','heading':i%7==0,'role':'metadata'if i%13==0 else'body'}for i in range(70)]
            outline=[{'id':'s'+str(i),'passage_id':'p'+str(i),'level':rng.randint(1,4)}for i in range(0,70,3)]
            material={'passages':passages,'reading_files':[{'id':'source','passage_ids':[p['id']for p in passages],'outline':outline}]}
            state={'read_passages':{p['id']:True for p in passages if rng.random()<.4}}
            before=copy.deepcopy((material,state));self.assertEqual(reading_progress(material,state),direct_progress(material,state));self.assertEqual((material,state),before)
    def fixture(self):
        m={'passages':[{'id':'source','en':'原材料','file_id':'file','content_version':2},{'id':'explain','en':'讲解','content_kind':'explanation','content_version':2}]}
        plan={'selected_id':'lesson','nodes':[{'id':'lesson','reading_passage_ids':['explain'],'effective_source_links':[{'passage_id':'source','file_id':'file'}]}]}
        state={'focus':{'passage_id':'source'},'manual_completions':{'lesson':{'completed':True,'passage_ids':['explain'],'versions':{'explain':2},'view':{'content_layout':{'columns':[['explanation']]}}}}}
        return m,plan,state
    def test_existing_plan_does_not_recalculate_learning(self):
        m,plan,state=self.fixture()
        with patch('reader_completion.learning_payload',side_effect=AssertionError('不应重复计算章节')):
            self.assertTrue(completion_payload(m,{},state,plan=plan)['completed'])
            self.assertEqual(completion_scope(m,{},state,plan=plan),('lesson',['explain']))
    def test_changed_content_still_invalidates_manual_completion(self):
        m,plan,state=self.fixture();m['passages'][1]['content_version']=3
        before=copy.deepcopy(state)
        result=completion_payload(m,{},state,plan=plan)
        self.assertTrue(result['content_changed']);self.assertFalse(result['can_advance']);self.assertEqual(state,before)
    def test_missing_confirmation_never_becomes_read(self):
        m,plan,state=self.fixture();state.pop('manual_completions')
        self.assertFalse(completion_payload(m,{},state,plan=plan)['completed'])
    def test_parent_counts_include_child_passages(self):
        m={'passages':[{'id':'p'+str(i),'en':'内容'}for i in range(6)],'outline':[{'id':'parent','passage_id':'p0','level':1},{'id':'first','passage_id':'p1','level':2},{'id':'second','passage_id':'p3','level':2},{'id':'next','passage_id':'p5','level':1}]}
        r=reading_progress(m,{'read_passages':{'p1':True,'p4':True}})
        self.assertEqual(r['chapters']['parent'],{'done':2,'total':5,'percent':40});self.assertEqual(r['chapters']['first']['percent'],50);self.assertEqual(r['chapters']['second']['percent'],50)

if __name__=='__main__':unittest.main(verbosity=2)
