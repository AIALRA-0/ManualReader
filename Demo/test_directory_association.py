import base64
from pathlib import Path
import tempfile
import unittest

from reader_directory_coverage import directory_association
from reader_project_files import ProjectFiles
from reader_workspace import Workspace


class DirectoryAssociationTests(unittest.TestCase):
    def test_new_changed_moved_and_deleted_files_update_the_denominator(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            project=root/'project'
            project.mkdir()
            files=ProjectFiles('project-one',project,root/'settings.json')
            files.put({'path':'source/one.md','content_base64':base64.b64encode(b'First material').decode()})
            work=Workspace(project)
            row=files.import_source(work,{'project_path':'source/one.md'})
            coverage={'files':[{'file_id':row['id'],'explanation':{'percent':75.0}}]}
            def current():
                return directory_association(files,work,coverage)
            self.assertEqual(current()['percent'],75.0)
            extra=Path(files.ensure()['root'])/'source/two.md'
            extra.write_text('A new original')
            result=current()
            self.assertEqual((result['total'],result['percent'],result['unregistered']),(2,37.5,1))
            extra.unlink()
            self.assertEqual(current()['percent'],75.0)
            source=files.target('source/one.md')
            files.action({'action':'move','path':'source/one.md','destination':'source/moved.md',
                          'expected_modified':source.stat().st_mtime_ns})
            self.assertEqual(current()['percent'],75.0)
            files.target('source/moved.md').write_text('Changed material')
            self.assertEqual(current()['percent'],0.0)
            (Path(files.ensure()['root'])/'explanations').mkdir()
            (Path(files.ensure()['root'])/'explanations/0. Intro.md').write_text('Generated explanation')
            self.assertEqual(current()['total'],1)
