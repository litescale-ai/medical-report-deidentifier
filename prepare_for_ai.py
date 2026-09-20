"""Create a local Markdown review packet from documents belonging to ONE patient."""
import argparse
import asyncio
import os
from pathlib import Path
import tempfile
from utils.clinical_packet import NerDetector, QwenDetector, extract_case, prepare_packet, write_packet


DEFAULT_KEEP_TERMS = (
    'timed classroom', 'SNAP-IV Rating Scales', 'FCPaed', 'Beery VMI',
    'Beery-Buktenica', 'VMI', 'DAP', 'Kaleidovision', 'technology startup', 'Grade 2',
)
SAVED_KEEP_TERMS = Path(__file__).resolve().parent / 'data/secure/ner-keep-terms.txt'


def unique_terms(terms):
    """Deduplicate using the same case/spacing tolerance as phrase matching."""
    result, seen = [], set()
    for term in terms:
        term = ' '.join(term.split())
        if term and term.casefold() not in seen:
            result.append(term)
            seen.add(term.casefold())
    return result


def save_keep_terms(path, terms):
    """Replace the editable local list atomically with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(''.join(term + '\n' for term in terms))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def main(args):
    if args.files and args.output.exists():
        raise ValueError('Choose a new output directory; existing results are never overwritten.')
    keep_terms = []
    if not args.no_keep_terms:
        path = args.keep_terms if args.keep_terms is not None else SAVED_KEEP_TERMS
        if args.keep_terms is not None or path.exists():
            keep_terms = unique_terms(path.read_text(encoding='utf-8-sig').splitlines())
        else:
            keep_terms = list(DEFAULT_KEEP_TERMS)
        updated = unique_terms([*keep_terms, *args.add_keep_term])
        if updated != keep_terms or (not path.exists() and (args.files or args.add_keep_term)):
            save_keep_terms(path, updated)
        keep_terms = updated
        print(f'Keep terms: {len(keep_terms)}. Editable list: {path}')
        if args.list_keep_terms:
            print('\n'.join(keep_terms))
    if not args.files:
        return
    sections = extract_case(args.files)
    print(f'Extracted {len(sections)} sections. Loading {args.detector} locally.', flush=True)
    detector = NerDetector(args.threshold) if args.detector == 'ner' else QwenDetector()
    result = await prepare_packet(sections, detector, keep_terms=keep_terms)
    write_packet(result, args.output, args.files)
    print(f'Review draft: {args.output / "REVIEW_REQUIRED.md"}')
    print(f'Keep PRIVATE.json local. Detection and replacement: {result["stats"]["seconds"]} seconds.')


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='*', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--detector', choices=['ner', 'qwen'], default='ner')
    keep = parser.add_mutually_exclusive_group()
    keep.add_argument('--keep-terms', type=Path, help='Use a different UTF-8 keep list instead of the saved list')
    keep.add_argument('--no-keep-terms', action='store_true', help='Disable keep terms for this run only, for comparisons')
    parser.add_argument('--add-keep-term', action='append', default=[], metavar='PHRASE',
                        help='Save a reviewed clinical or ordinary phrase for future runs; repeat to add more')
    parser.add_argument('--list-keep-terms', action='store_true', help='Show the current keep list without needing an input document')
    parser.add_argument('--threshold', type=float, default=0.3)
    args = parser.parse_args(argv)
    if args.no_keep_terms and (args.add_keep_term or args.list_keep_terms):
        parser.error('--no-keep-terms cannot be combined with adding or listing terms')
    if any(not term.strip() for term in args.add_keep_term):
        parser.error('--add-keep-term requires a nonempty phrase')
    if args.files and args.output is None:
        parser.error('--output is required when processing documents')
    if not args.files and (args.output is not None or not (args.add_keep_term or args.list_keep_terms)):
        parser.error('provide input documents and --output, or use --add-keep-term / --list-keep-terms')
    try:
        asyncio.run(main(args))
    except Exception as error:
        parser.exit(1, f'Packet failed: {error}\n')


if __name__ == '__main__':
    cli()
