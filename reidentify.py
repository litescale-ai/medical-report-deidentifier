import os
import json
import argparse
from utils.helpers import load_json, get_data_dirs
from utils.document_editor import reidentify_document

def reidentify_report(pseudonymised_filepath: str, output_filepath: str = None) -> str:
    """Re-identifies a pseudonymised report using the secure Identity Catalogue.
    
    Supports .txt, .json, .pdf, and .docx files.  For PDF and DOCX, the
    document formatting is preserved by delegating to the document editor.
    
    Args:
        pseudonymised_filepath: Path to the deidentified/returned report.
        output_filepath: Path to save the re-identified report.
        
    Returns:
        The content of the re-identified report (text for txt/json,
        output path string for pdf/docx).
    """
    dirs = get_data_dirs()
    catalogue_path = os.path.join(dirs["secure"], "identity_catalogue.json")
    
    if not os.path.exists(catalogue_path):
        raise FileNotFoundError(f"Secure Identity Catalogue not found at {catalogue_path}")
        
    identity_catalogue = load_json(catalogue_path)
    
    # Determine file type
    ext = os.path.splitext(pseudonymised_filepath)[1].lower()
    
    # --- PDF / DOCX: format-preserved re-identification ---
    if ext in (".pdf", ".docx"):
        if not output_filepath:
            dirname, filename = os.path.split(pseudonymised_filepath)
            output_filepath = os.path.join(dirname, f"reidentified_{filename}")
        
        reidentify_document(pseudonymised_filepath, output_filepath, identity_catalogue)
        return output_filepath
    
    # --- Text / JSON: string-level re-identification ---
    is_json = ext == ".json"
    
    if is_json:
        report_data = load_json(pseudonymised_filepath)
        report_str = json.dumps(report_data, ensure_ascii=False, indent=2)
    else:
        with open(pseudonymised_filepath, "r", encoding="utf-8") as f:
            report_str = f.read()
            
    # Reverse replacement: replace hashes with canonical names
    for pseudonym_hash, details in identity_catalogue.items():
        real_name = details["canonical_name"]
        report_str = report_str.replace(pseudonym_hash, real_name)
        
    # Clean up the recipient instruction banner if it was a text file
    instruction_banner_indicator = "CRITICAL RECIPIENT INSTRUCTIONS"
    if instruction_banner_indicator in report_str and not is_json:
        lines = report_str.split("\n")
        banner_end_idx = -1
        for i, line in enumerate(lines[:30]):
            if "================================================================================" in line and i > 5:
                banner_end_idx = i
                break
        if banner_end_idx != -1:
            report_str = "\n".join(lines[banner_end_idx + 1:]).strip()

    # Save to output
    if output_filepath:
        os.makedirs(os.path.dirname(output_filepath), exist_ok=True)
        if is_json:
            with open(output_filepath, "w", encoding="utf-8") as f:
                json.dump(json.loads(report_str), f, indent=2, ensure_ascii=False)
        else:
            with open(output_filepath, "w", encoding="utf-8") as f:
                f.write(report_str)
                
    return report_str

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-identify a returned pseudonymised medical report.")
    parser.add_argument("input_file", help="Path to the pseudonymised/returned report file")
    parser.add_argument("--output", "-o", help="Path to save the re-identified output file")
    
    args = parser.parse_args()
    
    try:
        output_path = args.output
        if not output_path:
            # Default output name: prepend "reidentified_" to the input filename
            dirname, filename = os.path.split(args.input_file)
            output_path = os.path.join(dirname, f"reidentified_{filename}")
            
        print(f"Re-identifying {args.input_file}...")
        reidentify_report(args.input_file, output_path)
        print(f"Success! Re-identified report saved to {output_path}")
    except Exception as e:
        print(f"Error: {e}")
