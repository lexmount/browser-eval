"""Deterministic anomaly triggers and publication gates for result review.

This is deliberately separate from verdict review: triggers are computed only after
the blind reviewer has judged the saved answer, so they cannot prime that verdict.
"""

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import median

from lexbench_webfetch.paths import contained_path

LAYERS = ('input', 'reference', 'request', 'raw', 'adapter', 'scorer', 'final_verdict')
TRIGGER_VERSION = 2
_SHORT_OUTPUT = 512
_TINY_OUTPUT = 64
_SHORT_ERROR_MS = 1000


def _bounded_transport_raw_absence(source, record, layers) -> bool:
    """Allow an explicitly bounded missing raw layer, never a missing call.

    A client read timeout can end before the provider adapter receives an
    ``HttpResponse``. There are then no raw bytes to inspect, but the failure
    decision is still auditable from the public physical-call log. This does
    not apply to successful or status-bearing responses, missing request
    evidence, or an unbound log.
    """
    if (layers['raw'].get('status') != 'insufficient'
            or layers['request'].get('status') != 'verified'
            or layers['scorer'].get('status') != 'verified'
            or layers['final_verdict'].get('status') != 'verified'
            or record.get('ok') is not False
            or record.get('status_code') is not None
            or record.get('error_type') != 'timeout'):
        return False
    metadata = record.get('provider_metadata', {})
    if metadata.get('raw_response_path') or metadata.get('raw_stdout_path'):
        return False
    binding = metadata.get('call_log')
    if not isinstance(binding, dict):
        return False
    try:
        path = contained_path(Path(source), binding['path'])
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != binding['sha256']:
            return False
        log = json.loads(data)
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(log, dict) or any(
            not isinstance(log.get(key), dict)
            for key in ('response', 'error', 'output')):
        return False
    return (log['response'].get('status_code') is None
            and log['error'].get('type') == record['error_type']
            and log['error'].get('message') == record.get('error_message')
            and log['output'].get('sha256') ==
            record.get('metrics', {}).get('content_sha256')
            and log['output'].get('bytes') == 0)


def _text(path):
    try:
        return Path(path).read_text(errors='replace')
    except (OSError, TypeError):
        return ''


def _artifact_text(root, record):
    path = record.get('artifact_path')
    return _text(root / path) if path else ''


def _raw_sizes(root, record):
    metadata = record.get('provider_metadata', {})
    candidates = [
        (metadata.get('raw_response_path'), metadata.get('raw_response_bytes')),
        (metadata.get('raw_stdout_path'), metadata.get('raw_stdout_bytes')),
    ]
    candidates.extend(
        (item.get('artifact_path'), item.get('bytes'))
        for item in metadata.get('file_evaluation', {}).get('files', [])
        if isinstance(item, dict))
    sizes = []
    for path, declared_size in candidates:
        if isinstance(declared_size, int) and not isinstance(declared_size, bool):
            sizes.append(declared_size)
            continue
        if path:
            try:
                sizes.append((root / path).stat().st_size)
            except OSError:
                pass
    return sizes


def _normalized(text):
    return re.sub(r'\s+', ' ', text).strip()


def _only_forbidden_failed(metrics):
    stable_errors = metrics.get('stable_task_errors', [])
    if (metrics.get('stable_structure_pass') is True
            and metrics.get('forbidden_requirement_hits')
            and stable_errors
            and all('forbidden' in str(error).lower() for error in stable_errors)):
        return True
    aggregate = {'content_pass', 'task_pass', 'pass'}
    booleans = {key: value for key, value in metrics.items()
                if isinstance(value, bool) and key not in aggregate}
    forbidden = {key: value for key, value in booleans.items()
                 if any(word in key.lower() for word in
                        ('forbidden', 'excluded', 'exclusion', 'unexpected'))}
    positive = {key: value for key, value in booleans.items() if key not in forbidden}
    return bool(forbidden) and any(value is False for value in forbidden.values()) and bool(positive) \
        and all(positive.values()) and all(value is False for value in forbidden.values())


