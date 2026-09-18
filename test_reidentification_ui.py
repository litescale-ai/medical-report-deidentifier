"""Exercise the actual restoration UI with generated files and a private test catalogue."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class RestorationUiTest(unittest.TestCase):
    def test_folder_bulk_restore_retry_and_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dirs = {name: str(root / name) for name in ('input', 'output', 'secure')}
            for folder in dirs.values():
                Path(folder).mkdir()
            returned = root / 'returned'
            returned.mkdir()
            (returned / 'one.txt').write_text('PATIENT_A1B2C3D4 [PHONE REMOVED]')
            (returned / 'two.md').write_text('PATIENT_FFFFFFFF')
            catalogue = Path(dirs['secure']) / 'identity_catalogue.json'
            catalogue.write_text(json.dumps({'PATIENT_A1B2C3D4': {'canonical_name': 'Alex Example',
                'entity_type': 'PATIENT', 'relationship_context': '', 'variations': []}}))
            with patch('utils.helpers.get_data_dirs', return_value=dirs):
                app = AppTest.from_file('app.py').run()
                next(item for item in app.radio if item.label == 'Returned document source').set_value('Folder').run()
                next(item for item in app.text_input if item.label == 'Returned input folder').set_value(str(returned)).run()
                next(item for item in app.button if item.label == 'Restore documents').click().run()
                self.assertFalse(app.exception)
                result = app.session_state['restoration_result']
                self.assertEqual(len(result['completed']), 1)
                self.assertEqual(set(result['failed']), {'two.md'})
                catalogue.write_text(json.dumps({
                    'PATIENT_A1B2C3D4': {'canonical_name': 'Alex Example'},
                    'PATIENT_FFFFFFFF': {'canonical_name': 'Jamie Sample'}}))
                # The catalogue tab expects descriptive fields on each mapping.
                data = json.loads(catalogue.read_text())
                for details in data.values():
                    details.update(entity_type='PATIENT', relationship_context='', variations=[])
                catalogue.write_text(json.dumps(data))
                next(item for item in app.button if item.label == 'Retry failed restorations').click().run()
                self.assertFalse(app.exception)
                result = app.session_state['restoration_result']
                self.assertFalse(result['failed'])
                self.assertEqual(len(result['completed']), 2)
                self.assertEqual((Path(result['output']) / 'two.md').read_text(), 'Jamie Sample')
                self.assertTrue(any(item.label == 'Download restored documents ZIP'
                                    for item in app.get('download_button')))

    def test_multiple_uploads_restore_together(self):
        class Upload(BytesIO):
            def __init__(self, name):
                super().__init__(b'PATIENT_A1B2C3D4 [PHONE REMOVED]')
                self.name = name
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dirs = {name: str(root / name) for name in ('input', 'output', 'secure')}
            for folder in dirs.values():
                Path(folder).mkdir()
            (Path(dirs['secure']) / 'identity_catalogue.json').write_text(json.dumps({
                'PATIENT_A1B2C3D4': {'canonical_name': 'Alex Example'}}))
            script = 'from utils.reidentification_ui import render_reidentification\nrender_reidentification(' + repr(dirs) + ')'
            with patch('streamlit.file_uploader', return_value=[Upload('one.txt'), Upload('two.md')]):
                app = AppTest.from_string(script).run()
                next(item for item in app.button if item.label == 'Restore documents').click().run()
                self.assertFalse(app.exception)
                result = app.session_state['restoration_result']
                self.assertEqual(len(result['completed']), 2)
                self.assertFalse(result['failed'])


if __name__ == '__main__':
    unittest.main()
