"""Validate the separate, same-window WCXB latency diagnostic."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path


def load_paired_latency(path: Path, profile: dict, valid_cases: set[str]) -> tuple[dict, dict]:
    if path.is_symlink() or not path.is_file():
        raise ValueError('WCXB paired latency evidence is missing or linked')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != profile['latency_diagnostic_sha256']:
        raise ValueError('WCXB paired latency evidence hash differs from profile')
    data = json.loads(raw)
    expected_bins = {
        'release': profile['release_binary_sha256'],
        'pr': profile['candidate_binary_sha256'],
    }
    if (data.get('kind') != 'diagnostic_paired_crossover'
            or data.get('rounds') != 2 or data.get('case_count') != 64
            or data.get('source_sha256') != profile['source_dataset_sha256']
            or data.get('binary_sha256') != expected_bins):
        raise ValueError('WCXB paired latency protocol or identity differs')
    rows = data.get('results')
    if not isinstance(rows, list) or len(rows) != 256:
        raise ValueError('WCXB paired latency call inventory is incomplete')
    cases = {row.get('case') for row in rows if isinstance(row, dict)}
    if len(cases) != 64 or not cases <= valid_cases:
        raise ValueError('WCXB paired latency cases differ from valid cohort')
    expected = {(case, round_number, method)
                for case in cases for round_number in (0, 1) for method in expected_bins}
    identities = [(row.get('case'), row.get('round'), row.get('method')) for row in rows]
    if set(identities) != expected or len(set(identities)) != len(rows):
        raise ValueError('WCXB paired latency calls are missing or duplicated')
    for row in rows:
        elapsed = row.get('elapsed_ms')
        if (row.get('exit') != 0 or not isinstance(elapsed, (int, float))
                or not math.isfinite(elapsed) or elapsed <= 0
                or not isinstance(row.get('output_sha256'), str)
                or len(row['output_sha256']) != 64):
            raise ValueError('WCXB paired latency call failed validation')
    lookup = {(row['case'], row['round'], row['method']): row for row in rows}
    if any(lookup[case, 0, method]['output_sha256']
           != lookup[case, 1, method]['output_sha256']
           for case in cases for method in expected_bins):
        raise ValueError('WCXB paired latency output changed between rounds')
    deltas = [lookup[case, round_number, 'pr']['elapsed_ms']
              - lookup[case, round_number, 'release']['elapsed_ms']
              for case in sorted(cases) for round_number in (0, 1)]
    metrics = {method: {
        'mean_ms': statistics.fmean(row['elapsed_ms'] for row in rows if row['method'] == method),
        'p50_ms': statistics.median(row['elapsed_ms'] for row in rows if row['method'] == method),
    } for method in expected_bins}
    metrics['paired_delta_mean_ms'] = statistics.fmean(deltas)
    metrics['paired_delta_p50_ms'] = statistics.median(deltas)
    return data, metrics
