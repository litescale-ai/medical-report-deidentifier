"""Exercise the folder controls through Streamlit's real event loop."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from streamlit.testing.v1 import AppTest
from utils.batch import process_batch


class BatchUiTest(unittest.TestCase):
    def test_folder_selection_processing_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            input_dir = home / 'input'
            input_dir.mkdir()
            (input_dir / 'record.txt').write_text('Patient: Alex Example. Practice No. 1270753. Dose 5 mg.')
            dirs = {name: str(home / name) for name in ('input', 'output', 'secure')}
            for path in dirs.values():
                Path(path).mkdir(exist_ok=True)
            async def run(files, **kwargs):
                return await process_batch(files, **kwargs, model_revision='ui-test')
            entity = dict(canonical_name='Alex Example', entity_type='PATIENT',
                          variations=['Alex Example'], relationship_context='')
            with patch('utils.helpers.get_data_dirs', return_value=dirs), patch(
                'utils.batch_ui.process_batch', run
            ), patch('utils.batch.discover_names', AsyncMock(return_value=[entity])) as model:
                app = AppTest.from_file('app.py').run()
                self.assertFalse(app.exception)
                next(item for item in app.radio if item.label == 'Document source').set_value('Folder').run()
                next(item for item in app.text_input if item.label == 'Input folder').set_value(str(input_dir)).run()
                next(item for item in app.button if item.label == 'De-identify documents').click().run()
                self.assertFalse(app.exception)
                result = app.session_state['batch_result']
                self.assertFalse(result['failed'])
                self.assertTrue(Path(result['manifest']).exists())
                self.assertTrue(any(item.label == 'Generation tokens/s' for item in app.metric))
                self.assertTrue(any(item.label == 'Elapsed' for item in app.metric))
                self.assertEqual(len(result['completed']), 1)
                text = Path(result['completed'][0]).read_text()
                self.assertNotIn('Alex Example', text)
                self.assertNotIn('1270753', text)
                self.assertIn('5 mg', text)
                next(item for item in app.button if item.label == 'De-identify documents').click().run()
                self.assertEqual(app.session_state['batch_result']['reused'], 1)
                self.assertEqual(model.await_count, 1)
                next(item for item in app.radio if item.label == 'Processing mode').set_value('Create clinical chronology').run()
                self.assertFalse(app.exception)
                self.assertTrue(any(item.label == '🚀 Execute Pipeline' for item in app.button))

if __name__ == '__main__':
    unittest.main()