def _task_succeeded(record):
    metrics = record.get('metrics', {})
    kind = metrics.get('task_kind')
    if kind == 'file':
        files = record.get('provider_metadata', {}).get('file_evaluation', {}).get('files', [])
        return bool(files) and all(item.get('pass') is True for item in files)
    for key in ('stable_task_pass', 'resource_pass', 'content_pass'):
        if isinstance(metrics.get(key), bool):
            return metrics[key]
    return None


def _successful_file(record):
    return record.get('metrics', {}).get('task_kind') == 'file' and _task_succeeded(record) is True


def _raw_document_mismatch(root, record):
    metadata = record.get('provider_metadata', {})
    if metadata.get('document_response') and metadata.get('document_response') != 'raw_document':
        return True
    for path, _ in [(metadata.get('raw_stdout_path'), None),
                    (metadata.get('raw_response_path'), None)]:
        if not path:
            continue
        try:
            prefix = (root / path).read_bytes()[:512].lower()
        except OSError:
            continue
        binary_signatures = (b'pk\x03\x04', b'%pdf', b'\x89png')
        if (prefix.lstrip().startswith((b'<html', b'<!doctype html'))
                and any(signature in prefix for signature in binary_signatures)):
            return True
    return False


def detect_anomalies(root, records, dataset=None, reviewer_anomalies=None):
    """Return trigger IDs by attempt without importing production scoring."""
    root = Path(root)
    cases = {case['id']: case for case in (dataset or {}).get('cases', [])}
    found = defaultdict(set)
    short_outputs = defaultdict(list)
    for record in records:
        attempt = record['attempt_id']
        text = _artifact_text(root, record)
        normalized = _normalized(text)
        if record.get('ok') is True and not normalized and not _successful_file(record):
            found[attempt].add('request-ok-empty-answer')
        raw_sizes = _raw_sizes(root, record)
        if (raw_sizes and len(normalized) < _TINY_OUTPUT and not _successful_file(record)
                and max(raw_sizes) > max(256, len(text.encode()) * 4)):
            found[attempt].add('raw-nonempty-normalized-empty-or-tiny')
        if normalized and len(normalized) <= _SHORT_OUTPUT:
            short_outputs[(record.get('provider'), normalized)].append(record)
        if _only_forbidden_failed(record.get('metrics', {})):
            found[attempt].add('positive-structure-only-forbidden-failed')
        metrics = record.get('metrics', {})
        if (metrics.get('blocked_page') is True or metrics.get('stable_blocked_page') is True
                or metrics.get('blocked_page_hits')):
            found[attempt].add('blocked-or-forbidden-page')
        lowered = normalized.lower()
        if re.search(
            r"(?:^|[\s(])(?:/Users/[^\s:]+|/home/[^\s:]+|/private/(?:tmp|var)/[^\s:]+)",
            text,
            re.MULTILINE,
        ):
            found[attempt].add('public-artifact-local-path')
        if (re.search(r'\b403\b.{0,40}\bforbidden\b|\bforbidden\b.{0,40}\b403\b', lowered)
                or 'anubis.techaro.lol' in lowered or 'botstopper' in lowered):
            found[attempt].add('blocked-or-forbidden-page')
        metadata = record.get('provider_metadata', {})
        if metadata.get('exit_code') == 0 and not normalized and not _successful_file(record):
            found[attempt].add('exit-zero-empty-output')
        if record.get('error_type') in {'tool_error', 'configuration_error', 'invalid_configuration'}:
            found[attempt].add('tool-or-configuration-error')
        status = record.get('status_code')
        if status == 202 or (isinstance(status, int) and 400 <= status <= 599):
            found[attempt].add('suspicious-http-status')
        if _raw_document_mismatch(root, record):
            found[attempt].add('raw-document-response-mismatch')
        if (metadata.get('route') == 'challenge_json_trace'
                and metrics.get('stable_task_pass') is False
                and metrics.get('stable_complete_record_count') == 0):
            found[attempt].add('route-output-contract-mismatch')
        if metadata.get('route') == 'raw_file' and not _successful_file(record):
            found[attempt].add('route-output-contract-mismatch')
        elapsed = record.get('elapsed_ms')
        if (record.get('ok') is False and isinstance(elapsed, (int, float))
                and elapsed <= _SHORT_ERROR_MS and record.get('error_type')):
            found[attempt].add('short-tool-error')
    for rows in short_outputs.values():
        if len({row.get('case_id') for row in rows}) > 1:
            reference_ids = {
                cases.get(row.get('case_id'), {}).get('source_verification', {}).get('reference_sha256')
                for row in rows}
            # A redirect and its destination may intentionally produce the same
            # answer; identical independently frozen source hashes prove that
            # equivalence and are not suspicious duplication.
            if len(reference_ids) == 1 and None not in reference_ids:
                continue
            for row in rows:
                found[row['attempt_id']].add('identical-short-output-across-cases')
    cohorts = defaultdict(list)
    for record in records:
        track = cases.get(record.get('case_id'), {}).get('track')
        outcome = _task_succeeded(record)
        if track and outcome is not None:
            cohorts[(record.get('provider'), track)].append((record, outcome))
    rates_by_track = defaultdict(list)
    for (provider, track), rows in cohorts.items():
        if len(rows) >= 3:
            rates_by_track[track].append((provider, sum(ok for _, ok in rows) / len(rows)))
    for (provider, track), rows in cohorts.items():
        if len(rows) < 3:
            continue
        rate = sum(ok for _, ok in rows) / len(rows)
        peers = [peer_rate for peer, peer_rate in rates_by_track[track] if peer != provider]
        trigger = ('provider-track-all-zero' if rate == 0 else
                   ('provider-track-success-cliff' if len(rows) >= 5 and peers
                    and rate <= .1 and median(peers) >= .6 else None))
        if trigger:
            for record, _ in rows:
                found[record['attempt_id']].add(trigger)
    # A provider-family result with a single success is intrinsically unusual
    # even when every peer also performs poorly.  Only expand this trigger over
    # a cohort that the dataset declared and that the evidence contains exactly
    # once; a partial run must not turn missing attempts into an anomaly claim.
    coverage = (dataset or {}).get('coverage_plan', {})
    declared_cases = defaultdict(set)
    for case in (dataset or {}).get('cases', []):
        track = case.get('track', 'web')
        for provider in coverage.get(track, []):
            declared_cases[(provider, track)].add(case['id'])
    records_by_cohort = defaultdict(list)
    for record in records:
        track = cases.get(record.get('case_id'), {}).get('track')
        if track:
            records_by_cohort[(record.get('provider'), track)].append(record)
    for cohort, expected_case_ids in declared_cases.items():
        rows = records_by_cohort.get(cohort, [])
        observed_case_ids = [record.get('case_id') for record in rows]
        outcomes = [_task_succeeded(record) for record in rows]
        complete = (
            len(expected_case_ids) >= 3
            and len(rows) == len(expected_case_ids)
            and set(observed_case_ids) == expected_case_ids
            and len(observed_case_ids) == len(set(observed_case_ids))
            and all(outcome is not None for outcome in outcomes)
        )
        if complete and sum(outcomes) == 1:
            for record in rows:
                found[record['attempt_id']].add('provider-track-at-most-one-pass')
    for attempt, anomalies in (reviewer_anomalies or {}).items():
        if anomalies:
            found[attempt].add('reviewer-active-anomaly')
    return {attempt: sorted(triggers) for attempt, triggers in found.items()}


