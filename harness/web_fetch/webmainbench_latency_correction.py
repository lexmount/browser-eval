"""Verify a latency-only supersession without rewriting scored provider evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .safety import contained_path


def prior_results_path(root: Path) -> Path:
    if (root / 'latency-corrections.json').is_file():
        validate_latency_correction(root)
        return contained_path(root, 'prior-latency-results.jsonl')
    return contained_path(root, 'results.jsonl')


def validate_latency_correction(root: Path) -> dict | None:
    receipt_path = root / 'latency-corrections.json'
    if not receipt_path.is_file():
        return None
    receipt = json.loads(contained_path(root, 'latency-corrections.json').read_text(encoding='utf-8'))
    prior_path = contained_path(root, 'prior-latency-results.jsonl')
    current_path = contained_path(root, 'results.jsonl')
    prior_bytes = prior_path.read_bytes()
    current_bytes = current_path.read_bytes()
    if (receipt.get('schema_version') != 1
            or receipt.get('kind') != 'webmainbench_moli_200_balanced_latency_only_v1'
            or receipt.get('prior_results_sha256') != hashlib.sha256(prior_bytes).hexdigest()
            or receipt.get('output_results_sha256') != hashlib.sha256(current_bytes).hexdigest()
            or receipt.get('selected_round') != 2
            or receipt.get('changed_result_count') != 400):
        raise ValueError('Invalid WebMainBench latency correction binding')
    previous = [json.loads(line) for line in prior_bytes.decode('utf-8').splitlines() if line.strip()]
    current = [json.loads(line) for line in current_bytes.decode('utf-8').splitlines() if line.strip()]
    if len(previous) != len(current) or len(current) != 1200:
        raise ValueError('WebMainBench latency correction changed result inventory')
    changes = receipt.get('changes')
    if not isinstance(changes, list) or len(changes) != 400:
        raise ValueError('WebMainBench latency correction has incomplete scope')
    by_attempt = {item.get('attempt_id'): item for item in changes}
    if len(by_attempt) != 400:
        raise ValueError('WebMainBench latency correction duplicated an attempt')
    expected = set()
    for old, new in zip(previous, current):
        if old.get('attempt_id') != new.get('attempt_id'):
            raise ValueError('WebMainBench latency correction reordered results')
        changed = old.get('provider') in ('moli', 'moli-1.1.7')
        if changed:
            expected.add(old['attempt_id'])
            before_metadata = old.get('provider_metadata', {})
            after_metadata = new.get('provider_metadata', {})
            correction = after_metadata.get('latency_correction', {})
            change = by_attempt.get(old['attempt_id'], {})
            if (correction.get('kind') != 'certified_balanced_final_round'
                    or correction.get('selected_round') != 2
                    or correction.get('original_elapsed_ms') != old['elapsed_ms']
                    or correction.get('corrected_elapsed_ms') != new['elapsed_ms']
                    or change.get('before_ms') != old['elapsed_ms']
                    or change.get('after_ms') != new['elapsed_ms']
                    or change.get('attempt_id') != old['attempt_id']
                    or {key: value for key, value in after_metadata.items()
                        if key != 'latency_correction'} != before_metadata):
                raise ValueError('WebMainBench latency correction metadata mismatch')
            before = {key: value for key, value in old.items() if key != 'elapsed_ms'}
            after = {key: value for key, value in new.items()
                     if key not in ('elapsed_ms', 'provider_metadata')}
            before.pop('provider_metadata', None)
            if before != after:
                raise ValueError('WebMainBench latency correction changed scored evidence')
        elif old != new:
            raise ValueError('WebMainBench latency correction changed another provider')
    if set(by_attempt) != expected:
        raise ValueError('WebMainBench latency correction omitted a Moli result')
    return receipt
