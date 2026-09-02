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


def rescore(source):
    dataset = json.loads(contained_path(source, 'dataset.json').read_text())
    path = contained_path(source, 'results.jsonl')
    records = [json.loads(line) for line in path.read_text().splitlines()]
    validate_report_ids(dataset, records)
    cases = {c['id']: c for c in dataset['cases']}
    changes = []
    keys = ['required_facts', 'forbidden_any', 'min_content_chars',
            'fact_coverage_threshold', 'ground_truth_main_content', 'required_patterns']
    for r in records:
        c = cases[r['case_id']]
        raw = contained_path(source, r['artifact_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != r['metrics']['content_sha256']:
            raise ValueError('Output integrity failure: ' + r['attempt_id'])
        before = succeeded(r, c)
        metrics = evaluate_content(raw.decode('utf-8', errors='replace'), c['expected_any'],
                                   **{k:c[k] for k in keys if k in c})
        if not r['ok'] or not c.get('content_score_applicable', True):
            metrics['content_pass'] = False
        if not r['ok'] and metrics['word_f1'] is not None:
            metrics.update(word_precision=0.0, word_recall=0.0, word_f1=0.0)
        r['metrics'].update(metrics)
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
