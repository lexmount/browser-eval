"""Recompute shared task metrics from immutable evidence; never call providers."""
from datetime import datetime, timezone
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.scoring import SCORING_SOURCE_SHA256, load_list_reference, score_attempt
from harness.web_fetch.failure_diagnosis import diagnose
from harness.web_fetch.models import execution_id
from harness.web_fetch.metrics import summarize
from harness.web_fetch.response_input import apply_request_status
from harness.web_fetch.report import succeeded, failure_category
from harness.web_fetch.safety import contained_path, validate_report_ids
from harness.web_fetch.reference_lifecycle import (
    check_case_equivalent_reference_binding, check_case_reference_binding,
    check_case_scoring_revision_binding, check_case_reference_revision_binding,
    requires_fresh_reference, ReferenceLifecycleError,
)


def rescore(source, case_ids=None, *, allow_rule_revision=False,
            reference_revision_hashes=None):
    dataset_path = contained_path(source, 'dataset.json')
    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes)
    path = contained_path(source, 'results.jsonl')
    original_results = path.read_bytes()
    records = [json.loads(line) for line in original_results.splitlines()]
    validate_report_ids(dataset, records)
    cases = {c['id']: c for c in dataset['cases']}
    execution = contained_path(source, 'execution.json')
    batches = (json.loads(execution.read_text())['batches'] if execution.exists()
               else [json.loads(contained_path(source, 'manifest.json').read_text())])
    for record in records:
        reference_hash = record.get('metrics', {}).get('reference_sha256')
        owners = [b for b in batches if record['attempt_id'] in b.get('attempt_ids', [])]
        bound = any(record['case_id'] in b.get('reference_checks', {}).get('cases', {}) for b in owners)
        if not bound:
            if reference_hash is None or not requires_fresh_reference(cases[record['case_id']]):
                continue
            raise ReferenceLifecycleError(record['attempt_id']+': fresh reference record has no original binding')
        if len(owners) != 1:
            raise ReferenceLifecycleError(record['attempt_id']+': bound record requires one original execution batch')
        binding = owners[0].get('reference_checks', {}).get('cases', {}).get(record['case_id'])
        revised_hashes = (reference_revision_hashes or {}).get(record['case_id'])
        revision_applies = bool(
            revised_hashes and binding
            and binding.get('reference_sha256') in revised_hashes)
        if not binding or (not revision_applies and binding.get('reference_sha256') != reference_hash):
            raise ReferenceLifecycleError(record['attempt_id']+': bound record has no matching original reference')
        checker = ((lambda case, binding: check_case_reference_revision_binding(
                        case, binding, revised_hashes)) if revision_applies else
                   check_case_scoring_revision_binding if allow_rule_revision else
                   check_case_equivalent_reference_binding
                   if dataset.get('comparison', {}).get('reference_binding_mode') ==
                   'equivalent_reference_bytes_with_independent_batch_captures'
                   else check_case_reference_binding)
        checker(cases[record['case_id']], binding)
    if not any(case_ids is None or r['case_id'] in case_ids for r in records):
        return []
    # One offline scoring batch, never a new provider invocation. Include this
    # script and diagnosis/report dispatch as well as the shared scorer sources.
    audit = {
        'mode': 'offline_rescore',
        'scored_at': datetime.now(timezone.utc).isoformat(),
        'source_sha256': hashlib.sha256(b'\0'.join([
            Path(__file__).read_bytes(), SCORING_SOURCE_SHA256.encode('ascii'),
            (ROOT/'harness/web_fetch/failure_diagnosis.py').read_bytes(),
            (ROOT/'harness/web_fetch/report.py').read_bytes(),
        ])).hexdigest(),
        'dataset_sha256': hashlib.sha256(dataset_bytes).hexdigest(),
        'input_results_sha256': hashlib.sha256(original_results).hexdigest(),
        'request_status_basis': 'shared_normalization_with_original_adapter_status_retained',
        'rule_revision': bool(allow_rule_revision),
        'reference_revision': bool(reference_revision_hashes),
    }
    changes = []
    for r in records:
        if case_ids is not None and r['case_id'] not in case_ids:
            continue
        c = cases[r['case_id']]
        raw = contained_path(source, r['artifact_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != r['metrics']['content_sha256']:
            raise ValueError('Output integrity failure: ' + r['attempt_id'])
        before = succeeded(r, c)
        # Status and artifact normalization are shared with live execution.
        r['metrics'] = score_attempt(c, raw, ok=bool(r['ok']), status_code=r.get('status_code'),
            title=r.get('title'), final_url=r.get('final_url'),
            metadata=r.get('provider_metadata', {}), previous_metrics=r['metrics'],
            list_reference=load_list_reference(c, source))
        apply_request_status(r)
        if 'run_id' not in r:
            r['run_id'] = execution_id(r)
        r['diagnosis'] = diagnose(r, c, raw.decode('utf-8', errors='replace'))
        r['failure_category'] = failure_category(r, c)
        r['scoring'] = {**r.get('scoring', {}), **audit}
        after = succeeded(r, c)
        if before != after:
            changes.append({'attempt_id':r['attempt_id'], 'before':before, 'after':after})
    temporary = contained_path(source, 'results.jsonl.tmp')
    if path.read_bytes() != original_results or dataset_path.read_bytes() != dataset_bytes:
        raise ValueError('Scoring inputs changed during rescoring')
    # JSON Lines readers split on physical line boundaries. Escaping Unicode
    # line/paragraph separators keeps one attempt on exactly one line too.
    temporary.write_text(''.join(json.dumps(r, ensure_ascii=True)+'\n' for r in records))
    temporary.replace(path)
    contained_path(source, 'summary.json').write_text(
        json.dumps(summarize(records), ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    return changes


if __name__ == '__main__':
    from harness.web_fetch.storage import current_evidence_dir
    print(json.dumps(rescore(Path(sys.argv[1]) if len(sys.argv)>1 else current_evidence_dir()), ensure_ascii=False, indent=2))
