"""Verify the complete scope and unchanged evidence of a TEDS-only correction."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.record_io import load_jsonl
from harness.web_fetch.safety import contained_path
from harness.web_fetch.scoring_regressions import valid_correction_provenance
from harness.web_fetch.webmainbench_diagnostics import require_valid_teds_for_release


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def review(source, corrected):
    receipt = json.loads((corrected / 'teds-correction.json').read_text())
    if not valid_correction_provenance(receipt) or receipt.get('provider_calls') != 0:
        raise ValueError('Correction lacks executable regression provenance')
    for field, path in [('input_results_sha256', source / 'results.jsonl'),
                        ('output_results_sha256', corrected / 'results.jsonl'),
                        ('dataset_sha256', corrected / 'dataset.json')]:
        if digest(path) != receipt[field]:
            raise ValueError('Correction hash mismatch: ' + field)
    for relative, expected in receipt['source_code_sha256'].items():
        if digest(contained_path(corrected / 'correction-source', relative)) != expected:
            raise ValueError('Correction implementation snapshot changed: ' + relative)
    if digest(corrected / 'original-results.jsonl') != receipt['input_results_sha256']:
        raise ValueError('Original official records were not preserved byte-for-byte')
    before = load_jsonl(source / 'results.jsonl')
    after = load_jsonl(corrected / 'results.jsonl')
    expected_count = len(json.loads((source / 'dataset.json').read_text())['cases']) * len(receipt['scope']['providers'])
    if len(before) != expected_count or len(after) != len(before):
        raise ValueError('Correction must cover the complete declared matrix')
    if len({row['attempt_id'] for row in after}) != len(after):
        raise ValueError('Duplicate corrected attempt')
    for name in ('dataset.json', 'sample-manifest.json', 'physical-attempts.jsonl', 'execution.json', 'result-review.json'):
        source_exists = (source / name).exists()
        corrected_exists = (corrected / name).exists()
        if not source_exists and not corrected_exists:
            continue
        if source_exists != corrected_exists:
            raise ValueError('Correction changed evidence file presence: ' + name)
        if digest(source / name) != digest(corrected / name):
            raise ValueError('Unexpected change: ' + name)
    changed = []
    for old, new in zip(before, after):
        if {k: v for k, v in new.items() if k not in ('metrics', 'official_metrics')} != {
                k: v for k, v in old.items() if k not in ('metrics', 'official_metrics')}:
            raise ValueError('Changed provider evidence: ' + old['attempt_id'])
        if new['official_metrics'] != old.get('official_metrics', old['metrics']):
            raise ValueError('Original official metrics were not preserved')
        for metric in ('text_edit', 'code_edit', 'formula_edit', 'table_edit'):
            if new['metrics'].get(metric) != old['metrics'].get(metric):
                raise ValueError('Changed unrelated component: ' + metric)
        if digest(contained_path(corrected, new['artifact_path'])) != new['output_sha256']:
            raise ValueError('Changed raw output bytes')
        if not old['ok']:
            if new['metrics']:
                raise ValueError('Added scores to a failed conversion')
            continue
        a, b = old['metrics']['table_TEDS'], new['metrics']['table_TEDS']
        if a['success'] != b['success'] and not (
            a['success'] and not b['success'] and
            'Exact tree calculation exceeds safe workspace bound' in b.get('error', '')
        ):
            raise ValueError('Unexpected denominator change')
        if a['score'] != b['score']:
            changed.append({'attempt_id': new['attempt_id'], 'before': a['score'], 'after': b['score']})
        values = [v['score'] for k, v in new['metrics'].items() if k != 'overall' and v.get('success')]
        if abs(new['metrics']['overall']['score'] - sum(values) / len(values)) > 1e-12:
            raise ValueError('Per-page overall mismatch')
    if changed != receipt['changed_scores']:
        raise ValueError('Changed-score inventory mismatch')
    require_valid_teds_for_release(after)
    summaries = json.loads((corrected / 'score-summaries.json').read_text())
    impacts = {}
    for provider, summary in summaries.items():
        records = [row for row in after if row['provider'] == provider]
        if len(records) != len(json.loads((source / 'dataset.json').read_text())['cases']):
            raise ValueError('Provider scope mismatch')
        current = summary['official_aggregate']
        for name in ('text_edit', 'code_edit', 'formula_edit', 'table_edit', 'table_TEDS'):
            scores = [row['metrics'][name]['score'] for row in records if row['metrics'].get(name, {}).get('success')]
            if len(scores) != current['denominators'][name] or abs(sum(scores)/len(scores) - current['scores'][name]) > 1e-12:
                raise ValueError('Aggregate mismatch: ' + provider + '/' + name)
        impacts[provider] = {'official': summary['original_official_aggregate'], 'corrected': current}
    return {'schema_version': 1, 'status': 'passed', 'scope': 'TEDS correction integrity and arithmetic, not a new provider run',
            'logical_results': expected_count, 'changed_scores': len(changed), 'provider_calls': 0,
            'output_results_sha256': digest(corrected / 'results.jsonl'),
            'correction_receipt_sha256': digest(corrected / 'teds-correction.json'),
            'review_script_sha256': digest(Path(__file__)), 'impacts': impacts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--corrected', type=Path, required=True)
    args = parser.parse_args()
    output = args.corrected / 'teds-correction-review.json'
    if output.exists():
        raise ValueError('Refusing to overwrite the correction audit')
    result = review(args.source, args.corrected)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ('status', 'logical_results', 'changed_scores', 'provider_calls')}))


if __name__ == '__main__':
    main()
