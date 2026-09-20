"""Local NER preparation shared by the Markdown and PDF review screens."""
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from time import perf_counter

from utils.batch import file_digest, prepare_source, save_private, read_json
from utils.batch_stats import utc_now
from utils.clinical_packet import (extract_case, prepare_packet, write_packet, approve_packet,
                                   reviewed_term_pattern, rule_replacements, packet_findings)
from utils.document_editor import deidentify_pdf
from utils.document_formats import extract_document
from utils.identifier_rules import replacement_pattern, replace_data
from utils.hashing import generate_pseudonym_hash


def document_label(location, names):
    """Resolve internal document numbers to original relative filenames for display."""
    match = re.match(r'^Document (\d+)(.*)$', location)
    if match and 0 < int(match[1]) <= len(names):
        return names[int(match[1]) - 1] + match[2]
    return location


def verify_pdf_text(text, result):
    """Verify actual PDF text, allowing only explicitly preserved phrases."""
    keep = reviewed_term_pattern(result['keep_terms'])
    checked = keep.sub(' ', text) if keep else text
    checked = re.sub(r'\[[A-Z]+(?:_\d+| REMOVED)\]', '', checked)
    if rule_replacements(checked) or (result['replacements'] and re.search(
            replacement_pattern(result['replacements']), checked)):
        raise ValueError('Known identifiers remain in the PDF. This draft was withheld.')


