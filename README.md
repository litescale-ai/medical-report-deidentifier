# 🛡️ Guardian Medical De-identifier

Guardian extracts and organises clinical records, replaces discovered identifiers with pseudonyms, and restores identities in returned documents. It runs locally with Ollama by default.

---

## 🌟 Key Features

*   **Multimodal Verbatim Extraction (Stage 1)**: Natively parses documents (PDFs, text), audio recordings (MP3s, WAVs), and video sessions (MP4s). Dialogue is transcribed verbatim, and patient behaviors/movements in video are logged chronologically as "Visual Action" entries.
*   **Unified Chronological Ledger (Stage 2)**: Interleaves and synthesizes separate session records, sorting events strictly by normalized timestamps into designated clinical categories.
*   **Salt-Based Deterministic Pseudonymisation (Stage 3)**: Discovers sensitive named entities (patients, doctors, relatives, facilities) and their aliases using LLM intelligence. Replaces all occurrences with unique secure hashes (e.g. `PATIENT_672EDD80`) utilizing a secure local salt key.
*   **Stage 4 Re-identification**: Restores original patient PII into returned, processed documents by reversing the hash-to-identity mappings and cleanly removing recipient header instructions.
*   **Interactive Streamlit UI**: An ultra-premium glassmorphism browser dashboard that supports simple file ingestion, progress logs, interactive timeline rendering, secure expandable key browsers, and instant file downloads.
*   **Dry-Run Mock Mode**: Support for running high-fidelity offline mock simulations without needing a live API key or burning monthly token quotas.

---

## 📂 Project Architecture

```
medical-report-deidentifier/
├── main.py                  # Orchestration script running all pipeline stages
├── reidentify.py            # CLI script to re-identify returned files
├── verify_mock.py           # Verification script utilizing offline mock inputs
├── app.py                   # Premium Streamlit Web UI Dashboard
├── run_app.sh               # One-click shell launcher script
├── requirements.txt         # Project dependencies
├── agents/
│   ├── transcriber.py       # Multimodal Verbatim Transcriber Agent
│   ├── cataloguer.py        # Chronological Cataloguer Agent
│   └── deidentifier.py      # Entity Discovery & Deterministic Replacement
├── utils/
│   ├── hashing.py           # Salt-based cryptographic hashing utilities
│   └── helpers.py           # Directory and JSON management helpers
└── data/
    ├── input/               # [Place raw records here: PDF, MP3, MP4, TXT]
    ├── output/              # [Shareable pseudonymised reports generated here]
    └── secure/              # [CONFIDENTIAL: Private mappings and salt stored here]
```

> [!CAUTION]
> **Data Security Protocol**: The contents of the `data/secure/` folder (such as `identity_catalogue.json` and `salt.txt`) and your local `.env` file contain highly confidential information and API credentials. They are strictly ignored by `.gitignore` and **must never be pushed to remote version control or shared with external recipients**.

---

## Install and open on a Mac

