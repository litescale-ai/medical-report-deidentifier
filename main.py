import os
import sys
import json
import asyncio
from dotenv import load_dotenv

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.helpers import get_data_dirs, save_json, load_json
from utils.document_editor import deidentify_document, write_synthesis_summary
from agents.transcriber import transcribe_media
from agents.cataloguer import catalogue_transcripts
from agents.deidentifier import discover_pii_entities, perform_deidentification

RECIPIENT_INSTRUCTIONS = """================================================================================
CRITICAL RECIPIENT INSTRUCTIONS - PLEASE READ CAREFULLY
================================================================================
This medical document has been pseudonymised for data privacy and security.
All Personal Identifiable Information (PII) including names of patients, clinicians,
relatives, facilities, and locations have been replaced with secure pseudonym hashes:
e.g., PATIENT_A4B3D2, DOCTOR_E8F9A0, etc.

IMPORTANT: You MUST preserve all these pseudonym hashes (e.g. PATIENT_XXXX) exactly 
as they appear in this document in any returned, updated, or generated reports.
Do NOT remove, edit, or replace these hashes. 

The originator retains the secure Identity Catalogue. When you return the processed 
report, the originator will use the preserved hashes to automatically and securely 
re-identify the patient and parties.
================================================================================

"""

async def run_pipeline():
    # 1. Load environment variables
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    backend = os.getenv("AGENT_BACKEND", "gemini").lower().strip()
    ollama_model = os.getenv("OLLAMA_MODEL")
    if backend == "gemini" and not api_key:
        print("Error: GEMINI_API_KEY environment variable not found in .env file.")
        print("Set AGENT_BACKEND=ollama to use a local Ollama model instead.")
        sys.exit(1)
        
    dirs = get_data_dirs()
    input_dir = dirs["input"]
    secure_dir = dirs["secure"]
    output_dir = dirs["output"]
    
    # 2. Get input files
    input_files = [
        os.path.join(input_dir, f) for f in os.listdir(input_dir)
        if os.path.isfile(os.path.join(input_dir, f)) and not f.startswith(".")
    ]
    
    if not input_files:
        print(f"No input files found in {input_dir}. Please place raw medical data there.")
        return
        
    print(f"Found {len(input_files)} input file(s) to process. Backend: {backend.upper()}")
    
    _agent_kwargs = dict(
        backend=backend,
        api_key=api_key if backend == "gemini" else None,
        ollama_model=ollama_model if backend == "ollama" else None,
    )
    
    # 3. Stage 1: Multimodal Verbatim Extraction & Transcription
    transcripts = []
    for filepath in input_files:
        filename = os.path.basename(filepath)
        print(f"--- Stage 1: Transcribing and extracting {filename} ---")
        try:
            transcript = await transcribe_media(filepath, **_agent_kwargs)
            transcripts.append(transcript)
            # Save intermediate secure verbatim transcript
            verbatim_path = os.path.join(secure_dir, f"verbatim_{os.path.splitext(filename)[0]}.json")
            save_json(transcript, verbatim_path)
            print(f"Verbatim extraction completed and saved securely to {verbatim_path}")
        except Exception as e:
            print(f"Error processing {filename}: {e}")
            
    if not transcripts:
        print("No transcripts successfully processed. Aborting pipeline.")
        return
        
    # 4. Stage 2: Chronological Cataloguing
    print("\n--- Stage 2: Compiling unified chronological catalogue ---")
    try:
        unified_chronology = await catalogue_transcripts(transcripts, **_agent_kwargs)
        chrono_path = os.path.join(secure_dir, "unified_chronology.json")
        save_json(unified_chronology, chrono_path)
        print(f"Unified chronological catalogue compiled and saved securely to {chrono_path}")
    except Exception as e:
        print(f"Error during cataloguing stage: {e}")
        return
        
    # 5. Stage 3.1 & 3.2: PII Discovery and Deterministic Pseudonymisation
    print("\n--- Stage 3: Discovering PII Named Entities and Hashing ---")
    try:
        discovered_entities = await discover_pii_entities(unified_chronology, **_agent_kwargs)
        
        # Save discovered entities for reference
        entities_path = os.path.join(secure_dir, "discovered_entities.json")
        save_json(discovered_entities, entities_path)
        
        print(f"Discovered {len(discovered_entities)} unique PII entities and relationships.")
        
        # Perform deterministic pseudonymisation
        deidentified_chrono, identity_catalogue, replacement_map = perform_deidentification(
            unified_chronology, discovered_entities
        )
        
        # Save secure identity catalogue
        catalogue_path = os.path.join(secure_dir, "identity_catalogue.json")
        save_json(identity_catalogue, catalogue_path)
        print(f"Secure Identity Catalogue mapping saved to {catalogue_path}")
        
        # Save deidentified chronology securely
        deidentified_path = os.path.join(secure_dir, "deidentified_chronology.json")
        save_json(deidentified_chrono, deidentified_path)
        
    except Exception as e:
        print(f"Error during de-identification stage: {e}")
        return

    # 6. Stage 3.2b: In-place document de-identification (PDF/DOCX)
    print("\n--- Stage 3.2b: De-identifying original documents in-place ---")
    doc_extensions = {".pdf", ".docx"}
    deidentified_docs = []
    for filepath in input_files:
        filename = os.path.basename(filepath)
        ext = os.path.splitext(filename)[1].lower()
        if ext in doc_extensions:
            out_name = f"deidentified_{filename}"
            out_path = os.path.join(output_dir, out_name)
            try:
                success = deidentify_document(filepath, out_path, replacement_map)
                if success:
                    deidentified_docs.append(out_path)
                    print(f"  ✓ {filename} → {out_name} (formatting preserved)")
                else:
                    print(f"  ⚠ {filename}: unsupported format, skipped")
            except Exception as e:
                print(f"  ✗ Error processing {filename}: {e}")
        else:
            print(f"  · {filename}: not a PDF/DOCX, covered by text/JSON output")

    # 7. Stage 3.3: Generate synthesis summary and shareable reports
    print("\n--- Stage 3.3: Preparing synthesis summary and shareable reports ---")
    try:
        # Write the standalone synthesis summary
        synthesis_path = os.path.join(output_dir, "synthesis_summary.txt")
        write_synthesis_summary(synthesis_path, deidentified_chrono, identity_catalogue)
        print(f"  - Synthesis Summary: {synthesis_path}")
            
        # Also save the pure deidentified JSON in output for easy programmatic use
        shareable_json_path = os.path.join(output_dir, "shareable_pseudonymised_report.json")
        save_json(deidentified_chrono, shareable_json_path)
        
        print(f"\nSuccess! Final shareable outputs generated at:")
        print(f"  - Synthesis Summary: {synthesis_path}")
        print(f"  - JSON Format:       {shareable_json_path}")
        for doc_path in deidentified_docs:
            print(f"  - Document:          {doc_path}")
        
    except Exception as e:
        print(f"Error preparing final output files: {e}")

if __name__ == "__main__":
    asyncio.run(run_pipeline())
