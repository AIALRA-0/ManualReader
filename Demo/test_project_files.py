"""用临时项目检查文件范围、隐藏文件、版本核对和常见预览。"""
from pathlib import Path
import base64,json,sys,tempfile,unittest,zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader_project_files import ProjectFiles

class ProjectFileTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.settings=self.base/'settings.json';self.project=self.base/'library'/'project-one';self.project.mkdir(parents=True);self.store=ProjectFiles('project-one',self.project,self.settings)
 def tearDown(self):self.temp.cleanup()
 def write(self,name,content):return self.store.put({'path':name,'content_base64':base64.b64encode(content).decode()})
 def test_hidden_files_are_included(self):
  self.write('.hidden',b'abc');self.assertEqual(self.store.listing()['entries'][0]['name'],'.hidden')
 def test_each_project_has_its_own_root(self):
  other=ProjectFiles('project-two',self.base/'library/project-two',self.settings);self.assertNotEqual(self.store.ensure()['root'],other.ensure()['root'])
 def test_default_change_keeps_existing_project_in_place(self):
  root=self.store.ensure()['root'];self.settings.write_text(json.dumps({'project_root_base':str(self.base/'new-projects')}),'utf8');self.assertEqual(root,self.store.ensure()['root']);other=ProjectFiles('project-new',self.base/'library/project-new',self.settings);self.assertTrue(Path(other.ensure()['root']).is_relative_to((self.base/'new-projects').resolve()))
 def test_traversal_is_rejected(self):
  for name in('../private','/etc/passwd','C:/private','a/../../private','a/./b'):
   with self.subTest(path=name),self.assertRaises(ValueError):self.store.target(name,False)
 def test_symlink_cannot_read_outside_root(self):
  root=Path(self.store.ensure()['root']);outside=self.base/'private.txt';outside.write_text('private')
  try:(root/'link').symlink_to(outside)
  except OSError:self.skipTest('当前系统没有创建符号链接的权限')
  self.assertFalse(self.store.listing()['entries'][0]['accessible'])
  with self.assertRaises(ValueError):self.store.target('link')
 def test_overwrite_requires_current_version(self):
  row=self.write('notes.txt',b'first')
  with self.assertRaises(ValueError):self.write('notes.txt',b'next')
  self.store.put({'path':'notes.txt','content_base64':'bmV4dA==','expected_modified':row['modified']});self.assertEqual(self.store.target('notes.txt').read_text(),'next')
 def test_source_registration_preserves_ids_and_original(self):
  source=self.project/'original.txt';source.write_text('source');store=ProjectFiles('project-one',self.project,self.settings,{'original_file':'original.txt','original_name':'source.txt'});config=store.ensure();self.assertEqual(config['sources']['primary'],'sources/source.txt');self.assertEqual(source.read_text(),'source')
  with self.assertRaises(ValueError):store.put({'path':config['sources']['primary'],'expected_modified':store.target(config['sources']['primary']).stat().st_mtime_ns,'content_base64':'bmV4dA=='})
 def test_parser_data_is_not_copied_into_file_root(self):
  (self.project/'state.sqlite3').write_bytes(b'private-notes');self.assertEqual(self.store.listing()['entries'],[])
 def test_file_root_cannot_expose_reader_database_folder(self):
  self.settings.write_text(json.dumps({'project_root_base':str(self.project.parent)}),'utf8')
  with self.assertRaises(ValueError):self.store.ensure()
 def test_tree_pagination_keeps_every_file(self):
  root=Path(self.store.ensure()['root'])
  for i in range(620):(root/(str(i)+'.txt')).write_text(str(i))
  first=self.store.listing();second=self.store.listing(offset=first['next_offset']);self.assertEqual(len({p['path']for p in first['entries']+second['entries']}),620)
 def test_html_is_previewed_as_source(self):
  self.write('index.html',b'<script>alert(1)</script>');row=self.store.preview('index.html');self.assertEqual(row['kind'],'text');self.assertIn('<script>',row['text'])
 def test_text_markdown_csv_and_binary_previews(self):
  for name,data,kind in [('source.py',b'print(1)','text'),('notes.md',b'# heading','markdown'),('table.csv',b'a,b\n1,2','table'),('raw.bin',bytes(range(256)),'binary')]:
   with self.subTest(name=name):self.write(name,data);self.assertEqual(self.store.preview(name)['kind'],kind)
 def test_archive_includes_hidden_entries(self):
  root=Path(self.store.ensure()['root'])
  with zipfile.ZipFile(root/'files.zip','w')as archive:archive.writestr('.hidden','abc')
  self.assertEqual(self.store.preview('files.zip')['entries'][0]['name'],'.hidden')
 def test_pdf_can_render_any_page(self):
  import fitz
  path=Path(self.store.ensure()['root'])/'sample.pdf';doc=fitz.open();doc.new_page();doc.new_page();doc.save(path);doc.close();self.assertEqual(self.store.preview('sample.pdf')['pages'],2);self.assertTrue(self.store.page('sample.pdf',1).startswith(b'\x89PNG'))
if __name__=='__main__':unittest.main()
