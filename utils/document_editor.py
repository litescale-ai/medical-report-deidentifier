"""Document editor for in-place PDF and DOCX de-identification.

Provides format-preserving find-and-replace for:
  - PDF files (via PyMuPDF): redact + insert with font matching
  - DOCX files (via python-docx): run-level replacement preserving styles

Also provides a synthesis summary writer for the companion output file.
"""

import os
import json
from typing import Optional


# ---------------------------------------------------------------------------
# PDF Editing (PyMuPDF)
# ---------------------------------------------------------------------------

def _int_to_rgb(color_int: int) -> tuple:
    """Convert an integer colour (0xRRGGBB) to a (r, g, b) float tuple."""
    if isinstance(color_int, (list, tuple)):
        return tuple(color_int)
    r = ((color_int >> 16) & 0xFF) / 255.0
    g = ((color_int >> 8) & 0xFF) / 255.0
    b = (color_int & 0xFF) / 255.0
    return (r, g, b)


def _pymupdf_fontname_to_base(fontname: str) -> str:
    """Map a PDF-internal font name to a PyMuPDF base-14 font name.

    PyMuPDF's insert_text only accepts base-14 font names.  We detect
    bold and italic variants to preserve weight/style.

    Base-14 families:
      Helvetica:  helv, hebo (bold), heit (italic), hebi (bold-italic)
      Times:      tiro, tibo (bold), tiit (italic), tibi (bold-italic)
      Courier:    cour, cobo (bold), coit (italic), cobi (bold-italic)
    """
    fn = fontname.lower()

    # Detect weight/style flags
    is_bold = "bold" in fn or "black" in fn or "heavy" in fn
    is_italic = "italic" in fn or "oblique" in fn

    # Detect family
    if "courier" in fn or "mono" in fn:
        if is_bold and is_italic:
            return "cobi"
        if is_bold:
            return "cobo"
        if is_italic:
            return "coit"
        return "cour"

    if "times" in fn or "serif" in fn:
        if is_bold and is_italic:
            return "tibi"
        if is_bold:
            return "tibo"
        if is_italic:
            return "tiit"
        return "tiro"

    if "symbol" in fn:
        return "symb"
    if "zapf" in fn:
        return "zadb"

    # Default: Helvetica family
    if is_bold and is_italic:
        return "hebi"
    if is_bold:
        return "hebo"
    if is_italic:
        return "heit"
    return "helv"


def _find_span_replacements(page, replacement_map: dict[str, str]) -> list[tuple]:
    """Find all PII text in a page using span-aware matching.

    Uses a hybrid strategy:
    - For short spans (labels, values): requires the target to match nearly
      the entire span text, preventing partial-label matches like "EasyPay"
      inside "EasyPay No:".
    - For longer spans (prose sentences): allows embedded matches with
      word-boundary checks, using search_for() to get tight bounding rects.

    Returns a list of (rect, original_text, replacement_text, style_dict,
    is_full_span) for every match found.
    """
    import pymupdf

    sorted_keys = sorted(replacement_map.keys(), key=len, reverse=True)
    already_matched = set()  # (block_idx, line_idx, span_idx)
    results = []

    blocks = page.get_text("dict")["blocks"]

    for bi, block in enumerate(blocks):
        if block.get("type", 0) != 0:
            continue
        for li, line in enumerate(block.get("lines", [])):
            for si, span in enumerate(line.get("spans", [])):
                span_id = (bi, li, si)
                if span_id in already_matched:
                    continue

                span_text = span["text"]
                span_stripped = span_text.strip()
                if not span_stripped:
                    continue

                style = {
                    "fontname": span.get("font", "helv"),
                    "size": span.get("size", 11.0),
                    "color": _int_to_rgb(span.get("color", 0)),
                    "flags": span.get("flags", 0),
                }

                for target in sorted_keys:
                    if target not in span_text:
                        continue

                    remaining = span_stripped.replace(target, "", 1).strip()

                    if not remaining:
                        # Target matches the entire span — use span bbox
                        bbox = pymupdf.Rect(span["bbox"])
                        results.append((bbox, target, replacement_map[target],
                                        style, True))
                        already_matched.add(span_id)
                        break  # full-span match — no other target can match

                    # Check if remaining text is just punctuation/whitespace
                    # (e.g. trailing comma, period) vs. a meaningful label
                    # suffix like "No:" or " Ltd"
                    import re
                    remaining_words = re.findall(r'[a-zA-Z]{2,}', remaining)
                    if remaining_words and len(remaining_words) <= 2:
                        # Remaining text has 1-2 real words — likely a label
                        # like "EasyPay No:" where "EasyPay" is part of the
                        # label, not a standalone entity.
                        continue

                    # Either remaining text is just punctuation (use span bbox)
                    # or it's prose with 3+ words (use search_for for tight rects)
                    if not remaining_words:
                        # Only punctuation/symbols remain — treat as full match
                        bbox = pymupdf.Rect(span["bbox"])
                        results.append((bbox, target, replacement_map[target],
                                        style, True))
                        already_matched.add(span_id)
                        break

                    # Prose span — use search_for() for tight rects.
                    # DON'T break — continue to find other targets in the
                    # same span (e.g. both "John Doe" and "Dr. Jane Smith").
                    span_rect = pymupdf.Rect(span["bbox"])
                    search_rects = page.search_for(target, clip=span_rect)
                    for sr in search_rects:
                        results.append((sr, target, replacement_map[target],
                                        style, False))

    return results


