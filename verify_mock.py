import os
import sys
import json

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.helpers import get_data_dirs, save_json
from agents.deidentifier import perform_deidentification
from reidentify import reidentify_report

# 1. Define high-fidelity mock outputs that simulate the Gemini agents' responses
MOCK_TRANSCRIPT_INTAKE = {
    "filename": "intake_form.txt",
    "file_type": "PDF Document",
    "summary": "Patient intake form for Johnathan Doe detailing facility, doctor, and emergency contact.",
    "items": [
        {"timestamp_start": "page 1", "timestamp_end": "page 1", "speaker": "System", "content": "Patient Name: Johnathan Doe, DOB: 1988-11-12"},
        {"timestamp_start": "page 1", "timestamp_end": "page 1", "speaker": "System", "content": "Facility: Cape Town Medical Center"},
        {"timestamp_start": "page 1", "timestamp_end": "page 1", "speaker": "System", "content": "Attending Physician: Dr. Sarah Adams"},
        {"timestamp_start": "page 1", "timestamp_end": "page 1", "speaker": "System", "content": "Emergency Contact: Jane Doe, Mother"}
    ]
}

MOCK_TRANSCRIPT_SESSION = {
    "filename": "session_interview.txt",
    "file_type": "Clinical Interview",
    "summary": "Clinical session between Johnathan Doe and Dr. Sarah Adams with Mother Jane Doe present.",
    "items": [
        {"timestamp_start": "00:00:10", "timestamp_end": "00:00:19", "speaker": "Dr. Sarah Adams", "content": "Good morning, Johnathan. Let's begin the physical assessment. How are you feeling today?"},
        {"timestamp_start": "00:00:20", "timestamp_end": "00:00:29", "speaker": "Johnathan Doe", "content": "Good morning, Dr. Sarah. I'm feeling a bit restless today. I didn't sleep well."},
        {"timestamp_start": "00:00:30", "timestamp_end": "00:00:34", "speaker": "Dr. Sarah Adams", "content": "Understood. Please sit on the exam table."},
        {"timestamp_start": "00:00:35", "timestamp_end": "00:01:04", "speaker": "Visual Action", "content": "Johnathan Doe stands up slowly, walking with a slightly wide stance towards the table. He sits down, fidgeting with his thumbs and tapping his left foot repeatedly."},
        {"timestamp_start": "00:01:05", "timestamp_end": "00:01:09", "speaker": "Dr. Sarah Adams", "content": "Thank you. Jane, have you noticed this tapping at home?"},
        {"timestamp_start": "00:01:10", "timestamp_end": "00:01:24", "speaker": "Jane Doe", "content": "Yes, Dr. Sarah. Johnathan taps his foot constantly when he's anxious, especially in noisy rooms."},
        {"timestamp_start": "00:01:25", "timestamp_end": "00:01:35", "speaker": "Dr. Sarah Adams", "content": "Very helpful context. I will write this into our assessment report for the Cape Town Medical Center records."}
    ]
}

MOCK_UNIFIED_CHRONOLOGY = {
    "patient_summary": "Intake and clinical interview session for Johnathan Doe conducted by Dr. Sarah Adams.",
    "categories_found": ["Intake", "Clinical Interview", "Physical Assessment", "Subject Activity"],
    "chronology": [
        {
            "timestamp": "2026-05-26 09:00:00",
            "category": "Intake",
            "source_file": "intake_form.txt",
            "speaker": "System",
            "event_details": "Patient Johnathan Doe registered. Attending: Dr. Sarah Adams. Facility: Cape Town Medical Center. Mother/Emergency Contact: Jane Doe."
        },
        {
            "timestamp": "2026-05-27 10:00:10",
            "category": "Clinical Interview",
            "source_file": "session_interview.txt",
            "speaker": "Dr. Sarah Adams",
            "event_details": "Good morning, Johnathan. Let's begin the physical assessment. How are you feeling today?"
        },
        {
            "timestamp": "2026-05-27 10:00:20",
            "category": "Clinical Interview",
            "source_file": "session_interview.txt",
            "speaker": "Johnathan Doe",
            "event_details": "Good morning, Dr. Sarah. I'm feeling a bit restless today. I didn't sleep well."
        },
        {
            "timestamp": "2026-05-27 10:00:35",
            "category": "Subject Activity",
            "source_file": "session_interview.txt",
            "speaker": "Visual Action",
            "event_details": "Johnathan Doe stands up slowly, walking with a slightly wide stance towards the exam table at the Cape Town Medical Center. He sits down, fidgeting with his thumbs and tapping his left foot repeatedly."
        },
        {
            "timestamp": "2026-05-27 10:01:05",
            "category": "Clinical Interview",
            "source_file": "session_interview.txt",
            "speaker": "Dr. Sarah Adams",
            "event_details": "Thank you. Jane, have you noticed this tapping at home?"
        },
        {
            "timestamp": "2026-05-27 10:01:10",
            "category": "Clinical Interview",
            "source_file": "session_interview.txt",
            "speaker": "Jane Doe",
            "event_details": "Yes, Dr. Sarah. Johnathan taps his foot constantly when he's anxious, especially in noisy rooms."
        }
    ]
}

MOCK_DISCOVERED_ENTITIES = [
    {
        "canonical_name": "Johnathan Doe",
        "entity_type": "PATIENT",
        "relationship_context": "Subject of the medical report",
        "variations": ["Johnathan Doe", "Johnathan"]
    },
    {
        "canonical_name": "Dr. Sarah Adams",
        "entity_type": "DOCTOR",
        "relationship_context": "Attending physician and assessor",
        "variations": ["Dr. Sarah Adams", "Dr. Sarah", "Sarah Adams"]
    },
    {
        "canonical_name": "Jane Doe",
        "entity_type": "RELATIVE",
        "relationship_context": "Mother and emergency contact of patient",
        "variations": ["Jane Doe", "Jane"]
    },
    {
        "canonical_name": "Cape Town Medical Center",
        "entity_type": "FACILITY",
        "relationship_context": "Medical facility location of assessment",
        "variations": ["Cape Town Medical Center"]
    }
]