1. Open **Terminal**. Press **Command + Space**, type **Terminal**, then press **Return**.
2. Copy this whole line, paste it into Terminal, and press **Return**:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/litescale-ai/medical-report-deidentifier/main/install.sh | bash
   ```

3. If asked, enter your Mac login password and press Return. The password does not
   appear while you type. Follow any Homebrew prompts and leave the window open
   while software and the model download. The first setup can take several minutes.
4. Guardian opens in your browser. Upload your documents, leave **Local Ollama**
   selected, choose **De-identify documents only (faster)**, and click
   **De-identify documents**. Download the results when it finishes.

**Next time:** double-click **Guardian.command** on your Desktop. It starts the
local model service if needed and opens the app. It does not reinstall packages or
redownload an existing model. Keep its Terminal window open while using the app;
press **Control + C** in that window when finished.

**To update:** stop Guardian with Control + C, then run the same install command
again. Your records and identity mappings stay in the install folder. Do not delete
that folder if you need to restore identities in previously shared reports.

New installations live in `~/Applications/Guardian`. If you run the command from
an existing Guardian checkout, it updates that checkout instead. The Desktop
shortcut points to the selected installation. Setup refuses to overwrite a
checkout with local code changes or a different branch.

Setup installs Homebrew if needed, then Python 3.12, Git, Tesseract, Ghostscript
and Ollama as needed. It installs the app packages, configures local Ollama, and
launches Guardian. The launcher downloads `qwen3.5:2b` only if it is absent; an
existing configured model is retained. Progress and errors remain visible in
Terminal. PDF dependencies use prebuilt `pikepdf` wheels, so pip can select a
compatible OCRmyPDF version on older Intel Macs without compiling QPDF.
See [Homebrew's installation documentation](https://docs.brew.sh/Installation)
for system requirements and password prompts.

If setup fails, keep the error text and rerun the install command after resolving
it. If the model requires a newer Ollama, the launcher updates a Homebrew-installed
Ollama and retries once when it started the server itself. If an older server was
already running, follow the instruction to restart your Mac, then double-click
Guardian.command. For Ollama installed outside Homebrew, update it from
[ollama.com/download](https://ollama.com/download) and restart your Mac. The
launcher never stops a server started by another terminal or app.

### Process a folder of documents

1. Leave **Local Ollama** selected. Choose **De-identify documents only (faster)**.
2. Select **Folder**, then **Choose folder…** on a Mac, or paste the folder path.
3. Leave **Include subfolders** checked. Review the file list and skipped files.
4. Use the suggested separate output folder, or enter another one.
5. Click **De-identify documents**. Results preserve the folder structure and
   document formats. Originals are never overwritten.

Run the same selection again to resume after an interruption. Completed model
requests are reused; changed files are reprocessed. Existing outputs edited outside
Guardian are not overwritten. Choose a new output folder when switching models.
Private checkpoints and identity mappings stay in `data/secure`, not the output
folder. Final output checks withhold files that still contain recognised phone,
registration or labelled-address values. This check does not prove that the model
found every identifier; review the documents before sharing. File and folder names
are preserved and may themselves contain identifying information.

The default is `qwen3.5:2b`, the fastest model in our [synthetic benchmark](docs/benchmarks/2026-09-18.md) that passed all checked identifier-removal and clinical-text-preservation tests. Existing installations retain their configured model; select Qwen in the sidebar to try it, or set `OLLAMA_MODEL="qwen3.5:2b"` in `.env` to use it on future launches.

The model selector also offers `gemma4:e2b` and `gemma4:e4b`. Download an alternative
model once in Terminal before selecting it:

```bash
ollama pull gemma4:e2b
ollama pull gemma4:e4b
```

For a reproducible synthetic 20-document, 60-page comparison, use a fresh folder:

```bash
python scripts/benchmark_local.py --output /tmp/guardian-model-comparison
```

### Prepare for AI with local NER

Guardian opens with **Prepare for AI (local NER)**, **Folder** input and **Both**
Markdown and PDF outputs selected. Each input gets its own output file in each selected format. This integrates the NER
experiment and its saved keep-term list into the application. Ollama and cloud
credentials are not used by this workflow; the existing Ollama workflows remain
available.

1. On a new machine, click **Install local NER support** once. The first run
   downloads the pinned model; document processing stays local.
2. Choose **Markdown documents**, **Redacted PDFs**, or **Both**. PDF output requires
   PDF inputs and preserves their page appearance. Markdown also accepts TXT,
   MD, HTML, DOCX, XLSX and PPTX.
3. Select files or a folder for **one patient**. Folder mode shares **Input folder**
   and **Include subfolders** with the LLM workflow. Each selected format has its
   own destination: **Markdown output folder** and **PDF output folder**.
   Change either suggested location if needed. Confirm
   that the selection belongs to that patient, then click **Prepare documents**.
4. Guardian assigns sequential names (`document-001`, `document-002`, etc.) to
   private working copies before processing. Source files stay untouched.
   Compare original and prepared text by page or section. Download PDF drafts to
   inspect their appearance. Every selected file appears in the status table under
   its original filename and new filename, with a separate Markdown and PDF status. Withheld PDFs
   show the filename, exact remaining text, type and page. Review all pages,
   not only the first displayed page.
5. Correct false positives in **Keep terms**, or add missed names and addresses
   under **Additional identifying text to remove**, then prepare again. Saved
   keep terms apply to later NER runs. You can also edit each Markdown document directly.
6. Confirm review and click **Save reviewed outputs**. Only share the reviewed
   documents. Keep `PRIVATE.json` and the case folder private.

Reviewed NER exports go into the folder selected for each format. Suggested
locations are `Medical Reports-redacted/markdown` and `Medical Reports-redacted/pdf`.
PDF and Markdown exports use matching neutral names: the first input becomes
`document-001.pdf` and `document-001.md`, the second becomes `document-002.pdf`
and `document-002.md`. Numbering follows the selected file order within each run.
Original subfolders are not carried into the exported filenames. The private
`PRIVATE.json` manifest includes `filename_mapping`, recording each original
relative path, assigned working filename, planned output names, and saved output
paths. Keep this mapping private. Markdown headings use the new document names.
Uploaded files also have editable per-format destinations, suggested under
`data/output/<document name>-redacted/markdown` and `.../pdf`, or
`data/output/Documents-redacted/` for multiple files. An existing output folder is
used when the new filenames do not conflict. A conflict creates a numbered sibling
such as `pdf-2`, preserving earlier files. Folder scanning excludes both selected
output folders. Switching formats retains the destinations you entered.
The screen and private manifest record both saved folders and offer downloads
with the same filenames.
Private manifests and identity mappings stay in the private case folder.

If the edited Markdown still contains potential identifiers, the review screen
lists the output filename, exact text, identity type, document/page, Markdown line and nearby text,
along with why it was flagged. Correct it in the editor, or select **Keep the
flagged text and save anyway**. An optional note, the accepted findings and the
saved file digest are recorded privately as `user_override`. Each affected document is marked
as retaining flagged text. Editing any Markdown document resets the override; the choice
does not change detection or the keep list for future runs.

Each run gets its own folder under `data/secure/ner-cases/`. Its private manifest
records sources, identity mapping, keep terms, manual removals, document/page/word
counts, identity types, processing time, PDF failures and approval. NER is a span
detector, so generation tokens per second does not apply. Previous results and
originals are retained. A failed new run does not display an older result as its
own output. PDF export failures retain successful drafts and the Markdown documents.

The ten reviewed phrases from the experiment are the initial keep list. Edit and
save them in the UI or run `python prepare_for_ai.py --add-keep-term "Digit span"`.
The UI and CLI share `data/secure/ner-keep-terms.txt`. Keep terms cannot exempt
recognised phone numbers, registrations or other required removals. They can still
preserve an identifying use of an ordinary phrase, so review matters.

UI exports use the existing private identity catalogue and can be restored in
bulk through **Re-identify Returned Report**, for both Markdown and PDF. Names
are reversible; values replaced with removal markers stay removed.
The standalone experiment CLI retains its separate case-local placeholder format.
OCR may miss names or clinical text, and text packets omit visual evidence.
Preserving PDF appearance does not prove that every identifier was found.

For a manually managed environment: `python -m pip install -r requirements-ner.txt`.
The optional model stack has been exercised on Apple Silicon; Intel installation
has not been verified. The normal installer still prepares the Ollama workflow.

### Existing checkouts and Linux

From a clean checkout, run `bash bootstrap.sh` to install/update or
`bash run_app.sh` to open the installed app. On Linux, install Git, Python 3.12
with venv support, Tesseract, Ghostscript and Ollama using your system's package
manager first. Automatic system-tool installation and the Desktop shortcut are
macOS features. These Bash scripts do not install the Windows app.

### Processing and configuration

The installer and launcher select Ollama automatically; no API key is required.
For a manually managed CLI environment, use:

```env
AGENT_BACKEND="ollama"
OLLAMA_MODEL="qwen3.5:2b"
OLLAMA_BASE_URL="http://127.0.0.1:11434/v1"
```

TXT, Markdown, HTML, XLSX, DOCX, PPTX and searchable PDF files are read locally,
without asking a model to transcribe the same text again. Image-only PDF pages
use local Tesseract OCR. The default document mode extracts text, removes labelled
addresses and recognised numbers locally, then asks Ollama only for identifying
names, addresses and other phrases. It does not generate a chronology. Requests
use overlapping chunks of at most 6,000 characters, one request at a time.
Repeated text and completed requests are cached privately; OCR layers are reused
for extraction and PDF redaction. Select **Create clinical chronology** when you
also need the original combined report or media-transcription workflow.

Chronology and entity discovery use Ollama's native
structured output with schema validation and thinking disabled. Discovery reads
the original extracted text as well as the chronology, so omitted summary details
are still considered. Processing stays on the selected backend without cloud fallback.

The UI shows elapsed time while each stage runs and uses the Ollama model/server
configured by the installer. Each native request has a total deadline of 120
seconds; set a finite positive `MODEL_TIMEOUT_SECONDS` if larger documents need
more time. In document-only mode, a timed-out section longer than 3,000 characters
gets one recovery pass using smaller overlapping pieces, each with its own request
deadline. A remaining timeout flags that section for review. Identifier matching
accepts case and whitespace differences but always replaces exact text found in the
source; suggestions absent from the source are saved for review while processing continues.
Invalid or truncated responses flag document sections for review; they still stop
the chronology workflow rather than becoming an empty report. Image, audio and video transcription retain the selected
backend's SDK media path; SDK cleanup can extend the configured deadline.

Email addresses, labelled address fields, phone numbers and labelled HPCSA, practice and PCNS registration numbers are
removed by local rules, independently of model discovery. The rules inspect the
original extraction as well as the chronology. They recognise South African
phone formats, international numbers beginning with `+`, and labelled phone/fax
numbers. These rule-based removals have no reversible mapping and remain removed when
names are restored. The local model also looks for unlabelled addresses and
address components such as street numbers, cities and postal codes. This requires readable text or accurate OCR; unrecognised
formats and numbers in images still need review. Dates, doses and clinical
measurements are not removed merely because they contain digits.

Both the UI and CLI generate documents in their original supported format. The
returned-document tab also restores identifiers in these formats.

| Format | Text processed locally |
| --- | --- |
| TXT, MD/Markdown, RST, CSV, TSV, JSON, LOG, XML, YAML | UTF-8 or BOM-marked UTF-16 text; exported as UTF-8 |
| HTML/HTM | Text across inline tags and attribute values, including HTML entities |
| DOCX | Paragraphs, hyperlinks, nested tables, headers and footers |
| XLSX | Cells, comments, hyperlinks and sheet names; formula operators and cell references retained |
| PPTX | Slide text, tables, grouped shapes and speaker notes |
| PDF | Page text; local OCR for image-only pages |

These are text replacements, not a complete Office-package privacy scrub: embedded
images/charts/attachments, document metadata and tracked changes need separate
review before sharing. OCR and model discovery can miss identifiers. Legacy
`.doc`, `.xls`, and `.ppt` files must first be converted to modern formats.

PDF editing reuses page style data and avoids duplicate replacements for
full names and overlapping aliases. Existing identity mappings are retained
when processing another batch, so earlier reports can still be restored.

The optional chronology workflow supports Gemini with `AGENT_BACKEND="gemini"`
and `GEMINI_API_KEY`, or an explicit UI selection. Fast document mode always uses Ollama.

Run the focused offline regression checks with:

```bash
python -m unittest test_pipeline test_bootstrap test_document_formats test_identifier_removal test_batch test_batch_ui test_reidentification test_reidentification_ui
```

---

## 💻 Running the Application

### Open the browser app

Double-click **Guardian.command** on your Desktop, or run `bash run_app.sh` from
the installed folder. If the browser does not open automatically, visit
[http://localhost:8501](http://localhost:8501). If Guardian is already running
from that folder, the shortcut reopens it without starting a second server.

### Option B: The Command Line Interface (CLI)

#### 1. De-identify a folder

```bash
python main.py --input "/path/to/source folder" --output "/path/to/results" --model qwen3.5:2b
```

Subfolders are included by default. Use `--no-recursive` for only the selected
folder. Without arguments, the CLI reads `data/input` recursively and writes to
`data/output/documents`. `python main.py --chronology` runs the original combined
chronology workflow on `data/input` instead.

#### 2. Re-identify returned documents

In the **Re-identify Returned Report** tab, upload several returned files or choose **Folder** and include subfolders. Click **Restore documents**. Guardian reads the local private identity catalogue once, restores known pseudonyms without a model, and writes separate outputs with the same formats and folder structure. Download individual files or the combined ZIP.

Progress shows processed documents, failures, elapsed time and restored pseudonym occurrences. The result links to a private manifest. Use **Retry failed restorations** after correcting a source file or adding the missing original catalogue mappings. A full rerun reuses unchanged, verified outputs. User-edited outputs are never overwritten.

Files containing unknown Guardian pseudonyms or pseudonyms left after editing are withheld and reported. Phone/practice numbers, email and addresses replaced with removal markers remain removed because they have no reverse mapping. Names and other values represented by reversible pseudonyms can be restored. Keep restored files and ZIP downloads private. This mode restores supported text surfaces; it cannot reconstruct deleted values or recover tokens that a recipient changed beyond recognition.

For an entire folder, including subfolders:

```bash
python reidentify.py "/path/to/returned folder" --output "/path/to/restored folder"
```

Use `--no-recursive` to exclude subfolders. If you omit `--output`, Guardian creates a sibling folder ending in `-reidentified`. Use `--secure-dir` only when your private catalogue is stored elsewhere. The command returns a nonzero exit code if any documents fail.

For one file:
```bash
python reidentify.py data/output/shareable_pseudonymised_report.txt -o data/output/final_identified_report.txt
```

#### 3. Run the Offline Mock Validation Suite:
Run the mathematical verification test locally in under 3 seconds:
```bash
python verify_mock.py
```

### Live progress and private run statistics

During folder or file processing, Guardian shows a running timer, discovery and verified-export progress, document counts, PDF pages read, extracted words, unique identifiers grouped by type, failures, cached sections and Ollama input/output tokens. The document table updates while processing. Page counts apply to PDFs only; other formats do not have a reliable page count without rendering.

Generation tokens per second uses Ollama's output-token count divided by its generation duration. It updates after each model response and excludes loading and prompt evaluation. Cached sections add no new tokens. Identifier counts describe discovered names and locally removed values, not a guarantee that every identity was detected.

Each run saves a private `manifest.json` beside its resumable checkpoint under `data/secure/batches/`. The results screen displays its exact location. It records elapsed time, per-file status and counts, identity types, measured token statistics and prior-run summaries. When review is needed, it also contains source sections, model suggestions and your decisions. These can identify people: keep manifests, checkpoints and review drafts private.

[Three-model benchmark and limitations](docs/benchmarks/2026-09-18.md)

If some documents fail, choose **Retry failed documents**. It retries only those files using the model currently selected in the sidebar. You can keep the same model to resume cached sections or choose another installed model for fresh discovery. Successful outputs are retained. Retry statistics describe that attempt; the results list also includes earlier successes. The private manifest links to an immutable record of the previous attempt.

### Review uncertain sections

Unmatched suggestions, malformed model replies and unresolved timeouts no longer discard an entire document. Guardian keeps matched identifiers, finishes the remaining sections and creates a private `REVIEW_REQUIRED-` draft. Drafts appear under **Needs review**, separately from completed downloads. They may still contain identities and are not final outputs.

Select a flagged section to compare the original document text with text extracted from its saved review draft, side by side. The preview shows full overlapping pages (or document parts), labelled by location, with extra spacing removed for readability. Category labels mistakenly returned by the model are explained together; raw suggestions remain available in an expandable list. Choose **Ignore incorrect suggestions** after checking the displayed pages, or **Add manual redactions** and copy identifying text from the original view, one value per line. Matching occurrences throughout that document are removed. Manual redactions use `[IDENTITY REMOVED]` and cannot be reversed by re-identification. Confirm that you reviewed the pages, then apply the decision. Alternatively, **Retry this section** with the same or another installed local model; only that section calls the model again.

Every flagged section must be resolved before the document enters completed downloads. Human decisions produce a **User-approved** result; successful model-only results say **Automatic checks passed**. The private manifest records decisions, notes, timestamps and retry models. Neither status guarantees that every identifier was found.

Known remaining phone, registration, email or labelled-address values still block exports. Unreadable or locked documents and edited outputs also remain failures. After restarting Guardian, process the same selection with the same output folder and original model to reload cached work and pending reviews. The command line exits nonzero for drafts awaiting review; use the app to resolve them.