def deidentify_pdf(
    input_path: str,
    output_path: str,
    replacement_map: dict[str, str],
) -> bool:
    """Replace PII strings in a PDF with pseudonym hashes, preserving layout.

    Uses span-level text matching to avoid partial-word matches, and a
    two-phase redact-then-insert approach for reliable text replacement.

    Args:
        input_path: Path to the original PDF.
        output_path: Path to save the de-identified PDF.
        replacement_map: Mapping of real PII strings → pseudonym hashes.

    Returns:
        True if the PDF was successfully processed.
    """
    import pymupdf

    doc = pymupdf.open(input_path)

    for page in doc:
        # Phase 1: Find all matches at the span level with precise styles
        matches = _find_span_replacements(page, replacement_map)

        if not matches:
            continue

        # Phase 2: Redact all matched regions
        for rect, _, _, _, _ in matches:
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()

        # Phase 3: Insert replacement text at original positions
        for rect, _, replacement, style, _ in matches:
            base_font = _pymupdf_fontname_to_base(style["fontname"])
            fontsize = style["size"]
            color = style["color"]

            # insert_text uses the baseline point; offset from bottom of bbox
            insert_point = pymupdf.Point(rect.x0, rect.y1 - 1)
            page.insert_text(
                insert_point,
                replacement,
                fontsize=fontsize,
                fontname=base_font,
                color=color,
            )

    doc.save(output_path)
    doc.close()
    return True


# ---------------------------------------------------------------------------
# DOCX Editing (python-docx)
# ---------------------------------------------------------------------------

def _replace_in_runs(paragraph, replacement_map: dict[str, str]):
    """Replace PII text in a paragraph's runs, preserving per-run formatting.

    Handles the common case where a PII string is contained within a single
    run.  For cross-run splits, falls back to a joined-run replacement
    strategy that preserves the formatting of the first matching run.
    """
    sorted_keys = sorted(replacement_map.keys(), key=len, reverse=True)

    # First pass: simple per-run replacement
    for run in paragraph.runs:
        for real_text in sorted_keys:
            if real_text in run.text:
                run.text = run.text.replace(real_text, replacement_map[real_text])

    # Second pass: handle cross-run splits
    full_text = paragraph.text
    for real_text in sorted_keys:
        if real_text not in full_text:
            continue

        # Check if it was already handled in per-run pass
        remaining = "".join(r.text for r in paragraph.runs)
        if real_text not in remaining:
            continue

        # Cross-run replacement: find the runs that span this text
        _replace_across_runs(paragraph, real_text, replacement_map[real_text])


def _replace_across_runs(paragraph, target: str, replacement: str):
    """Replace text that spans multiple runs in a paragraph.

    Keeps the formatting of the first run that contains part of the target.
    """
    runs = paragraph.runs
    if not runs:
        return

    # Build a character-to-run mapping
    char_positions = []  # list of (run_index, char_index_in_run)
    for ri, run in enumerate(runs):
        for ci in range(len(run.text)):
            char_positions.append((ri, ci))

    full_text = "".join(r.text for r in runs)
    start_idx = full_text.find(target)
    if start_idx == -1:
        return

    end_idx = start_idx + len(target)

    # Determine which runs are affected
    start_run, start_char = char_positions[start_idx]
    end_run, end_char = char_positions[end_idx - 1]

    # Put the replacement text into the first affected run
    runs[start_run].text = (
        runs[start_run].text[:start_char]
        + replacement
        + runs[end_run].text[end_char + 1:]
    )

    # Clear intermediate and end runs (if different from start)
    for ri in range(start_run + 1, end_run + 1):
        runs[ri].text = ""


def _process_paragraphs(paragraphs, replacement_map: dict[str, str]):
    """Apply replacements to a list of paragraphs."""
    for paragraph in paragraphs:
        _replace_in_runs(paragraph, replacement_map)


