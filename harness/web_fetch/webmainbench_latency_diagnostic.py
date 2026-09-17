"""Verify and summarize append-only Moli latency reruns without changing scored results."""
from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .metrics import percentile
from .safety import contained_path
from .webmainbench_diagnostics import ERROR_PAGE
from .webmainbench_evidence import retry_chain_matches


RUNS = {'moli-115-control': 'moli', 'moli-117-repeat': 'moli-1.1.7'}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def stats(values: list[float]) -> dict:
    return {'n': len(values), 'mean': statistics.fmean(values),
            'p50': statistics.median(values), 'p95': percentile(values, .95)}


def _resource_samples(path: Path, execution: dict) -> dict:
    start = datetime.fromisoformat(execution['started_at'])
    end = datetime.fromisoformat(execution['completed_at'])
    samples = []
    current = None
    for line in path.read_text(encoding='utf-8').splitlines():
        if re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', line):
            current = {'time': datetime.fromisoformat(line.replace('Z', '+00:00'))}
            if start <= current['time'] <= end:
                samples.append(current)
        elif current is not None:
            if match := re.search(r'([\d.]+)% idle', line):
                current['idle_cpu_pct'] = float(match.group(1))
            if match := re.search(r'([\d.]+)([MG]) unused', line):
                current['unused_memory_mb'] = float(match.group(1)) * (1024 if match.group(2) == 'G' else 1)
            if match := re.search(r'^Swapouts:\s+(\d+)', line):
                current['swapouts'] = int(match.group(1))
            if match := re.search(r'^Swapins:\s+(\d+)', line):
                current['swapins'] = int(match.group(1))
    complete = [item for item in samples if all(key in item for key in
                ('idle_cpu_pct', 'unused_memory_mb', 'swapouts', 'swapins'))]
    if len(complete) < 3:
        raise ValueError('Too few complete system samples for the latency diagnostic')
    return {'samples': len(complete),
            'min_idle_cpu_pct': min(x['idle_cpu_pct'] for x in complete),
            'median_idle_cpu_pct': statistics.median(x['idle_cpu_pct'] for x in complete),
            'min_unused_memory_mb': min(x['unused_memory_mb'] for x in complete),
            'swapouts_delta': complete[-1]['swapouts'] - complete[0]['swapouts'],
            'swapins_delta': complete[-1]['swapins'] - complete[0]['swapins']}


