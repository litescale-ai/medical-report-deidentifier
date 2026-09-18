"""Bulk restoration tests use generated files and an isolated synthetic catalogue."""
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_document_formats import create_fixtures, extracted_text, PATIENT, DOCTOR
from utils.batch import scan_folder
from utils.document_editor import deidentify_document
from utils.reidentification import restore_batch
from utils.reidentification_ui import make_archive, save_uploads

TOKEN = 'PATIENT_A1B2C3D4'
CATALOGUE = {TOKEN: {'canonical_name': PATIENT}, 'DOCTOR_1234ABCD': {'canonical_name': DOCTOR}}


class RestorationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root, self.output, self.secure = [self.base / name for name in ('returned', 'restored', 'secure')]
        self.root.mkdir()
        self.secure.mkdir()
        self.catalogue(CATALOGUE)

    def catalogue(self, value):
        (self.secure / 'identity_catalogue.json').write_text(json.dumps(value))

    def run_batch(self, files=None, **kwargs):
        if files is None:
            files, _ = scan_folder(self.root, self.output)
        return restore_batch(files, root=self.root, output=self.output, secure=self.secure, **kwargs)

    def test_mixed_formats_nested_same_names_and_private_archive(self):
        originals = {}
        replacements = {PATIENT: TOKEN, DOCTOR: 'DOCTOR_1234ABCD'}
        for folder in ('one', 'two/nested'):
            for source in create_fixtures(self.base / 'originals' / folder):
                target = self.root / folder / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                deidentify_document(str(source), str(target), replacements)
                originals[target] = target.read_bytes()
        updates = []
        with patch('utils.batch.discover_names', side_effect=AssertionError('Restoration must not call a model')):
            result = self.run_batch(on_update=updates.append)
        self.assertFalse(result['failed'])
        self.assertEqual(len(result['completed']), 14)
        for filename in result['completed']:
            text = extracted_text(filename)
            self.assertIn(PATIENT, text)
            self.assertIn(DOCTOR, text)
            self.assertIn('improving mobility', text)
            self.assertNotIn(TOKEN, text)
            self.assertEqual(Path(filename).stat().st_mode & 0o777, 0o600)
        for source, original in originals.items():
            self.assertEqual(source.read_bytes(), original)
        self.assertTrue(any(0 < state['totals']['processed'] < 14 for state in updates))
        self.assertEqual(updates[-1]['totals']['completed'], 14)
        self.assertEqual(updates[-1]['totals']['pages'], 2)
        manifest = Path(result['manifest'])
        self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(PATIENT, manifest.read_text())
        with zipfile.ZipFile(BytesIO(make_archive(result))) as archive:
            self.assertEqual(len(archive.namelist()), 14)
            self.assertIn('one/record.pdf', archive.namelist())
            self.assertIn('two/nested/record.pdf', archive.namelist())
        with patch('utils.reidentification.reidentify_document', side_effect=AssertionError('Unnecessary rewrite')):
            resumed = self.run_batch()
        self.assertFalse(resumed['failed'])
        self.assertEqual(resumed['reused'], 14)

    def test_twenty_multipage_pdfs_restore_all_pages(self):
        import pymupdf
        for index in range(20):
            with pymupdf.open() as pdf:
                for _ in range(3):
                    pdf.new_page().insert_text((40, 60), f'{TOKEN}. Dose 5 mg. [PHONE REMOVED]')
                pdf.save(self.root / f'{index}.pdf')
        result = self.run_batch()
        self.assertEqual(len(result['completed']), 20)
        self.assertEqual(result['stats']['totals']['pages'], 60)
        self.assertEqual(result['stats']['totals']['restored_occurrences'], 60)
        for filename in result['completed']:
            with pymupdf.open(filename) as pdf:
                for page in pdf:
                    text = page.get_text()
                    self.assertIn(PATIENT, text)
                    self.assertIn('[PHONE REMOVED]', text)
                    self.assertIn('5 mg', text)
                    self.assertNotIn(TOKEN, text)

    def test_failure_isolated_retry_and_unknown_tokens_withheld(self):
        (self.root / 'good.txt').write_text(TOKEN + ' [PHONE REMOVED] [ADDRESS REMOVED] [REGISTRATION REMOVED]')
        (self.root / 'bad.pdf').write_bytes(b'not a pdf')
        (self.root / 'unknown.txt').write_text('PATIENT_FFFFFFFF')
        initial = self.run_batch()
        self.assertEqual(len(initial['completed']), 1)
        self.assertEqual(set(initial['failed']), {'bad.pdf', 'unknown.txt'})
        self.assertFalse((self.output / 'bad.pdf').exists())
        self.assertFalse((self.output / 'unknown.txt').exists())
        success = self.output / 'good.txt'
        before = success.stat().st_mtime_ns
        self.assertIn('[ADDRESS REMOVED]', success.read_text())
        self.catalogue({**CATALOGUE, 'PATIENT_FFFFFFFF': {'canonical_name': 'Jamie Sample'}})
        retried = self.run_batch([self.root / 'unknown.txt'])
        self.assertFalse(retried['failed'])
        self.assertEqual((self.output / 'unknown.txt').read_text(), 'Jamie Sample')
        self.assertEqual(success.stat().st_mtime_ns, before)
        self.assertEqual(retried['stats']['files']['good.txt']['status'], 'completed')

    def test_json_escaping_and_single_file_cli_formats(self):
        name = 'Zoë "Example" \\ Test'
        self.catalogue({TOKEN: {'canonical_name': name}})
        (self.root / 'report.json').write_text(json.dumps({TOKEN: ['Patient: ' + TOKEN, '[PHONE REMOVED]']}))
        result = self.run_batch()
        self.assertFalse(result['failed'])
        self.assertEqual(json.loads((self.output / 'report.json').read_text()), {name: ['Patient: ' + name, '[PHONE REMOVED]']})
        redacted = self.base / 'roundtrip.json'
        deidentify_document(str(self.output / 'report.json'), str(redacted), {name: TOKEN})
        self.assertEqual(json.loads(redacted.read_text()), {TOKEN: ['Patient: ' + TOKEN, '[PHONE REMOVED]']})
        from reidentify import reidentify_report
        for extension in ('.md', '.html', '.json'):
            source = self.root / ('single' + extension)
            source.write_text(json.dumps({'name': TOKEN}) if extension == '.json' else TOKEN)
            output = self.base / ('single-restored' + extension)
            value = reidentify_report(str(source), str(output), secure=self.secure)
            self.assertTrue(output.exists())
            if extension == '.json':
                self.assertEqual(json.loads(value)['name'], name)
            else:
                self.assertIn('Zoë', value)

    def test_failed_export_verification_never_installs_partial_output(self):
        source = self.root / 'record.txt'
        source.write_text(TOKEN)
        def broken_restore(source, target, catalogue):
            Path(target).write_text(Path(source).read_text())
            return True
        with patch('utils.reidentification.reidentify_document', broken_restore):
            result = self.run_batch()
        self.assertIn('Pseudonyms remain', result['failed']['record.txt'])
        self.assertFalse((self.output / 'record.txt').exists())
        self.assertEqual(result['stats']['totals']['restored_occurrences'], 0)
        self.assertFalse(list(self.output.glob('.guardian-restore-*')))

    def test_json_key_collision_is_withheld_without_losing_values(self):
        self.catalogue({TOKEN: {'canonical_name': 'Alex Example'},
                        'DOCTOR_1234ABCD': {'canonical_name': 'Alex Example'}})
        source = self.root / 'record.json'
        original = json.dumps({TOKEN: 'first clinical record', 'DOCTOR_1234ABCD': 'second clinical record'})
        source.write_text(original)
        result = self.run_batch()
        self.assertIn('duplicate JSON keys', result['failed']['record.json'])
        self.assertFalse((self.output / 'record.json').exists())
        self.assertEqual(source.read_text(), original)

    def test_missing_invalid_catalogue_and_unsafe_output(self):
        source = self.root / 'record.txt'
        source.write_text(TOKEN)
        for value in ({}, [], {TOKEN: {}}, {'[PHONE REMOVED]': {'canonical_name': '0215550123'}}):
            self.catalogue(value)
            with self.assertRaises(ValueError):
                self.run_batch()
            self.assertFalse(self.output.exists())
        (self.secure / 'identity_catalogue.json').write_text('{broken')
        with self.assertRaisesRegex(ValueError, 'valid JSON'):
            self.run_batch()
        (self.secure / 'identity_catalogue.json').unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.run_batch()
        self.catalogue(CATALOGUE)
        with self.assertRaises(ValueError):
            restore_batch([source], root=self.root, output=self.root, secure=self.secure)
        link = self.root / 'link.txt'
        link.symlink_to(source)
        with self.assertRaises(ValueError):
            self.run_batch([link])

    def test_user_outputs_protected_and_source_and_catalogue_changes_invalidate_cache(self):
        source = self.root / 'record.txt'
        source.write_text(TOKEN)
        self.run_batch()
        self.catalogue({TOKEN: {'canonical_name': 'New Name'}})
        changed = self.run_batch()
        self.assertEqual(changed['reused'], 0)
        self.assertEqual((self.output / source.name).read_text(), 'New Name')
        source.write_text(TOKEN + '. Edited clinical content.')
        self.run_batch()
        self.assertIn('Edited clinical content', (self.output / source.name).read_text())
        (self.output / source.name).write_text('User-owned changes')
        result = self.run_batch()
        self.assertEqual(set(result['failed']), {'record.txt'})
        self.assertEqual((self.output / source.name).read_text(), 'User-owned changes')

    def test_cli_folder_exit_codes_and_no_recursive(self):
        (self.root / 'record.txt').write_text(TOKEN)
        (self.root / 'nested').mkdir()
        (self.root / 'nested' / 'bad.pdf').write_bytes(b'bad pdf')
        command = [sys.executable, 'reidentify.py', str(self.root), '-o', str(self.output), '--secure-dir', str(self.secure)]
        shallow = subprocess.run(command + ['--no-recursive'], capture_output=True, text=True)
        self.assertEqual(shallow.returncode, 0, shallow.stderr)
        recursive = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(recursive.returncode, 1)
        self.assertIn('nested/bad.pdf', recursive.stdout)
        missing = subprocess.run([sys.executable, 'reidentify.py', str(self.root / 'missing.txt'), '--secure-dir', str(self.secure)], capture_output=True, text=True)
        self.assertEqual(missing.returncode, 1)

    def test_upload_names_do_not_collide(self):
        class Upload(BytesIO):
            def __init__(self, name, data):
                super().__init__(data)
                self.name = name
        uploads = [Upload('one.txt', TOKEN.encode()), Upload('two.md', TOKEN.encode())]
        root, files = save_uploads(uploads, self.secure)
        result = restore_batch(files, root=root, output=self.output, secure=self.secure)
        self.assertEqual(len(result['completed']), 2)
        with self.assertRaisesRegex(ValueError, 'distinct filenames'):
            save_uploads([uploads[0], uploads[0]], self.secure)
        with self.assertRaisesRegex(ValueError, 'distinct filenames'):
            save_uploads([Upload('one.txt', b'a'), Upload('ONE.TXT', b'b')], self.secure)


if __name__ == '__main__':
    unittest.main()