def deidentify_docx(
    input_path: str,
    output_path: str,
    replacement_map: dict[str, str],
) -> bool:
    """Replace PII strings in a DOCX with pseudonym hashes, preserving formatting.

    Processes body paragraphs, tables, headers, and footers.

    Args:
        input_path: Path to the original DOCX.
        output_path: Path to save the de-identified DOCX.
        replacement_map: Mapping of real PII strings → pseudonym hashes.

    Returns:
        True if the DOCX was successfully processed.
    """
    from docx import Document

    doc = Document(input_path)

    # Body paragraphs
    _process_paragraphs(doc.paragraphs, replacement_map)

    # Tables (including nested tables)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                _process_paragraphs(cell.paragraphs, replacement_map)

    # Headers and footers
    for section in doc.sections:
        for header in [section.header, section.first_page_header, section.even_page_header]:
            if header and header.is_linked_to_previous is False:
                _process_paragraphs(header.paragraphs, replacement_map)
                for table in header.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            _process_paragraphs(cell.paragraphs, replacement_map)

        for footer in [section.footer, section.first_page_footer, section.even_page_footer]:
            if footer and footer.is_linked_to_previous is False:
                _process_paragraphs(footer.paragraphs, replacement_map)
                for table in footer.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            _process_paragraphs(cell.paragraphs, replacement_map)

    doc.save(output_path)
    return True


# ---------------------------------------------------------------------------
# Synthesis Summary Writer
# ---------------------------------------------------------------------------

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


def write_synthesis_summary(
    output_path: str,
    deidentified_chrono: dict,
    identity_catalogue: Optional[dict] = None,
) -> str:
    """Write a standalone synthesis summary file with recipient instructions.

    This companion file contains:
    - Recipient instructions banner
    - Patient synthesis summary
    - Identified categories
    - Pseudonym hash legend (entity types, no real names)

    Args:
        output_path: Path to save the synthesis summary.
        deidentified_chrono: The deidentified chronology data dict.
        identity_catalogue: Optional catalogue (only pseudonym types are included,
                           not real names — this file is shareable).

    Returns:
        The full text content of the synthesis summary.
    """
    summary = RECIPIENT_INSTRUCTIONS

    summary += f"PATIENT SYNTHESIS SUMMARY:\n{deidentified_chrono.get('patient_summary', '')}\n\n"

    categories = deidentified_chrono.get("categories_found", [])
    if categories:
        summary += "IDENTIFIED CATEGORIES:\n"
        for cat in categories:
            summary += f"- {cat}\n"
        summary += "\n"

    # Pseudonym legend (safe to share — no real names)
    if identity_catalogue:
        summary += "================================================================================\n"
        summary += "PSEUDONYM HASH LEGEND\n"
        summary += "================================================================================\n\n"
        for pseudonym_hash, details in identity_catalogue.items():
            entity_type = details.get("entity_type", "UNKNOWN")
            relationship = details.get("relationship_context", "")
            summary += f"  {pseudonym_hash}  [{entity_type}]  {relationship}\n"
        summary += "\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(summary)

    return summary


# ---------------------------------------------------------------------------
# Dispatcher — choose editor by file extension
# ---------------------------------------------------------------------------

def deidentify_document(
    input_path: str,
    output_path: str,
    replacement_map: dict[str, str],
) -> bool:
    """Dispatch to the correct format-specific editor based on file extension.

    Args:
        input_path: Path to the original document.
        output_path: Path to save the de-identified document.
        replacement_map: Mapping of real PII strings → pseudonym hashes.

    Returns:
        True if the document was processed, False if format is unsupported.
    """
    ext = os.path.splitext(input_path)[1].lower()

    if ext == ".pdf":
        return deidentify_pdf(input_path, output_path, replacement_map)
    elif ext == ".docx":
        return deidentify_docx(input_path, output_path, replacement_map)
    else:
        return False


def reidentify_document(
    input_path: str,
    output_path: str,
    identity_catalogue: dict,
) -> bool:
    """Reverse de-identification on a PDF or DOCX document.

    Builds a reverse replacement map (pseudonym → canonical name) from the
    identity catalogue and applies it to the document.

    Args:
        input_path: Path to the pseudonymised document.
        output_path: Path to save the re-identified document.
        identity_catalogue: The secure identity catalogue mapping.

    Returns:
        True if the document was processed, False if format is unsupported.
    """
    ext = os.path.splitext(input_path)[1].lower()
    if ext not in (".pdf", ".docx"):
        return False

    # Build reverse map: pseudonym_hash → canonical_name
    reverse_map = {}
    for pseudonym_hash, details in identity_catalogue.items():
        reverse_map[pseudonym_hash] = details["canonical_name"]

    return deidentify_document(input_path, output_path, reverse_map)
