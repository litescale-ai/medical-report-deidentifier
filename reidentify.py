"""Restore one returned document or an entire folder using the private catalogue."""
import argparse
from pathlib import Path
import sys
import tempfile

from utils.batch import scan_folder
from utils.document_editor import reidentify_document, RECIPIENT_INSTRUCTIONS
from utils.document_formats import DOCUMENT_EXTENSIONS, TEXT_EXTENSIONS
from utils.helpers import get_data_dirs
from utils.reidentification import load_catalogue, restore_batch


def reidentify_report(pseudonymised_filepath: str, output_filepath: str = None, *, secure=None) -> str:
    """Restore one document. Text formats return content; Office/PDF return a path.

    Existing callers may request text without writing an output. Folder callers
    use restore_batch for isolated failures, ownership checks and resumable exports.
    """
    source = Path(pseudonymised_filepath).expanduser().resolve()
    if source.suffix.lower() not in DOCUMENT_EXTENSIONS:
        raise ValueError(f'Unsupported document extension: {source.suffix}')
    catalogue = load_catalogue(Path(secure or get_data_dirs()['secure']) / 'identity_catalogue.json')
    target = Path(output_filepath).expanduser().resolve() if output_filepath else None
    if target == source:
        raise ValueError('Choose an output path different from the source.')
    if target and target.exists():
        raise ValueError('The output already exists. Choose another filename or use folder mode to resume.')
    if target is None and source.suffix.lower() not in TEXT_EXTENSIONS:
        target = source.with_name('reidentified_' + source.name)
        if target.exists():
            raise ValueError('The output already exists. Choose another filename.')
    with tempfile.TemporaryDirectory(prefix='guardian-restore-') as temporary:
        staged = Path(temporary) / source.name
        if not reidentify_document(str(source), str(staged), catalogue):
            raise ValueError('This document format cannot be restored.')
        text = None
        if source.suffix.lower() in TEXT_EXTENSIONS:
            text = staged.read_text()
            if source.suffix.lower() == '.txt' and text.startswith(RECIPIENT_INSTRUCTIONS):
                text = text.removeprefix(RECIPIENT_INSTRUCTIONS)
                staged.write_text(text)
        if target:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Exclusive creation preserves existing files even if another process creates one meanwhile.
            with target.open('xb') as stream:
                target.chmod(0o600)
                stream.write(staged.read_bytes())
        return text if text is not None else str(target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_path', type=Path, help='A returned document or a folder of returned documents')
    parser.add_argument('--output', '-o', type=Path, help='Separate output file or folder')
    parser.add_argument('--no-recursive', action='store_true', help='Exclude subfolders in folder mode')
    parser.add_argument('--secure-dir', type=Path, help='Folder containing the private identity_catalogue.json')
    args = parser.parse_args(argv)
    root = args.input_path.expanduser().resolve()
    secure = args.secure_dir or get_data_dirs()['secure']
    try:
        if root.is_dir():
            output = args.output or root.with_name(root.name + '-reidentified')
            files, skipped = scan_folder(root, output, recursive=not args.no_recursive)
            print(f'Found {len(files)} documents; skipped {len(skipped)} unsupported files or links.')
            result = restore_batch(files, root=root, output=output, secure=secure,
                                   on_update=lambda state: print(f"{state['totals']['processed']}/{state['totals']['documents']} {state['current_file']}"))
            print(f"Restored: {len(result['completed'])}; failed: {len(result['failed'])}; reused: {result['reused']}")
            for name, error in result['failed'].items():
                print(f'FAILED {name}: {error}')
            print(f"Output: {result['output']}\nPrivate manifest: {result['manifest']}")
            return 1 if result['failed'] else 0
        if args.no_recursive:
            parser.error('--no-recursive applies to folders only')
        output = args.output or root.with_name('reidentified_' + root.name)
        reidentify_report(str(root), str(output), secure=secure)
        print(f'Restored document saved to {output}')
        return 0
    except Exception as error:
        print(f'Restoration failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
