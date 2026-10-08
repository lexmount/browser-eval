"""Validate complete, certified WCXB version timing corrections."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path

from harness.record_io import parse_jsonl


def load_full_paired_latency(root: Path, profile: dict, valid_cases: set[str]) -> tuple[dict, dict]:
    """Validate a complete, preselected final-round timing correction."""
    receipt_path, calls_path = root / 'receipt.json', root / 'calls.jsonl'
    if any(path.is_symlink() or not path.is_file() for path in (receipt_path, calls_path)):
        raise ValueError('WCXB full paired latency evidence is missing or linked')
    receipt_bytes, calls_bytes = receipt_path.read_bytes(), calls_path.read_bytes()
    if hashlib.sha256(receipt_bytes).hexdigest() != profile.get('latency_correction_receipt_sha256'):
        raise ValueError('WCXB full paired latency receipt differs from current profile')
    receipt = json.loads(receipt_bytes)
    expected_bins = {'release': profile['release_binary_sha256'],
                     'pr': profile['candidate_binary_sha256']}
    load_checks = receipt.get('host_load_1m_checks')
    load_limit = receipt.get('quiet_host_load_limit')
    if (receipt.get('kind') != 'wcxb_full_paired_latency'
            or receipt.get('case_count') != len(valid_cases)
            or receipt.get('rounds') != 2
            or receipt.get('selection') != 'final round, all cases'
            or receipt.get('call_count') != len(valid_cases) * 4
            or receipt.get('source_sha256') != profile['source_dataset_sha256']
            or receipt.get('manifest_sha256') != profile['sample_manifest_sha256']
            or receipt.get('binary_sha256') != expected_bins
            or receipt.get('calls_sha256') != hashlib.sha256(calls_bytes).hexdigest()
            or not isinstance(load_checks, list) or len(load_checks) < 50
            or not isinstance(load_limit, (int, float)) or load_limit <= 0
            or any(not isinstance(value, (int, float)) or not math.isfinite(value)
                   or value > load_limit for value in load_checks)):
        raise ValueError('WCXB full paired latency protocol or identity differs')
    calls = parse_jsonl(calls_bytes)
    expected = {(case, round_number, method)
                for case in valid_cases for round_number in (0, 1) for method in expected_bins}
    lookup = {(row.get('case'), row.get('round'), row.get('method')): row for row in calls}
    if len(calls) != len(expected) or set(lookup) != expected:
        raise ValueError('WCXB full paired latency inventory is incomplete or duplicated')
    for round_number in (0, 1):
        round_calls = [row for row in calls if row.get('round') == round_number]
        if (len(round_calls) != len(valid_cases) * 2
                or len({round_calls[index]['case'] for index in range(0, len(round_calls), 2)})
                != len(valid_cases)):
            raise ValueError('WCXB paired latency round is incomplete')
        for index in range(0, len(round_calls), 2):
            first, second = round_calls[index:index + 2]
            if (first['case'] != second['case'] or first.get('order') != 1
                    or second.get('order') != 2 or first['method'] == second['method']):
                raise ValueError('WCXB physical call order differs from declared pairs')
    for case in valid_cases:
        for round_number in (0, 1):
            if {lookup[case, round_number, method].get('order') for method in expected_bins} != {1, 2}:
                raise ValueError('WCXB paired-call order is invalid')
            for method in expected_bins:
                row = lookup[case, round_number, method]
                elapsed = row.get('elapsed_ms')
                if (row.get('ok') is not True or row.get('error_type') is not None
                        or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed)
                        or elapsed <= 0 or not isinstance(row.get('output_sha256'), str)
                        or len(row['output_sha256']) != 64):
                    raise ValueError('WCXB full paired latency has failed or invalid calls')
                if (lookup[case, 0, method]['output_sha256']
                        != lookup[case, 1, method]['output_sha256']):
                    raise ValueError('WCXB paired-call output changed between rounds')
        if lookup[case, 0, 'release']['order'] == lookup[case, 1, 'release']['order']:
            raise ValueError('WCXB call order was not reversed for a case')
    selected = {(case, method): lookup[case, 1, method] for case in valid_cases for method in expected_bins}
    return receipt, selected


def summarize_full_paired_latency(selected: dict, cases: set[str]) -> dict:
    metrics = {}
    for method in ('release', 'pr'):
        values = [selected[case, method]['elapsed_ms'] for case in cases]
        metrics[method] = {'mean_ms': statistics.fmean(values),
                           'p50_ms': statistics.median(values)}
    deltas = [selected[case, 'pr']['elapsed_ms'] - selected[case, 'release']['elapsed_ms']
              for case in cases]
    metrics['paired_delta_mean_ms'] = statistics.fmean(deltas)
    metrics['paired_delta_p50_ms'] = statistics.median(deltas)
    metrics['candidate_slower_cases'] = sum(value > 0 for value in deltas)
    metrics['case_count'] = len(cases)
    return metrics