def require_anomaly_review(source, dataset, records, review):
    """Validate required seven-layer anomaly resolution before install/publication."""
    if not dataset.get('comparison', {}).get('require_anomaly_review', False):
        return review
    reviewer = {row['attempt_id']: row.get('reviewer_anomalies', [])
                for row in review.get('attempts', [])}
    expected = detect_anomalies(source, records, dataset, reviewer)
    by_record = {row['attempt_id']: row for row in records}
    record_ids = {row['attempt_id'] for row in records}
    actual = {row['attempt_id']: row for row in review.get('attempts', [])
              if row.get('anomaly_review') is not None}
    if set(actual) != set(expected):
        raise ValueError('Anomaly review must cover every deterministic and reviewer-raised anomaly exactly once')
    for attempt, triggers in expected.items():
        audit = actual[attempt]['anomaly_review']
        if sorted(audit.get('triggers', [])) != triggers:
            raise ValueError(f'{attempt} anomaly triggers no longer match persisted evidence')
        layers = audit.get('layers', {})
        allowed_layer_statuses = {'verified', 'defect', 'not_applicable', 'insufficient'}
        if set(layers) != set(LAYERS) or any(
                not isinstance(layers[layer], dict)
                or layers[layer].get('status') not in allowed_layer_statuses
                or not str(layers[layer].get('evidence', '')).strip()
                for layer in LAYERS):
            raise ValueError(f'{attempt} anomaly review must check all seven evidence layers')
        insufficient = {layer for layer in LAYERS
                        if layers[layer].get('status') == 'insufficient'}
        if insufficient and not (insufficient == {'raw'}
                                 and _bounded_transport_raw_absence(
                                     source, by_record[attempt], layers)):
            raise ValueError(f'{attempt} anomaly review has insufficient layer evidence')
        scope = audit.get('same_class_scope', {})
        scoped_attempts = scope.get('attempt_ids', [])
        if (not str(scope.get('rationale', '')).strip() or attempt not in scoped_attempts
                or len(scoped_attempts) != len(set(scoped_attempts))
                or any(item not in record_ids for item in scoped_attempts)):
            raise ValueError(f'{attempt} anomaly review must declare its same-class scope')
        rerun = audit.get('rerun_scope', {})
        rerun_attempts = rerun.get('attempt_ids')
        if (not isinstance(rerun.get('required'), bool)
                or not isinstance(rerun_attempts, list)
                or len(rerun_attempts) != len(set(rerun_attempts))
                or any(item not in record_ids for item in rerun_attempts)
                or any(item not in scoped_attempts for item in rerun_attempts)
                or not str(rerun.get('reason', '')).strip()):
            raise ValueError(f'{attempt} anomaly review must declare its rerun scope')
        if rerun.get('required') and not rerun_attempts:
            raise ValueError(f'{attempt} required rerun must declare exact attempt scope')
        if not rerun.get('required') and rerun_attempts:
            raise ValueError(f'{attempt} non-required rerun scope must be empty')
        if audit.get('status') not in {'no-defect', 'corrected'}:
            raise ValueError(f'{attempt} anomaly review is unresolved')
        layer_defect = any(layers[layer].get('status') == 'defect' for layer in LAYERS)
        if ((audit.get('evaluation_defect') is True or layer_defect)
                and audit.get('correction_applied') is not True):
            raise ValueError(f'{attempt} evaluation defect must be corrected before publication')
        for field in ('root_cause', 'corrective_action'):
            if not str(audit.get(field, '')).strip():
                raise ValueError(f'{attempt} anomaly review is missing {field}')
    protocol = review.get('review_protocol', {}).get('anomaly_review', {})
    if protocol.get('trigger_version') != TRIGGER_VERSION:
        raise ValueError('Anomaly review trigger version is missing or stale')
    if protocol.get('performed_after_blind_verdict') is not True:
        raise ValueError('Anomaly investigation must run after the independent blind verdict')
    if protocol.get('triggered_attempts') != len(expected):
        raise ValueError('Anomaly review trigger count no longer matches persisted evidence')
    if tuple(protocol.get('seven_layers', ())) != LAYERS:
        raise ValueError('Anomaly review protocol does not declare the required seven layers')
    return review
