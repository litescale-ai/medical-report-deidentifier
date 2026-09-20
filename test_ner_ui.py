"""Exercise both NER outputs through the real Streamlit event loop."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
from streamlit.testing.v1 import AppTest

from test_clinical_packet import Detector, person


class NerUiTest(unittest.TestCase):
    def test_select_both_prepare_compare_edit_approve_and_save_keep_terms(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            dirs = {name: str(home / name) for name in ('input', 'output', 'secure')}
            for path in dirs.values():
                Path(path).mkdir()
            with pymupdf.open() as pdf:
                page = pdf.new_page()
                page.insert_text((40, 60), 'Alex Example. Beery VMI score 85.')
                page.insert_text((40, 100), 'Practice No. 1270753. Dose 5 mg.')
                pdf.save(home / 'input/source.pdf')
            with patch('utils.helpers.get_data_dirs', return_value=dirs), patch(
                'utils.ner_ui.importlib.util.find_spec', return_value=object()), patch(
                'utils.ner_ui.load_ner', return_value=Detector([person('Alex Example'), person('Beery VMI')])):
                app = AppTest.from_file('app.py').run()
                self.assertEqual(next(item for item in app.radio if item.label == 'Processing mode').value, 'Prepare for AI (local NER)')
                self.assertEqual(app.radio(key='ner_format').value, 'Both')
                self.assertEqual(next(item for item in app.radio if item.label == 'NER document source').value, 'Folder')
                self.assertFalse(app.exception)
                self.assertFalse(any(item.label == 'Execution Mode' for item in app.radio))
                next(item for item in app.text_area if item.label == 'Keep terms').set_value('Beery VMI\nDigit span').run()
                next(item for item in app.button if item.label == 'Save keep terms').click().run()
                self.assertEqual((home / 'secure/ner-keep-terms.txt').read_text(), 'Beery VMI\nDigit span\n')
                next(item for item in app.radio if item.label == 'Output format').set_value('Both').run()
                next(item for item in app.radio if item.label == 'NER document source').set_value('Folder').run()
                next(item for item in app.text_input if item.label == 'Input folder').set_value(dirs['input']).run()
                self.assertEqual(next(item for item in app.text_input if item.label == 'Markdown output folder').value,
                                 str(home / 'input-redacted/markdown'))
                selected_output = home / 'input' / 'Prepared reports'
                selected_output.mkdir()
                pdf_output = home / 'input' / 'Prepared PDFs'
                pdf_output.mkdir()
                (pdf_output / 'earlier.pdf').write_bytes((home / 'input/source.pdf').read_bytes())
                previous = selected_output / 'previous.pdf'
                previous.write_bytes((home / 'input/source.pdf').read_bytes())
                next(item for item in app.text_input if item.label == 'Markdown output folder').set_value(str(selected_output)).run()
                next(item for item in app.text_input if item.label == 'PDF output folder').set_value(str(pdf_output)).run()
                app.radio(key='ner_format').set_value('Markdown packet').run()
                self.assertFalse(any(item.label == 'PDF output folder' for item in app.text_input))
                app.radio(key='ner_format').set_value('Both').run()
                self.assertEqual(app.text_input(key='ner_pdf_output_folder').value, str(pdf_output))
                self.assertEqual(app.text_input(key='ner_markdown_output_folder').value, str(selected_output))
                self.assertTrue(next(item for item in app.button if item.label == 'Prepare documents').disabled)
                next(item for item in app.checkbox if item.label == 'These documents belong to one patient.').check().run()
                next(item for item in app.button if item.label == 'Prepare documents').click().run()
                self.assertFalse(app.exception)
                result = app.session_state['ner_result']
                self.assertFalse(result['failed'])
                self.assertEqual(result['stats']['documents'], 1)
                self.assertEqual(result['export_folders'], {'markdown': str(selected_output), 'pdf': str(pdf_output)})
                self.assertIn('Beery VMI', result['markdown'])
                self.assertEqual(len(result['pdfs']), 1)
                statuses = next(item.value for item in app.dataframe if 'File' in item.value.columns)
                self.assertEqual(statuses.iloc[0]['File'], 'source.pdf')
                self.assertEqual(statuses.iloc[0]['PDF'], 'Ready for review')
                self.assertEqual(next(item for item in app.selectbox if item.label == 'Page or section').options,
                                 ['source.pdf, page 1'])
                self.assertTrue(next(item for item in app.button if item.label == 'Save reviewed outputs').disabled)
                next(item for item in app.radio if item.label == 'Review output').set_value('Redacted PDFs').run()
                previews = [item.value for item in app.code if 'Dose 5 mg' in item.value]
                self.assertEqual(len(previews), 2)
                self.assertIn('Alex Example', previews[0])
                self.assertNotIn('Alex Example', previews[1])
                self.assertIn('Beery VMI', previews[1])
                next(item for item in app.checkbox if item.label.startswith('I reviewed all')).check().run()
                next(item for item in app.button if item.label == 'Save reviewed outputs').click().run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.session_state['ner_approved']['files']), 2)
                self.assertEqual(app.session_state['ner_approved']['files'],
                                 [str(selected_output / 'source-redacted.md'),
                                  str(pdf_output / 'source-redacted.pdf')])
                self.assertIn(str(selected_output), [item.value for item in app.code])
                self.assertEqual(previous.read_bytes(), (home / 'input/source.pdf').read_bytes())
                manifest = json.loads((Path(result['output']) / 'PRIVATE.json').read_text())
                self.assertEqual(manifest['verification'], 'user_approved')
                next(item for item in app.text_area if item.label == 'Reviewed Markdown').set_value(result['markdown'] + '\nEdited').run()
                self.assertTrue(next(item for item in app.button if item.label == 'Save reviewed outputs').disabled)
                self.assertFalse(any(item.label == 'Download source-redacted.pdf' for item in app.get('download_button')))
                edited = result['markdown'] + '\nAlex Example called 0215550123.'
                next(item for item in app.text_area if item.label == 'Reviewed Markdown').set_value(edited).run()
                self.assertFalse(app.exception)
                flagged = next(item.value for item in app.dataframe if 'Flagged text' in item.value.columns)
                self.assertEqual(set(flagged['Flagged text']), {'Alex Example', '0215550123'})
                self.assertTrue(all(flagged['Packet line'] > 0))
                next(item for item in app.checkbox if item.label.startswith('I reviewed all')).check().run()
                self.assertTrue(next(item for item in app.button if item.label == 'Save reviewed outputs').disabled)
                next(item for item in app.checkbox if item.label == 'Keep the flagged text and save anyway.').check().run()
                next(item for item in app.text_input if item.label == 'Reason for keeping the text (optional)').set_value('Synthetic review override.').run()
                next(item for item in app.button if item.label == 'Save reviewed outputs').click().run()
                self.assertFalse(app.exception)
                self.assertTrue(app.session_state['ner_approved']['override'])
                self.assertEqual(app.session_state['ner_approved']['folders'],
                                 {'markdown': str(selected_output) + '-2', 'pdf': str(pdf_output) + '-2'})
                manifest = json.loads((Path(result['output']) / 'PRIVATE.json').read_text())
                self.assertEqual(manifest['verification'], 'user_override')
                self.assertEqual(manifest['reviews'][-1]['note'], 'Synthetic review override.')
                next(item for item in app.text_area if item.label == 'Reviewed Markdown').set_value(edited + ' Changed.').run()
                self.assertFalse(next(item for item in app.checkbox if item.label == 'Keep the flagged text and save anyway.').value)
                self.assertTrue(next(item for item in app.button if item.label == 'Save reviewed outputs').disabled)
                # Withheld PDFs remain visible under their filenames, even when no PDF can be downloaded.
                import shutil
                with patch('utils.ner_workflow.deidentify_pdf',
                           side_effect=lambda source, target, *args, **kwargs: shutil.copyfile(source, target)):
                    next(item for item in app.button if item.label == 'Prepare documents').click().run()
                self.assertFalse(app.exception)
                statuses = next(item.value for item in app.dataframe if 'File' in item.value.columns)
                row = statuses.loc[statuses['File'] == 'source.pdf'].iloc[0]
                self.assertEqual(row['PDF'], 'Failed')
                self.assertEqual(row['Markdown'], 'Included in packet')
                self.assertIn('Alex Example', row['Error'])
                self.assertTrue(any(item.label == 'Flagged text in source.pdf' for item in app.expander))


if __name__ == '__main__':
    unittest.main()
