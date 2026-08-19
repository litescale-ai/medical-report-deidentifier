import os
import sys
import json
import shutil
import asyncio
import streamlit as st
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.helpers import get_data_dirs, save_json, load_json
from utils.document_editor import (
    deidentify_document, write_synthesis_summary, reidentify_document,
)
from agents.transcriber import transcribe_media
from agents.cataloguer import catalogue_transcripts
from agents.deidentifier import discover_pii_entities, perform_deidentification
from reidentify import reidentify_report

# Page Config
st.set_page_config(
    page_title="Guardian Medical De-identifier",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Load environment
load_dotenv()
dirs = get_data_dirs()

# Custom Premium Styling
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Space+Grotesk:wght@400;600&display=swap');

html, body, [class*="css"] {
    font-family: 'Outfit', sans-serif;
}

.title-container {
    background: linear-gradient(135deg, #1e0b36 0%, #0c0414 100%);
    padding: 35px;
    border-radius: 20px;
    color: white;
    text-align: center;
    margin-bottom: 30px;
    box-shadow: 0 10px 30px 0 rgba(79, 30, 143, 0.25);
    border: 1px solid rgba(139, 92, 246, 0.2);
}

.title-container h1 {
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 800;
    font-size: 2.8rem;
    margin-bottom: 5px;
    letter-spacing: -0.03em;
    background: linear-gradient(to right, #a78bfa, #ec4899);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.title-container p {
    font-size: 1.1rem;
    opacity: 0.85;
    font-weight: 300;
}

.glass-card {
    background: rgba(17, 12, 28, 0.6);
    border-radius: 16px;
    padding: 25px;
    margin-bottom: 20px;
    border: 1px solid rgba(139, 92, 246, 0.15);
    box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
    backdrop-filter: blur(12px);
}

.timeline-event {
    background: rgba(30, 27, 46, 0.4);
    border-left: 4px solid #8b5cf6;
    border-radius: 0 12px 12px 0;
    padding: 18px;
    margin-bottom: 15px;
    border-top: 1px solid rgba(255, 255, 255, 0.03);
    border-bottom: 1px solid rgba(255, 255, 255, 0.03);
    border-right: 1px solid rgba(255, 255, 255, 0.03);
}

.timeline-badge {
    padding: 5px 12px;
    border-radius: 30px;
    font-size: 0.75rem;
    font-weight: 600;
    display: inline-block;
    margin-right: 10px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.badge-intake { background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); }
.badge-interview { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
.badge-assessment { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }
.badge-activity { background: rgba(139, 92, 246, 0.2); color: #a78bfa; border: 1px solid rgba(139, 92, 246, 0.3); }
.badge-unknown { background: rgba(156, 163, 175, 0.2); color: #9ca3af; border: 1px solid rgba(156, 163, 175, 0.3); }

.log-box {
    background-color: #0d0915;
    color: #a7f3d0;
    font-family: 'Courier New', monospace;
    font-size: 0.9rem;
    padding: 15px;
    border-radius: 8px;
    border: 1px solid #10b981;
    overflow-y: scroll;
    max-height: 250px;
    margin-bottom: 20px;
}
</style>
""", unsafe_allow_html=True)

# Helper to save API Key to .env
def save_api_key(key: str):
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    with open(env_path, "w") as f:
        f.write(f'GEMINI_API_KEY="{key}"\n')
        f.write(f'APP_DATA_DIR="{dirs["secure"]}"\n')
    os.environ["GEMINI_API_KEY"] = key

# App Title Header
st.markdown("""
<div class="title-container">
    <h1>🛡️ GUARDIAN MEDICAL DE-IDENTIFIER</h1>
    <p>Securely extract, catalog, and de-identify patient medical records using local salt-based pseudonymisation</p>
</div>
""", unsafe_allow_html=True)

# Sidebar Config
st.sidebar.markdown("### ⚙️ Pipeline Configuration")

run_mode = st.sidebar.radio(
    "Execution Mode",
    ["🌟 Gemini API (Cloud)", "🏠 Local Ollama (Gemma 4)", "🧪 Mock/Dry-Run (No model needed)"],
    help="Choose where inference runs: Google's API, a local Ollama server, or a mock demo."
)

# --- Gemini API settings ---
if run_mode.startswith("🌟"):
    env_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
    if env_key and "GEMINI_API_KEY" not in st.session_state.get("_manually_saved", []):
        st.sidebar.success("✅ API key auto-detected from environment")

    api_key_input = st.sidebar.text_input(
        "Gemini API Key",
        value=env_key,
        type="password",
        help="Paste your Gemini API key here, or set GEMINI_API_KEY in your environment."
    )
    
    gemini_model_input = st.sidebar.selectbox(
        "Gemini Model",
        ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-2.5-pro", "gemini-1.5-pro"],
        index=0,
        help="Select the Gemini model to use for transcription and de-identification."
    )

    if st.sidebar.button("Save Credentials"):
        save_api_key(api_key_input)
        st.session_state.setdefault("_manually_saved", []).append("GEMINI_API_KEY")
        st.sidebar.success("Credentials saved!")

    st.sidebar.markdown(
        "🔑 [Get a free Gemini API key →](https://aistudio.google.com/apikey)",
        help="Opens Google AI Studio where you can create an API key in seconds."
    )
    st.session_state["_backend"] = "gemini"
    st.session_state["_gemini_model"] = gemini_model_input

# --- Ollama local settings ---
elif run_mode.startswith("🏠"):
    ollama_model = st.sidebar.selectbox(
        "Ollama Model",
        ["gemma4:e4b", "gemma4:e2b", "gemma4:12b", "gemma4:26b"],
        index=0,
        help="Select the Gemma 4 model variant pulled in Ollama."
    )
    ollama_url = st.sidebar.text_input(
        "Ollama Server URL",
        value="http://localhost:11434/v1",
        help="Default Ollama endpoint. Change only if running on a remote host."
    )
    st.sidebar.info(
        "💡 **Setup:** Install Ollama and pull Gemma 4:\n"
        "```\nbrew install ollama\nollama pull gemma4:e4b\n```"
    )
    st.session_state["_backend"] = "ollama"
    st.session_state["_ollama_model"] = ollama_model
    st.session_state["_ollama_url"] = ollama_url
    api_key_input = None

# --- Mock mode ---
else:
    st.sidebar.info(
        "💡 **Mock Mode** simulates the pipeline using pre-compiled data, "
        "allowing you to test de-identification without any API or model."
    )
    st.session_state["_backend"] = "mock"
    api_key_input = None

# Tabs
tab_deidentify, tab_reidentify, tab_catalogue = st.tabs([
    "📥 Process & De-identify", 
    "📤 Re-identify Returned Report", 
    "🔐 Private Identity Catalogue"
])

# ==========================================
# TAB 1: PROCESS & DE-IDENTIFY
# ==========================================
with tab_deidentify:
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.subheader("1. Ingest Raw Session Data")
    
    uploaded_files = st.file_uploader(
        "Upload raw medical documents, audio transcripts, or video recordings (PDF, TXT, MP3, MP4, etc.)", 
        accept_multiple_files=True
    )
    
    if uploaded_files:
        st.write("📂 **Ready to process:**")
        cols = st.columns(len(uploaded_files))
        for idx, file in enumerate(uploaded_files):
            with cols[idx]:
                st.info(f"📄 {file.name}\n({round(file.size / 1024, 2)} KB)")
                # Save file to input directory
                dest_path = os.path.join(dirs["input"], file.name)
                with open(dest_path, "wb") as f:
                    f.write(file.getbuffer())
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.subheader("2. Run De-identification Pipeline")
    
    if st.button("🚀 Execute Pipeline", use_container_width=True):
        _backend = st.session_state.get("_backend", "mock")
        _is_live = _backend in ("gemini", "ollama")
        if not uploaded_files and _is_live:
            st.error("Please upload at least one raw medical record file before processing.")
        elif not os.getenv("GEMINI_API_KEY") and _backend == "gemini":
            st.error("API Key not set. Please configure it in the sidebar settings.")
        else:
            log_container = st.empty()
            logs = []
            
            def add_log(msg):
                logs.append(msg)
                log_container.markdown(
                    f'<div class="log-box">{"<br>".join(logs)}</div>', 
                    unsafe_allow_html=True
                )

            async def execute():
                try:
                    if _backend == "mock":
                        add_log("[SYSTEM] Starting pipeline in Mock/Dry-Run Mode...")
                        add_log("[Stage 1] Ingesting files...")
                        add_log("  - Found mock file: intake_form.txt")
                        add_log("  - Found mock file: session_interview.txt")
                        await asyncio.sleep(0.5)
                        add_log("[Stage 1] Transcribing and extracting transcripts verbatim...")
                        add_log("  - Extraction of 'intake_form.txt' complete.")
                        add_log("  - Extraction of 'session_interview.txt' complete (including visual behaviors).")
                        await asyncio.sleep(0.5)
                        add_log("[Stage 2] Compiling unified chronological ledger...")
                        add_log("  - Interleaved and sorted events based on normalised timestamps.")
                        
                        from verify_mock import MOCK_UNIFIED_CHRONOLOGY, MOCK_DISCOVERED_ENTITIES
                        unified_chronology = MOCK_UNIFIED_CHRONOLOGY
                        discovered_entities = MOCK_DISCOVERED_ENTITIES
                        await asyncio.sleep(0.5)
                    else:
                        _ollama_model = st.session_state.get("_ollama_model")
                        _gemini_model = st.session_state.get("_gemini_model")
                        _agent_kwargs = dict(
                            backend=_backend,
                            api_key=os.getenv("GEMINI_API_KEY") if _backend == "gemini" else None,
                            gemini_model=_gemini_model if _backend == "gemini" else None,
                            ollama_model=_ollama_model if _backend == "ollama" else None,
                        )
                        add_log(f"[SYSTEM] Starting pipeline via {_backend.upper()} backend...")
                        input_files = [
                            os.path.join(dirs["input"], f.name) for f in uploaded_files
                        ]
                        
                        # Live Stage 1: Transcription
                        transcripts = []
                        for filepath in input_files:
                            fname = os.path.basename(filepath)
                            add_log(f"[Stage 1] Running TranscriberAgent on '{fname}'...")
                            transcript = await transcribe_media(filepath, **_agent_kwargs)
                            transcripts.append(transcript)
                            # Save securely
                            save_json(transcript, os.path.join(dirs["secure"], f"verbatim_{os.path.splitext(fname)[0]}.json"))
                            add_log(f"  - Verbatim transcription of '{fname}' saved securely.")
                        
                        # Live Stage 2: Cataloguing
                        add_log("[Stage 2] Running CataloguerAgent to unify timelines...")
                        unified_chronology = await catalogue_transcripts(transcripts, **_agent_kwargs)
                        save_json(unified_chronology, os.path.join(dirs["secure"], "unified_chronology.json"))
                        add_log("  - Chronological ledger compiled and stored securely.")
                        
                        # Live Stage 3.1: PII Discovery
                        add_log("[Stage 3.1] Running DeidentifierAgent to discover sensitive entities...")
                        discovered_entities = await discover_pii_entities(unified_chronology, **_agent_kwargs)
                        save_json(discovered_entities, os.path.join(dirs["secure"], "discovered_entities.json"))
                    
                    # Stage 3.2: Deterministic replacement (Runs same python code for both modes!)
                    add_log("[Stage 3.2] Generating pseudonym hashes and applying deterministic replacement...")
                    deidentified_chrono, identity_catalogue, replacement_map = perform_deidentification(
                        unified_chronology, discovered_entities
                    )
                    
                    # Save local secure keys
                    save_json(identity_catalogue, os.path.join(dirs["secure"], "identity_catalogue.json"))
                    save_json(deidentified_chrono, os.path.join(dirs["secure"], "deidentified_chronology.json"))
                    add_log("  - Secure Identity Catalogue generated locally.")
                    add_log("  - Replaced all discovered PII names & aliases with secure hashes.")
                    
                    # Stage 3.2b: In-place document de-identification (PDF/DOCX)
                    add_log("[Stage 3.2b] De-identifying original documents in-place...")
                    doc_extensions = {".pdf", ".docx"}
                    deidentified_doc_paths = []
                    input_files_list = [
                        os.path.join(dirs["input"], f.name) for f in uploaded_files
                    ] if uploaded_files else []
                    
                    for filepath in input_files_list:
                        fname = os.path.basename(filepath)
                        ext = os.path.splitext(fname)[1].lower()
                        if ext in doc_extensions:
                            out_name = f"deidentified_{fname}"
                            out_path = os.path.join(dirs["output"], out_name)
                            try:
                                success = deidentify_document(filepath, out_path, replacement_map)
                                if success:
                                    deidentified_doc_paths.append(out_path)
                                    add_log(f"  ✓ {fname} → {out_name} (formatting preserved)")
                                else:
                                    add_log(f"  · {fname}: scanned/unsearchable — OCR did not find matches")
                            except Exception as e:
                                add_log(f"  ✗ Error processing {fname}: {e}")
                        else:
                            add_log(f"  · {fname}: not PDF/DOCX, covered by JSON output")
                    
                    # Stage 3.3: Generate synthesis summary and shareable reports
                    add_log("[Stage 3.3] Generating synthesis summary and shareable reports...")
                    
                    synthesis_path = os.path.join(dirs["output"], "synthesis_summary.txt")
                    synthesis_txt = write_synthesis_summary(
                        synthesis_path, deidentified_chrono, identity_catalogue
                    )
                        
                    shareable_json_path = os.path.join(dirs["output"], "shareable_pseudonymised_report.json")
                    save_json(deidentified_chrono, shareable_json_path)
                    
                    add_log("[SYSTEM] PIPELINE RUN SUCCESSFULLY COMPLETED!")
                    st.success("Pipeline executed successfully! Scroll down to review and download outputs.")
                    st.session_state["pipeline_run"] = deidentified_chrono
                    st.session_state["synthesis_summary_txt"] = synthesis_txt
                    st.session_state["deidentified_doc_paths"] = deidentified_doc_paths
                    
                except Exception as e:
                    add_log(f"[ERROR] Pipeline aborted: {e}")
                    st.error(f"Execution failed: {e}")

            asyncio.run(execute())
    st.markdown('</div>', unsafe_allow_html=True)

    # Display results if present
    if "pipeline_run" in st.session_state:
        deidentified_chrono = st.session_state["pipeline_run"]
        synthesis_txt = st.session_state.get("synthesis_summary_txt", "")
        deidentified_doc_paths = st.session_state.get("deidentified_doc_paths", [])
        
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.subheader("📥 3. Download De-identified Reports")
        
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                label="📋 Download Synthesis Summary (.txt)",
                data=synthesis_txt,
                file_name="synthesis_summary.txt",
                mime="text/plain",
                use_container_width=True
            )
        with col2:
            st.download_button(
                label="📊 Download Shareable JSON Report (.json)",
                data=json.dumps(deidentified_chrono, indent=2, ensure_ascii=False),
                file_name="shareable_pseudonymised_report.json",
                mime="application/json",
                use_container_width=True
            )
        
        # Offer download buttons for each deidentified document
        if deidentified_doc_paths:
            st.markdown("---")
            st.write("**📄 De-identified Documents (formatting preserved):**")
            doc_cols = st.columns(min(len(deidentified_doc_paths), 3))
            for idx, doc_path in enumerate(deidentified_doc_paths):
                doc_name = os.path.basename(doc_path)
                ext = os.path.splitext(doc_name)[1].lower()
                mime = "application/pdf" if ext == ".pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                icon = "📕" if ext == ".pdf" else "📘"
                with doc_cols[idx % len(doc_cols)]:
                    with open(doc_path, "rb") as df:
                        st.download_button(
                            label=f"{icon} {doc_name}",
                            data=df.read(),
                            file_name=doc_name,
                            mime=mime,
                            use_container_width=True,
                            key=f"download_doc_{idx}",
                        )
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.subheader("📊 4. Interactive Clinical Chronology")
        st.info("ℹ️ All Personally Identifiable Information (PII) has been safely replaced by unique cryptographic hash pseudonyms.")
        
        st.write(f"**Patient Summary Synthesis:** {deidentified_chrono.get('patient_summary')}")
        
        for idx, event in enumerate(deidentified_chrono.get("chronology", [])):
            cat = event.get('category', '').lower()
            badge_class = "badge-unknown"
            if "intake" in cat:
                badge_class = "badge-intake"
            elif "interview" in cat:
                badge_class = "badge-interview"
            elif "assessment" in cat:
                badge_class = "badge-assessment"
            elif "activity" in cat:
                badge_class = "badge-activity"
                
            st.markdown(f"""
            <div class="timeline-event">
                <span class="timeline-badge {badge_class}">{event.get('category')}</span>
                <strong>{event.get('timestamp')}</strong> | Speaker: <code>{event.get('speaker')}</code>
                <div style="margin-top: 10px; font-size: 1.05rem;">{event.get('event_details')}</div>
            </div>
            """, unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

# ==========================================
# TAB 2: RE-IDENTIFY RETURNED REPORT
# ==========================================
with tab_reidentify:
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.subheader("Reverse Pseudonymisation Mapping")
    st.write(
        "Upload a completed or edited report returned by the external clinician/recipient. "
        "The system will automatically scan the text for pseudonym hashes, consult the private "
        "local `Identity Catalogue`, and restore the patient's original PII details."
    )
    
    returned_file = st.file_uploader(
        "Upload returned file (.txt, .json, .pdf, or .docx)",
        key="returned_file",
        type=["txt", "json", "pdf", "docx"],
    )
    
    if returned_file:
        file_ext = os.path.splitext(returned_file.name)[1].lower()
        temp_path = os.path.join(dirs["output"], f"temp_returned{file_ext}")
        with open(temp_path, "wb") as f:
            f.write(returned_file.getbuffer())
            
        if st.button("🔓 Restore Original Identity Details", use_container_width=True):
            try:
                is_document = file_ext in (".pdf", ".docx")
                
                if is_document:
                    # Use document editor for format-preserved re-identification
                    cat_path = os.path.join(dirs["secure"], "identity_catalogue.json")
                    identity_catalogue = load_json(cat_path)
                    if not identity_catalogue:
                        st.error("Identity Catalogue not found. Run the de-identification pipeline first.")
                    else:
                        out_name = f"reidentified_{returned_file.name}"
                        out_path = os.path.join(dirs["output"], out_name)
                        reidentify_document(temp_path, out_path, identity_catalogue)
                        
                        st.success("Re-identification successful! Document formatting preserved.")
                        
                        mime = "application/pdf" if file_ext == ".pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        with open(out_path, "rb") as df:
                            st.download_button(
                                label=f"💾 Download Re-identified {file_ext.upper()} Report",
                                data=df.read(),
                                file_name=out_name,
                                mime=mime,
                                use_container_width=True,
                            )
                else:
                    # Text/JSON re-identification (existing flow)
                    reidentified_content = reidentify_report(temp_path)
                    
                    st.success("Re-identification successful!")
                    
                    st.write("### 📄 Re-identified Clinical Record Preview")
                    st.text_area("Final Identified Report", value=reidentified_content, height=400)
                    
                    st.download_button(
                        label="💾 Download Re-identified Final Report (.txt)",
                        data=reidentified_content,
                        file_name="final_identified_report.txt",
                        mime="text/plain",
                        use_container_width=True,
                    )
                
            except Exception as e:
                st.error(f"Re-identification failed: {e}")
    st.markdown('</div>', unsafe_allow_html=True)

# ==========================================
# TAB 3: PRIVATE IDENTITY CATALOGUE (SECURE)
# ==========================================
with tab_catalogue:
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.subheader("🔐 Active Identity Mappings")
    st.warning("⚠️ **CONFIDENTIAL DATA**: Keep this catalogue private. It is never shared with external recipients.")
    
    cat_path = os.path.join(dirs["secure"], "identity_catalogue.json")
    if os.path.exists(cat_path):
        identity_catalogue = load_json(cat_path)
        
        st.write("The following pseudonym hashes are securely registered on this machine:")
        
        for key, details in identity_catalogue.items():
            with st.expander(f"🔑 {key} ── mapped to ── {details['canonical_name']}"):
                st.write(f"**Primary Full Name:** `{details['canonical_name']}`")
                st.write(f"**Entity Category:** `{details['entity_type']}`")
                st.write(f"**Relationship Role:** {details['relationship_context']}")
                st.write(f"**Discovered Aliases/Variations:** {', '.join([f'`{v}`' for v in details['variations']])}")
    else:
        st.info("No active Identity Catalogue found. Run the de-identification pipeline first to generate mappings.")
    st.markdown('</div>', unsafe_allow_html=True)
