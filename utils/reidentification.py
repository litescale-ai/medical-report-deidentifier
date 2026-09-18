"""Bulk local restoration with private checkpoints and verified, separate outputs."""
import json
import os
from pathlib import Path
import re
import tempfile
from time import perf_counter

from utils.batch import digest, file_digest, read_json, save_private
from utils.batch_stats import utc_now
from utils.document_editor import reidentify_document
from utils.document_formats import DOCUMENT_EXTENSIONS, extract_document
from utils.identifier_rules import replacement_pattern

PSEUDONYM = re.compile(r'\b[A-Z][A-Z_]*_[0-9A-F]{8}\b')


def load_catalogue(path):
    """Require a usable catalogue before creating any identified outputs."""
    path = Path(path)
    if not path.is_file():
        raise ValueError('The private identity catalogue is missing. Use the catalogue from the original de-identification.')
    try:
        catalogue = json.loads(path.read_text())
    except (ValueError, UnicodeError):
        raise ValueError('The private identity catalogue is not valid JSON.') from None
    if not isinstance(catalogue, dict) or not catalogue:
        raise ValueError('The private identity catalogue is empty or invalid.')
    for token, details in catalogue.items():
        if (not isinstance(token, str) or not token.strip() or token.startswith('[')
                or not isinstance(details, dict) or not isinstance(details.get('canonical_name'), str)
                or not details['canonical_name'].strip()):
            raise ValueError('The private identity catalogue contains an invalid mapping.')
    return catalogue


def restore_batch(files, *, root, output, secure, on_update=None):
    """Restore each file independently. Reruns reuse verified outputs and retry failures.

    Only exports previously recorded by this restoration job may be replaced.
    Unknown Guardian tokens or tokens left after editing withhold that file.
    """
    started = perf_counter()
    root, output, secure = (Path(path).expanduser().resolve() for path in (root, output, secure))
    if root == output or root.is_relative_to(output):
        raise ValueError('Choose a separate output folder, not the input folder or its parent.')
    if output == secure or output.is_relative_to(secure) or secure.is_relative_to(output):
        raise ValueError('Keep restored outputs separate from the private catalogue folder.')
    sources = [Path(path).absolute() for path in files]
    if not sources or len(set(sources)) != len(sources):
        raise ValueError('Choose at least one document, without duplicate paths.')
    for source in sources:
        if (source.is_symlink() or not source.resolve().is_relative_to(root)
                or source.resolve().is_relative_to(output) or source.resolve().is_relative_to(secure)):
            raise ValueError('Every source must be a file inside the input folder, outside the output folder, without symlinks.')
        if source.suffix.lower() not in DOCUMENT_EXTENSIONS:
            raise ValueError(f'Unsupported document extension: {source.suffix}')
    sources = [source.resolve() for source in sources]
    catalogue = load_catalogue(secure / 'identity_catalogue.json')
    catalogue_version = digest(catalogue)
    # Ownership survives catalogue updates; the content signature invalidates old exports.
    manifest = secure / 'restoration' / digest([str(root), str(output), 1]) / 'manifest.json'
    state = read_json(manifest, {'files': {}})
    selected = [str(source.relative_to(root)) for source in sources]
    state.update(version=1, operation='reidentify', root=str(root), output=str(output),
                 catalogue_digest=catalogue_version, selected=selected, started_at=utc_now(), status='running')
    completed, failures, reused = [], {}, 0
    known = re.compile(replacement_pattern(sorted(catalogue, key=len, reverse=True)))

    def publish(current=''):
        state.update(current_file=current, elapsed_seconds=round(perf_counter() - started, 2), updated_at=utc_now())
        rows = [state['files'].get(name, {}) for name in selected]
        state['totals'] = dict(documents=len(sources), completed=len(completed), failed=len(failures), reused=reused,
                               processed=len(completed) + len(failures), pages=sum(row.get('pages') or 0 for row in rows),
                               words=sum(row.get('words', 0) for row in rows),
                               restored_occurrences=sum(row.get('restored_occurrences', 0) for row in rows if row.get('status') in ('completed', 'reused')))
        save_private(manifest, state)
        if on_update:
            on_update(json.loads(json.dumps(state)))

    # Clear old presentation counts while retaining ownership and cache signatures.
    for name in selected:
        old = state['files'].get(name, {})
        state['files'][name] = {key: old[key] for key in ('signature', 'output_digest') if key in old}
        state['files'][name]['status'] = 'pending'
    publish()
    for source, name in zip(sources, selected):
        row = state['files'][name]
        temporary = None
        row['status'] = 'restoring'
        publish(name)
        try:
            fingerprint = file_digest(source)
            signature = digest([fingerprint, catalogue_version])
            target = output / name
            if not target.resolve().is_relative_to(output) or target.is_symlink():
                raise ValueError('An output symlink is not a safe restoration destination.')
            if target.exists() and file_digest(target) != row.get('output_digest'):
                raise ValueError('An existing output belongs to another run or was edited. Choose another output folder.')
            sections = extract_document(source)
            text = '\n'.join(value for _, value in sections)
            if not text.strip():
                raise ValueError('No readable text was found. Supply a searchable document for restoration.')
            missing = set(PSEUDONYM.findall(text)) - set(catalogue)
            if missing:
                raise ValueError(f'{len(missing)} pseudonym(s) are missing from this catalogue. Output withheld.')
            matches = list(known.finditer(text))
            row.update(words=len(text.split()), pages=len(sections) if source.suffix.lower() == '.pdf' else None,
                       restored_occurrences=len(matches), identities=len({match.group() for match in matches}))
            if target.exists() and row.get('signature') == signature:
                row['status'] = 'reused'
                completed.append(str(target))
                reused += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(prefix='.guardian-restore-', suffix=source.suffix, dir=target.parent)
            os.close(fd)
            if not reidentify_document(str(source), temporary, catalogue):
                raise ValueError('This document format cannot be restored.')
            checked = '\n'.join(value for _, value in extract_document(temporary))
            if known.search(checked) or PSEUDONYM.search(checked):
                raise ValueError('Pseudonyms remain after restoration. Output withheld.')
            if file_digest(source) != fingerprint:
                raise ValueError('The source changed during restoration. Retry the document.')
            os.chmod(temporary, 0o600)
            # Check once more before replacing a previously owned output.
            if target.exists():
                if file_digest(target) != row.get('output_digest'):
                    raise ValueError('The output changed during restoration. Choose another output folder.')
                os.replace(temporary, target)
            else:
                os.link(temporary, target)  # Atomic no-clobber installation of a new output.
            row.update(status='completed', signature=signature, output_digest=file_digest(target), output=str(target))
            completed.append(str(target))
        except Exception as error:
            row.update(status='failed', error=str(error))
            failures[name] = str(error)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
            publish(name)
    state['status'] = 'completed_with_errors' if failures else 'completed'
    publish()
    return dict(root=str(root), output=str(output), completed=completed, failed=failures,
                reused=reused, manifest=str(manifest), stats=state)
