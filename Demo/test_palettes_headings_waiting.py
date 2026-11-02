from pathlib import Path
import sys,unittest,tempfile,json,copy,os,subprocess,time
from contextlib import closing
from unittest.mock import patch
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from reader_quality import explanation_headings,standalone_heading
from reader_workspace import Workspace
from reader_features import settings,save_settings
from reader_execute import wait_for_report,process_running
from reader_database import DatabaseConnections

class Changes(unittest.TestCase):
    def test_database_connections_preserve_durability_external_updates_and_project_isolation(self):
        with tempfile.TemporaryDirectory()as folder:
            dbs=DatabaseConnections(limit=2);path=Path(folder)/'a.sqlite3';other=Path(folder)/'b.sqlite3'
            try:
                with closing(dbs.connect(path))as db,db:
                    self.assertEqual(db.execute('PRAGMA synchronous').fetchone()[0],2)
                    db.execute('INSERT INTO reader_state VALUES(1,?)',('old',))
                with closing(__import__('sqlite3').connect(path))as external,external:external.execute('UPDATE reader_state SET value=? WHERE id=1',('new',))
                with closing(dbs.connect(path))as db:self.assertEqual(db.execute('SELECT value FROM reader_state').fetchone()[0],'new')
                with closing(dbs.connect(other))as db:self.assertEqual(db.execute('SELECT count(*)FROM reader_state').fetchone()[0],0)
                with closing(dbs.connect(Path(folder)/'c.sqlite3')):pass
                self.assertEqual(len(dbs.anchors),2)
            finally:dbs.close()
    def test_heading_detection_does_not_treat_bold_in_a_sentence_as_heading(self):
        for text in ['**旧话题**','### 原话题','__原话题__']:self.assertTrue(standalone_heading(text))
        for text in ['先看**这个概念**，再继续','**术语**是一个概念','**第一段**\n\n第二段']:self.assertFalse(standalone_heading(text))

    def test_adjacent_synonymous_titles_need_no_model_or_deleted_block(self):
        passages=[{'id':'a','zh':'**宽度和频率怎样影响速度**'},{'id':'b','zh':'连续的正文','reading_heading':'频率相同时，每周期完成多少指令'},{'id':'c','zh':'另一话题','reading_heading':'另一话题怎样解释'}]
        before=copy.deepcopy(passages);explanation_headings(passages,[{'passage_ids':['a','b','c']}])
        self.assertEqual(passages[1]['display_reading_heading'],'');self.assertEqual(passages[2]['display_reading_heading'],'另一话题怎样解释')
        self.assertEqual([{k:v for k,v in p.items()if k!='display_reading_heading'}for p in passages],before)

    def test_clear_heading_survives_legacy_sidecar_and_omitted_field(self):
        with tempfile.TemporaryDirectory()as folder:
            work=Workspace(folder);material={'id':'fixture','title':'测试','kind':'markdown','passages':[],'outline':[],'pages':{}}
            first=work.explain({'id':'lesson','title':'标题','blocks':[{'id':'a','markdown':'**已有标题**'},{'id':'b','markdown':'这是正文','reading_heading':'额外标题'}]},material)
            (Path(folder)/'reading-headings.json').write_text(json.dumps({'headings':{'lesson--b':'旧文件重新补的标题'}}),'utf8')
            receipt=work.format_explanation({'explanation_id':'lesson','expected_version':1,'headings':[{'block_id':'b','reading_heading':''}]})
            self.assertEqual(receipt['version'],2);self.assertFalse(receipt['content_changed']);self.assertFalse(receipt['assets_required'])
            with patch('reader_workspace.prepare_reading',return_value={'groups':[]}):project=work.project(material)
            body=next(p for p in project['passages']if p['id']=='lesson--b');self.assertEqual(body['reading_heading'],'');self.assertEqual(body['display_reading_heading'],'')
            work.explain({'id':'lesson','expected_version':2,'blocks':[{'id':'a','markdown':'**已有标题**'},{'id':'b','markdown':'这是正文'}]},project)
            self.assertEqual(work.read()['explanations'][0]['passages'][1]['reading_heading'],'')
            self.assertEqual(work.read()['explanations'][0]['passage_ids'],first['passage_ids'])
            self.assertTrue(list((Path(folder)/'reading-history').glob('*.json')))

    def test_format_rejects_body_edits_unknown_ids_and_stale_versions(self):
        with tempfile.TemporaryDirectory()as folder:
            work=Workspace(folder);m={'passages':[]};work.explain({'id':'e','title':'测试','blocks':[{'id':'b','markdown':'正文'}]},m)
            before=work.path.read_bytes()
            for extra in [{'markdown':'不可改正文'},{'expected_version':0},{'headings':[{'block_id':'missing','reading_heading':''}]}]:
                with self.assertRaises(ValueError):work.format_explanation({'explanation_id':'e','expected_version':1,'headings':[{'block_id':'b','reading_heading':''}],**extra})
                self.assertEqual(work.path.read_bytes(),before)

    def test_title_inside_markdown_body_uses_no_additional_heading(self):
        passages=[{'id':'b','zh':'**同一话题**\n\n这是正文','reading_heading':'另一个说法'}]
        explanation_headings(passages,[{'passage_ids':['b']}]);self.assertEqual(passages[0]['display_reading_heading'],'')

    def test_palettes_preserve_existing_settings_and_accept_multiple_favorites(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'settings.json';old=settings(path);old['appearance']['dark']['term_line']='#123456';save_settings(path,{'appearance':old['appearance'],'motion_speed':1.1})
            result=save_settings(path,{'palette_favorites':['neutral','plum','ocean'],'default_palette':'plum','apply_palette':'plum'})
            self.assertEqual(result['palette_favorites'],['neutral','plum','ocean']);self.assertEqual(result['active_palette'],'plum');self.assertEqual(result['default_palette'],'plum');self.assertEqual(result['motion_speed'],1.1);self.assertEqual(result['templates'],old['templates']);self.assertEqual(len(result['palette_presets']),8)
            result=save_settings(path,{'palette_action':{'action':'save','name':'我的组合','appearance':old['appearance']}});identity=result['custom_palettes'][0]['id']
            result=save_settings(path,{'apply_palette':identity,'default_palette':identity});self.assertEqual(result['appearance'],old['appearance'])
            result=save_settings(path,{'palette_action':{'action':'save','id':identity,'name':'改名组合'}});self.assertEqual(len(result['custom_palettes']),1)
            result=save_settings(path,{'palette_action':{'action':'delete','id':identity}});self.assertEqual(result['appearance'],old['appearance']);self.assertIsNone(result['default_palette']);self.assertNotIn(identity,result['palette_favorites'])

    def test_palette_invalid_input_does_not_write(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'settings.json';save_settings(path,{});before=path.read_bytes()
            for data in [{'apply_palette':'missing'},{'palette_action':{'action':'delete','id':'neutral'}},{'palette_favorites':['missing']},{'palette_action':{'action':'save','name':'','appearance':{}}},{'appearance':{'dark':{'term_line':'bad'}}}]:
                with self.assertRaises(ValueError):save_settings(path,data)
                self.assertEqual(path.read_bytes(),before)

    def test_launcher_waits_for_existing_worker_without_redispatch(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder);(path/'execution-state.json').write_text(json.dumps({'state':'running','pid':123}),'utf8');calls=[]
            def pause(seconds):
                calls.append(seconds)
                if len(calls)==3:(path/'measured-run.json').write_text(json.dumps({'run':{'id':'same-run'}}),'utf8')
            self.assertEqual(wait_for_report(path,alive=lambda pid:True,pause=pause)['run']['id'],'same-run');self.assertEqual(len(calls),3)
            self.assertFalse(json.loads((path/'daily-state.json').read_text('utf8'))['redispatched'])

    def test_real_background_process_finishes_after_its_caller_has_returned(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)
            code="from pathlib import Path;import os,json,time;out=Path("+repr(folder)+");(out/'execution-state.json').write_text(json.dumps({'pid':os.getpid(),'state':'running'}));time.sleep(.4);p=out/'measured-run.next';p.write_text(json.dumps({'run':{'id':'original-worker'},'dispatches':1}));p.replace(out/'measured-run.json')"
            child=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                for _ in range(100):
                    if(path/'execution-state.json').exists():break
                    time.sleep(.01)
                result=wait_for_report(path);self.assertEqual(result['run']['id'],'original-worker');self.assertEqual(result['dispatches'],1)
                self.assertEqual(child.wait(timeout=5),0)
            finally:
                if child.poll()is None:child.terminate();child.wait(timeout=5)

    def test_launcher_does_not_wait_for_a_dead_process_or_replace_outputs(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder);saved=path/'saved-one.json';saved.write_text('{"saved":1}','utf8');(path/'execution-state.json').write_text(json.dumps({'state':'stopped','pid':123}),'utf8')
            with self.assertRaisesRegex(RuntimeError,'已保存回答'):wait_for_report(path,pause=lambda seconds:None)
            self.assertEqual(saved.read_text('utf8'),'{"saved":1}')
        self.assertTrue(process_running(os.getpid()));self.assertFalse(process_running(None))

if __name__=='__main__':unittest.main()
