"""Create an immutable score-only correction; never rerun or replace provider bytes."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.metrics import task_passed
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.scoring_regressions import build_correction_provenance, load_scoring_regressions
from harness.web_fetch.safety import contained_path
from harness.web_fetch.webmainbench_diagnostics import require_valid_teds_for_release
from harness.web_fetch.webmainbench_diagnostics import validate_teds_revision
from scripts.score_webmainbench_results import aggregate, metric_dict, official_calculator

CONTROLS = ['ordered-tree-identical-nested-content-is-perfect',
            'ordered-tree-missing-children-cannot-be-perfect']
ROOT_CAUSE = ('The pinned official TEDS implementation passes a serialized string to APTED, '
              'whose default child accessor sees no tree children. The correction passes explicit '
              'nodes with a children accessor and preserves official parsing and label costs; '
              'algorithm errors no longer fall back to an optimistic node-count difference.')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_controls():
    registry = load_scoring_regressions()
    for identifier in CONTROLS:
        row = registry[identifier]
        result = score_attempt(row['case'], row['artifact'].encode(), ok=row['ok'])
        if task_passed(row['ok'], result) != row['expected_task_pass']:
            raise ValueError('Regression task predicate failed: ' + identifier)
        if any(result[key] != value for key, value in row['expected_metrics'].items()):
            raise ValueError('Regression metric failed: ' + identifier)


def corrected_distance(self, predicted, reference):
    # Use the production public scorer for derived tree inputs. Raw provider
    # identity is separately checked and remains unchanged in the record.
    result = score_attempt({'url': 'https://example.test/ordered-tree',
                            'ordered_tree_reference': reference},
                           json.dumps(predicted).encode(), ok=True)
    return result['ordered_tree']['edit_distance']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--scorer-repo', required=True, type=Path)
    parser.add_argument('--prior-corrected', type=Path,
                        help='Previously verified correction whose unchanged results are inherited')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite corrected evidence')
    validate_controls()
    provenance = build_correction_provenance('structured-extraction', ROOT_CAUSE, CONTROLS)
    source = args.source
    dataset = json.loads((source / 'dataset.json').read_text())
    summaries = json.loads((source / 'score-summaries.json').read_text())
    records = [json.loads(line) for line in (source / 'results.jsonl').read_text().splitlines()]
    inherited = {}
    inherited_receipt_sha256 = None
    if args.prior_corrected:
        validate_teds_revision(args.prior_corrected)
        inherited = {row['attempt_id']: row for row in
                     (json.loads(line) for line in (args.prior_corrected / 'results.jsonl').read_text().splitlines())}
        inherited_receipt_sha256 = digest(args.prior_corrected / 'teds-correction.json')
    if any(path.is_symlink() for path in source.rglob('*')):
        raise ValueError('Refusing symlinked correction evidence')
    expected = {(provider, case['id']) for provider in dataset['coverage_plan']['static_html']
                for case in dataset['cases']}
    if len(records) != len(expected) or len(expected) != len(dataset['cases']) * len(summaries) or {
            (row['provider'], row['case_id']) for row in records} != expected:
        raise ValueError(f'Correction requires the complete declared {len(dataset["cases"])}-by-{len(summaries)} matrix')
    # Verify the exact official implementation, not only its Git HEAD.
    for summary in summaries.values():
        for relative, expected in summary['scorer']['metric_source_sha256'].items():
            path = args.scorer_repo / relative
            if not path.is_file():
                path = args.scorer_repo / 'webmainbench/metrics' / relative
            if digest(path) != expected:
                raise ValueError('Official source differs: ' + relative)
    with tempfile.TemporaryDirectory(prefix='webmainbench-teds-cache-') as cache_dir:
        calculator = official_calculator(args.scorer_repo, Path(cache_dir))
        metric = calculator.metrics['table_TEDS']
        metric._tree_edit_distance = types.MethodType(corrected_distance, metric)
        from webmainbench.metrics.base import MetricResult
        corrected = []
        changed = []
        for index, original in enumerate(records, 1):
            row = copy.deepcopy(original)
            artifact = contained_path(source, row['artifact_path'])
            reference_path = contained_path(source, 'references/' + row['case_id'] + '.md')
            if digest(artifact) != row['output_sha256'] or digest(reference_path) != row['reference_sha256']:
                raise ValueError('Evidence binding mismatch: ' + row['attempt_id'])
            prior = inherited.get(row['attempt_id'])
            if prior is not None:
                if (prior.get('official_metrics') != row.get('metrics') or
                    {key: value for key, value in prior.items() if key not in ('metrics', 'official_metrics')} !=
                    {key: value for key, value in row.items() if key != 'metrics'}):
                    raise ValueError('Inherited correction differs from frozen provider evidence: ' + row['attempt_id'])
                row = copy.deepcopy(prior)
                old = original['metrics'].get('table_TEDS', {})
                current = row['metrics'].get('table_TEDS', {})
                if current.get('score') != old.get('score'):
                    changed.append({'attempt_id': row['attempt_id'], 'before': old['score'], 'after': current['score']})
            else:
                row['official_metrics'] = copy.deepcopy(row.get('metrics', {}))
            if prior is None and row.get('ok'):
                prior = row['metrics']['table_edit']
                dependency = MetricResult('table_edit', prior['score'], prior.get('details', {}),
                                          prior.get('success', False), prior.get('error'))
                current = metric_dict(metric.calculate(artifact.read_text(), reference_path.read_text(),
                                                        table_edit_result=dependency))
                old = row['metrics']['table_TEDS']
                if current['success'] != old['success'] and not (
                    old['success'] and not current['success'] and
                    'Exact tree calculation exceeds safe workspace bound' in current.get('error', '')
                ):
                    raise ValueError('Unexpected denominator change: ' + row['attempt_id'])
                row['metrics']['table_TEDS'] = current
                successful = [item['score'] for key, item in row['metrics'].items()
                              if key != 'overall' and item.get('success')]
                if successful:
                    row['metrics']['overall']['score'] = sum(successful) / len(successful)
                    if current['success']:
                        row['metrics']['overall']['details']['individual_scores']['table_TEDS'] = current['score']
                    else:
                        row['metrics']['overall']['details']['individual_scores'].pop('table_TEDS', None)
                if current.get('score') != old.get('score'):
                    changed.append({'attempt_id': row['attempt_id'], 'before': old['score'], 'after': current['score']})
            corrected.append(row)
            if index % 10 == 0:
                print(f'Scored {index}/{len(records)}; changed {len(changed)}', flush=True)
        require_valid_teds_for_release(corrected)
        for provider, summary in summaries.items():
            rows = [row for row in corrected if row['provider'] == provider]
            summary['original_official_aggregate'] = summary['official_aggregate']
            summary['official_aggregate'] = aggregate(rows)
            cases = {case['id']: case for case in dataset['cases']}
            for axis, values in summary['dimensions'].items():
                for value in values:
                    def category(row):
                        item = cases[row['case_id']]['meta'].get(axis)
                        if isinstance(item, list):
                            return '+'.join(sorted(item)) if item else 'none'
                        return str(item if item not in (None, '') else 'unavailable')
                    values[value] = aggregate([row for row in rows if category(row) == value])
        # Write only after every computation succeeds. The predecessor is untouched.
        shutil.copytree(source, args.output)
        shutil.copy2(source / 'results.jsonl', args.output / 'original-results.jsonl')
        results_path = args.output / 'results.jsonl'
        results_path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in corrected))
        (args.output / 'score-summaries.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + '\n')
        source_files = [Path(__file__), ROOT / 'harness/web_fetch/ordered_tree.py', ROOT / 'harness/web_fetch/scoring.py']
        for path in source_files:
            target = args.output / 'correction-source' / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        unavailable = [row['attempt_id'] for row in corrected if row.get('official_metrics', {}).get('table_TEDS', {}).get('success')
                       and not row.get('metrics', {}).get('table_TEDS', {}).get('success')]
        receipt = {'schema_version': 2, 'kind': 'webmainbench_teds_tree_adapter_correction',
                   'provider_calls': 0, 'correction_provenance': provenance,
                   'input_results_sha256': digest(source / 'results.jsonl'),
                   'output_results_sha256': digest(results_path),
                   'dataset_sha256': digest(source / 'dataset.json'),
                   'sample_manifest_sha256': dataset.get('sample_manifest_sha256'),
                   'inherited_correction_receipt_sha256': inherited_receipt_sha256,
                   'inherited_logical_results': len(inherited),
                   'source_code_sha256': {str(path.relative_to(ROOT)): digest(path) for path in source_files},
                   'scope': {'logical_results': len(records), 'providers': sorted(summaries)},
                   'changed_scores': changed, 'unavailable_exact_teds': unavailable,
                   'denominator_changed': bool(unavailable), 'latency_changed': False}
        (args.output / 'teds-correction.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'changed_scores': len(changed), 'provider_calls': 0}))


if __name__ == '__main__':
    main()
