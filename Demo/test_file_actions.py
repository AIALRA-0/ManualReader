from pathlib import Path
import base64,json,sys,tempfile,unittest,time,threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_project_files import ProjectFiles
from reader_file_events import EVENTS

class Actions(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name);self.store=ProjectFiles('one',self.home/'library/one',self.home/'settings.json');self.root=Path(self.store.ensure()['root'])
 def tearDown(self):self.temp.cleanup()
 def file(self,path='a.txt',text='hello'):return self.store.put({'path':path,'content_base64':base64.b64encode(text.encode()).decode()})
 def row(self,path):return {'path':path,'expected_modified':str(self.store.target(path).lstat().st_mtime_ns)}
 def test_create_folder_and_hidden_file(self):
  self.store.action({'action':'mkdir','path':'a/b'});self.store.action({'action':'create','path':'a/b/.hidden'});self.assertTrue(self.store.target('a/b/.hidden').exists())
 def test_rename_and_move_preserve_content(self):
  self.file();self.store.action({'action':'move',**self.row('a.txt'),'destination':'sub/b.txt'});self.assertEqual(self.store.target('sub/b.txt').read_text(),'hello')
 def test_copy_preserves_source(self):
  self.file();self.store.action({'action':'copy',**self.row('a.txt'),'destination':'b.txt'});self.assertTrue(self.store.target('a.txt').exists());self.assertEqual(self.store.target('b.txt').read_text(),'hello')
 def test_stale_version_rejected(self):
  self.file();row=self.row('a.txt');self.store.target('a.txt').write_text('changed');
  with self.assertRaises(ValueError):self.store.action({'action':'move',**row,'destination':'b.txt'})
 def test_destination_collision_rejected(self):
  self.file();self.file('b.txt')
  with self.assertRaises(ValueError):self.store.action({'action':'move',**self.row('a.txt'),'destination':'b.txt'})
 def test_folder_cannot_move_inside_itself(self):
  self.file('a/file')
  with self.assertRaises(ValueError):self.store.action({'action':'move',**self.row('a'),'destination':'a/sub'})
 def test_delete_requires_preview_and_confirmation(self):
  self.file()
  with self.assertRaises(ValueError):self.store.action({'action':'trash','path':'a.txt','confirmed':True})
 def test_trash_and_restore_preserve_original(self):
  self.file();review=self.store.action({'action':'trash-preview',**self.row('a.txt')});result=self.store.action({'action':'trash','path':'a.txt','confirmed':True,'review_id':review['review_id']});self.assertFalse((self.root/'a.txt').exists());self.assertEqual(len(self.store.trash_items()['items']),1);self.store.action({'action':'restore','trash_id':result['trash_id']});self.assertEqual(self.store.target('a.txt').read_text(),'hello')
 def test_changed_descendant_invalidates_delete_preview(self):
  self.file('a/child');review=self.store.action({'action':'trash-preview',**self.row('a')});self.store.target('a/child').write_text('new-content')
  with self.assertRaises(ValueError):self.store.action({'action':'trash','path':'a','confirmed':True,'review_id':review['review_id']})
 def test_review_cannot_cross_projects(self):
  self.file();review=self.store.action({'action':'trash-preview',**self.row('a.txt')});other=ProjectFiles('two',self.home/'library/two',self.home/'settings.json');other.put({'path':'a.txt','content_base64':'eA=='})
  with self.assertRaises(ValueError):other.action({'action':'trash','path':'a.txt','confirmed':True,'review_id':review['review_id']})
 def test_actions_do_not_touch_reader_records(self):
  notes=self.store.folder/'state.sqlite3';notes.parent.mkdir(parents=True,exist_ok=True);notes.write_bytes(b'notes-progress');self.file();self.store.action({'action':'move',**self.row('a.txt'),'destination':'b.txt'});self.assertEqual(notes.read_bytes(),b'notes-progress')
 def test_external_hidden_file_is_reported_without_polling(self):
  with EVENTS.subscribe(self.root)as c:
   (self.root/'.external').write_text('test')
   with c['condition']:c['condition'].wait_for(lambda:any('.external'in e['paths']for e in c['events']),timeout=3)
   self.assertTrue(any('.external'in e['paths']for e in c['events']))
  self.assertNotIn(str(self.root.resolve()),EVENTS.channels)
 def test_idle_watch_has_no_events(self):
  with EVENTS.subscribe(self.root)as c:
   time.sleep(.15);self.assertEqual(c['version'],0)
 def test_registered_original_rename_keeps_id(self):
  source=self.store.folder/'original.txt';source.parent.mkdir(parents=True,exist_ok=True);source.write_text('original');self.store=ProjectFiles('one',self.store.folder,self.home/'settings.json',{'original_file':'original.txt'});name=self.store.ensure()['sources']['primary'];self.store.action({'action':'move',**self.row(name),'destination':'sources/renamed.txt'});self.assertEqual(self.store.ensure()['sources']['primary'],'sources/renamed.txt');self.assertEqual(source.read_text(),'original')
if __name__=='__main__':unittest.main()