def load_diagnostic(source: Path, dataset: dict, official_records: list[dict], manifests: list[dict]) -> dict | None:
    root = source / 'latency-diagnostic'
    if not root.exists():
        return None
    manifest = json.loads(contained_path(root, 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('kind') != 'moli_115_117_latency_rerun_v1':
        raise ValueError('Unrecognized Moli latency diagnostic')
    official = {(row['provider'], row['case_id']): row for row in official_records}
    cases = {case['id']: case for case in dataset['cases']}
    original_manifests = {row['provider']: row for row in manifests}
    if manifest.get('official_results_sha256') != digest(source / 'results.jsonl'):
        raise ValueError('Latency diagnostic does not bind the unchanged official matrix')
    if manifest.get('system_monitor_sha256') != digest(contained_path(root, 'system-monitor.log')):
        raise ValueError('System monitor hash mismatch')
    all_rows = {}
    all_executions = {}
    resource = {}
    for label, provider in RUNS.items():
        run = contained_path(root, label)
        binding = manifest['runs'][label]
        for filename in ('execution.json', 'results.jsonl', 'physical-attempts.jsonl'):
            if binding.get(filename + '_sha256') != digest(contained_path(run, filename)):
                raise ValueError('Latency run file hash mismatch: ' + label + '/' + filename)
        execution = json.loads((run / 'execution.json').read_text(encoding='utf-8'))
        original_execution = original_manifests[provider]
        if (execution['provider'] != provider or execution['case_count'] != len(cases)
                or execution['source_dataset_sha256'] != dataset['source_dataset_sha256']
                or execution['selection_manifest']['sha256'] != dataset['sample_manifest_sha256']
                or execution['timeout_seconds'] != original_execution['timeout_seconds']
                or execution['max_failure_retries'] != original_execution['max_failure_retries']
                or execution['implementation_identity']['binary_sha256'] !=
                   original_execution['implementation_identity']['binary_sha256']):
            raise ValueError('Latency run changed input, binary, or execution contract: ' + label)
        rows = read_jsonl(run / 'results.jsonl')
        physical = defaultdict(list)
        for attempt in read_jsonl(run / 'physical-attempts.jsonl'):
            physical[attempt['track_id']].append(attempt)
        if len(rows) != len(cases) or {row['track_id'] for row in rows} != set(cases) or set(physical) != set(cases):
            raise ValueError('Latency rerun is missing declared cases: ' + label)
        by_case = {}
        for row in rows:
            case_id = row['track_id']
            if (row['provider'] != provider or row['input_html_sha256'] != cases[case_id]['input_html_sha256']
                    or row['reference_sha256'] != cases[case_id]['reference_sha256']
                    or not retry_chain_matches(row, physical[case_id])
                    or digest(contained_path(run, row['artifact_path'])) != row['output_sha256']):
                raise ValueError('Latency rerun result is unbound: ' + label + '/' + case_id)
            text = contained_path(run, row['artifact_path']).read_text(encoding='utf-8')
            by_case[case_id] = {**row, '_body_present': bool(row['ok'] and text.strip() and not ERROR_PAGE.search(text))}
        all_rows[label] = by_case
        all_executions[label] = execution
        resource[label] = _resource_samples(root / 'system-monitor.log', execution)
    if (all_executions['moli-115-control']['runner_sha256'] !=
            all_executions['moli-117-repeat']['runner_sha256']):
        raise ValueError('Moli reruns used different runner implementations')
    run_rows = {}
    for label, provider in RUNS.items():
        candidates = all_rows[label].values()
        values = [row['elapsed_ms'] for row in candidates if row['_body_present']]
        run_rows[label] = {'provider': provider, 'run_id': all_executions[label]['run_id'],
                           'binary_sha256': all_executions[label]['implementation_identity']['binary_sha256'],
                           'runner_sha256': all_executions[label]['runner_sha256'],
                           **stats(values), 'success_case_ids': sorted(row['track_id'] for row in candidates if row['_body_present'])}
    for label, provider in [('moli-115-original', 'moli'), ('moli-117-original', 'moli-1.1.7')]:
        rows = [official[provider, case_id] for case_id in cases]
        values = [row['elapsed_ms'] for row in rows if row['ok'] and
                  contained_path(source, row['artifact_path']).read_text(encoding='utf-8').strip() and
                  not ERROR_PAGE.search(contained_path(source, row['artifact_path']).read_text(encoding='utf-8'))]
        run_rows[label] = {'provider': provider, 'run_id': original_manifests[provider]['run_id'],
                           'binary_sha256': original_manifests[provider]['implementation_identity']['binary_sha256'],
                           'runner_sha256': original_manifests[provider]['runner_sha256'], **stats(values)}
    common = sorted(set(run_rows['moli-115-control']['success_case_ids']) &
                    set(run_rows['moli-117-repeat']['success_case_ids']))
    differences = [all_rows['moli-117-repeat'][case_id]['elapsed_ms'] -
                   all_rows['moli-115-control'][case_id]['elapsed_ms'] for case_id in common]
    sorted_differences = sorted(differences, reverse=True)
    mechanism = contained_path(root, 'mechanism')
    if (manifest['mechanism']['selection_json_sha256'] != digest(mechanism / 'selection.json')
            or manifest['mechanism']['calls_jsonl_sha256'] != digest(mechanism / 'calls.jsonl')):
        raise ValueError('Mechanism probe hash mismatch')
    selection = json.loads((mechanism / 'selection.json').read_text(encoding='utf-8'))
    calls = read_jsonl(mechanism / 'calls.jsonl')
    selected = selection['case_ids']
    if (len(selected) != 40 or len(set(selected)) != 40 or not set(selected).issubset(cases)
            or len(calls) != 4 * len(selected)
            or selection['prior_results_sha256'] != {
                '1.1.5': manifest['runs']['moli-115-control']['results.jsonl_sha256'],
                '1.1.7': manifest['runs']['moli-117-repeat']['results.jsonl_sha256']}
            or selection['binaries'] != {
                '1.1.5': {'version': '1.1.5', 'sha256': run_rows['moli-115-control']['binary_sha256']},
                '1.1.7': {'version': '1.1.7', 'sha256': run_rows['moli-117-repeat']['binary_sha256']}}):
        raise ValueError('Mechanism probe population or identity mismatch')
    probe = {}
    for row in calls:
        key = (row['case_id'], row['version'], row['mode'])
        artifact = contained_path(mechanism, f'outputs/{row["version"]}/{row["mode"]}/{row["case_id"]}.txt')
        if (key in probe or row['case_id'] not in selected
                or row['input_html_sha256'] != cases[row['case_id']]['input_html_sha256']
                or digest(artifact) != row['output_sha256']):
            raise ValueError('Mechanism call binding failed')
        probe[key] = row
    expected = {(case_id, version, mode) for case_id in selected
                for version in ('1.1.5', '1.1.7') for mode in ('html', 'markdown')}
    if set(probe) != expected:
        raise ValueError('Mechanism call matrix incomplete')
    valid = [case_id for case_id in selected if all(probe[case_id, version, mode]['exit_code'] == 0
             for version in ('1.1.5', '1.1.7') for mode in ('html', 'markdown'))]
    probe_differences = {mode: [probe[case_id, '1.1.7', mode]['elapsed_ms'] -
                                probe[case_id, '1.1.5', mode]['elapsed_ms'] for case_id in valid]
                         for mode in ('html', 'markdown')}
    return {'kind': 'same_host_latency_diagnostic', 'official_results_sha256': manifest['official_results_sha256'],
            'runs': run_rows, 'resource': resource,
            'paired': {'n': len(common), 'new_slower': sum(x > 0 for x in differences),
                       'new_faster': sum(x < 0 for x in differences),
                       'mean_difference_ms': statistics.fmean(differences),
                       'median_difference_ms': statistics.median(differences),
                       'top_20_share_of_net_difference': sum(sorted_differences[:20]) / sum(differences),
                       'older_over_500_ms': sum(all_rows['moli-115-control'][cid]['elapsed_ms'] > 500 for cid in common),
                       'newer_over_500_ms': sum(all_rows['moli-117-repeat'][cid]['elapsed_ms'] > 500 for cid in common)},
            'mechanism': {'selected': len(selected), 'valid_pairs': len(valid),
                          'markdown_median_difference_ms': statistics.median(probe_differences['markdown']),
                          'html_median_difference_ms': statistics.median(probe_differences['html'])},
            'per_case': [{
                'case_id': case_id,
                'original_115_ms': official['moli', case_id]['elapsed_ms'],
                'original_117_ms': official['moli-1.1.7', case_id]['elapsed_ms'],
                'repeat_115_ms': all_rows['moli-115-control'][case_id]['elapsed_ms'],
                'repeat_117_ms': all_rows['moli-117-repeat'][case_id]['elapsed_ms'],
                'repeat_115_ok': all_rows['moli-115-control'][case_id]['_body_present'],
                'repeat_117_ok': all_rows['moli-117-repeat'][case_id]['_body_present'],
            } for case_id in sorted(cases)]}
