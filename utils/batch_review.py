"""Apply explicit review decisions using cached sections, never a cloud model."""
from pathlib import Path
import re
from uuid import uuid4

from utils.batch import chunk_ranges, file_digest, process_batch, read_json, save_private, source_variants
from utils.batch_stats import utc_now
from utils.document_formats import extract_document
from utils.identifier_rules import identifier_replacements, replace_data, replacement_pattern


def readable_text(text):
    """Remove PDF layout padding for display only, retaining lines and paragraphs."""
    lines = [re.sub(r'[^\S\n]+', ' ', line).strip() for line in text.splitlines()]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def review_source_sections(sections, section):
    """Locate the full original pages touched by a cached, pre-scrubbed model chunk."""
    text = '\n\n'.join(value for _, value in sections)
    removals = identifier_replacements(text)
    scrubbed = replace_data(text, removals)
    ranges = list(chunk_ranges(scrubbed))
    index = section['section'] - 1
    if index < 0 or index >= len(ranges):
        raise ValueError('The review section no longer matches the source. Process the document again.')
    start, end = ranges[index]
    if scrubbed[start:end] != section['text']:
        raise ValueError('The review section no longer matches the source. Process the document again.')

    # Undo length changes from automatic removals when mapping chunk edges to pages.
    matches = list(re.finditer(replacement_pattern(sorted(removals, key=len, reverse=True)), text)) if removals else []
    def original_offset(offset, right=False):
        delta = 0
        for match in matches:
            left = match.start() + delta
            length = len(removals[match.group()])
            if offset <= left:
                break
            if offset < left + length:
                return match.end() if right else match.start()
            delta += length - len(match.group())
        return offset - delta

    start, end = original_offset(start), original_offset(end, right=True)
    selected, position = [], 0
    for location, value in sections:
        if position < end and position + len(value) > start:
            selected.append((location, value))
        position += len(value) + 2
    return selected


def review_preview(review, relative, section):
    """Read actual draft text and cached original pages, rejecting changed files."""
    context = review['context']
    root = Path(context['root']).resolve()
    source = (root / relative).resolve()
    if not source.is_relative_to(root) or file_digest(source) != section['source']:
        raise ValueError('The source changed. Process the document again before reviewing it.')
    state = read_json(Path(context['manifest']).with_name('progress.json'), {})
    record = state.get('files', {}).get(relative, {})
    if file_digest(review['draft']) != record.get('draft_digest'):
        raise ValueError('The review draft changed. Process the document into a new output folder.')
    original = review_source_sections(record['sections'], section)
    draft = dict(extract_document(review['draft']))
    if any(location not in draft for location, _ in original):
        raise ValueError('The draft locations do not match the source. Download the draft to inspect it.')
    return [{'location': location, 'original': readable_text(value), 'draft': readable_text(draft[location])}
            for location, value in original]


async def resolve_review(previous, *, relative, section_id, action, secure,
                         values=(), note='', model=None, progress=lambda message: None, on_update=None):
    """Resolve one current section and export only after all sections are resolved."""
    if action not in {'dismiss', 'redact', 'retry'}:
        raise ValueError('Choose dismiss, redact or retry.')
    review = previous['needs_review'][relative]
    context = review['context']
    section = next((item for item in review['sections']
                    if item['id'] == section_id), None)
    if section is None:
        raise ValueError('This section is no longer awaiting review. Refresh the results.')
    root = Path(context['root']).resolve()
    source = (root / relative).resolve()
    if not source.is_relative_to(root) or file_digest(source) != section['source']:
        raise ValueError('The source changed. Process the document again before reviewing it.')
    checkpoint = Path(context['manifest']).with_name('progress.json')
    state = read_json(checkpoint, {})
    current = state.get('files', {}).get(relative, {}).get('review_sections', [])
    if not any(item['id'] == section_id for item in current):
        raise ValueError('This review is stale. Process the selection again to load current results.')
    if file_digest(review['draft']) != state['files'][relative].get('draft_digest'):
        raise ValueError('The review draft was edited outside Guardian. Use a new output folder to create a fresh draft.')
    values = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    source_text = '\n\n'.join(value for _, value in review_source_sections(state['files'][relative]['sections'], section))
    if action == 'redact' and (not values or any(not source_variants(source_text, value) for value in values)):
        raise ValueError('Enter identifying text from the displayed source pages, one value per line.')
    if action == 'retry' and not model:
        raise ValueError('Select a local model for the retry.')
    decision = {'file': relative, 'section_id': section_id, 'section': section['section'], 'source': section['source'],
                'action': action, 'values': values if action == 'redact' else [], 'note': note.strip(),
                'model': model if action == 'retry' else section['model'], 'at': utc_now(),
                'issues': section['issues']}
    # Keep an immutable audit of the previous attempt, including private suggestions.
    parent = Path(context['manifest']).with_name(f'attempt-{uuid4().hex}.json')
    save_private(parent, previous['stats'])
    state.setdefault('decision_history', []).append(decision)
    state.setdefault('decisions', {})[section_id] = decision
    save_private(checkpoint, state)
    result = await process_batch(
        [source], root=root, output=context['output'], secure=secure,
        model=context['model'], model_revision=context['model_revision'],
        base_url=context['base_url'], progress=progress, on_update=on_update,
        retry_of=parent, review_retry={section_id: model} if action == 'retry' else None)
    result['completed'] = list(dict.fromkeys(previous['completed'] + result['completed']))
    result['failed'] = {**{name: error for name, error in previous['failed'].items() if name != relative},
                        **result['failed']}
    result['needs_review'] = {**{name: review for name, review in previous['needs_review'].items() if name != relative},
                              **result['needs_review']}
    if action == 'retry' and relative in result['failed']:
        result['needs_review'][relative] = review
    result['verifications'] = {**previous.get('verifications', {}), **result['verifications']}
    result['stats']['output_verification'] = result['verifications']
    result['stats']['retained_completed'] = previous['completed']
    result['stats']['batch_completed'] = len(result['completed'])
    result['stats']['batch_failed'] = len(result['failed'])
    result['stats']['batch_needs_review'] = len(result['needs_review'])
    result['stats']['decision_history'] = state['decision_history']
    save_private(Path(result['manifest']), result['stats'])
    if on_update:
        on_update(result['stats'])
    return result
