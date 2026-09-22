"""Prepare local NER Markdown documents, redacted PDFs, or both for one patient."""
import asyncio
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from uuid import uuid4

import streamlit as st

from prepare_for_ai import DEFAULT_KEEP_TERMS, save_keep_terms, unique_terms
from utils.batch import scan_folder, read_json
from utils.batch_ui import input_folder_controls, output_folder_control
from utils.batch_review import readable_text
from utils.clinical_packet import NerDetector, packet_findings
from utils.document_formats import DOCUMENT_EXTENSIONS
from utils.ner_workflow import prepare_outputs, approve_outputs, document_label


@st.cache_resource(show_spinner=False)
def load_ner():
    return NerDetector()


def render_ner(dirs):
    st.subheader('Prepare for AI with local NER')
    st.caption('Select documents for one patient. Detect identifying text locally, preserve reviewed clinical terms, '
               'then review one Markdown file, redacted PDF, or both for each source document.')
    available = all(importlib.util.find_spec(name) is not None for name in ('gliner', 'torch', 'transformers'))
    if not available:
        st.info('Local NER support needs a one-time installation. The first run also downloads the model. '
                'Your documents stay on this computer.')
        if st.button('Install local NER support'):
            with st.spinner('Installing NER support…', show_time=True):
                installed = subprocess.run([sys.executable, '-m', 'pip', 'install', '-r',
                    str(Path(__file__).resolve().parents[1] / 'requirements-ner.txt')], capture_output=True, text=True)
            if installed.returncode:
                st.error('Installation failed. Details:')
                st.code((installed.stdout + installed.stderr)[-6000:], language=None)
            else:
                importlib.invalidate_caches()
                st.rerun()
    terms_path = Path(dirs['secure']) / 'ner-keep-terms.txt'
    saved = terms_path.read_text(encoding='utf-8-sig').splitlines() if terms_path.exists() else DEFAULT_KEEP_TERMS
    with st.expander('Keep terms — preserve reviewed clinical phrases'):
        st.caption('One phrase per line. Saved terms apply to future NER runs. Only preserve phrases you have checked; '
                   'an identifying use of the same phrase would also be kept.')
        terms_text = st.text_area('Keep terms', value='\n'.join(saved), height=180, key='ner_keep_terms')
        if st.button('Save keep terms'):
            save_keep_terms(terms_path, unique_terms(terms_text.splitlines()))
            st.success('Keep terms saved for future runs.')
    keep_terms = unique_terms(terms_text.splitlines())
    output_choice = st.radio('Output format', ['Markdown documents', 'Redacted PDFs', 'Both'], index=2, horizontal=True, key='ner_format')
    formats = ['markdown', 'pdf'] if output_choice == 'Both' else ['pdf'] if output_choice == 'Redacted PDFs' else ['markdown']
    st.caption('PDF output preserves page appearance and accepts PDF inputs. Markdown accepts all supported document types. '
               'Review scans and tables against the originals; extracted text can miss visual details.')
    mode = st.radio('NER document source', ['Files', 'Folder'], index=1, horizontal=True)
    files, uploads, source_root = [], [], None
    export_folders = {}
    if mode == 'Folder':
        folder, recursive = input_folder_controls('ner')
        source_root = Path(folder).expanduser().resolve() if folder.strip() else None
        base = source_root.with_name(source_root.name + '-redacted') if source_root and source_root.name else None
    else:
        uploads = st.file_uploader('Patient documents', type=sorted(ext[1:] for ext in DOCUMENT_EXTENSIONS),
                                   accept_multiple_files=True, key='ner_uploads')
        label = Path(uploads[0].name).stem if len(uploads) == 1 else 'Documents'
        base = Path(dirs['output']) / (label + '-redacted')
    for kind, label in [('markdown', 'Markdown'), ('pdf', 'PDF')]:
        if kind in formats:
            export_folders[kind] = output_folder_control('ner_' + kind,
                str(base / kind) if base else '', label=label + ' output folder')
    if mode == 'Folder' and source_root and all(value.strip() for value in export_folders.values()):
        try:
            destinations = [Path(value).expanduser().resolve() for value in export_folders.values()]
            if any(source_root == path or source_root.is_relative_to(path) for path in destinations):
                raise ValueError('Output folders must be separate from the input folder, not its parent.')
            files, skipped = scan_folder(source_root, destinations[0], recursive)
            excluded = [*destinations, Path(dirs['secure']).resolve()]
            previous = st.session_state.get('ner_result')
            if previous:
                manifest = read_json(Path(previous['output']) / 'PRIVATE.json', {})
                for saved in manifest.get('export_history', []):
                    folders = saved.get('folders', {}).values() if 'folders' in saved else [saved['folder']]
                    excluded.extend(Path(path).resolve() for path in folders)
            files = [path for path in files if not any(path.is_relative_to(target) for target in excluded)]
            st.write(f'{len(files)} supported documents selected.')
            with st.expander('Selected documents'):
                st.code('\n'.join(str(path.relative_to(source_root)) for path in files), language=None)
            if skipped:
                st.caption(f'{len(skipped)} unsupported files or links skipped.')
        except ValueError as error:
            st.error(str(error))
    with st.expander('Additional identifying text to remove'):
        st.caption('If review reveals a missed name or address, copy it here and prepare the documents again. '
                   'Use one value per line. These removals cannot be restored.')
        remove_terms = unique_terms(st.text_area('Additional redactions', key='ner_remove_terms').splitlines())
    same_patient = st.checkbox('These documents belong to one patient.', key='ner_one_patient')
    if st.button('Prepare documents', type='primary', disabled=not available or not same_patient):
        st.session_state.pop('ner_result', None)
        st.session_state.pop('ner_approved', None)
        try:
            case_id = uuid4().hex
            if uploads:
                names = [Path(upload.name).name for upload in uploads]
                if len({name.casefold() for name in names}) != len(names) or any(name in ('', '.', '..') for name in names):
                    raise ValueError('Select files with distinct names, or use Folder mode.')
                source_dir = Path(dirs['secure']) / 'ner-inputs' / case_id / 'Documents'
                source_dir.mkdir(parents=True, mode=0o700)
                for name, upload in zip(names, uploads):
                    target = source_dir / name
                    target.write_bytes(upload.getbuffer())
                    target.chmod(0o600)
                    files.append(target)
                source_root = source_dir
            if not all(value.strip() for value in export_folders.values()):
                raise ValueError('Choose an output folder for each selected format.')
            if not files:
                raise ValueError('Select documents first.')
            if 'pdf' in formats and any(path.suffix.lower() != '.pdf' for path in files):
                raise ValueError('Select only PDFs for PDF output, or choose Markdown for other document types.')
            save_keep_terms(terms_path, keep_terms)
            started = perf_counter()
            with st.spinner('Loading local NER (first use may download model files)…', show_time=True):
                detector = load_ner()
            model_seconds = perf_counter() - started
            status = st.empty()
            with st.spinner('Preparing documents locally…', show_time=True):
                result = asyncio.run(prepare_outputs(files, output=Path(dirs['secure']) / 'ner-cases' / case_id,
                    secure=dirs['secure'], detector=detector, formats=formats, keep_terms=keep_terms, remove_terms=remove_terms,
                    source_root=source_root, export_folders=export_folders,
                    model_load_seconds=model_seconds,
                    progress=lambda message: status.info(f'{message} · {perf_counter() - started:.1f}s')))
            status.empty()
            result['model_load_seconds'] = round(model_seconds, 2)
            st.session_state['ner_result'] = result
        except Exception as error:
            st.error(f'Preparation failed: {error}. Earlier case folders are retained.')
    result = st.session_state.get('ner_result')
    if not result:
        return
    names = result.get('source_names') or [Path(path).name for path in
        read_json(Path(result['output']) / 'PRIVATE.json', {}).get('sources', [])]
    failures = {document_label(name, names): error for name, error in result['failed'].items()}
    stats = result['stats']
    st.subheader('Review prepared documents')
    cards = st.columns(5)
    for card, label, value in zip(cards, ['Documents', 'PDF pages', 'Words', 'Identities', 'Processing seconds'],
                                   [stats['documents'], stats['pages'], stats['words'], stats['entities'], stats['elapsed_seconds']]):
        card.metric(label, value)
    st.caption(f"Model loading: {result['model_load_seconds']}s. NER identifies text spans; generation tokens/s does not apply.")
    if stats['identity_types']:
        st.table([{'Identity type': kind, 'Count': count} for kind, count in stats['identity_types'].items()])
    completed_pdfs = {pdf['document'] for pdf in result['pdfs']}
    st.write('Status of every selected document')
    st.dataframe([{'File': name,
                   'New filename': result['filename_mapping'][number - 1]['new_name'] if 'filename_mapping' in result else name,
                   'Markdown': 'Ready for review' if 'markdown' in result['formats'] else 'Not selected',
                   'PDF': ('Failed' if name in failures else 'Ready for review' if number in completed_pdfs else 'Not selected'),
                   'Error': failures.get(name, '')}
                  for number, name in enumerate(names, 1)], hide_index=True)
    if failures:
        st.error('Some PDFs were withheld. Correct the cause and prepare again; successful drafts remain available.')
        st.table([{'File': name, 'Error': error} for name, error in failures.items()])
        for name, findings in result.get('failure_details', {}).items():
            if findings:
                with st.expander('Flagged text in ' + name, expanded=True):
                    st.dataframe([{'Text': item['text'], 'Type': item['type'], 'Page': item['location'],
                                   'Context': item['context']} for item in findings], hide_index=True)
                    st.caption('If this is ordinary content, add the exact phrase to Keep terms and prepare again. '
                               'If it identifies someone, keep it marked for removal.')
    st.caption('Private manifest: ' + str(Path(result['output']) / 'PRIVATE.json'))
    destinations = result.get('export_folders', {kind: result.get('export_folder', '') for kind in result['formats']})
    for kind, folder in destinations.items():
        st.caption(f'{kind.upper()} output folder: {folder}')
    st.caption('Working copies use sequential names such as document-001. PDF and Markdown exports use matching names. '
               'The private manifest records the original filenames and paths. '
               'If filenames conflict, a numbered sibling folder is used for that format.')
    st.warning('Review every page for missed identities and altered clinical content before sharing. '
               'Keep PRIVATE.json local. The Re-identify tab can restore names using this machine’s catalogue; '
               'removal markers stay removed.')
    views = (['Markdown documents'] if 'markdown' in result['formats'] else []) + (['Redacted PDFs'] if result['pdfs'] else [])
    if not views:
        return
    view = st.radio('Review output', views, horizontal=True)
    draft = (dict(zip((s['source'] for s in result['sections']), result['cleaned'])) if view == 'Markdown documents' else
             {s['source']: s['text'] for pdf in result['pdfs'] for s in pdf['sections']})
    location = st.selectbox('Page or section', list(draft), format_func=lambda value: document_label(value, names),
                           key='ner_review_location_' + Path(result['output']).name + view)
    st.caption(document_label(location, names))
    original = next(s['text'] for s in result['sections'] if s['source'] == location)
    left, right = st.columns(2)
    with left:
        st.write('Original document text')
        st.code(readable_text(original), language=None, wrap_lines=True, height=350)
    with right:
        st.write('Prepared text — check for remaining identities')
        st.code(readable_text(draft[location]), language=None, wrap_lines=True, height=350)
    markdown = {}
    if 'markdown' in result['formats']:
        documents = result.get('markdown_documents', {result.get('packet_name', 'packet-redacted.md'): result['markdown']})
        for name, text in documents.items():
            with st.expander('Edit ' + name + ' before approval'):
                markdown[name] = st.text_area('Reviewed Markdown — ' + name, value=text, height=400,
                    key='ner_edit_' + Path(result['output']).name + name)
                st.download_button('Download Markdown review draft — ' + name, text,
                                   file_name=Path(name).stem + '-review-required.md')
    for pdf in result['pdfs']:
        source_name = Path(result['filename_mapping'][pdf['document'] - 1]['new_name']) if 'filename_mapping' in result else Path(pdf['path'])
        st.download_button('Download PDF review draft — ' + document_label(f'Document {pdf["document"]}', names), Path(pdf['path']).read_bytes(),
                           file_name=source_name.stem + '-review-required' + source_name.suffix, key='ner_pdf_' + pdf['path'])
    stamp = sha256((result['output'] + json.dumps(markdown, sort_keys=True)).encode()).hexdigest()
    findings = [dict(item, file=name) for name, text in markdown.items() for item in packet_findings(result['output'], text)]
    override, review_note = False, ''
    if findings:
        st.warning(f'{len(findings)} occurrence(s) in the Markdown documents need your decision. '
                   'These are potential identifiers, not proof that the text identifies someone.')
        st.dataframe([{'File': item['file'], 'Flagged text': item['text'], 'Type': item['type'], 'Document / page': document_label(item['location'], names),
                       'Markdown line': item['line'], 'Context': item['context'], 'Why flagged': item['reason']}
                      for item in findings], hide_index=True)
        st.caption('Correct the text in the corresponding Markdown editor, or explicitly keep it below. '
                   'An override retains the listed text in the saved documents and is recorded in the private manifest.')
        override = st.checkbox('Keep the flagged text and save anyway.', key='ner_override_' + stamp)
        review_note = st.text_input('Reason for keeping the text (optional)', key='ner_override_note_' + stamp)
    confirmed = st.checkbox('I reviewed all prepared pages and corrected their identifying information.', key='ner_confirm_' + stamp)
    if st.button('Save reviewed outputs', disabled=not confirmed or (bool(findings) and not override)):
        try:
            reviewed = markdown if 'markdown_documents' in result else next(iter(markdown.values()), '')
            exports = approve_outputs(result, reviewed, override=override, review_note=review_note)
            st.session_state['ner_approved'] = {'stamp': stamp, 'files': exports, 'override': bool(findings),
                                                'folders': result['reviewed_folders']}
        except (OSError, ValueError) as error:
            st.error(str(error))
    approved = st.session_state.get('ner_approved', {})
    if approved.get('stamp') == stamp:
        if approved.get('override'):
            st.warning('Saved with your verification override. The flagged text remains in the Markdown documents.')
        else:
            st.success('User-reviewed outputs saved.')
        for kind, folder in approved.get('folders', {}).items():
            st.write(kind.upper() + ' saved to')
            st.code(folder, language=None)
        for path in approved['files']:
            st.download_button('Download ' + Path(path).name, Path(path).read_bytes(), file_name=Path(path).name,
                               key='ner_reviewed_' + path)
