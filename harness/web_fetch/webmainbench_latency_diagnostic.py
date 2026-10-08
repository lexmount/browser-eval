"""Verify and summarize append-only Moli latency reruns without changing scored results."""
from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from harness.record_io import load_jsonl, parse_jsonl

from .metrics import percentile
from .safety import contained_path
from .webmainbench_diagnostics import ERROR_PAGE
from .webmainbench_evidence import retry_chain_matches
from .webmainbench_latency_correction import prior_results_path

RUNS = {'moli-115-control': 'moli', 'moli-117-repeat': 'moli-1.1.7'}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified_bytes(root: Path, filename: str, expected: str) -> bytes:
    data = contained_path(root, filename).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Latency diagnostic file hash mismatch: ' + filename)
    return data


def read_jsonl(path: Path) -> list[dict]:
    return load_jsonl(path, skip_blank=True)


def stats(values: list[float]) -> dict:
    if not values:
        raise ValueError('Latency diagnostic has no successful timed pages')
    return {'n': len(values), 'mean': statistics.fmean(values),
            'p50': statistics.median(values), 'p95': percentile(values, .95)}


def top_positive_gap_share(differences: list[float]) -> float | None:
    net = sum(differences)
    return sum(sorted(differences, reverse=True)[:20]) / net if net > 0 else None


def _repeat_calls(root: Path, binding: dict, cases: dict, source_sha256: str,
                  sample_sha256: str, prior_sha256: dict, binaries: dict,
                  expected_count: int, expected_rounds: int) -> tuple[dict, list[dict]]:
    directory = contained_path(root, binding['directory'])
    selection = json.loads(verified_bytes(directory, 'selection.json', binding['selection_json_sha256']))
    calls = parse_jsonl(verified_bytes(directory, 'calls.jsonl',
             binding['calls_jsonl_sha256']), skip_blank=True)
    selected = selection['case_ids']
    if (len(selected) != expected_count or len(set(selected)) != expected_count
            or not set(selected).issubset(cases) or selection['rounds'] != expected_rounds
            or len(calls) != 2 * expected_count * expected_rounds
            or {version: selection['binaries'][version]['sha256'] for version in binaries} != binaries
            or selection.get('source_dataset_sha256', selection.get('dataset_sha256')) != source_sha256
            or selection['sample_sha256'] != sample_sha256
            or selection.get('prior_results_sha256', selection.get('prior_sha256')) != prior_sha256):
        raise ValueError('Moli repeat selection or binary identity changed')
    seen = set()
    for row in calls:
        key = (row['case_id'], row['version'], row['round'])
        if (key in seen or row['case_id'] not in selected or row['version'] not in binaries
                or not isinstance(row['round'], int) or not 1 <= row['round'] <= expected_rounds
                or row['input_html_sha256'] != cases[row['case_id']]['input_html_sha256']):
            raise ValueError('Moli repeat call is outside the frozen matrix')
        seen.add(key)
        artifact = verified_bytes(directory,
                                  f'outputs/{row["round"]}/{row["version"]}/{row["case_id"]}.md',
                                  row['output_sha256'])
        if row['output_bytes'] != len(artifact):
            raise ValueError('Moli repeat output length disagrees with evidence')
        row['_body_present'] = bool(row['exit_code'] == 0 and artifact.strip()
                                    and not ERROR_PAGE.search(artifact.decode('utf-8', 'replace')))
    expected = {(case_id, version, round_index)
                for case_id in selected for version in binaries
                for round_index in range(1, expected_rounds + 1)}
    if seen != expected:
        raise ValueError('Moli repeat call matrix is incomplete')
    return selection, calls


def _resource_samples(path: Path | str, execution: dict) -> dict:
    start = datetime.fromisoformat(execution['started_at'])
    end = datetime.fromisoformat(execution['completed_at'])
    samples = []
    current = None
    content = path.read_text(encoding='utf-8') if isinstance(path, Path) else path
    for line in content.splitlines():
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