async def prepare_outputs(files, *, output, secure, detector, formats, keep_terms=(), remove_terms=(),
                          source_root=None, export_folder=None, export_folders=None,
                          model_load_seconds=0, progress=lambda message: None):
    """Discover once for one patient, then export either or both formats privately."""
    started = perf_counter()
    files, output = [Path(path).resolve() for path in files], Path(output)
    if not files:
        raise ValueError('Select documents first.')
    root = Path(source_root).expanduser().resolve() if source_root else Path(os.path.commonpath([p.parent for p in files]))
    relative_sources = [str(path.relative_to(root)) for path in files]
    destination = Path(export_folder).expanduser().absolute() if export_folder else root.with_name(root.name + '-redacted')
    destinations = {kind: str(destination) for kind in formats}
    if export_folders is not None:
        if set(export_folders) != set(formats) or any(not str(value).strip() for value in export_folders.values()):
            raise ValueError('Choose an output folder for each selected format.')
        destinations = {kind: str(Path(value).expanduser().resolve()) for kind, value in export_folders.items()}
        if len(set(destinations.values())) != len(destinations):
            raise ValueError('Choose a different output folder for each format.')
    for value in destinations.values():
        if Path(value).resolve() == root or root.is_relative_to(Path(value).resolve()):
            raise ValueError('Choose an export folder separate from the source folder.')
    packet_name = files[0].stem + '-redacted.md' if len(files) == 1 else (
        root.name + '-redacted.md' if source_root else destination.name.removesuffix('-redacted') + '-redacted.md')
    if not formats or set(formats) - {'markdown', 'pdf'}:
        raise ValueError('Choose Markdown, PDFs or both.')
    if 'pdf' in formats and any(path.suffix.lower() != '.pdf' for path in files):
        raise ValueError('PDF output requires PDF inputs. Use Markdown for Word, spreadsheet, slide or text documents.')
    if output.exists():
        raise ValueError('Choose a new output folder; earlier results are retained.')
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fingerprints = [file_digest(path) for path in files]
    with tempfile.TemporaryDirectory(prefix='.ner-source-', dir=output.parent) as temporary:
        prepared = []
        for number, (path, fingerprint) in enumerate(zip(files, fingerprints), 1):
            progress(f'Reading {number} / {len(files)}: {relative_sources[number - 1]}')
            prepared.append(prepare_source(path, Path(temporary), fingerprint))
        sections = extract_case(prepared)
        result = await prepare_packet(sections, detector, keep_terms=keep_terms, remove_terms=remove_terms,
            on_progress=lambda done, total: progress(f'Identifying section {done} / {total}: '
                                                     + document_label(sections[done - 1].source, relative_sources)))
        # UI exports use the existing catalogue so both formats can be restored
        # through the same bulk re-identification screen as Ollama exports.
        tokens = {token: generate_pseudonym_hash(values[0], token[1:].rsplit('_', 1)[0])
                  for token, values in result['mapping'].items()}
        result['markdown'] = replace_data(result['markdown'], tokens)
        result['cleaned'] = replace_data(result['cleaned'], tokens)
        result['replacements'] = {value: tokens.get(replacement, replacement) for value, replacement in result['replacements'].items()}
        result['mapping'] = {tokens[token]: values for token, values in result['mapping'].items()}
        catalogue_path = Path(secure) / 'identity_catalogue.json'
        catalogue_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        catalogue = read_json(catalogue_path, {})
        catalogue.update({token: {'canonical_name': values[0], 'variations': values,
                                  'entity_type': token.rsplit('_', 1)[0], 'relationship_context': ''}
                          for token, values in result['mapping'].items()})
        save_private(catalogue_path, catalogue)
        result['stats'].update(documents=len(files), pages=sum(', page ' in s.source for s in sections),
                               words=sum(len(s.text.split()) for s in sections), identity_types={})
        for token in result['mapping']:
            kind = token.rsplit('_', 1)[0]
            result['stats']['identity_types'][kind] = result['stats']['identity_types'].get(kind, 0) + 1
        if fingerprints != [file_digest(path) for path in files]:
            raise ValueError('A source changed during processing. Prepare it again.')
        write_packet(result, output, files)
        pdfs, failures, failure_details = [], {}, {}
        if 'pdf' in formats:
            for number, path in enumerate(prepared, 1):
                name = relative_sources[number - 1]
                progress(f'Redacting PDF {number} / {len(files)}: {name}')
                target = output / f'REVIEW_REQUIRED-Document-{number}.pdf'
                checked = []
                try:
                    deidentify_pdf(str(path), str(target), result['replacements'], keep_terms=keep_terms)
                    target.chmod(0o600)
                    checked = extract_document(target)
                    verify_pdf_text('\n'.join(text for _, text in checked), result)
                    pdfs.append({'path': str(target), 'sha256': file_digest(target), 'document': number,
                                 'sections': [{'source': f'Document {number}, {location}', 'text': text}
                                              for location, text in checked]})
                except Exception as error:
                    target.unlink(missing_ok=True)
                    details = [dict(item, location=f'{name}, {location}')
                               for location, text in checked for item in packet_findings(output, text)]
                    failure_details[name] = details
                    message = str(error)
                    if details:
                        values = list(dict.fromkeys(f"{item['text']!r} ({item['location']})" for item in details))
                        message += ' Flagged text: ' + '; '.join(values)
                    failures[name] = message
                    progress(f'PDF failed: {name}: {message}')
        result.update(output=str(output), formats=list(formats), pdfs=pdfs, failed=failures,
                      failure_details=failure_details,
                      source_names=relative_sources, export_folder=str(destination), export_folders=destinations, packet_name=packet_name,
                      sections=[{'source': s.source, 'text': s.text} for s in sections])
        result['stats']['elapsed_seconds'] = round(perf_counter() - started, 2)
        result['stats']['model_load_seconds'] = round(model_load_seconds, 2)
        result['stats']['total_seconds'] = round(perf_counter() - started + model_load_seconds, 2)
        result['stats'].update(pdf_completed=len(pdfs), pdf_failed=len(failures))
        private = json.loads((output / 'PRIVATE.json').read_text())
        private.update(stats=result['stats'], formats=list(formats), pdfs=pdfs, failed=failures,
                       failure_details=failure_details,
                       source_names=relative_sources, export_folder=str(destination), export_folders=destinations, packet_name=packet_name,
                       source_sha256=fingerprints, remove_terms=list(remove_terms), verification='needs_review')
        save_private(output / 'PRIVATE.json', private)
        return result


