"""Streamlit controls for local file and recursive folder processing."""
import asyncio
from pathlib import Path
import subprocess
import sys
from queue import Queue, Empty
from threading import Thread
from time import perf_counter

import streamlit as st

from utils.batch import digest, process_batch, scan_folder
from utils.document_formats import DOCUMENT_EXTENSIONS


def choose_folder():
    """Open the native picker on the Mac hosting this local application."""
    result = subprocess.run(
        ['osascript', '-e', 'POSIX path of (choose folder with prompt "Choose documents to de-identify")'],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        st.session_state['batch_folder'] = result.stdout.strip()


def draw_stats(stats, elapsed=None):
    """Render aggregate counts only; identity values remain in the private catalogue."""
    if not stats:
        st.info('Preparing local model…')
        return
    totals, tokens = stats['totals'], stats['tokens']
    seconds = int(stats['elapsed_seconds'] if elapsed is None else elapsed)
    st.write(f"{stats['stage']} · {stats['model']}")
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
    total = max(totals['documents'], 1)
    st.progress(totals['identified'] / total, text=f"Identity discovery: {totals['identified']} / {totals['documents']} documents")
    st.progress(totals['completed'] / total, text=f"Verified exports: {totals['completed']} / {totals['documents']} documents")
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


def start_job(files, **kwargs):
    """Keep inference off the UI thread; only the UI thread calls Streamlit."""
    job = {'events': Queue(), 'started': perf_counter(), 'stats': None, 'logs': [], 'done': False}
    def worker():
        try:
            result = asyncio.run(process_batch(
                files, **kwargs, progress=lambda value: job['events'].put(('log', value)),
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


def render_batch(dirs):
    st.subheader('De-identify documents')
    st.caption('Uses local Ollama. Keeps the original document format and skips chronology generation. '
               'Names receive consistent pseudonyms; recognised phone numbers, registrations, email and labelled addresses are removed.')
    source_mode = st.radio('Document source', ['Files', 'Folder'], horizontal=True)
    files, skipped, uploads = [], [], []
    root = None
    if source_mode == 'Folder':
        if sys.platform == 'darwin':
            st.button('Choose folder…', on_click=choose_folder)
        folder = st.text_input('Input folder', key='batch_folder', placeholder='/Users/yourname/Documents/Reports')
        recursive = st.checkbox('Include subfolders', value=True)
        default_output = str(Path(folder.rstrip('/')).expanduser().with_name(Path(folder.rstrip('/')).name + '-deidentified')) if folder.strip('/') else ''
        output = st.text_input('Output folder', value=default_output, placeholder='Choose a separate folder for results')
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
                    model=st.session_state.get('_ollama_model', 'gemma4:e4b'),
                    base_url=st.session_state.get('_ollama_url', 'http://localhost:11434'),
                )
                st.session_state['batch_job'] = job
            except Exception as error:
                st.error(f'Processing stopped: {error}. Completed sections are saved; run again to resume.')
    if job and not job['done']:
        monitor_job(job)
    result = st.session_state.get('batch_result')
    if job and job.get('error') and job.get('stats'):
        draw_stats(job['stats'])
    if result:
        draw_stats(result['stats'])
        st.caption('Private run manifest: ' + result['manifest'])
        st.write(f"{len(result['completed'])} completed · {len(result['failed'])} failed · "
                 f"{result['reused']} already complete · {result['seconds']} seconds")
        st.code(result['output'], language=None)
        if result['failed']:
            st.error('Failed documents were not exported in this run. Fix the errors and run again to resume.')
            st.table([{'File': name, 'Error': error} for name, error in result['failed'].items()])
        for index, path in enumerate(result['completed']):
            source = Path(path)
            if source.exists():
                with source.open('rb') as stream:
                    st.download_button(str(source.relative_to(result['output'])), stream,
                                       file_name=source.name, key=f'batch_download_{index}')
