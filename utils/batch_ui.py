"""Streamlit controls for local file and recursive folder processing."""
import asyncio
from pathlib import Path
import subprocess
import sys
from queue import Queue, Empty
from threading import Thread
from time import perf_counter

import streamlit as st

from utils.batch import digest, process_batch, retry_failed, scan_folder
from utils.batch_review import resolve_review, review_preview
from utils.agent_config import DEFAULT_OLLAMA_MODEL
from utils.document_formats import DOCUMENT_EXTENSIONS


def choose_folder(key='batch_folder'):
    """Open the native picker on the Mac hosting this local application."""
    result = subprocess.run(
        ['osascript', '-e', 'POSIX path of (choose folder with prompt "Choose documents to de-identify")'],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        st.session_state[key] = result.stdout.strip()


def input_folder_controls(prefix):
    """Share input selection and recursion controls across detectors."""
    if sys.platform == 'darwin':
        st.button('Choose folder…', on_click=choose_folder, args=(prefix + '_folder',))
    folder = st.text_input('Input folder', key=prefix + '_folder', placeholder='/Users/yourname/Documents/Reports')
    recursive = st.checkbox('Include subfolders', value=True, key=prefix + '_recursive')
    return folder, recursive


def output_folder_control(prefix, suggested, *, label='Output folder'):
    """Keep a custom destination when defaults or visible output formats change."""
    output_key, suggested_key = prefix + '_output_folder', prefix + '_suggested_output'
    saved_key = prefix + '_saved_output'
    if output_key not in st.session_state:
        st.session_state[output_key] = st.session_state.get(saved_key, suggested)
    if st.session_state[output_key] == st.session_state.get(suggested_key, ''):
        st.session_state[output_key] = suggested
    st.session_state[suggested_key] = suggested
    output = st.text_input(label, key=output_key, placeholder='Choose a separate folder for results')
    st.session_state[saved_key] = output
    return output


def folder_controls(prefix, *, suffix='-redacted'):
    """Share folder selection across detectors while keeping their selections independent."""
    folder, recursive = input_folder_controls(prefix)
    path = Path(folder).expanduser() if folder.strip() else None
    suggested = str(path.with_name(path.name + suffix)) if path and path.name else ''
    output = output_folder_control(prefix, suggested)
    return folder, output, recursive


def draw_stats(stats, elapsed=None):
    """Render aggregate counts only; identity values remain in the private catalogue."""
    if not stats:
        st.info('Preparing local model…')
        return
    totals, tokens = stats['totals'], stats['tokens']
    seconds = int(stats['elapsed_seconds'] if elapsed is None else elapsed)
    st.write(f"{stats['stage']} · {stats['model']}")
    if stats.get('retry_of'):
        st.caption('Statistics below cover this attempt. Earlier successful documents are retained.')
    cards = st.columns(4)
    cards[0].metric('Elapsed', f'{seconds // 60}:{seconds % 60:02d}')
    cards[1].metric('Documents completed', f"{totals['completed']} / {totals['documents']}")
    cards[2].metric('PDF pages read', totals['pages'])
    cards[3].metric('Words read', f"{totals['words']:,}")
    cards = st.columns(4)
    cards[0].metric('Unique identifiers found', totals['identities'])
    speed = tokens['tokens_per_second']
    cards[1].metric('Generation tokens/s', f'{speed:.1f}' if speed is not None else '—')
    cards[2].metric('Input / output tokens', f"{tokens['input']:,} / {tokens['output']:,}")
    cards[3].metric('Failed documents', totals['failed'])
    st.caption(f"Needs review: {totals.get('needs_review', 0)} · User-approved: {totals.get('user_approved', 0)}")
    total = max(totals['documents'], 1)
    st.progress(totals['identified'] / total, text=f"Identity discovery: {totals['identified']} / {totals['documents']} documents")
    st.progress(totals['completed'] / total, text=f"Completed exports: {totals['completed']} / {totals['documents']} documents")
    st.caption(f"{totals['chunks_completed']} / {totals['chunks']} sections processed · {totals['cached_chunks']} cached. "
               'Token speed updates after each model response and excludes loading and prompt processing. '
               'Page counts apply to PDFs; words count extracted text.')
    if stats['current_file']:
        st.text(stats['current_file'])
    if totals['identity_types']:
        st.table([{'Identity type': kind, 'Unique identifiers': count}
                  for kind, count in sorted(totals['identity_types'].items())])
    st.dataframe([{'Document': name, 'Status': row['status'], 'PDF pages': row['pages'],
                   'Words': row['words'], 'Identifiers': row['identities']}
                  for name, row in stats['files'].items()], hide_index=True)


def start_job(files=None, *, previous=None, review=None, **kwargs):
    """Keep inference off the UI thread; only the UI thread calls Streamlit."""
    job = {'events': Queue(), 'started': perf_counter(), 'stats': None, 'logs': [], 'done': False, 'previous': previous}
    def worker():
        try:
            runner, selection = ((resolve_review, previous) if review else
                                 (retry_failed, previous) if previous else (process_batch, files))
            result = asyncio.run(runner(
                selection, **kwargs, **(review or {}), progress=lambda value: job['events'].put(('log', value)),
                on_update=lambda value: job['events'].put(('stats', value))))
            result['seconds'] = round(perf_counter() - job['started'], 1)
            job['events'].put(('result', result))
        except Exception as error:
            job['events'].put(('error', str(error)))
    job['thread'] = Thread(target=worker, daemon=True, name='guardian-batch')
    job['thread'].start()
    return job


def monitor_job(job):
    panel, log_box = st.empty(), st.empty()
    last_render = 0
    while not job['done']:
        try:
            kind, value = job['events'].get(timeout=0.25)
            if kind == 'stats':
                job['stats'] = value
            elif kind == 'log':
                job['logs'].append(value)
            elif kind == 'result':
                st.session_state['batch_result'] = value
                job['done'] = True
            else:
                job['error'], job['done'] = value, True
                if job.get('previous'):
                    st.session_state['batch_result'] = job['previous']
        except Empty:
            pass
        if perf_counter() - last_render >= 0.5 or job['done']:
            with panel.container():
                draw_stats(job['stats'], perf_counter() - job['started'])
            log_box.code('\n'.join(job['logs'][-8:]), language=None)
            last_render = perf_counter()
    if job.get('error'):
        st.error(f"Processing stopped: {job['error']}. Completed sections are saved; run again to resume.")
    panel.empty()
    log_box.empty()


def render_reviews(result, dirs, active):
    """Keep draft downloads and explicit human decisions separate from final exports."""
    reviews = result.get('needs_review', {})
    if not reviews:
        return
    st.subheader('Needs review')
    st.warning('These drafts may still contain identities in the sections below. They are not completed outputs. '
               'Review every flagged section before approving a document.')
    for relative, review in reviews.items():
        with st.expander(f"Review {relative}", expanded=True):
            draft = Path(review['draft'])
            if draft.exists():
                st.download_button('Download review draft', draft.read_bytes(), file_name=draft.name,
                                   key='review_download_' + digest(relative))
            options = [item['id'] for item in review['sections']]
            by_id = {item['id']: item for item in review['sections']}
            selected = st.selectbox('Section to review', options, key='review_section_' + digest(relative),
                                    format_func=lambda value: f"Section {by_id[value]['section']} · {by_id[value]['model']}")
            section = by_id[selected]
            candidates = [item for issue in section['issues'] for item in issue['candidates']]
            categories = {'patients', 'doctors', 'relatives', 'organisations', 'organizations',
                          'facilities', 'addresses', 'locations', 'email_addresses', 'personal_identifiers'}
            category_echo = candidates and all(item['name'].strip().lower().replace(' ', '_') in categories for item in candidates)
            if category_echo:
                st.warning('The model returned category labels, such as “patients” and “doctors”, '
                           'instead of identifying text. These suggestions were not found in the analysed section '
                           'and were not used for redaction. Check the document text below for missed identities.')
            for reason in dict.fromkeys(issue['reason'] for issue in section['issues']
                                        if not category_echo or not issue['candidates']):
                st.write(reason)
            if candidates:
                with st.expander(f'Model suggestions not found in the analysed text ({len(candidates)})'):
                    st.table([{'Suggested text': item['name'], 'Type': item['kind'],
                               'Aliases': ', '.join(item['aliases'])} for item in candidates])
            preview_ok = False
            try:
                pages = review_preview(review, relative, section)
                st.caption('Showing full source pages or document parts that overlap the flagged section. '
                           'Compare the original with text read from the saved review draft. '
                           'Extra spacing is removed for readability; the document files are unchanged.')
                for page in pages:
                    st.text(page['location'].capitalize())
                    original_column, draft_column = st.columns(2)
                    with original_column:
                        st.markdown('**Original document text**')
                        st.code(page['original'], language=None, wrap_lines=True, height=400)
                    with draft_column:
                        st.markdown('**Draft text — check for remaining identities**')
                        st.code(page['draft'], language=None, wrap_lines=True, height=400)
                preview_ok = bool(pages)
            except (OSError, ValueError, KeyError) as error:
                st.error(f'Cannot display the current document: {error}')
            action = st.radio('Review action', ['Ignore incorrect suggestions', 'Add manual redactions', 'Retry this section'],
                              key='review_action_' + selected)
            values = []
            model = None
            if action == 'Add manual redactions':
                st.caption('Copy identifying text from the original document text above, one value per line. '
                           'Matching occurrences throughout this document will be removed. These removals cannot be restored.')
                values = st.text_area('Text to remove', key='review_values_' + selected).splitlines()
            if action == 'Retry this section':
                current = st.session_state.get('_ollama_model', DEFAULT_OLLAMA_MODEL)
                model = st.selectbox('Local model for this section', list(dict.fromkeys(
                    [current, section['model'], 'qwen3.5:2b', 'gemma4:e2b', 'gemma4:e4b'])), key='review_model_' + selected)
                st.caption('Only this section is rechecked. Other completed sections and documents are retained.')
            note = st.text_input('Review note (optional)', key='review_note_' + selected)
            confirmed = action == 'Retry this section' or st.checkbox(
                'I reviewed the displayed pages and resolved their identifying information.', key='review_confirm_' + selected)
            if st.button('Apply review decision', key='review_apply_' + selected,
                         disabled=active or not confirmed or (not preview_ok and action != 'Retry this section')):
                if action == 'Retry this section' and st.session_state.get('_backend') != 'ollama':
                    st.error('Select Local Ollama before retrying.')
                else:
                    st.session_state['batch_job'] = start_job(
                        previous=result, secure=dirs['secure'], review=dict(
                            relative=relative, section_id=selected,
                            action={'Ignore incorrect suggestions': 'dismiss', 'Add manual redactions': 'redact',
                                    'Retry this section': 'retry'}[action], values=values, note=note, model=model))
                    st.session_state.pop('batch_result', None)
                    st.rerun()


def render_batch(dirs):
    st.subheader('De-identify documents')
    st.caption('Uses local Ollama. Keeps the original document format and skips chronology generation. '
               'Names receive consistent pseudonyms; recognised phone numbers, registrations, email and labelled addresses are removed.')
    source_mode = st.radio('Document source', ['Files', 'Folder'], horizontal=True)
    files, skipped, uploads = [], [], []
    root = None
    if source_mode == 'Folder':
        folder, output, recursive = folder_controls('batch', suffix='-deidentified')
        if folder and output:
            try:
                root = Path(folder).expanduser().resolve()
                files, skipped = scan_folder(root, output, recursive)
                st.write(f'{len(files)} supported documents found.')
                with st.expander('Review files', expanded=len(files) < 6):
                    st.code('\n'.join(str(path.relative_to(root)) for path in files) or 'No supported documents')
                if skipped:
                    with st.expander(f'{len(skipped)} unsupported files or links skipped'):
                        st.code('\n'.join(skipped))
            except ValueError as error:
                st.error(str(error))
    else:
        uploads = st.file_uploader('Choose documents', type=sorted(ext[1:] for ext in DOCUMENT_EXTENSIONS),
                                   accept_multiple_files=True, key='batch_uploads')
        output = str(Path(dirs['output']) / 'documents')
        st.caption('Results go to ' + output)
    st.caption('Scanned PDFs use local OCR. Review outputs before sharing; embedded Office images, '
               'metadata and names in file or folder paths are not scrubbed by this mode.')
    job = st.session_state.get('batch_job')
    active = job is not None and not job['done']
    if st.button('De-identify documents', type='primary', use_container_width=True, disabled=active):
        st.session_state.pop('batch_result', None)
        if st.session_state.get('_backend') != 'ollama':
            st.error('Select Local Ollama in the sidebar to use this mode.')
        elif not files and not uploads:
            st.error('Choose at least one supported document.')
        else:
            try:
                if uploads:
                    names = [Path(upload.name).name for upload in uploads]
                    if len(names) != len(set(names)):
                        raise ValueError('Some uploads have the same filename. Use Folder mode to preserve their subfolders.')
                    # Isolate each selection, preserving older source files and their resumable state.
                    import hashlib
                    selection = digest([(name, hashlib.sha256(upload.getbuffer()).hexdigest())
                                        for name, upload in zip(names, uploads)])
                    root = Path(dirs['input']) / 'uploads' / selection
                    root.mkdir(parents=True, exist_ok=True, mode=0o700)
                    files = []
                    for name, upload in zip(names, uploads):
                        source = root / name
                        source.write_bytes(upload.getbuffer())
                        source.chmod(0o600)
                        files.append(source)
                    output = str(Path(dirs['output']) / 'documents' / selection[:12])
                job = start_job(
                    files, root=root, output=output, secure=dirs['secure'],
                    model=st.session_state.get('_ollama_model', DEFAULT_OLLAMA_MODEL),
                    base_url=st.session_state.get('_ollama_url', 'http://localhost:11434'),
                )
                st.session_state['batch_job'] = job
            except Exception as error:
                st.error(f'Processing stopped: {error}. Completed sections are saved; run again to resume.')
    previous = st.session_state.get('batch_result')
    if previous and previous['failed']:
        retry_model = st.session_state.get('_ollama_model', DEFAULT_OLLAMA_MODEL)
        st.caption(f"Retry failed files with {retry_model}. Change the model in the sidebar to try another installed model.")
        if st.button('Retry failed documents', disabled=active):
            if st.session_state.get('_backend') != 'ollama':
                st.error('Select Local Ollama before retrying.')
            else:
                job = start_job(previous=previous, secure=dirs['secure'], model=retry_model,
                                base_url=st.session_state.get('_ollama_url', 'http://localhost:11434'))
                st.session_state['batch_job'] = job
                st.session_state.pop('batch_result', None)
    if job and not job['done']:
        monitor_job(job)
        if not job.get("error"):
            st.rerun()
    result = st.session_state.get('batch_result')
    if job and job.get('error') and job.get('stats'):
        draw_stats(job['stats'])
    if result:
        draw_stats(result['stats'])
        st.caption('Private run manifest: ' + result['manifest'])
        st.write(f"{len(result['completed'])} completed · {len(result['failed'])} failed · "
                 f"{len(result.get('needs_review', {}))} need review · "
                 f"{result['reused']} already complete · {result.get('seconds', result['stats']['elapsed_seconds'])} seconds")
        st.code(result['output'], language=None)
        if result['failed']:
            st.error('These documents failed. Correct the cause, then use Retry failed documents above. Successful outputs are retained.')
            st.table([{'File': name, 'Error': error} for name, error in result['failed'].items()])
        render_reviews(result, dirs, active)
        if result['stats'].get('decision_history'):
            with st.expander('Review decision history'):
                st.table([{'File': item['file'], 'Section': item['section'], 'Action': item['action'], 'Model': item['model'],
                           'Time (UTC)': item['at'], 'Note': item['note']} for item in result['stats']['decision_history']])
        for index, path in enumerate(result['completed']):
            source = Path(path)
            if source.exists():
                with source.open('rb') as stream:
                    relative = str(source.relative_to(result['output']))
                    verification = result.get('verifications', {}).get(path)
                    if verification == 'user_approved':
                        st.caption(relative + ' · User-approved; unresolved model suggestions were reviewed by you.')
                    elif verification == 'automatic':
                        st.caption(relative + ' · Automatic checks passed.')
                    st.download_button(relative, stream,
                                       file_name=source.name, key=f'batch_download_{index}')
