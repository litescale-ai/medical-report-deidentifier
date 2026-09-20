"""Local removal rules for addresses, email, registrations and telephone numbers.

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

# Consume a labelled address line, stopping before another field on that line.
# Unlabelled addresses and continuation lines are also requested from the model.
_ADDRESS = re.compile(
    r'(?im)\b(?:(?:postal|physical|residential|home|work|business|practice|street|patient|email|e-mail)\s+)?'
    r'address[ \t]*:[ \t]*(?:\n[ \t]*)?'
    r'(?P<address>[^\r\n]+?)(?=[ \t]+(?:Tel(?:ephone)?|Phone|Mobile|Fax|Email|Patient|Doctor|'
    r'Assessment|Dose|BP|Date|HPCSA|Practice)[ \t]*:|$)')

_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}\b")


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
        for match in _ADDRESS.finditer(text):
            address = match['address'].strip()
            # PDF extraction can leave a removed email under an address label,
            # with spacing and separators. Only ignore fully removed values.
            removed = re.fullmatch(r'(?:\[(?:ADDRESS|EMAIL) REMOVED\][\s;,.]*)+', address)
            if address and not removed and not re.match(r'^[A-Za-z ]+:', address):
                replacements[address] = '[ADDRESS REMOVED]'
        for match in _LABELLED.finditer(text):
            number = match['number']
            count = sum(char.isdigit() for char in number)
            label = match['label'].lower()
            phone = label.startswith(('tel', 'phone', 'mobile', 'cell', 'fax'))
            valid_length = 7 <= count <= 15 if phone else 5 <= count <= 13
            if valid_length:
                replacements[number] = '[PHONE REMOVED]' if phone else '[REGISTRATION REMOVED]'
        for match in _EMAIL.finditer(text):
            replacements[match.group()] = '[EMAIL REMOVED]'
        for match in _PHONE.finditer(text):
            replacements.setdefault(match.group(), '[PHONE REMOVED]')
    return replacements


def replacement_pattern(keys):
    """Match whole identifiers so short names cannot alter words such as sleep."""
    return '|'.join((r'(?<!\d)' if key[0].isdigit() else r'(?<!\w)' if key[0].isalnum() or key[0] == '_' else '') + re.escape(key) +
                    (r'(?!\d)' if key[-1].isdigit() else r'(?!\w)' if key[-1].isalnum() or key[-1] == '_' else '') for key in keys if key)


def replace_data(data, replacements):
    """Replace original matches once, preserving JSON structure and escaped text."""
    keys = sorted((key for key in replacements if key), key=len, reverse=True)
    pattern = re.compile(replacement_pattern(keys)) if keys else None

    def visit(value):
        if isinstance(value, str):
            return pattern.sub(lambda match: replacements[match.group()], value) if pattern else value
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                replaced_key = visit(key)
                if replaced_key in result:
                    raise ValueError('Replacing identifiers would create duplicate JSON keys. Output withheld.')
                result[replaced_key] = visit(item)
            return result
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, tuple):
            return tuple(visit(item) for item in value)
        return value

    return visit(data)
