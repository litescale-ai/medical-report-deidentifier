"""Streamlit controls for local file and recursive folder processing."""
import asyncio
from pathlib import Path
import subprocess
import sys
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
    if st.button('De-identify documents', type='primary', use_container_width=True):
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
                started = perf_counter()
                logs = []
                with st.status('Processing locally…', expanded=True) as status:
                    log_box = st.empty()
                    def progress(message):
                        logs.append(f'{perf_counter() - started:.1f}s  {message}')
                        log_box.code('\n'.join(logs[-15:]), language=None)
                    result = asyncio.run(process_batch(
                        files, root=root, output=output, secure=dirs['secure'],
                        model=st.session_state.get('_ollama_model', 'gemma4:e4b'),
                        base_url=st.session_state.get('_ollama_url', 'http://localhost:11434'), progress=progress,
                    ))
                    result['seconds'] = round(perf_counter() - started, 1)
                    st.session_state['batch_result'] = result
                    status.update(label='Finished with failures' if result['failed'] else 'Documents processed',
                                  state='error' if result['failed'] else 'complete', expanded=bool(result['failed']))
            except Exception as error:
                st.error(f'Processing stopped: {error}. Completed sections are saved; run again to resume.')
    result = st.session_state.get('batch_result')
    if result:
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