def load_diagnostic(source: Path, dataset: dict, official_records: list[dict], manifests: list[dict],
                    expected_manifest_sha256: str | None = None) -> dict | None:
    root = source / 'latency-diagnostic'
    if not root.exists():
        return None
    manifest_bytes = contained_path(root, 'manifest.json').read_bytes()
    if expected_manifest_sha256 is None:
        profile_path = Path(__file__).resolve().parents[2] / 'evaluation/current-report.json'
        profile = json.loads(profile_path.read_text(encoding='utf-8'))
        expected_manifest_sha256 = profile.get('latency_diagnostic', {}).get('manifest_sha256')
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
        raise ValueError('Latency diagnostic manifest differs from the current report profile')
    manifest = json.loads(manifest_bytes)
    if manifest.get('kind') != 'moli_115_117_latency_rerun_v1':
        raise ValueError('Unrecognized Moli latency diagnostic')
    cases = {case['id']: case for case in dataset['cases']}
    original_manifests = {row['provider']: row for row in manifests}
    if manifest.get('official_results_sha256') != digest(prior_results_path(source)):
        raise ValueError('Latency diagnostic does not bind the preserved original matrix')
    monitor_data = verified_bytes(root, 'system-monitor.log', manifest.get('system_monitor_sha256'))
    all_rows = {}
    all_executions = {}
    resource = {}
    for label, provider in RUNS.items():
        run = contained_path(root, label)
        binding = manifest['runs'][label]
        files = {name: verified_bytes(run, name, binding.get(name + '_sha256')) for name in
                 ('execution.json', 'results.jsonl', 'physical-attempts.jsonl')}
        execution = json.loads(files['execution.json'])
        original_execution = original_manifests[provider]
        if (execution['provider'] != provider or execution['case_count'] != len(cases)
                or execution['source_dataset_sha256'] != dataset['source_dataset_sha256']
                or execution['selection_manifest']['sha256'] != dataset['sample_manifest_sha256']
                or execution['timeout_seconds'] != original_execution['timeout_seconds']
                or execution['max_failure_retries'] != original_execution['max_failure_retries']
                or execution['implementation_identity']['binary_sha256'] !=
                   original_execution['implementation_identity']['binary_sha256']):
            raise ValueError('Latency run changed input, binary, or execution contract: ' + label)
        rows = parse_jsonl(files['results.jsonl'], skip_blank=True)
        physical = defaultdict(list)
        for attempt in iter(parse_jsonl(files['physical-attempts.jsonl'], skip_blank=True)):
            physical[attempt['track_id']].append(attempt)
        if len(rows) != len(cases) or {row['track_id'] for row in rows} != set(cases) or set(physical) != set(cases):
            raise ValueError('Latency rerun is missing declared cases: ' + label)
        by_case = {}
        for row in rows:
            case_id = row['track_id']
            artifact_data = verified_bytes(run, row['artifact_path'], row['output_sha256'])
            if (row['provider'] != provider or row['input_html_sha256'] != cases[case_id]['input_html_sha256']
                    or row['reference_sha256'] != cases[case_id]['reference_sha256']
                    or not retry_chain_matches(row, physical[case_id])):
                raise ValueError('Latency rerun result is unbound: ' + label + '/' + case_id)
            text = artifact_data.decode('utf-8')
            by_case[case_id] = {**row, '_body_present': bool(row['ok'] and text.strip() and not ERROR_PAGE.search(text))}
        all_rows[label] = by_case
        all_executions[label] = execution
        resource[label] = _resource_samples(monitor_data.decode('utf-8'), execution)
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
    common = sorted(set(run_rows['moli-115-control']['success_case_ids']) &
                    set(run_rows['moli-117-repeat']['success_case_ids']))
    if not common:
        raise ValueError('Moli reruns have no common successful pages')
    differences = [all_rows['moli-117-repeat'][case_id]['elapsed_ms'] -
                   all_rows['moli-115-control'][case_id]['elapsed_ms'] for case_id in common]
    mechanism = contained_path(root, 'mechanism')
    selection = json.loads(verified_bytes(mechanism, 'selection.json', manifest['mechanism']['selection_json_sha256']))
    calls = parse_jsonl(verified_bytes(mechanism, 'calls.jsonl',
             manifest['mechanism']['calls_jsonl_sha256']), skip_blank=True)
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
        artifact_name = f'outputs/{row["version"]}/{row["mode"]}/{row["case_id"]}.txt'
        if (key in probe or row['case_id'] not in selected
                or row['input_html_sha256'] != cases[row['case_id']]['input_html_sha256']):
            raise ValueError('Mechanism call binding failed')
        verified_bytes(mechanism, artifact_name, row['output_sha256'])
        probe[key] = row
    expected = {(case_id, version, mode) for case_id in selected
                for version in ('1.1.5', '1.1.7') for mode in ('html', 'markdown')}
    if set(probe) != expected:
        raise ValueError('Mechanism call matrix incomplete')
    valid = [case_id for case_id in selected if all(probe[case_id, version, mode]['exit_code'] == 0
             for version in ('1.1.5', '1.1.7') for mode in ('html', 'markdown'))]
    if not valid:
        raise ValueError('Moli mechanism probe has no complete successful pairs')
    probe_differences = {mode: [probe[case_id, '1.1.7', mode]['elapsed_ms'] -
                                probe[case_id, '1.1.5', mode]['elapsed_ms'] for case_id in valid]
                         for mode in ('html', 'markdown')}
    failure = json.loads(verified_bytes(root, 'failure-retest/summary.json',
                         manifest['failure_retest']['summary_json_sha256']))
    failed_case = failure.get('case_id')
    if failure.get('kind') != 'moli_origin_redirect_retry_diagnostic_v1' or failed_case not in cases:
        raise ValueError('Moli failed-page retest has an invalid case binding')
    for version, label in (('1.1.5', 'moli-115-control'), ('1.1.7', 'moli-117-repeat')):
        item = failure['runs'][version]
        run = contained_path(root, 'failure-retest/' + version)
        files = {name: verified_bytes(run, name, digest_value) for name, digest_value in item['files'].items()}
        execution = json.loads(files['execution.json'])
        rows = parse_jsonl(files['results.jsonl'], skip_blank=True)
        attempts = parse_jsonl(files['physical-attempts.jsonl'], skip_blank=True)
        if (len(rows) != 1 or len(attempts) != 2 or rows[0]['track_id'] != failed_case
                or rows[0]['provider'] != RUNS[label] or rows[0]['ok'] or any(attempt['ok'] for attempt in attempts)
                or not retry_chain_matches(rows[0], attempts)
                or rows[0]['input_html_sha256'] != cases[failed_case]['input_html_sha256']
                or execution['implementation_identity']['binary_sha256'] != run_rows[label]['binary_sha256']
                or item['binary_sha256'] != run_rows[label]['binary_sha256']
                or item['attempts'] != 2 or item['result_ok'] is not False
                or item['error_type'] != rows[0]['error_type']
                or 'langrensha.163.com/wanfa/guize/' not in rows[0]['error_message']):
            raise ValueError('Moli failed-page retest did not reproduce the bound origin failure')
        disabled = failure['disable_page_js'][version]
        output = verified_bytes(root, f'failure-retest/disable-js/{version}.md', disabled['output_sha256'])
        verified_bytes(root, f'failure-retest/disable-js/{version}.stderr', disabled['stderr_sha256'])
        if disabled['exit_code'] != 0 or not output.strip() or disabled['output_bytes'] != len(output):
            raise ValueError('Moli script-disabled control did not return body text')
    binaries = {'1.1.5': run_rows['moli-115-control']['binary_sha256'],
                '1.1.7': run_rows['moli-117-repeat']['binary_sha256']}
    initial_gaps = sorted(common, key=lambda case_id:
                          all_rows['moli-117-repeat'][case_id]['elapsed_ms'] -
                          all_rows['moli-115-control'][case_id]['elapsed_ms'], reverse=True)
    tail_calls = defaultdict(list)
    for key in ('tail_repeat_r1', 'tail_repeat_r2'):
        tail_selection, repeated = _repeat_calls(root, manifest[key], cases,
                                                  dataset['source_dataset_sha256'],
                                                  dataset['sample_manifest_sha256'],
                                                  {'1.1.5': manifest['runs']['moli-115-control']['results.jsonl_sha256'],
                                                   '1.1.7': manifest['runs']['moli-117-repeat']['results.jsonl_sha256']},
                                                  binaries,
                                                  expected_count=30,
                                                  expected_rounds=5 if key.endswith('r1') else 10)
        if tail_selection['slow_case_ids'] != initial_gaps[:20]:
            raise ValueError('Moli tail repeat selected different slow pages')
        for row in repeated:
            if not row['_body_present']:
                raise ValueError('Moli slow-page repeat failed to return body text')
            tail_calls[row['case_id'], row['version']].append(row)
    tail_cases = []
    for case_id in initial_gaps[:20]:
        older = tail_calls[case_id, '1.1.5']
        newer = tail_calls[case_id, '1.1.7']
        if len(older) != 15 or len(newer) != 15 or any(len({row['output_sha256'] for row in group}) != 1
                                                  for group in (older, newer)):
            raise ValueError('Moli slow-page repeated output is incomplete or inconsistent')
        old_median = statistics.median(row['elapsed_ms'] for row in older)
        new_median = statistics.median(row['elapsed_ms'] for row in newer)
        tail_cases.append({'case_id': case_id, 'url': cases[case_id]['url'],
                           'initial_gap_ms': all_rows['moli-117-repeat'][case_id]['elapsed_ms'] -
                                             all_rows['moli-115-control'][case_id]['elapsed_ms'],
                           'older_median_ms': old_median, 'newer_median_ms': new_median,
                           'older_max_ms': max(row['elapsed_ms'] for row in older),
                           'newer_max_ms': max(row['elapsed_ms'] for row in newer),
                           'repetitions_per_version': 15})
    balanced_selection, balanced_calls = _repeat_calls(
        root, manifest['balanced_200'], cases, dataset['source_dataset_sha256'],
        dataset['sample_manifest_sha256'],
        {'1.1.5': manifest['runs']['moli-115-control']['results.jsonl_sha256'],
         '1.1.7': manifest['runs']['moli-117-repeat']['results.jsonl_sha256']}, binaries,
        expected_count=len(cases), expected_rounds=2)
    if (set(balanced_selection['case_ids']) != set(cases)
            or balanced_selection['slow_case_ids'] != initial_gaps[:20]):
        raise ValueError('Balanced Moli crossover changed the 200-page scope')
    first_versions = defaultdict(list)
    first_minus_second = []
    for first, second in zip(balanced_calls[::2], balanced_calls[1::2]):
        if (first['case_id'] != second['case_id'] or first['round'] != second['round']
                or first['version'] == second['version']):
            raise ValueError('Balanced Moli calls are not serial page pairs')
        first_versions[first['case_id']].append(first['version'])
        if first['_body_present'] and second['_body_present']:
            first_minus_second.append(first['elapsed_ms'] - second['elapsed_ms'])
    if any(sorted(order) != ['1.1.5', '1.1.7'] for order in first_versions.values()):
        raise ValueError('Balanced Moli version order was not reversed per page')
    balanced_runs = {}
    for version in binaries:
        relevant = [row for row in balanced_calls if row['version'] == version]
        success = [row['elapsed_ms'] for row in relevant if row['_body_present']]
        failures = [row for row in relevant if not row['_body_present']]
        if (len(relevant) != 2 * len(cases) or len(failures) != 2
                or {row['case_id'] for row in failures} != {failed_case}
                or any('langrensha.163.com/wanfa/guize/' not in row['stderr'] for row in failures)):
            raise ValueError('Balanced Moli success/failure population changed')
        balanced_runs[version] = {**stats(success), 'calls': len(relevant),
                                  'failed_calls': len(failures)}
    return {'kind': 'same_host_latency_diagnostic', 'official_results_sha256': manifest['official_results_sha256'],
            'runs': run_rows, 'resource': resource,
            'tail_repeat': {'cases': tail_cases,
                            'within_20_ms': sum(abs(row['newer_median_ms'] - row['older_median_ms']) <= 20
                                                for row in tail_cases)},
            'balanced_200': {'runs': balanced_runs, 'paired_success_calls': len(first_minus_second),
                             'first_call_extra_mean_ms': statistics.fmean(first_minus_second),
                             'first_call_slower_count': sum(value > 0 for value in first_minus_second)},
            'failure_retest': {'case_id': failed_case, 'attempts_per_version': 2,
                               'same_origin_failure': True, 'script_disabled_returns_body': True},
            'paired': {'n': len(common), 'new_slower': sum(x > 0 for x in differences),
                       'new_faster': sum(x < 0 for x in differences),
                       'mean_difference_ms': statistics.fmean(differences),
                       'median_difference_ms': statistics.median(differences),
                       'top_20_share_of_net_difference': top_positive_gap_share(differences),
                       'older_over_500_ms': sum(all_rows['moli-115-control'][cid]['elapsed_ms'] > 500 for cid in common),
                       'newer_over_500_ms': sum(all_rows['moli-117-repeat'][cid]['elapsed_ms'] > 500 for cid in common)},
            'mechanism': {'selected': len(selected), 'valid_pairs': len(valid),
                          'markdown_median_difference_ms': statistics.median(probe_differences['markdown']),
                          'html_median_difference_ms': statistics.median(probe_differences['html'])},
            'per_case': [{
                'case_id': case_id,
                'repeat_115_ms': all_rows['moli-115-control'][case_id]['elapsed_ms'],
                'repeat_117_ms': all_rows['moli-117-repeat'][case_id]['elapsed_ms'],
                'repeat_115_ok': all_rows['moli-115-control'][case_id]['_body_present'],
                'repeat_117_ok': all_rows['moli-117-repeat'][case_id]['_body_present'],
            } for case_id in sorted(cases)]}
