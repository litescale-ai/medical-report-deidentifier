"""Format-preserving replacement and restoration of discovered identifiers.

Provides format-preserving find-and-replace for:
  - PDF files (via PyMuPDF): redact + insert with font matching
  - DOCX files (via python-docx): run-level replacement preserving styles

Also provides a synthesis summary writer for the companion output file.
"""

import os
import json
from utils.document_formats import (DOCUMENT_EXTENSIONS, TEXT_EXTENSIONS, HtmlText,
    read_text, replace_strings, all_docx_paragraphs, presentation_paragraphs)
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


def _extract_span_style(blocks, search_rect) -> dict:
    """Find the matching style in text blocks extracted once for this page."""
    import pymupdf
    
    best_span = None
    max_area = 0
    
    for b in blocks:
        if b.get("type", 0) != 0:
            continue
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                span_rect = pymupdf.Rect(s["bbox"])
                intersect = span_rect.intersect(search_rect)
                area = intersect.get_area()
                if area > max_area:
                    max_area = area
                    best_span = s
                    
    if best_span:
        return {
            "fontname": best_span.get("font", "helv"),
            "size": best_span.get("size", 11.0),
            "color": _int_to_rgb(best_span.get("color", 0)),
            "flags": best_span.get("flags", 0),
        }
        
    # Fallback default
    return {
        "fontname": "helv",
        "size": 11.0,
        "color": (0, 0, 0),
        "flags": 0,
    }


def _ocr_pdf(input_path: str) -> str:
    """Run OCR on a PDF to produce a searchable copy.

    Uses ocrmypdf (Tesseract) with force_ocr to replace any broken
    text layer with a fresh OCR'd one.  Returns the path to the OCR'd
    temporary file.
    """
    import tempfile
    import ocrmypdf

    fd, ocr_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)

    try:
        ocrmypdf.ocr(
            input_path, ocr_path, force_ocr=True, optimize=1, progress_bar=False,
        )
    except Exception:
        os.unlink(ocr_path)
        raise
    return ocr_path


def _redact_pdf(input_path: str, output_path: str, replacement_map: dict[str, str]) -> int:
    """Core redaction logic: search → redact → insert replacement text.

    Returns the total number of replacements made.
    """
    import pymupdf

    doc = pymupdf.open(input_path)
    total_replacements = 0
    sorted_keys = sorted(replacement_map.keys(), key=len, reverse=True)

    for page in doc:
        replacements = []
        blocks = None
        for target in sorted_keys:
            if not target.strip():
                continue
            rects = page.search_for(target)
            if rects and blocks is None:
                blocks = page.get_text("dict")["blocks"]
            for rect in rects:
                if any(rect.intersects(existing) for existing, _, _ in replacements):
                    continue
                style = _extract_span_style(blocks, rect)
                replacements.append((rect, replacement_map[target], style))

        if not replacements:
            continue

        total_replacements += len(replacements)

        # Redact all matched regions
        for rect, _, _ in replacements:
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()

        # Insert replacement text at original positions
        for rect, replacement, style in replacements:
            base_font = _pymupdf_fontname_to_base(style["fontname"])
            fontsize = style["size"]
            color = style["color"]

            insert_point = pymupdf.Point(rect.x0, rect.y1 - 1)
            page.insert_text(
                insert_point,
                replacement,
                fontsize=fontsize,
                fontname=base_font,
                color=color,
            )

    # Discard unreferenced original streams; visible redaction alone can leave PII recoverable.
    doc.save(output_path, garbage=4, deflate=True)
    doc.close()
    return total_replacements


def deidentify_pdf(
    input_path: str,
    output_path: str,
    replacement_map: dict[str, str],
) -> bool:
    """Replace known identifiers, OCRing image-only pages before editing.

    Raises if OCR fails; never exports the unchanged source as a successful result.
    A searchable document without matching identifiers is copied without OCR.
    """
    import pymupdf

    # OCR only when a page has images without usable text. A document with no
    # discovered entities is still a valid output; no-match is not an OCR signal.
    with pymupdf.open(input_path) as doc:
        needs_ocr = any(not page.get_text().strip() and page.get_images() for page in doc)
    ocr_path = None
    try:
        if needs_ocr:
            ocr_path = _ocr_pdf(input_path)
        _redact_pdf(ocr_path or input_path, output_path, replacement_map)
        return True
    finally:
        if ocr_path and os.path.exists(ocr_path):
            os.unlink(ocr_path)


# ---------------------------------------------------------------------------
# DOCX Editing (python-docx)
# ---------------------------------------------------------------------------

