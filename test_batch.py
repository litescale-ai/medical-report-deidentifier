"""Folder processing proofs use real files, with only model inference replaced."""
import tempfile
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from utils.batch import chunks, process_batch, retry_failed, scan_folder
from utils.document_formats import extract_document

TEXT = 'Patient: Alex Example. HPCSA MP 0723444. Practice No. 1270753. Tel: 0215550123. Dose 5 mg.\nHome address: 12 Fiction Road, Testville, 8001'
ENTITY = dict(canonical_name='Alex Example', entity_type='PATIENT', variations=['Alex Example'], relationship_context='')

class BatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_locked_pdf_fails_before_page_access_or_model_calls(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'input'
            root.mkdir()
            source = root / 'locked.pdf'
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((40, 60), 'Private document')
                pdf.save(source, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                         owner_pw='test-owner', user_pw='test-reader')
            with self.assertRaisesRegex(ValueError, 'password-protected.*unlocked copy'):
                extract_document(source)
            with patch('utils.batch.discover_names', AsyncMock()) as model:
                result = await process_batch([source], root=root, output=Path(directory) / 'output',
                                             secure=Path(directory) / 'secure', model_revision='test-model')
            model.assert_not_awaited()
            self.assertFalse(result['completed'])
            self.assertRegex(result['failed']['locked.pdf'], 'password-protected.*unlocked copy')

    async def test_pdf_email_removal_passes_verification(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'input'
            root.mkdir()
            source = root / 'email.pdf'
            with pymupdf.open() as pdf:
                page = pdf.new_page()
                page.insert_text((40, 60), 'Email address:')
                page.insert_text((60, 80), 'person@example.test')
                page.insert_text((400, 80), ';')
                page.insert_text((40, 110), 'Dose: 5 mg')
                pdf.save(source)
            with patch('utils.batch.discover_names', AsyncMock(return_value=[])):
                result = await process_batch([source], root=root, output=Path(directory) / 'output',
                                             secure=Path(directory) / 'secure', model_revision='test-model')
            self.assertFalse(result['failed'])
            self.assertEqual(len(result['completed']), 1)
            text = '\n'.join(text for _, text in extract_document(result['completed'][0]))
            self.assertNotIn('person@example.test', text)
            self.assertIn('Dose: 5 mg', text)

    async def test_retry_failed_with_same_then_different_model_preserves_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'input'
            root.mkdir()
            files = []
            for index in range(3):
                source = root / f'{index}.txt'
                source.write_text(f'Document {index}\n' + TEXT)
                files.append(source)
            secure = Path(directory) / 'secure'
            with patch('utils.batch.discover_names', AsyncMock(side_effect=[[ENTITY], RuntimeError('offline'), RuntimeError('offline')])):
                initial = await process_batch(files, root=root, output=Path(directory) / 'output',
                                              secure=secure, model='first', model_revision='one')
            original = Path(initial['completed'][0])
            original_bytes, original_mtime = original.read_bytes(), original.stat().st_mtime_ns
            with patch('utils.batch.discover_names', AsyncMock(side_effect=[[ENTITY], RuntimeError('offline')])) as model:
                same = await retry_failed(initial, secure=secure, model='first', model_revision='one')
            self.assertEqual(model.await_count, 2)
            self.assertEqual(len(same['completed']), 2)
            self.assertEqual(len(same['failed']), 1)
            parent = Path(same['stats']['retry_of'])
            self.assertNotEqual(parent, Path(same['manifest']))
            self.assertEqual(json.loads(parent.read_text())['totals']['failed'], 2)
            with patch('utils.batch.discover_names', AsyncMock(return_value=[ENTITY])) as model:
                changed = await retry_failed(same, secure=secure, model='second', model_revision='two')
            self.assertEqual(model.await_count, 1)
            self.assertEqual(model.call_args.kwargs['model'], 'second')
            self.assertEqual(len(changed['completed']), 3)
            self.assertFalse(changed['failed'])
            self.assertEqual(changed['stats']['totals']['documents'], 1)
            self.assertEqual(changed['stats']['batch_completed'], 3)
            self.assertEqual(original.read_bytes(), original_bytes)
            self.assertEqual(original.stat().st_mtime_ns, original_mtime)

    async def test_live_manifest_measured_tokens_and_cached_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'input'
            root.mkdir()
            source = root / 'report.txt'
            source.write_text(TEXT)
            updates = []
            kwargs = dict(root=root, output=Path(directory) / 'output',
                          secure=Path(directory) / 'secure', model_revision='test-model', on_update=updates.append)
            async def measured_model(text, *, metrics_callback, **kwargs):
                self.assertEqual(updates[-1]['status'], 'running')
                self.assertEqual(updates[-1]['totals']['extracted'], 1)
                self.assertEqual(updates[-1]['totals']['completed'], 0)
                metrics_callback(dict(input_tokens=64, output_tokens=48, generation_seconds=2,
                                      prompt_seconds=1, load_seconds=0.5, tokens_per_second=24))
                return [ENTITY]
            with patch('utils.batch.discover_names', measured_model):
                result = await process_batch([source], **kwargs)
            manifest = Path(result['manifest'])
            saved = json.loads(manifest.read_text())
            self.assertEqual(saved, result['stats'])
            self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
            self.assertEqual(saved['status'], 'completed')
            self.assertEqual(saved['tokens']['tokens_per_second'], 24)
            self.assertEqual(saved['tokens']['input'], 64)
            self.assertEqual(saved['totals']['words'], len(TEXT.split()))
            self.assertEqual(saved['totals']['completed'], 1)
            self.assertEqual(saved['totals']['identity_types']['PATIENT'], 1)
            self.assertEqual(saved['totals']['identity_types']['ADDRESS'], 1)
            self.assertNotIn('Alex Example', manifest.read_text())
            with patch('utils.batch.discover_names', side_effect=AssertionError('No new inference')):
                resumed = await process_batch([source], **kwargs)
            self.assertEqual(resumed['stats']['tokens']['requests'], 0)
            self.assertIsNone(resumed['stats']['tokens']['tokens_per_second'])
            self.assertEqual(resumed['stats']['totals']['identities'], saved['totals']['identities'])
            self.assertEqual(resumed['stats']['previous_runs'][-1]['tokens']['output'], 48)

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
            self.assertEqual(result['stats']['totals']['pages'], 60)
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
            self.assertEqual(result['stats']['status'], 'completed_with_errors')
            self.assertEqual(result['stats']['totals']['failed'], 1)
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
