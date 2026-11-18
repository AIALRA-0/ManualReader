import tempfile
import unittest

from reader_remote import Pairings, project_capabilities


class ProjectPermissionsTests(unittest.TestCase):
    def test_file_permissions_default_to_read_and_change_for_existing_token(self):
        with tempfile.TemporaryDirectory() as folder:
            pairs=Pairings(folder)
            result=pairs.issue('owner', {'material_id':'project-one'})
            session=pairs.exchange(result['pairing_code'])
            token=session['access_token']
            self.assertEqual(session['capabilities']['project_files'],
                             {'read':True,'create':False,'write':False,'delete':False})
            pairs.update_permissions('owner', result['id'], {
                'file_permissions':{'create':True,'write':True,'delete':False}})
            current=project_capabilities(pairs.authorize(token))
            self.assertEqual(current['project_files'], {'read':True,'create':True,'write':True,'delete':False})
            pairs.update_permissions('owner', result['id'], {'file_permissions':{}})
            self.assertFalse(project_capabilities(pairs.authorize(token))['project_files']['write'])
            with self.assertRaises(ValueError):
                pairs.update_permissions('someone-else', result['id'], {'file_permissions':{'write':True}})
            pairs.revoke('owner',result['id'])
            self.assertIsNone(pairs.authorize(token))
