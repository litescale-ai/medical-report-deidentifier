"""Private batch manifests and measured Ollama throughput, separate from document text."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from time import perf_counter


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class BatchStats:
    """Accumulate this run's counters; cached calls never masquerade as new tokens."""
    def __init__(self, files, *, model, revision, output, manifest, save, previous, emit):
        self.started = perf_counter()
        self.save, self.emit, self.path = save, emit, manifest
        self.identifiers = {}
        history = previous.get('previous_runs', [])
        if previous:
            history = history + [{key: previous.get(key) for key in
                                  ('started_at', 'updated_at', 'status', 'elapsed_seconds', 'tokens', 'totals')}]
        self.data = {
            'version': 1, 'model': model, 'model_revision': revision, 'output': str(output),
            'started_at': utc_now(), 'updated_at': utc_now(), 'status': 'running',
            'stage': 'Preparing', 'current_file': '', 'elapsed_seconds': 0,
            'files': {name: {'status': 'pending', 'pages': None, 'words': 0,
                             'extracted': False, 'identified': False, 'completed': False,
                             'chunks_total': 0, 'chunks_completed': 0, 'cached_chunks': 0,
                             'identities': 0, 'identity_types': {}} for name in files},
            'tokens': {'requests': 0, 'input': 0, 'output': 0, 'generation_seconds': 0.0,
                       'prompt_seconds': 0.0, 'load_seconds': 0.0,
                       'tokens_per_second': None, 'last_tokens_per_second': None},
            'previous_runs': history,
        }
        self.publish()

    def update(self, name, *, stage=None, **fields):
        self.data['current_file'] = name
        if stage:
            self.data['stage'] = stage
        self.data['files'][name].update(fields)
        self.publish()

    def record_identities(self, name, entities, removals):
        identifiers = {'entity:' + item['canonical_name'].casefold(): item['entity_type'] for item in entities}
        identifiers.update({'rule:' + value: marker.removeprefix('[').removesuffix(' REMOVED]')
                            for value, marker in removals.items()})
        self.identifiers[name] = identifiers
        self.data['files'][name].update(identities=len(identifiers), identity_types=dict(Counter(identifiers.values())))
        self.publish()

    def record_tokens(self, name, measured):
        totals = self.data['tokens']
        totals['requests'] += 1
        totals['input'] += measured.get('input_tokens') or 0
        totals['output'] += measured.get('output_tokens') or 0
        totals['generation_seconds'] += measured.get('generation_seconds') or 0
        totals['prompt_seconds'] += measured.get('prompt_seconds') or 0
        totals['load_seconds'] += measured.get('load_seconds') or 0
        totals['last_tokens_per_second'] = measured.get('tokens_per_second')
        totals['tokens_per_second'] = (totals['output'] / totals['generation_seconds']
                                      if totals['generation_seconds'] else None)
        self.data['files'][name].setdefault('requests', []).append(measured)
        self.publish()

    def publish(self):
        values = list(self.data['files'].values())
        unique = {}
        for identifiers in self.identifiers.values():
            unique.update(identifiers)
        self.data['totals'] = {
            'documents': len(values), 'extracted': sum(item['extracted'] for item in values),
            'identified': sum(item['identified'] for item in values),
            'completed': sum(item['completed'] for item in values),
            'failed': sum(item['status'] == 'failed' for item in values),
            'reused': sum(item['status'] == 'reused' for item in values),
            'pages': sum(item['pages'] or 0 for item in values),
            'words': sum(item['words'] for item in values),
            'chunks': sum(item['chunks_total'] for item in values),
            'chunks_completed': sum(item['chunks_completed'] for item in values),
            'cached_chunks': sum(item['cached_chunks'] for item in values),
            'identities': len(unique), 'identity_types': dict(Counter(unique.values())),
        }
        self.data['elapsed_seconds'] = round(perf_counter() - self.started, 2)
        self.data['updated_at'] = utc_now()
        self.save(self.path, self.data)
        if self.emit:
            self.emit(deepcopy(self.data))

    def finish(self, status, error=None):
        self.data['status'] = status
        self.data['stage'] = 'Finished' if status.startswith('complete') else 'Stopped'
        if error:
            self.data['error'] = error
        self.publish()
        return deepcopy(self.data)
