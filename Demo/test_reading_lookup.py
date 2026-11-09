"""批量排版只扫描少量整表，页序、重复编号的引用及原数据仍保持一致。"""
from pathlib import Path
import copy,sys,unittest
from collections import defaultdict
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_reading import page_ids,groups,_LOCK

class ReadingLookup(unittest.TestCase):
    def test_index_preserves_page_order_unknown_ids_and_duplicate_references(self):
        material={'pages':{'second':{},'first':{}},'passages':[{'id':'a','refs':[{'page_id':'first'}]},{'id':'a','refs':[{'page_id':'second'}]},{'id':'b','refs':[{'page_id':'missing'}]}]}
        index=defaultdict(set)
        for p in material['passages']:index[p['id']].update(r['page_id']for r in p['refs']if r['page_id']in material['pages'])
        for ids in [[],['a'],['b'],['unknown'],['a','b','a']]:
            self.assertEqual(page_ids(material,ids),page_ids(material,ids,index))

    def test_many_code_blocks_do_not_rescan_whole_material_per_block(self):
        class Passages(list):
            scans=0
            def __iter__(self):self.scans+=1;return super().__iter__()
        passages=Passages({'id':f'p{i}','kind':'code','en':f'line {i}','refs':[],'file_id':'code','sentences':[]}for i in range(400))
        material={'passages':passages,'pages':{}};before=copy.deepcopy(list(passages));passages.scans=0
        result,audit=groups(material,{})
        self.assertEqual([g['passage_ids']for g in result],[[f'p{i}']for i in range(400)])
        self.assertLessEqual(passages.scans,4);self.assertFalse(audit['missing']);self.assertFalse(audit['duplicate_assignments'])
        self.assertEqual(list(passages),before)

    def test_other_project_layout_lock_is_independent(self):
        self.assertIsNot(_LOCK.for_scope('project-a'),_LOCK.for_scope('project-b'))
        self.assertIs(_LOCK.for_scope('project-a'),_LOCK.for_scope('project-a'))

if __name__=='__main__':unittest.main()
