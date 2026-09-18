"""Multiple-file and recursive-folder restoration using the local private catalogue."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import subprocess
import sys
import zipfile

import streamlit as st

from utils.batch import digest, scan_folder
from utils.document_formats import DOCUMENT_EXTENSIONS
from utils.reidentification import restore_batch


def choose_returned_folder():
    result = subprocess.run(['osascript', '-e', 'POSIX path of (choose folder with prompt "Choose returned documents")'],
                            capture_output=True, text=True)
    if result.returncode == 0:
        st.session_state['restore_folder'] = result.stdout.strip()


def save_uploads(uploads, secure):
    """Isolate each upload selection and refuse duplicate names instead of overwriting."""
    names = [Path(upload.name).name for upload in uploads]
    if any(name in ('', '.', '..') for name in names) or len(names) != len({name.casefold() for name in names}):
        raise ValueError('Uploads must have distinct filenames. Use Folder mode for matching names in different subfolders.')
    selection = digest([(name, sha256(upload.getbuffer()).hexdigest()) for name, upload in zip(names, uploads)])
    root = Path(secure).parent / 'returned' / selection
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    files = []
    for name, upload in zip(names, uploads):
        source = root / name
        with source.open('wb') as stream:
            stream.write(upload.getbuffer())
        source.chmod(0o600)
        files.append(source)
    return root, files


def make_archive(result):
    """Keep relative paths so repeated filenames remain distinct in the download."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for filename in result['completed']:
            source = Path(filename)
            if source.is_file():
                archive.write(source, str(source.relative_to(result['output'])))
    return buffer.getvalue()


def render_reidentification(dirs):
    st.subheader('Restore identities in returned documents')
    st.caption('Uses this machine’s private identity catalogue. No model or internet connection is needed. '
               'Values replaced with removal markers stay removed. Restored documents contain private identities.')
    mode = st.radio('Returned document source', ['Files', 'Folder'], horizontal=True, key='restore_source')
    files, uploads, root = [], [], None
    output = str(Path(dirs['output']) / 'reidentified')
    if mode == 'Folder':
        if sys.platform == 'darwin':
            st.button('Choose returned folder…', on_click=choose_returned_folder)
        folder = st.text_input('Returned input folder', key='restore_folder')
        recursive = st.checkbox('Include returned subfolders', value=True)
        if folder.strip('/'):
            chosen = Path(folder.rstrip('/')).expanduser()
            output = str(chosen.with_name(chosen.name + '-reidentified'))
        output = st.text_input('Restored output folder', value=output)
        if folder:
            try:
                root = Path(folder).expanduser().resolve()
                files, skipped = scan_folder(root, output, recursive)
                st.write(f'{len(files)} supported returned documents found.')
                with st.expander('Review returned files'):
                    st.code('\n'.join(str(path.relative_to(root)) for path in files))
                if skipped:
                    st.caption(f'{len(skipped)} unsupported files or links skipped.')
            except ValueError as error:
                st.error(str(error))
    else:
        uploads = st.file_uploader('Upload returned documents', accept_multiple_files=True,
                                   type=sorted(ext[1:] for ext in DOCUMENT_EXTENSIONS), key='returned_files')
    start = st.button('Restore documents', type='primary')
    previous = st.session_state.get('restoration_result')
    retry = bool(previous and previous['failed']) and st.button('Retry failed restorations')
    if start or retry:
        try:
            if retry:
                root, output = Path(previous['root']), previous['output']
                files = [root / name for name in previous['failed']]
            elif uploads:
                root, files = save_uploads(uploads, dirs['secure'])
                output = str(Path(dirs['output']) / 'reidentified' / root.name[:12])
            if not files:
                raise ValueError('Choose at least one supported returned document.')
            progress = st.progress(0, text='Preparing restoration')
            counters = st.empty()
            def update(state):
                totals = state['totals']
                progress.progress(totals['processed'] / totals['documents'],
                                  text=f"{totals['processed']} / {totals['documents']} processed · {state['current_file']}")
                counters.caption(f"{totals['completed']} restored · {totals['failed']} failed · "
                                 f"{totals['restored_occurrences']} pseudonym occurrences restored · {state['elapsed_seconds']} seconds")
            with st.spinner('Restoring documents locally…', show_time=True):
                result = restore_batch(files, root=root, output=output, secure=dirs['secure'], on_update=update)
            if retry:
                result['completed'] = list(dict.fromkeys(previous['completed'] + result['completed']))
            st.session_state['restoration_result'] = result
            st.session_state['restoration_archive'] = make_archive(result) if result['completed'] else None
            st.rerun()
        except Exception as error:
            st.error(f'Restoration stopped: {error}')
    result = st.session_state.get('restoration_result')
    if result:
        st.write(f"{len(result['completed'])} restored · {len(result['failed'])} failed · {result['reused']} reused")
        st.code(result['output'], language=None)
        stats = result['stats']
        st.caption(f"Last attempt: {stats['elapsed_seconds']} seconds · {stats['totals']['pages']} PDF pages · "
                   f"{stats['totals']['words']} words · {stats['totals']['restored_occurrences']} pseudonym occurrences restored")
        st.caption('Private restoration manifest: ' + result['manifest'])
        if result['failed']:
            st.error('Failed files were withheld. Fix the source or catalogue and retry failed restorations.')
            st.table([{'File': name, 'Error': error} for name, error in result['failed'].items()])
        if result['completed']:
            st.download_button('Download restored documents ZIP', st.session_state['restoration_archive'],
                               file_name='reidentified-documents.zip', mime='application/zip')
            for index, filename in enumerate(result['completed']):
                source = Path(filename)
                if source.is_file():
                    st.download_button(str(source.relative_to(result['output'])), source.read_bytes(),
                                       file_name=source.name, key=f'restored_download_{index}')
