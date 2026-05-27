# 🛡️ Guardian Medical De-identifier

A premium, secure multimodal processing, chronological cataloguing, pseudonymisation, and re-identification pipeline designed for clinical records, assessor interviews, intake forms, and patient sessions. Built using the **Google Antigravity SDK** and powered by Gemini.

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

## 🚀 Local Installation & Setup

Ensure you have **Python 3.12** installed on your system.

### 1. Set Up the Project Environment
Open your terminal, navigate to the project directory, and initialize a virtual environment:
```bash
# Navigate to the folder
cd medical-report-deidentifier

# Create the Python 3.12 virtual environment
python3.12 -m venv .venv

# Activate the virtual environment
source .venv/bin/activate

# Install the necessary dependencies
pip install -r requirements.txt
```

### 2. Configure Your API Key
Create a `.env` file in the root of the project directory to register your Gemini API key:
```env
GEMINI_API_KEY="your-api-key-here"
```

---

## 💻 Running the Application

### Option A: The Streamlit Web UI Dashboard (Recommended)
Launch the beautiful browser interface with a single command:
```bash
# Make the launcher script executable (first time only)
chmod +x run_app.sh

# Start the dashboard
./run_app.sh
```
The application will automatically launch in your default web browser at `http://localhost:8501`. 
*   **💡 Pro-Tip**: In the app sidebar, you can toggle between **🌟 Live SDK Mode** and **🧪 Mock/Dry-Run Mode** to test the full pipeline offline instantly.

### Option B: The Command Line Interface (CLI)

#### 1. Ingest & De-identify Files:
Place your raw medical records (PDFs, audio recordings, text, etc.) into `data/input/` and run the orchestrator:
```bash
python main.py
```
This will populate the shareable files in `data/output/` and the private mappings in `data/secure/`.

#### 2. Re-identify a Returned Report:
When a recipient returns an edited/processed report containing hashes, pass the file to the re-identification script:
```bash
python reidentify.py data/output/shareable_pseudonymised_report.txt -o data/output/final_identified_report.txt
```

#### 3. Run the Offline Mock Validation Suite:
Run the mathematical verification test locally in under 3 seconds:
```bash
python verify_mock.py
```
