"""Reproducible synthetic PDF benchmark. Never reads real patient documents.

Run: .venv/bin/python scripts/benchmark_local.py --output /tmp/guardian-benchmark
"""
import argparse
import asyncio
import json
from pathlib import Path
import platform
import subprocess
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.batch import CACHE_VERSION, discover_names, process_batch, scan_folder
from utils.document_formats import extract_document

NAMES = ['Alex Example', 'Jamie Sample', 'Taylor Fiction', 'Morgan Demo', 'Casey Placeholder',
         'Jordan Mock', 'Avery Testcase', 'Riley Synthetic', 'Quinn Specimen', 'Parker Nominal',
         'Drew Imaginary', 'Reese Pretend', 'Cameron Trial', 'Skyler Fable', 'Rowan Sketch',
         'Finley Illustration', 'Emerson Draft', 'Dakota Simulated', 'Sage Invention', 'Harper Prototype']
CLINICAL = ('Assessment: improving mobility. Blood pressure 120/80. Dose 5 mg. '
            'The patient reports improved sleep and reduced pain after exercise. '
            'Examination shows normal gait with mild discomfort on flexion. '
            'Continue the current treatment and graded activity. No new adverse effects were reported. '
            'Review strength and range of motion at the next visit. '
            'The patient understands the exercise programme and will keep a daily symptom diary. '
            'Advice included regular rest breaks, hydration and avoiding strenuous lifting. '
            'Follow-up is planned in four weeks.')


def fixtures(root, documents=20, pages=3):
    import pymupdf
    expected = {}
    for index, name in enumerate(NAMES[:documents]):
        folder = root / f'case-{index + 1:02d}'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'report.pdf'
        doctor = 'Robin Tester' if index % 2 == 0 else 'Mira Sampleton'
        relative = 'Lee Exampleton'
        email = f'person{index + 1}@example.test'
        expected[str(path.relative_to(root))] = [name, doctor, relative, email, 'Willow Test Clinic', '12 Fiction Road', '45 Imaginary Avenue', 'Testville', '8001']
        with pymupdf.open() as pdf:
            for page_number in range(pages):
                page = pdf.new_page()
                text = (f'Patient: {name}\nTreating doctor: Dr {doctor}\nEmergency contact: {relative}\n'
                        f'Email: {email}\nFacility: Willow Test Clinic\nAddress: 12 Fiction Road\n'
                        'HPCSA MP 0723444\nPractice No. 1270753\nTelephone: +27 21 555 0123\n'
                        f'Visit {page_number + 1}; document {index + 1}.\n'
                        'Correspondence was sent to 45 Imaginary Avenue, Testville, 8001.\n\n' + CLINICAL)
                page.insert_textbox(pymupdf.Rect(40, 40, 555, 800), text, fontsize=11)
            pdf.save(path)
    return expected


async def main(args):
    args.output = args.output.expanduser().resolve()
    root = args.output / 'input'
    root.mkdir(parents=True, exist_ok=True)
    expected = fixtures(root, args.documents, args.pages)
    report = {'documents': args.documents, 'pages_per_document': args.pages,
              'fixture': 'Synthetic short clinical pages; not a clinical accuracy validation',
              'platform': platform.platform(), 'machine': platform.machine(), 'models': []}
    extracted = '\n'.join(text for path in root.rglob('*.pdf') for _, text in extract_document(path))
    report.update(cache_version=CACHE_VERSION, extracted_characters=len(extracted), words=len(extracted.split()))
    if sys.platform == 'darwin':
        report['hardware'] = subprocess.check_output(['sysctl', '-n', 'machdep.cpu.brand_string'], text=True).strip()
        report['memory_bytes'] = int(subprocess.check_output(['sysctl', '-n', 'hw.memsize'], text=True))
    for model in args.models:
        model_dir = args.output / model.replace(':', '-')
        model_dir.mkdir(parents=True, exist_ok=True)
        started = perf_counter()
        await discover_names('Patient: Alex Example. Doctor: Robin Tester.', model=model, base_url=args.base_url)
        warmup = perf_counter() - started
        output = model_dir / 'output'
        files, _ = scan_folder(root, output)
        started = perf_counter()
        result = await process_batch(files, root=root, output=output, secure=model_dir / 'secure',
                                     model=model, base_url=args.base_url, progress=lambda value: print(model, value, flush=True))
        elapsed = perf_counter() - started
        misses, damaged_clinical, checked_identifiers = {}, [], 0
        for path in result['completed']:
            relative = str(Path(path).relative_to(output))
            text = '\n'.join(text for _, text in extract_document(path))
            identifiers = expected[relative] + ['0723444', '1270753', '+27 21 555 0123']
            checked_identifiers += len(identifiers)
            remaining = [identifier for identifier in identifiers if identifier.casefold() in text.casefold()]
            if remaining:
                misses[relative] = remaining
            if '120/80' not in text or '5 mg' not in text or 'improving mobility' not in text:
                damaged_clinical.append(relative)
        row = {'model': model, 'revision': result['model_revision'], 'warmup_seconds': round(warmup, 2),
               'seconds': round(elapsed, 2), 'completed': len(result['completed']),
               'failed': result['failed'], 'reused': result['reused'], 'misses': misses,
               'checked_identifiers': checked_identifiers, 'damaged_clinical': damaged_clinical}
        report['models'].append(row)
        (args.output / 'results.json').write_text(json.dumps(report, indent=2))
        print('RESULT', json.dumps(row), flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--models', nargs='+', default=['gemma4:e4b', 'gemma4:e2b', 'qwen3.5:2b'])
    parser.add_argument('--documents', type=int, choices=range(1, 21), default=20)
    parser.add_argument('--pages', type=int, default=3)
    parser.add_argument('--base-url', default='http://localhost:11434')
    args = parser.parse_args()
    if (args.output / 'results.json').exists():
        parser.error('Use a fresh output directory so cached results cannot distort timings.')
    asyncio.run(main(args))
