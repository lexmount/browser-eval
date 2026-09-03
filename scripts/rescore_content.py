"""Recompute text metrics from immutable evidence; never call providers."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.metrics import evaluate_content
from harness.web_fetch.models import execution_id
from harness.web_fetch.report import succeeded, failure_category
from harness.web_fetch.safety import contained_path, validate_report_ids
from harness.web_fetch.reference_lifecycle import check_case_reference_binding, ReferenceLifecycleError


def rescore(source, case_ids=None):
    dataset = json.loads(contained_path(source, 'dataset.json').read_text())
    path = contained_path(source, 'results.jsonl')
    records = [json.loads(line) for line in path.read_text().splitlines()]
    validate_report_ids(dataset, records)
    cases = {c['id']: c for c in dataset['cases']}
    execution = contained_path(source, 'execution.json')
    batches = (json.loads(execution.read_text())['batches'] if execution.exists()
               else [json.loads(contained_path(source, 'manifest.json').read_text())])
    for record in records:
        reference_hash = record.get('metrics', {}).get('reference_sha256')
        owners = [b for b in batches if record['attempt_id'] in b.get('attempt_ids', [])]
        bound = any(record['case_id'] in b.get('reference_checks', {}).get('cases', {}) for b in owners)
        if reference_hash is None and not bound:
            continue
        if len(owners) != 1:
            raise ReferenceLifecycleError('Bound record requires one original execution batch')
        binding = owners[0].get('reference_checks', {}).get('cases', {}).get(record['case_id'])
        if not binding or binding.get('reference_sha256') != reference_hash:
            raise ReferenceLifecycleError('Bound record has no matching original reference')
        check_case_reference_binding(cases[record['case_id']], binding)
    changes = []
    keys = ['required_facts', 'forbidden_any', 'min_content_chars',
            'fact_coverage_threshold', 'ground_truth_main_content', 'required_patterns', 'acceptance_contract']
    for r in records:
        if case_ids is not None and r['case_id'] not in case_ids:
            continue
        c = cases[r['case_id']]
        raw = contained_path(source, r['artifact_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != r['metrics']['content_sha256']:
            raise ValueError('Output integrity failure: ' + r['attempt_id'])
        before = succeeded(r, c)
        metrics = evaluate_content(raw.decode('utf-8', errors='replace'), c['expected_any'],
                                   base_url=c['url'],
                                   **{k:c[k] for k in keys if k in c})
        if not r['ok'] or not c.get('content_score_applicable', True):
            metrics['content_pass'] = False
        if not r['ok'] and c.get('acceptance_contract'):
            metrics['stable_task_pass'] = False
        if not r['ok'] and metrics['word_f1'] is not None:
            metrics.update(word_precision=0.0, word_recall=0.0, word_f1=0.0)
        r['metrics'].update(metrics)
        if c.get('acceptance_contract'):
            r['metrics']['acceptance_contract_sha256'] = hashlib.sha256(json.dumps(
                c['acceptance_contract'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        r['run_id'] = execution_id(r)
        r.pop('diagnosis', None)  # Rebuilt against the new metrics by report_build.
        r['failure_category'] = failure_category(r, c)
        after = succeeded(r, c)
        if before != after:
            changes.append({'attempt_id':r['attempt_id'], 'before':before, 'after':after})
    temporary = contained_path(source, 'results.jsonl.tmp')
    temporary.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in records))
    temporary.replace(path)
    return changes


if __name__ == '__main__':
    print(json.dumps(rescore(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'evaluation'), ensure_ascii=False, indent=2))
