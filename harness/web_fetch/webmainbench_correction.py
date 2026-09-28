"""Validate and apply a certified complete method-by-case correction."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil

from .safety import contained_path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def validate(root: Path, original_rows: list[dict], expected_parent: str) -> tuple[dict, list, list]:
    certificate = json.loads((root / 'certificate.json').read_text())
    plan = certificate['preflight']
    if (certificate.get('kind') != 'webmainbench_origin_correction_v1'
            or plan != certificate.get('postflight')
            or plan['parent_results_sha256'] != expected_parent
            or digest(root / 'results.jsonl') != certificate['results_sha256']
            or digest(root / 'physical-attempts.jsonl') != certificate['physical_sha256']):
        raise ValueError('Case correction certificate mismatch')
    rows = read_rows(root / 'results.jsonl')
    physical = read_rows(root / 'physical-attempts.jsonl')
    originals = {r['provider']: r for r in original_rows if r['case_id'] == plan['case_id']}
    if (set(plan['providers']) != set(originals) or len(rows) != len(originals)
            or {r['provider'] for r in rows} != set(originals)):
        raise ValueError('Case correction must cover every method exactly once')
    for row in rows:
        old = originals[row['provider']]
        if (row['case_id'] != plan['case_id'] or row['supersedes_attempt_id'] != old['attempt_id']
                or any(row[key] != old[key] for key in ('input_html_sha256', 'reference_sha256'))
                or any(row['provider_metadata'].get(key) != old['provider_metadata'].get(key)
                       for key in ('binary_sha256', 'browser_version', 'extractor_version', 'converter_sha256'))):
            raise ValueError('Case correction changes the frozen input or provider identity')
        if digest(contained_path(root, row['artifact_path'])) != row['output_sha256']:
            raise ValueError('Corrected output changed')
        calls = [c for c in physical if c['provider'] == row['provider']]
        if len(calls) != row['physical_attempt_count'] or not calls:
            raise ValueError('Correction physical calls incomplete')
    if len({c['attempt_id'] for c in physical}) != len(physical):
        raise ValueError('Duplicate correction call identity')
    for call in physical:
        if (call['track_id'] != plan['case_id'] or call['provider'] not in originals
                or digest(contained_path(root, call['physical_artifact_path'])) != call['output_sha256']):
            raise ValueError('Correction physical output changed')
    return certificate, rows, physical


def apply(root: Path, staging: Path, original_rows: list, summaries: dict, *,
          validation_rows: list | None = None, expected_parent: str | None = None) -> tuple[list, dict]:
    from scripts.score_webmainbench_results import aggregate
    from scripts.apply_webmainbench_case_correction import dimensions
    certificate, replacements, calls = validate(
        root,
        validation_rows if validation_rows is not None else original_rows,
        expected_parent if expected_parent is not None else digest(staging / 'results.jsonl'),
    )
    lookup = {r['provider']: r for r in replacements}
    case_id = certificate['preflight']['case_id']
    result = [lookup[r['provider']] if r['case_id'] == case_id else r for r in original_rows]
    for row in replacements:
        path = contained_path(staging, row['artifact_path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(contained_path(root, row['artifact_path']), path)
    for call in calls:
        path = contained_path(staging, call['physical_artifact_path'])
        shutil.copy2(contained_path(root, call['physical_artifact_path']), path)
    # Retain previous physical artifacts and calls for audit, outside the current logical samples.
    previous = read_rows(staging / 'physical-attempts.jsonl')
    history = [r for r in previous if r['track_id'] == case_id]
    current_calls = [r for r in previous if r['track_id'] != case_id] + calls
    for filename, values in [('results.jsonl', result), ('physical-attempts.jsonl', current_calls),
                             ('superseded-case-results.jsonl', [r for r in original_rows if r['case_id'] == case_id]),
                             ('superseded-case-calls.jsonl', history)]:
        (staging / filename).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in values))
    revised = copy.deepcopy(summaries)
    for provider, summary in revised.items():
        selected = [r for r in result if r['provider'] == provider]
        summary['official_aggregate'] = aggregate(selected)
        summary['dimensions'] = dimensions(selected)
        summary['request_successes'] = sum(r['ok'] for r in selected)
        summary['request_success_rate'] = summary['request_successes'] / len(selected)
    (staging / 'score-summaries.json').write_text(json.dumps(revised, ensure_ascii=False, indent=2) + '\n')
    receipt = {**certificate, 'current_results_sha256': digest(staging / 'results.jsonl'),
               'superseded_execution_ms': sum(r['elapsed_ms'] for r in history),
               'superseded_physical_calls': len(history),
               'superseded_case_results_sha256': digest(staging / 'superseded-case-results.jsonl'),
               'superseded_case_calls_sha256': digest(staging / 'superseded-case-calls.jsonl'),
               'score_summaries_sha256': digest(staging / 'score-summaries.json')}
    (staging / 'case-correction.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    return result, revised
