"""Rebind a complete blind review after non-judgmental dataset metadata changes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.revise_body_scoring import rebind_unchanged_result_review
from harness.web_fetch.result_review import require_result_review
from harness.web_fetch.reference_lifecycle import scoring_revision_history_sha256


ALLOWED_REVISION_FIELDS = {
    'body_requirements', 'required_patterns', 'required_facts', 'acceptance_contract',
    'forbidden_any', 'expected_resources', 'resource_output_contract',
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def rebind_revision_receipts(evidence: Path, prior_dataset: bytes, results: bytes,
                             *, rebound_at: str, changed_fields: list[str]) -> None:
    """Bind prior audited revisions to a verdict-neutral assembled metadata update."""
    current_dataset = (evidence / 'dataset.json').read_bytes()
    old_hash, new_hash, results_hash = digest(prior_dataset), digest(current_dataset), digest(results)
    current_document = json.loads(current_dataset)
    records = [json.loads(line) for line in results.splitlines()]
    review = require_result_review(evidence, current_document, records)
    if review is None:
        raise ValueError('Independent result review is required for metadata rebinding')

    reference_path = evidence / 'reference-revisions.json'
    if reference_path.is_file():
        rows = json.loads(reference_path.read_text())
        rebound = 0
        for row in rows:
            if (row.get('output_dataset_sha256') == old_hash
                    and row.get('output_results_sha256') == results_hash):
                row['prior_output_dataset_sha256'] = old_hash
                row['output_dataset_sha256'] = new_hash
                row['rebound_after_nonjudgmental_metadata_change'] = {
                    'at': rebound_at, 'changed_top_level_fields': changed_fields,
                    'provider_calls': 0,
                }
                rebound += 1
        if not rebound:
            raise ValueError('No reference revision receipt is bound to the prior evidence')
        reference_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')

    scoring_path = evidence / 'scoring-revisions.json'
    if not scoring_path.is_file():
        raise ValueError('Scoring revision history is missing')
    original_scoring = scoring_path.read_bytes()
    history = json.loads(original_scoring)
    changed_cases = {}
    decision_changes = {}
    for row in history:
        if (not isinstance(row, dict) or row.get('provider_calls') != 0
                or row.get('kind') not in {'audited_body_scoring_revision',
                                           'audited_scoring_rule_revision',
                                           'audited_assembled_scoring_revision'}):
            raise ValueError('Invalid scoring revision history')
        for change in row.get('changed_cases', []):
            if set(change.get('fields', ())) - ALLOWED_REVISION_FIELDS:
                raise ValueError('Scoring revision changes unsupported fields')
            changed_cases[json.dumps(change, ensure_ascii=False, sort_keys=True)] = change
        for change in row.get('task_decision_changes', []):
            decision_changes[json.dumps(change, ensure_ascii=False, sort_keys=True)] = change
    history.append({
        'kind': 'audited_assembled_scoring_revision',
        'scored_at': rebound_at,
        'input_dataset_sha256': old_hash,
        'input_results_sha256': results_hash,
        'output_dataset_sha256': new_hash,
        'output_results_sha256': results_hash,
        'changed_cases': list(changed_cases.values()),
        'task_decision_changes': list(decision_changes.values()),
        'provider_calls': 0,
        'immutable_call_fields': 'attempt identity, output bytes/hash, timestamps, elapsed time, metrics and verdicts retained',
        'source_scoring_receipts_sha256': scoring_revision_history_sha256(history),
        'independent_result_review_sha256': digest((evidence / 'result-review.json').read_bytes()),
        'metadata_change': {'changed_top_level_fields': changed_fields},
    })
    scoring_path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--prior-dataset', type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    results = (evidence / 'results.jsonl').read_bytes()
    rebound_at = datetime.now(timezone.utc).isoformat()
    prior_dataset = args.prior_dataset.read_bytes()
    prior_document = json.loads(prior_dataset)
    current_document = json.loads((evidence / 'dataset.json').read_bytes())
    changed_fields = sorted(
        key for key in set(prior_document) | set(current_document)
        if prior_document.get(key) != current_document.get(key)
    )
    paths = [evidence / name for name in (
        'result-review.json', 'reference-revisions.json', 'scoring-revisions.json')]
    before = {path: path.read_bytes() for path in paths if path.is_file()}
    try:
        changed = rebind_unchanged_result_review(
            evidence / 'result-review.json',
            prior_dataset,
            results,
            (evidence / 'dataset.json').read_bytes(),
            results,
            rebound_at=rebound_at,
        )
        if not changed:
            raise ValueError('result-review.json is missing')
        rebind_revision_receipts(
            evidence, prior_dataset, results,
            rebound_at=rebound_at, changed_fields=changed_fields,
        )
    except BaseException:
        for path, raw in before.items():
            path.write_bytes(raw)
        raise
    print(evidence / 'result-review.json')