def copy_reviewed_exports(destination, sources, relative_targets):
    """Copy one format group without overwriting existing files."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    base = destination
    number = 2
    created_folder = False
    while True:
        try:
            destination.mkdir(mode=0o700)
            created_folder = True
            break
        except FileExistsError:
            if destination.is_dir() and not destination.is_symlink() and not any(
                    (destination / relative).exists() or (destination / relative).is_symlink()
                    for relative in relative_targets):
                break
            destination = base.with_name(f'{base.name}-{number}')
            number += 1
    named_exports = []
    try:
        for source, relative in zip(sources, relative_targets):
            target = destination / relative
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError('An export path points outside the output folder.')
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target.open('xb') as stream, Path(source).open('rb') as original:
                named_exports.append(str(target))
                os.chmod(target, 0o600)
                shutil.copyfileobj(original, stream)
    except Exception:
        if created_folder:
            shutil.rmtree(destination)
        else:
            for path in named_exports:
                Path(path).unlink(missing_ok=True)
        raise
    return destination, named_exports


def approve_outputs(result, markdown, *, override=False, review_note=''):
    """Approve unchanged PDF drafts and edited Markdown without touching originals."""
    output = Path(result['output'])
    # Check every file before publishing any reviewed download.
    for pdf in result['pdfs']:
        if file_digest(pdf['path']) != pdf['sha256']:
            raise ValueError('A PDF draft changed outside Guardian. Prepare it again before approving.')
    exports = []
    if 'markdown' in result['formats']:
        exports.append(str(approve_packet(output, markdown, override=override, review_note=review_note)))
    for pdf in result['pdfs']:
        target = output / f'REVIEWED-Document-{pdf["document"]}.pdf'
        if target.exists() and file_digest(target) != pdf['sha256']:
            raise ValueError('A reviewed PDF was edited outside Guardian. Prepare a new run.')
        if not target.exists():
            with target.open('xb') as stream, Path(pdf['path']).open('rb') as original:
                os.chmod(target, 0o600)
                shutil.copyfileobj(original, stream)
        exports.append(str(target))
    manifest = output / 'PRIVATE.json'
    private = json.loads(manifest.read_text())
    # Older in-memory runs can still be reviewed after the UI reloads.
    sources = [Path(path) for path in private['sources']]
    root = Path(os.path.commonpath([path.parent for path in sources]))
    names = result.get('source_names', [str(path.relative_to(root)) for path in sources])
    destination = Path(result.get('export_folder', root.with_name(root.name + '-redacted')))
    relative_targets = []
    if 'markdown' in result['formats']:
        relative_targets.append(Path(result.get('packet_name', (sources[0].stem if len(sources) == 1 else root.name) + '-redacted.md')))
    for pdf in result['pdfs']:
        original = Path(names[pdf['document'] - 1])
        relative_targets.append(original.with_name(original.stem + '-redacted' + original.suffix))
    destinations = result.get('export_folders', {kind: str(destination) for kind in result['formats']})
    kinds = (['markdown'] if 'markdown' in result['formats'] else []) + ['pdf'] * len(result['pdfs'])
    groups = {}
    for kind, source, relative in zip(kinds, exports, relative_targets):
        groups.setdefault(destinations[kind], []).append((kind, source, relative))
    named_exports, reviewed_folders = [], {}
    try:
        for folder, group in groups.items():
            actual, paths = copy_reviewed_exports(folder, [item[1] for item in group], [item[2] for item in group])
            named_exports.extend(paths)
            reviewed_folders.update({item[0]: str(actual) for item in group})
    except Exception:
        for path in named_exports:
            Path(path).unlink(missing_ok=True)
        raise
    exports = named_exports
    verification = private['reviews'][-1]['verification'] if 'markdown' in result['formats'] else 'user_approved'
    private.update(verification='partially_approved' if result['failed'] else verification,
                   approved_at=utc_now(), reviewed_outputs=exports,
                   reviewed_markdown_sha256=file_digest(exports[0]) if 'markdown' in result['formats'] else None)
    private.setdefault('export_history', []).append({'folders': reviewed_folders, 'files': exports,
                                                     'at': utc_now(), 'verification': private['verification']})
    save_private(manifest, private)
    result['reviewed_folders'] = reviewed_folders
    return exports
