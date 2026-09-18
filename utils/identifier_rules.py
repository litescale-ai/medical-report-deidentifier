"""Local removal rules for labelled registrations and telephone numbers.

Scan original extracted text as well as model summaries: a summary can omit a
header. Removal markers deliberately have no reverse identity mapping.
"""
import re

# Separators inside numbers stay on one line, so clinical values on the next
# line cannot accidentally become part of a phone or registration number.
_NUMBER = r'[+\d(][\d \t().-]*\d'
_LABELLED = re.compile(
    r'\b(?P<label>HPCSA(?:\s+[A-Z]{2,4})?|MP|Practice(?:\s+(?:No\.?|Number))?|'
    r'Pr\.?\s*No\.?|PCNS|Tel(?:ephone)?|Phone|Mobile|Cell(?:phone)?|Fax)'
    r'[ \t]*(?:No\.?|Number)?[ \t]*[:#.-]?[ \t]*\n?[ \t]*'
    r'(?P<number>' + _NUMBER + r')', re.IGNORECASE)
_PHONE = re.compile(
    r'(?<![\w\d])(?:\+\d(?:[ \t().-]*\d){7,14}|'
    r'\(?0[1-8]\d\)?(?:[ \t-]*\d){7})(?!\d)')


def text_values(data):
    """Walk extracted transcripts or report JSON without joining unrelated fields."""
    if isinstance(data, str):
        yield data
    elif isinstance(data, dict):
        for key, value in data.items():
            yield from text_values(key)
            yield from text_values(value)
    elif isinstance(data, (list, tuple)):
        for value in data:
            yield from text_values(value)


def identifier_replacements(data):
    replacements = {}
    for text in text_values(data):
        for match in _LABELLED.finditer(text):
            number = match['number']
            count = sum(char.isdigit() for char in number)
            label = match['label'].lower()
            phone = label.startswith(('tel', 'phone', 'mobile', 'cell', 'fax'))
            valid_length = 7 <= count <= 15 if phone else 5 <= count <= 13
            if valid_length:
                replacements[number] = '[PHONE REMOVED]' if phone else '[REGISTRATION REMOVED]'
        for match in _PHONE.finditer(text):
            replacements.setdefault(match.group(), '[PHONE REMOVED]')
    return replacements


def replace_data(data, replacements):
    """Replace original matches once, preserving JSON structure and escaped text."""
    keys = sorted((key for key in replacements if key), key=len, reverse=True)
    pattern = re.compile('|'.join(map(re.escape, keys))) if keys else None

    def visit(value):
        if isinstance(value, str):
            return pattern.sub(lambda match: replacements[match.group()], value) if pattern else value
        if isinstance(value, dict):
            return {visit(key): visit(item) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, tuple):
            return tuple(visit(item) for item in value)
        return value

    return visit(data)
