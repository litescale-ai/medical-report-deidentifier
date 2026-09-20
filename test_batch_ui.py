"""Exercise the folder controls through Streamlit's real event loop."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from streamlit.testing.v1 import AppTest
from utils.batch import process_batch, retry_failed, DiscoveryReview


class BatchUiTest(unittest.TestCase):
    def test_shared_folder_controls_suggest_paths_and_preserve_custom_output(self):
        for prefix, suffix in [('ner', '-redacted'), ('batch', '-deidentified')]:
            with self.subTest(prefix=prefix):
                app = AppTest.from_string(
                    f"from utils.batch_ui import folder_controls\nfolder_controls('{prefix}', suffix='{suffix}')"
                ).run()
                app.text_input(key=prefix + '_folder').set_value('/tmp/Reports').run()
                self.assertEqual(app.text_input(key=prefix + '_output_folder').value, '/tmp/Reports' + suffix)
                app.text_input(key=prefix + '_folder').set_value('/tmp/Letters').run()
                self.assertEqual(app.text_input(key=prefix + '_output_folder').value, '/tmp/Letters' + suffix)
                app.text_input(key=prefix + '_output_folder').set_value('/tmp/My exports').run()
                app.text_input(key=prefix + '_folder').set_value('/tmp/Assessments').run()
                self.assertEqual(app.text_input(key=prefix + '_output_folder').value, '/tmp/My exports')
                app.checkbox(key=prefix + '_recursive').uncheck().run()
                self.assertEqual(app.text_input(key=prefix + '_output_folder').value, '/tmp/My exports')
                self.assertFalse(app.exception)

    def test_review_draft_manual_decision_and_user_approved_download(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            dirs = {name: str(home / name) for name in ('input', 'output', 'secure')}
            Path(dirs['input']).mkdir()
            source = Path(dirs['input']) / 'record.txt'
            source.write_text('   Alex Example    met Jamie Review.\n    Practice No. 1270753. Dose 5 mg.')
            async def run(files, **kwargs):
                return await process_batch(files, **kwargs, model_revision='ui-review')
            entity = dict(canonical_name='Alex Example', entity_type='PATIENT', variations=[], relationship_context='')
            issue = {'reason': 'Model returned an identifier absent from the source.',
                     'candidates': [dict(name='patients', kind='group', aliases=['patient']),
                                    dict(name='doctors', kind='group', aliases=['doctor'])]}
            script = "import streamlit as st\nfrom utils.batch_ui import render_batch\nst.session_state['_backend']='ollama'\nrender_batch(" + repr(dirs) + ')'
            with patch('utils.batch_ui.process_batch', run), patch(
                'utils.batch.discover_names', AsyncMock(side_effect=DiscoveryReview([entity], [issue]))
            ) as model:
                app = AppTest.from_string(script).run()
                next(item for item in app.radio if item.label == 'Document source').set_value('Folder').run()
                next(item for item in app.text_input if item.label == 'Input folder').set_value(dirs['input']).run()
                next(item for item in app.button if item.label == 'De-identify documents').click().run()
                self.assertFalse(app.exception)
                self.assertFalse(app.session_state['batch_result']['completed'])
                self.assertTrue(any(item.label == 'Download review draft' for item in app.get('download_button')))
                self.assertTrue(next(item for item in app.button if item.label == 'Apply review decision').disabled)
                self.assertTrue(any('category labels' in item.value for item in app.warning))
                self.assertTrue(any(item.label.startswith('Model suggestions not found')
                                    for item in app.expander))
                previews = [item.value for item in app.code if 'Dose 5 mg' in item.value]
                self.assertEqual(len(previews), 2)
                self.assertIn('Alex Example met Jamie Review.', previews[0])
                self.assertIn('1270753', previews[0])
                self.assertNotIn('Alex Example', previews[1])
                self.assertNotIn('1270753', previews[1])
                self.assertIn('Jamie Review', previews[1])
                next(item for item in app.radio if item.label == 'Review action').set_value('Add manual redactions').run()
                next(item for item in app.text_area if item.label == 'Text to remove').set_value('Jamie Review').run()
                next(item for item in app.checkbox if item.label.startswith('I reviewed')).check().run()
                next(item for item in app.button if item.label == 'Apply review decision').click().run()
                self.assertFalse(app.exception)
                result = app.session_state['batch_result']
                self.assertFalse(result['needs_review'])
                self.assertEqual(model.await_count, 1)
                self.assertEqual(len(result['completed']), 1)
                self.assertEqual(result['verifications'][result['completed'][0]], 'user_approved')
                self.assertNotIn('Jamie Review', Path(result['completed'][0]).read_text())
                self.assertTrue(any('User-approved' in item.value for item in app.caption))

    def test_default_model_and_configured_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            dirs = {name: str(Path(directory) / name) for name in ('input', 'output', 'secure')}
            for path in dirs.values():
                Path(path).mkdir()
            for env, expected in (({}, 'qwen3.5:2b'), ({'OLLAMA_MODEL': 'gemma4:e2b'}, 'gemma4:e2b')):
                with self.subTest(expected=expected), patch.dict('os.environ', env, clear=True), patch(
                    'dotenv.load_dotenv'
                ), patch('utils.helpers.get_data_dirs', return_value=dirs):
                    app = AppTest.from_file('app.py').run()
                    next(item for item in app.radio if item.label == 'Processing mode').set_value('De-identify documents only (faster)').run()
                    self.assertFalse(app.exception)
                    self.assertEqual(app.session_state['_ollama_model'], expected)
                    self.assertEqual(next(item for item in app.selectbox if item.label == 'Ollama Model').value, expected)

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
            async def retry(previous, **kwargs):
                return await retry_failed(previous, **kwargs, model_revision='ui-test')
            entity = dict(canonical_name='Alex Example', entity_type='PATIENT',
                          variations=['Alex Example'], relationship_context='')
            with patch.dict('os.environ', {'OLLAMA_MODEL': 'gemma4:e4b'}), patch('utils.helpers.get_data_dirs', return_value=dirs), patch(
                'utils.batch_ui.process_batch', run
            ), patch('utils.batch_ui.retry_failed', retry), patch('utils.batch.discover_names', AsyncMock(return_value=[entity])) as model:
                app = AppTest.from_file('app.py').run()
                next(item for item in app.radio if item.label == 'Processing mode').set_value('De-identify documents only (faster)').run()
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
                (input_dir / 'failed.txt').write_text('Another document. ' + (input_dir / 'record.txt').read_text())
                model.side_effect = RuntimeError('test model failure')
                next(item for item in app.button if item.label == 'De-identify documents').click().run()
                self.assertEqual(len(app.session_state['batch_result']['failed']), 1)
                next(item for item in app.selectbox if item.label == 'Ollama Model').set_value('qwen3.5:2b').run()
                model.side_effect = None
                with patch('utils.batch_ui.retry_failed', AsyncMock(side_effect=RuntimeError('model unavailable'))):
                    next(item for item in app.button if item.label == 'Retry failed documents').click().run()
                self.assertEqual(len(app.session_state['batch_result']['failed']), 1)
                app.run()
                next(item for item in app.button if item.label == 'Retry failed documents').click().run()
                result = app.session_state['batch_result']
                self.assertFalse(result['failed'])
                self.assertEqual(len(result['completed']), 2)
                self.assertEqual(result['model'], 'qwen3.5:2b')
                self.assertEqual(result['stats']['totals']['documents'], 1)
                next(item for item in app.radio if item.label == 'Processing mode').set_value('Create clinical chronology').run()
                self.assertFalse(app.exception)
                self.assertTrue(any(item.label == '🚀 Execute Pipeline' for item in app.button))

if __name__ == '__main__':
    unittest.main()
