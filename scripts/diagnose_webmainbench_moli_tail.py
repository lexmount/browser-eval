"""Repeat the largest paired Moli latency gaps with serial, identical Markdown fetches."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.web_fetch.providers.headless import LOOPBACK_SANDBOX_PROFILE
from scripts.run_webmainbench_benchmark import fixture_server, load_selected_ids, load_source, verify_loopback_sandbox


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> dict[str, dict]:
    return {row['track_id']: row for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip() for row in (json.loads(line),)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('older', 'newer', 'prior-older', 'prior-newer', 'dataset', 'sample', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--all-cases', action='store_true', help='Repeat all 200 pages in balanced version order')
    parser.add_argument('--formal-correction', action='store_true', help='Declare final-round timing authority before calls')
    args = parser.parse_args()
    if args.rounds < (2 if args.all_cases else 3) or args.output.exists():
        raise ValueError('Insufficient rounds or output directory already exists')
    if args.formal_correction and (not args.all_cases or args.rounds != 2):
        raise ValueError('Formal correction requires all 200 pages and exactly two balanced rounds')
    binaries = {'1.1.5': args.older.resolve(), '1.1.7': args.newer.resolve()}
    for version, binary in binaries.items():
        result = subprocess.run([str(binary), '--version'], capture_output=True, text=True, check=True)
        if result.stdout.strip() != f'moli {version}':
            raise ValueError('Moli version mismatch: ' + version)
    selected_ids, sample = load_selected_ids(args.sample)
    if sample['case_count'] != 200:
        raise ValueError('Expected frozen 200-page sample')
    source = {row['track_id']: row for row in load_source(args.dataset, selected_ids)}
    prior = {'1.1.5': rows(args.prior_older), '1.1.7': rows(args.prior_newer)}
    if any(set(value) != set(source) for value in prior.values()):
        raise ValueError('Prior runs do not cover the same 200 pages')
    common_successes = {case_id for case_id in source if prior['1.1.5'][case_id]['ok']
                        and prior['1.1.7'][case_id]['ok']}
    gaps = sorted(common_successes,
                  key=lambda case_id: prior['1.1.7'][case_id]['elapsed_ms'] -
                  prior['1.1.5'][case_id]['elapsed_ms'], reverse=True)
    slow = gaps[:20]
    controls = (gaps[20:] + sorted(set(source) - common_successes) if args.all_cases
                else random.Random(117).sample(gaps[20:], 10))
    cases = slow + controls
    case_positions = {case_id: index for index, case_id in enumerate(cases)}
    args.output.mkdir(parents=True)
    selection = {
        'kind': 'moli_paired_crossover_v1' if args.all_cases else 'moli_paired_tail_repetition_v1',
        'case_ids': cases, 'slow_case_ids': slow,
        'control_case_ids': controls, 'rounds': args.rounds,
        'dataset_sha256': sha256(args.dataset), 'sample_sha256': sha256(args.sample),
        'prior_sha256': {'1.1.5': sha256(args.prior_older), '1.1.7': sha256(args.prior_newer)},
        'binaries': {v: {'path': str(p), 'sha256': sha256(p)} for v, p in binaries.items()},
        'runner_sha256': sha256(Path(__file__)),
        'call': 'sandbox-exec loopback-only moli fetch --dump markdown --wait-until done '
                '--ca-cert CA --timeout 45000 --http-timeout 45000 URL',
        'order': 'one process at a time; page order shuffled by round; each page alternates which version runs first',
    }
    if args.formal_correction:
        selection['authority'] = {
            'selected_round': 2,
            'rule': 'final_round_for_every_version_and_page; never select a fastest call',
            'scope': 'latency only; preserve original output, scoring and verdict',
        }
    (args.output / 'selection.json').write_text(json.dumps(selection, indent=2) + '\n')
    with fixture_server([source[case_id] for case_id in cases]) as (base, ca_cert):
        selection['network_probe'] = verify_loopback_sandbox(base, ca_cert, source[cases[0]])
        (args.output / 'selection.json').write_text(json.dumps(selection, indent=2) + '\n')
        with (args.output / 'calls.jsonl').open('w') as stream:
            for round_index in range(args.rounds):
                order = cases.copy()
                random.Random(117 + round_index).shuffle(order)
                for case_id in order:
                    versions = ('1.1.5', '1.1.7') if (round_index + case_positions[case_id]) % 2 == 0 else ('1.1.7', '1.1.5')
                    for version in versions:
                        command = ['/usr/bin/sandbox-exec', '-p', LOOPBACK_SANDBOX_PROFILE,
                                   str(binaries[version]), 'fetch', '--dump', 'markdown',
                                   '--wait-until', 'done', '--ca-cert', str(ca_cert),
                                   '--timeout', '45000', '--http-timeout', '45000', f'{base}/case/{case_id}']
                        start = time.monotonic()
                        try:
                            call = subprocess.run(command, capture_output=True, timeout=50, check=False)
                            status, body, error = call.returncode, call.stdout, call.stderr
                        except subprocess.TimeoutExpired as exc:
                            status, body, error = -1, exc.stdout or b'', exc.stderr or b'timeout'
                        elapsed_ms = round((time.monotonic() - start) * 1000, 2)
                        target = args.output / 'outputs' / str(round_index + 1) / version / (case_id + '.md')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(body)
                        row = {'round': round_index + 1, 'case_id': case_id, 'version': version,
                               'elapsed_ms': elapsed_ms, 'exit_code': status,
                               'output_sha256': sha256(target), 'output_bytes': len(body),
                               'stderr': error.decode('utf-8', 'replace')[-1000:],
                               'input_html_sha256': hashlib.sha256(source[case_id]['html'].encode()).hexdigest()}
                        stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                        stream.flush()
                print(f'round {round_index + 1}/{args.rounds}: {len(order)} pages, both versions', flush=True)


if __name__ == '__main__':
    main()
