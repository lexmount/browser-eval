"""Freeze WebMainBench references around a predeclared, balanced Moli latency correction."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.webmainbench_latency_diagnostic import _repeat_calls
from scripts.run_webmainbench_benchmark import load_selected_ids, load_source


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def competing_benchmark_pids() -> list[int]:
    """Reject another browser benchmark sharing this host during timing calls."""
    listing = subprocess.run(['ps', '-axo', 'pid=,command='], check=True,
                             capture_output=True, text=True).stdout
    markers = ('-m harness.browser_use.runner', '-m harness.trajectory_replay.',
               'scripts/run_webmainbench_benchmark.py', 'scripts/run_benchmark.py')
    processes = [line.strip().split(None, 1) for line in listing.splitlines() if line.strip()]
    return [int(pid) for pid, command in processes if
            not command.startswith(('zsh -c ', 'bash -lc ', '/bin/sh -c '))
            and any(marker in command for marker in markers)]


def snapshot(dataset: Path, sample: Path, official: Path) -> dict:
    selected, binding = load_selected_ids(sample)
    source = {row['track_id']: row for row in load_source(dataset, selected)}
    recorded = json.loads((official / 'dataset.json').read_text(encoding='utf-8'))
    cases = {row['id']: row for row in recorded['cases']}
    if (len(source) != 200 or set(source) != set(cases)
            or binding['sha256'] != recorded['sample_manifest_sha256']):
        raise ValueError('Frozen WebMainBench scope changed')
    for case_id, row in source.items():
        if (hashlib.sha256(row['html'].encode()).hexdigest() != cases[case_id]['input_html_sha256']
                or hashlib.sha256(row['groundtruth_content'].encode()).hexdigest() !=
                cases[case_id]['reference_sha256']
                or digest(official / 'references' / f'{case_id}.md') != cases[case_id]['reference_sha256']):
            raise ValueError('Frozen input or human reference changed: ' + case_id)
    return {'source_dataset_sha256': digest(dataset), 'sample_sha256': digest(sample),
            'official_dataset_sha256': digest(official / 'dataset.json'),
            'reference_sha256': {case_id: cases[case_id]['reference_sha256'] for case_id in sorted(cases)},
            'input_html_sha256': {case_id: cases[case_id]['input_html_sha256'] for case_id in sorted(cases)}}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('older', 'newer', 'prior-older', 'prior-newer', 'dataset', 'sample',
                 'official-evidence', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError('Certified output directory already exists')
    competing = competing_benchmark_pids()
    if competing:
        raise ValueError(f'Concurrent browser benchmark is active (PIDs {competing}); '
                         'do not collect comparable latency now')
    before = snapshot(args.dataset, args.sample, args.official_evidence)
    command = [sys.executable, str(ROOT / 'scripts/diagnose_webmainbench_moli_tail.py'),
               '--older', str(args.older), '--newer', str(args.newer),
               '--prior-older', str(args.prior_older), '--prior-newer', str(args.prior_newer),
               '--dataset', str(args.dataset), '--sample', str(args.sample),
               '--output', str(args.output), '--all-cases', '--rounds', '2', '--formal-correction']
    process = subprocess.Popen(command, cwd=ROOT)
    while process.poll() is None:
        time.sleep(2)
        competing = competing_benchmark_pids()
        if competing:
            process.terminate()
            process.wait(timeout=10)
            (args.output / 'environment-interference.json').write_text(
                json.dumps({'kind': 'concurrent_benchmark', 'pids': competing}, indent=2) + '\n')
            raise ValueError(f'Concurrent browser benchmark started during Moli timing: {competing}')
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command)
    after = snapshot(args.dataset, args.sample, args.official_evidence)
    if before != after:
        raise ValueError('Source or references changed during Moli calls')
    selection = json.loads((args.output / 'selection.json').read_text(encoding='utf-8'))
    expected_prior = {'1.1.5': digest(args.prior_older), '1.1.7': digest(args.prior_newer)}
    expected_binaries = {'1.1.5': digest(args.older), '1.1.7': digest(args.newer)}
    case_rows = {case_id: {'input_html_sha256': value,
                           'reference_sha256': before['reference_sha256'][case_id]}
                 for case_id, value in before['input_html_sha256'].items()}
    binding = {'directory': args.output.name, 'selection_json_sha256': digest(args.output / 'selection.json'),
               'calls_jsonl_sha256': digest(args.output / 'calls.jsonl')}
    _, calls = _repeat_calls(args.output.parent, binding, case_rows, before['source_dataset_sha256'],
                             before['sample_sha256'], expected_prior, expected_binaries, 200, 2)
    authority = {'selected_round': 2,
                 'rule': 'final_round_for_every_version_and_page; never select a fastest call',
                 'scope': 'latency only; preserve original output, scoring and verdict'}
    if selection.get('authority') != authority:
        raise ValueError('Final-round authority was not declared before calls')
    failures = [row for row in calls if not row['_body_present']]
    if (len(failures) != 4 or len({row['case_id'] for row in failures}) != 1
            or any('langrensha.163.com/wanfa/guize/' not in row['stderr'] for row in failures)):
        raise ValueError('Corrected cohort failure scope changed')
    receipt = {'kind': 'certified_webmainbench_moli_latency_crossover_v1',
               'precheck': before, 'postcheck': after,
               'selection_sha256': binding['selection_json_sha256'],
               'calls_sha256': binding['calls_jsonl_sha256'],
               'physical_calls': len(calls), 'failed_calls': len(failures),
               'selected_round': 2, 'versions': expected_binaries,
               'prior_results_sha256': expected_prior}
    (args.output / 'certification.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print('Certified WebMainBench Moli latency cohort:', args.output)
