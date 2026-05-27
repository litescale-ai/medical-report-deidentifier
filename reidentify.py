import os
import json
import argparse
from utils.helpers import load_json, get_data_dirs

def reidentify_report(pseudonymised_filepath: str, output_filepath: str = None) -> str:
    """Re-identifies a pseudonymised report using the secure Identity Catalogue.
    
    Args:
        pseudonymised_filepath: Path to the deidentified/returned report (JSON or Text).
        output_filepath: Path to save the re-identified report.
        
    Returns:
        The content of the re-identified report.
    """
    dirs = get_data_dirs()
    catalogue_path = os.path.join(dirs["secure"], "identity_catalogue.json")
    
    if not os.path.exists(catalogue_path):
        raise FileNotFoundError(f"Secure Identity Catalogue not found at {catalogue_path}")
        
    identity_catalogue = load_json(catalogue_path)
    
    # Read the pseudonymised report
    is_json = pseudonymised_filepath.endswith(".json")
    
    if is_json:
        report_data = load_json(pseudonymised_filepath)
        report_str = json.dumps(report_data, ensure_ascii=False, indent=2)
    else:
        with open(pseudonymised_filepath, "r", encoding="utf-8") as f:
            report_str = f.read()
            
    # Reverse replacement: replace hashes with canonical names
    # Sort hashes by key length descending just in case, but they are all similar length
    for pseudonym_hash, details in identity_catalogue.items():
        real_name = details["canonical_name"]
        # Replace hash with original canonical name
        report_str = report_str.replace(pseudonym_hash, real_name)
        
    # Clean up the recipient instruction banner if it was a text file
    instruction_banner_indicator = "CRITICAL RECIPIENT INSTRUCTIONS"
    if instruction_banner_indicator in report_str and not is_json:
        # If it's a text report, we strip the instruction banner block to make it clean
        lines = report_str.split("\n")
        banner_end_idx = -1
        for i, line in enumerate(lines[:30]): # look for banner end in first 30 lines
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