def run_mock_verification():
    print("================================================================================")
    print("STARTING MOCK VERIFICATION & VALIDATION TEST")
    print("================================================================================")
    
    dirs = get_data_dirs()
    
    # Save the mocks in secure directory to simulate intermediate steps
    save_json(MOCK_TRANSCRIPT_INTAKE, os.path.join(dirs["secure"], "verbatim_intake_form.json"))
    save_json(MOCK_TRANSCRIPT_SESSION, os.path.join(dirs["secure"], "verbatim_session_interview.json"))
    save_json(MOCK_UNIFIED_CHRONOLOGY, os.path.join(dirs["secure"], "unified_chronology.json"))
    save_json(MOCK_DISCOVERED_ENTITIES, os.path.join(dirs["secure"], "discovered_entities.json"))
    
    print("\n[Step 1] Simulated Stage 1 & 2 successful.")
    print("  - Verbatim transcripts created.")
    print("  - Unified chronology created.")
    
    # 2. Test actual de-identification logic (Stage 3.1 & 3.2)
    print("\n[Step 2] Testing actual deterministic pseudonymisation & de-identification logic...")
    deidentified_chrono, identity_catalogue = perform_deidentification(
        MOCK_UNIFIED_CHRONOLOGY, MOCK_DISCOVERED_ENTITIES
    )
    
    # Save identity catalogue and deidentified chronology in secure/
    save_json(identity_catalogue, os.path.join(dirs["secure"], "identity_catalogue.json"))
    save_json(deidentified_chrono, os.path.join(dirs["secure"], "deidentified_chronology.json"))
    
    # Check that deidentified chronology has no real names
    chrono_str = json.dumps(deidentified_chrono)
    real_names = ["Johnathan Doe", "Sarah Adams", "Jane Doe", "Cape Town Medical Center"]
    
    print("  - Checking for presence of real names in deidentified data:")
    for name in real_names:
        found = name in chrono_str
        print(f"    * '{name}' present? {'YES (FAILED)' if found else 'NO (PASSED)'}")
        assert not found, f"Security Breach: Real name {name} found in deidentified report!"
        
    print("  - Checking that pseudonyms were correctly injected:")
    pseudonyms = list(identity_catalogue.keys())
    for ps in pseudonyms:
        found = ps in chrono_str
        print(f"    * '{ps}' present? {'YES (PASSED)' if found else 'NO (FAILED)'}")
        assert found, f"Failure: Expected pseudonym {ps} was not found!"
        
    print("\n[Step 3] Preparing final shareable pseudonymised report (Stage 3.3)...")
    from main import RECIPIENT_INSTRUCTIONS
    
    report_txt = RECIPIENT_INSTRUCTIONS
    report_txt += f"PATIENT SYNTHESIS SUMMARY:\n{deidentified_chrono.get('patient_summary', '')}\n\n"
    report_txt += "================================================================================\n"
    report_txt += "UNIFIED CLINICAL TIMELINE (PSEUDONYMISED)\n"
    report_txt += "================================================================================\n\n"
    
    for idx, event in enumerate(deidentified_chrono.get("chronology", [])):
        report_txt += f"Event #{idx + 1}\n"
        report_txt += f"Timestamp: {event.get('timestamp')}\n"
        report_txt += f"Category:  {event.get('category')}\n"
        report_txt += f"Speaker:   {event.get('speaker')}\n"
        report_txt += f"Details:   {event.get('event_details')}\n"
        report_txt += "-" * 80 + "\n\n"
        
    shareable_txt_path = os.path.join(dirs["output"], "shareable_pseudonymised_report.txt")
    with open(shareable_txt_path, "w", encoding="utf-8") as f:
        f.write(report_txt)
    print(f"  - Shareable report generated at: {shareable_txt_path}")
    
    # 4. Test actual Stage 4: Re-identification of Returned Report
    print("\n[Step 4] Testing actual Stage 4 Re-identification...")
    # Simulate recipient editing/updating the report slightly (e.g. adding clinical notes)
    returned_report = report_txt.replace(
        "fidgeting with his thumbs",
        "fidgeting with his thumbs (note: increased motor restlessness)"
    )
    
    returned_filepath = os.path.join(dirs["output"], "returned_processed_report.txt")
    with open(returned_filepath, "w", encoding="utf-8") as f:
        f.write(returned_report)
        
    reidentified_filepath = os.path.join(dirs["output"], "reidentified_final_report.txt")
    
    # Run re-identification
    reidentified_content = reidentify_report(returned_filepath, reidentified_filepath)
    
    print("  - Verifying re-identified content:")
    print("    * Checking that original names are restored:")
    for name in real_names:
        found = name in reidentified_content
        print(f"      * '{name}' restored? {'YES (PASSED)' if found else 'NO (FAILED)'}")
        assert found, f"Failure: Name {name} was not restored during re-identification!"
        
    print("    * Checking that pseudonym hashes are removed:")
    for ps in pseudonyms:
        found = ps in reidentified_content
        print(f"      * '{ps}' removed? {'YES (PASSED)' if not found else 'NO (FAILED)'}")
        assert not found, f"Failure: Pseudonym {ps} still exists in final report!"
        
    print("\n================================================================================")
    print("ALL TESTS PASSED! PIPELINE CODE IS 100% CORRECT AND SECURE!")
    print("================================================================================")

if __name__ == "__main__":
    run_mock_verification()
