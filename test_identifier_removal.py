"""Identifiers must disappear even when the local model discovers no entities."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agents.deidentifier import perform_deidentification
from utils.document_editor import deidentify_document, reidentify_document, write_synthesis_summary
from utils.document_formats import extract_document

TEXT = ('HPCSA MP 0723444\nPractice No. 1270753\n'
        'Tel: (021) 555-0123\nMobile: +27 82 555 0199\n'
        'Fax: 0215550124\nPhone: +44 20 7946 0958\n'
        'Assessment: improving. Dose 5 mg. BP 120/80. Date 2024-01-02.\n')
NUMBERS = ('0723444', '1270753', '(021) 555-0123', '+27 82 555 0199', '0215550124', '+44 20 7946 0958')

class IdentifierRemovalTest(unittest.TestCase):
    def assert_removed(self, text):
        for number in NUMBERS:
            self.assertNotIn(number, text)
        self.assertIn('Dose 5 mg. BP 120/80. Date 2024-01-02.', text)

    def test_numbers_removed_without_model_discovery(self):
        result, catalogue, replacements = perform_deidentification({'patient_summary': TEXT}, [])
        self.assert_removed(result['patient_summary'])
        self.assertFalse(catalogue)  # These removals must not be reversible pseudonyms.
        self.assertTrue(replacements)

    def test_escaped_names_and_replacement_tokens_are_not_replaced_twice(self):
        entities = [
            {'canonical_name': 'Alex "Example"', 'entity_type': 'PATIENT',
             'relationship_context': 'Patient', 'variations': []},
            {'canonical_name': 'PATIENT', 'entity_type': 'DOCTOR',
             'relationship_context': 'Clinician', 'variations': []}]
        with patch('agents.deidentifier.generate_pseudonym_hash', side_effect=['PATIENT_TEST', 'DOCTOR_TEST']):
            result, _, _ = perform_deidentification({'text': 'Alex "Example" met PATIENT.'}, entities)
        self.assertEqual(result['text'], 'PATIENT_TEST met DOCTOR_TEST.')

    def test_registration_variants_and_unlabelled_phones(self):
        text = ('HPCSA: MP0723444; Practice Number: 1270753; Pr. No. 7654321; '
                'PCNS 1122334; 082 555 0199; 021-555-0123; +27 (0)21 555 0123')
        result, _, _ = perform_deidentification({'text': text}, [])
        self.assertFalse(any(char.isdigit() for char in result['text']))

    def test_model_name_and_relationship_cannot_restore_numbers(self):
        entity = {'canonical_name': 'Dr Example, Tel: 0215550123',
                  'entity_type': 'DOCTOR', 'variations': [],
                  'relationship_context': 'Practice No. 1270753'}
        with patch('agents.deidentifier.generate_pseudonym_hash', return_value='DOCTOR_TEST'):
            result, catalogue, replacements = perform_deidentification(
                {'patient_summary': entity['canonical_name']}, [entity])
        self.assertNotIn('0215550123', catalogue['DOCTOR_TEST']['canonical_name'])
        with tempfile.TemporaryDirectory() as directory:
            summary = write_synthesis_summary(str(Path(directory) / 'summary.txt'), result, catalogue)
        self.assertNotIn('1270753', summary)
        self.assertIn('DOCTOR_TEST', result['patient_summary'])

    def test_original_numbers_survive_chronology_omissions_and_all_exports(self):
        from docx import Document
        from openpyxl import Workbook
        from pptx import Presentation
        from pptx.util import Inches
        import pymupdf
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = []
            for ext in ('txt', 'md', 'html'):
                source = root / f'input.{ext}'
                source.write_text('<p>' + TEXT.replace('\n', '</p><p>') + '</p>' if ext == 'html' else TEXT)
                sources.append(source)
            doc = Document()
            doc.add_paragraph(TEXT)
            doc.save(root / 'input.docx')
            book = Workbook()
            book.active['A1'] = TEXT
            book.save(root / 'input.xlsx')
            book.close()
            deck = Presentation()
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            slide.shapes.add_textbox(Inches(1), Inches(1), Inches(9), Inches(5)).text_frame.text = TEXT
            deck.save(root / 'input.pptx')
            with pymupdf.open() as pdf:
                for _ in range(3):
                    pdf.new_page().insert_text((40, 60), TEXT)
                pdf.save(root / 'input.pdf')
            sources.extend(root / ('input.' + ext) for ext in ('docx', 'xlsx', 'pptx', 'pdf'))
            for source in sources:
                with self.subTest(format=source.suffix):
                    extracted = extract_document(source)
                    _, catalogue, replacements = perform_deidentification(
                        {'patient_summary': 'Assessment: improving.'}, [], source_data=extracted)
                    output = root / ('output' + source.suffix)
                    self.assertTrue(deidentify_document(str(source), str(output), replacements))
                    self.assert_removed('\n'.join(text for _, text in extract_document(output)))
                    restored = root / ('restored' + source.suffix)
                    reidentify_document(str(output), str(restored), catalogue)
                    self.assert_removed('\n'.join(text for _, text in extract_document(restored)))

if __name__ == '__main__':
    unittest.main()
