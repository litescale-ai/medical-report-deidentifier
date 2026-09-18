"""Folder processing proofs use real files, with only model inference replaced."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from utils.batch import chunks, process_batch, scan_folder
from utils.document_formats import extract_document

TEXT = 'Patient: Alex Example. HPCSA MP 0723444. Practice No. 1270753. Tel: 0215550123. Dose 5 mg.\nHome address: 12 Fiction Road, Testville, 8001'
ENTITY = dict(canonical_name='Alex Example', entity_type='PATIENT', variations=['Alex Example'], relationship_context='')

class BatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_twenty_multipage_pdfs_resume_without_model_calls(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'input'
            root.mkdir()
            for index in range(20):
                folder = root / str(index)
                folder.mkdir()
                with pymupdf.open() as pdf:
                    for _ in range(3):
                        pdf.new_page().insert_text((40, 60), TEXT)
                    pdf.save(folder / 'report.pdf')
            output, secure = Path(directory) / 'output', Path(directory) / 'secure'
            files, _ = scan_folder(root, output)
            kwargs = dict(root=root, output=output, secure=secure, model_revision='test-model')
            with patch('utils.batch.discover_names', AsyncMock(return_value=[ENTITY])) as model:
                result = await process_batch(files, **kwargs)
            self.assertEqual(len(result['completed']), 20)
            self.assertFalse(result['failed'])
            self.assertEqual(model.await_count, 1)  # Identical text shares discovery, not twenty requests.
            for path in result['completed']:
                text = '\n'.join(text for _, text in extract_document(path))
                for sensitive in ('Alex Example', '0723444', '1270753', '0215550123', '12 Fiction Road', 'Testville', '8001'):
                    self.assertNotIn(sensitive, text)
                self.assertIn('5 mg', text)
            with patch('utils.batch.discover_names', side_effect=AssertionError('Unnecessary model call')):
                resumed = await process_batch(files, **kwargs)
            self.assertEqual(resumed['reused'], 20)
            self.assertFalse(resumed['failed'])
            self.assertEqual(len(list(root.rglob('*.pdf'))), 20)

    async def test_failed_chunk_resumes_and_does_not_export_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'input'
            root.mkdir()
            source = root / 'report.txt'
            source.write_text(TEXT + '\n' + ('Assessment stable. ' * 500))
            output, secure = Path(directory) / 'output', Path(directory) / 'secure'
            kwargs = dict(root=root, output=output, secure=secure, model_revision='test-model')
            with patch('utils.batch.discover_names', AsyncMock(side_effect=[[ENTITY], RuntimeError('offline')])) as model:
                result = await process_batch([source], **kwargs)
            self.assertEqual(model.await_count, 2)
            self.assertIn('report.txt', result['failed'])
            self.assertFalse((output / 'report.txt').exists())
            with patch('utils.batch.discover_names', AsyncMock(return_value=[])) as model:
                resumed = await process_batch([source], **kwargs)
            self.assertEqual(model.await_count, 1)
            self.assertFalse(resumed['failed'])
            self.assertNotIn('Alex Example', (output / 'report.txt').read_text())

    async def test_verification_failure_withholds_output_and_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'input'
            root.mkdir()
            source = root / 'report.txt'
            source.write_text(TEXT)
            output, secure = Path(directory) / 'output', Path(directory) / 'secure'
            kwargs = dict(root=root, output=output, secure=secure, model_revision='test-model')
            def broken_editor(source, target, replacements):
                Path(target).write_text(TEXT)
                return True
            with patch('utils.batch.discover_names', AsyncMock(return_value=[ENTITY])), patch('utils.batch.deidentify_document', broken_editor):
                result = await process_batch([source], **kwargs)
            self.assertIn('verification failed', result['failed']['report.txt'])
            self.assertFalse((output / 'report.txt').exists())
            (output / 'report.txt').write_text('User-owned output')
            with patch('utils.batch.discover_names', side_effect=AssertionError('Use cached entities')):
                result = await process_batch([source], **kwargs)
            self.assertIn('existing output', result['failed']['report.txt'])
            self.assertEqual((output / 'report.txt').read_text(), 'User-owned output')

    async def test_changed_source_is_rediscovered_and_only_owned_output_is_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'input'
            root.mkdir()
            source = root / 'report.txt'
            source.write_text(TEXT)
            kwargs = dict(root=root, output=Path(directory) / 'output',
                          secure=Path(directory) / 'secure', model_revision='test-model')
            with patch('utils.batch.discover_names', AsyncMock(return_value=[ENTITY])):
                initial = await process_batch([source], **kwargs)
            source.write_text(TEXT.replace('Alex Example', 'Morgan Sample'))
            updated_entity = {**ENTITY, 'canonical_name': 'Morgan Sample', 'variations': ['Morgan Sample']}
            with patch('utils.batch.discover_names', AsyncMock(return_value=[updated_entity])) as model:
                updated = await process_batch([source], **kwargs)
            self.assertEqual(model.await_count, 1)
            self.assertEqual(updated['reused'], 0)
            self.assertFalse(updated['failed'])
            self.assertNotIn('Morgan Sample', Path(updated['completed'][0]).read_text())
            self.assertEqual(initial['completed'], updated['completed'])

    def test_prepared_ocr_is_reused_for_extraction_and_editing(self):
        import pymupdf
        from utils.batch import prepare_source, file_digest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, ready = root / 'scan.pdf', root / 'ocr.pdf'
            with pymupdf.open() as original:
                page = original.new_page()
                page.insert_text((40, 60), TEXT)
                pixels = page.get_pixmap().tobytes('png')
                original.save(ready)
            with pymupdf.open() as scanned:
                page = scanned.new_page()
                page.insert_image(page.rect, stream=pixels)
                scanned.save(source)
            cache = root / 'cache'
            cache.mkdir()
            with patch('utils.batch._ocr_pdf', return_value=str(ready)) as ocr:
                first = prepare_source(source, cache, file_digest(source))
                second = prepare_source(source, cache, file_digest(source))
            self.assertEqual(first, second)
            self.assertEqual(ocr.call_count, 1)
            self.assertIn('Alex Example', '\n'.join(text for _, text in extract_document(first)))

    def test_scan_excludes_output_hidden_and_symlinks_reports_unsupported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'record.txt').write_text(TEXT)
            (root / 'sub').mkdir()
            (root / 'sub' / 'record.txt').write_text(TEXT)
            (root / 'image.png').write_bytes(b'image')
            (root / '.private').mkdir()
            (root / '.private' / 'secret.txt').write_text(TEXT)
            (root / 'link').symlink_to(root / 'sub', target_is_directory=True)
            output = root / 'output'
            output.mkdir()
            (output / 'old.txt').write_text(TEXT)
            files, skipped = scan_folder(root, output)
            self.assertEqual(len(files), 2)
            self.assertEqual(skipped, ['image.png'])
            self.assertEqual(len(scan_folder(root, output, recursive=False)[0]), 1)
            with self.assertRaises(ValueError):
                scan_folder(root, root)

    def test_chunks_cover_every_character_with_bounded_requests(self):
        text = '\n'.join(f'Line {index}: Alex Example attended.' for index in range(900))
        parts = list(chunks(text))
        self.assertTrue(all(len(part) <= 6000 for part in parts))
        for line in text.splitlines():
            self.assertTrue(any(line in part for part in parts))
        self.assertEqual(list(chunks('a' * 18000)), ['a' * 6000] * 3)

if __name__ == '__main__':
    unittest.main()
