"""Apply a certified, balanced 200-page Moli timing cohort to formal results."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.record_io import parse_jsonl
from harness.web_fetch.webmainbench_latency_correction import validate_latency_correction
from harness.web_fetch.webmainbench_latency_diagnostic import _repeat_calls
from scripts.run_certified_webmainbench_latency import snapshot


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base', 'cohort', 'dataset', 'sample', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or (args.cohort / 'environment-interference.json').exists():
        raise ValueError('Output already exists or cohort is known to have host interference')
    cert_path = args.cohort / 'certification.json'
    cert = json.loads(cert_path.read_text(encoding='utf-8'))
    selection_bytes = (args.cohort / 'selection.json').read_bytes()
    exclusion = cert.get('environment_exclusion', {})
    resources = cert.get('host_resources', {})
    observed_resources = [resources.get(point, {}).get(metric)
                          for point in ('before', 'after')
                          for metric in ('cpu_idle_percent', 'memory_free_percent')]
    frozen = snapshot(args.dataset, args.sample, args.base)
    if (cert.get('kind') != 'certified_webmainbench_moli_latency_crossover_v1'
            or cert.get('precheck') != frozen or cert.get('postcheck') != frozen
            or cert.get('physical_calls') != 800 or cert.get('failed_calls') != 4
            or cert.get('selected_round') != 2
            or exclusion.get('rule') != 'no other browser benchmark process before or during Moli timing'
            or exclusion.get('clean_process_checks', 0) < 2
            or any(not isinstance(value, (int, float)) or not 0 <= value <= 100
                   for value in observed_resources)
            or exclusion.get('certifying_runner_sha256') != digest(
                ROOT / 'scripts/run_certified_webmainbench_latency.py')
            or cert.get('selection_sha256') != hashlib.sha256(selection_bytes).hexdigest()
            or cert.get('calls_sha256') != digest(args.cohort / 'calls.jsonl')):
        raise ValueError('Moli latency cohort is not certified against the formal references')
    selection = json.loads(selection_bytes)
    if selection.get('authority') != {
        'selected_round': 2,
        'rule': 'final_round_for_every_version_and_page; never select a fastest call',
        'scope': 'latency only; preserve original output, scoring and verdict',
    }:
        raise ValueError('Final-round timing authority was not declared before calls')
    dataset = json.loads((args.base / 'dataset.json').read_text())
    cases = {case['id']: case for case in dataset['cases']}
    _, calls = _repeat_calls(args.cohort.parent, {
        'directory': args.cohort.name,
        'selection_json_sha256': cert['selection_sha256'],
        'calls_jsonl_sha256': cert['calls_sha256'],
    }, cases, frozen['source_dataset_sha256'], frozen['sample_sha256'],
        cert['prior_results_sha256'], cert['versions'], 200, 2)
    if set(selection['case_ids']) != set(cases):
        raise ValueError('Latency correction omitted a frozen page')
    order = {}
    for first, second in zip(calls[::2], calls[1::2]):
        if (first['case_id'] != second['case_id'] or first['round'] != second['round']
                or first['version'] == second['version']):
            raise ValueError('Moli versions were not called serially on each page')
        order.setdefault(first['case_id'], []).append(first['version'])
    if any(sorted(versions) != ['1.1.5', '1.1.7'] for versions in order.values()):
        raise ValueError('Moli first-call order was not reversed on every page')
    final = {(row['version'], row['case_id']): row for row in calls if row['round'] == 2}
    if len(final) != 400:
        raise ValueError('Moli final round is incomplete')
    prior_path = args.base / 'results.jsonl'
    old_bytes = prior_path.read_bytes()
    records = parse_jsonl(old_bytes, skip_blank=True)
    changes = []
    for record in records:
        version = {'moli': '1.1.5', 'moli-1.1.7': '1.1.7'}.get(record['provider'])
        if version is None:
            continue
        call = final[version, record['case_id']]
        if bool(record['ok']) != call['_body_present']:
            raise ValueError('Corrected call changed success population: ' + record['attempt_id'])
        original = record['elapsed_ms']
        metadata = record.setdefault('provider_metadata', {})
        if metadata.get('binary_sha256') != cert['versions'][version]:
            raise ValueError('Corrected call changed Moli binary: ' + record['attempt_id'])
        record['elapsed_ms'] = call['elapsed_ms']
        metadata['latency_correction'] = {
            'kind': 'certified_balanced_final_round', 'selected_round': 2,
            'original_elapsed_ms': original, 'corrected_elapsed_ms': call['elapsed_ms'],
            'cohort_call_output_sha256': call['output_sha256'],
            'content_byte_identical': call['output_sha256'] == record['output_sha256'],
        }
        changes.append({'attempt_id': record['attempt_id'],
                        'before_ms': original, 'after_ms': call['elapsed_ms']})
    if len(changes) != 400:
        raise ValueError('Latency correction did not cover both complete Moli methods')
    shutil.copytree(args.base, args.output)
    (args.output / 'prior-latency-results.jsonl').write_bytes(old_bytes)
    corrected = ''.join(json.dumps(row, ensure_ascii=True) + '\n' for row in records).encode()
    (args.output / 'results.jsonl').write_bytes(corrected)
    receipt = {
        'schema_version': 1, 'kind': 'webmainbench_moli_200_balanced_latency_only_v1',
        'selection': 'all 200 pages, both Moli versions, final round of the balanced crossover',
        'selected_round': 2, 'changed_result_count': 400,
        'prior_results_sha256': hashlib.sha256(old_bytes).hexdigest(),
        'output_results_sha256': hashlib.sha256(corrected).hexdigest(),
        'certification_sha256': digest(cert_path),
        'selection_sha256': cert['selection_sha256'],
        'calls_sha256': cert['calls_sha256'],
        'scored_outputs_changed': 0, 'verdicts_changed': 0,
        'changes': changes,
    }
    (args.output / 'latency-corrections.json').write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    validate_latency_correction(args.output)
    print(json.dumps({'output': str(args.output), 'changed': len(changes),
                      'byte_identical_rerun_outputs': sum(
                          row['provider_metadata']['latency_correction']['content_byte_identical']
                          for row in records if row['provider'] in ('moli', 'moli-1.1.7'))},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