def _replace_in_runs(paragraph, replacement_map: dict[str, str]) -> int:
    """Replace across formatting boundaries without changing unrelated run styles."""
    from docx.text.paragraph import Paragraph
    from docx.text.run import Run
    runs = [Run(element, paragraph) for element in paragraph._p.xpath(".//w:r")] if isinstance(paragraph, Paragraph) else paragraph.runs
    original = [run.text for run in runs]
    updated = replace_strings(original, replacement_map)
    for run, text in zip(runs, updated):
        if run.text != text:
            run.text = text
    return int(original != updated)


def _process_paragraphs(paragraphs, replacement_map: dict[str, str]) -> int:
    """Apply replacements to a list of paragraphs. Returns number of replacements made."""
    count = 0
    for paragraph in paragraphs:
        count += _replace_in_runs(paragraph, replacement_map)
    return count


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

    _process_paragraphs(all_docx_paragraphs(doc), replacement_map)

    doc.save(output_path)
    return True


def _replace_formula(formula, replacement_map, sheet_titles):
    """Replace formula string literals and renamed sheet references, never operators/cell names."""
    from openpyxl.formula import Tokenizer
    tokenizer = Tokenizer(formula)
    for token in tokenizer.items:
        if token.type == "OPERAND" and token.subtype == "TEXT":
            literal = token.value[1:-1].replace('""', '"')
            token.value = '"' + replace_strings([literal], replacement_map)[0].replace('"', '""') + '"'
        elif token.type == "OPERAND" and token.subtype == "RANGE" and "!" in token.value:
            sheet, reference = token.value.rsplit("!", 1)
            name = sheet[1:-1].replace("''", "'") if sheet.startswith("'") else sheet
            if name in sheet_titles and sheet_titles[name] != name:
                token.value = "'" + sheet_titles[name].replace("'", "''") + "'!" + reference
    return tokenizer.render()


def deidentify_xlsx(input_path, output_path, replacement_map):
    """Replace spreadsheet strings, comments and links; retain numbers and cell styles."""
    from openpyxl import load_workbook
    workbook = load_workbook(input_path)
    try:
        sheet_titles = {sheet.title: replace_strings([sheet.title], replacement_map)[0] for sheet in workbook}
        titles = list(sheet_titles.values())
        if len({title.lower() for title in titles}) != len(titles) or any(len(title) > 31 for title in titles):
            raise ValueError("Pseudonymised sheet titles would be duplicate or too long; rename the source sheets first.")
        for sheet in workbook:
            sheet.title = sheet_titles[sheet.title]
            for row in sheet:
                for cell in row:
                    if cell.data_type == "f":
                        cell.value = _replace_formula(cell.value, replacement_map, sheet_titles)
                    elif isinstance(cell.value, str):
                        cell.value = replace_strings([cell.value], replacement_map)[0]
                    if cell.comment:
                        cell.comment.text = replace_strings([cell.comment.text], replacement_map)[0]
                        cell.comment.author = replace_strings([cell.comment.author], replacement_map)[0]
                    if cell.hyperlink and cell.hyperlink.target:
                        cell.hyperlink.target = replace_strings([cell.hyperlink.target], replacement_map)[0]
        workbook.save(output_path)
    finally:
        workbook.close()
    return True


def deidentify_pptx(input_path, output_path, replacement_map):
    """Replace slide text, tables, grouped shapes and speaker notes in the original deck."""
    from pptx import Presentation
    presentation = Presentation(input_path)
    for _, paragraphs in presentation_paragraphs(presentation):
        _process_paragraphs(paragraphs, replacement_map)
    presentation.save(output_path)
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
    elif ext == ".xlsx":
        return deidentify_xlsx(input_path, output_path, replacement_map)
    elif ext == ".pptx":
        return deidentify_pptx(input_path, output_path, replacement_map)
    elif ext in TEXT_EXTENSIONS:
        text = read_text(input_path)
        if ext in {".html", ".htm"}:
            text = HtmlText(text).replace(replacement_map)
        else:
            text = replace_strings([text], replacement_map)[0]
        with open(output_path, "w", encoding="utf-8") as output:
            output.write(text)
        return True
    else:
        return False


def reidentify_document(
    input_path: str,
    output_path: str,
    identity_catalogue: dict,
) -> bool:
    """Reverse de-identification in any supported document format.

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
    if ext not in DOCUMENT_EXTENSIONS:
        return False

    # Build reverse map: pseudonym_hash → canonical_name
    reverse_map = {}
    for pseudonym_hash, details in identity_catalogue.items():
        reverse_map[pseudonym_hash] = details["canonical_name"]

    return deidentify_document(input_path, output_path, reverse_map)
