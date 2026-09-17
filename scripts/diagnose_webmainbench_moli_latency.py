"""Run paired Moli HTML/Markdown probes on a declared diagnostic page subset."""
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


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--older', type=Path, required=True)
    parser.add_argument('--newer', type=Path, required=True)
    parser.add_argument('--prior-older', type=Path, required=True)
    parser.add_argument('--prior-newer', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--sample', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Diagnostic evidence already exists')
    binaries = {'1.1.5': args.older.resolve(), '1.1.7': args.newer.resolve()}
    for version, binary in binaries.items():
        probe = subprocess.run([str(binary), '--version'], capture_output=True, text=True, check=True)
        if probe.stdout.strip() != f'moli {version}':
            raise ValueError('Unexpected Moli version: ' + version)
    selected_ids, selection = load_selected_ids(args.sample)
    if selection['case_count'] != 200:
        raise ValueError('Expected the frozen 200-case sample')
    original = load_source(args.dataset, selected_ids)
    by_id = {row['track_id']: row for row in original}
    prior = {}
    for version, path in [('1.1.5', args.prior_older), ('1.1.7', args.prior_newer)]:
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        prior[version] = {row['track_id']: row for row in rows}
        if set(prior[version]) != set(by_id):
            raise ValueError('Prior full-run task population differs')
    gaps = sorted(by_id, key=lambda case_id: (
        prior['1.1.7'][case_id]['elapsed_ms'] - prior['1.1.5'][case_id]['elapsed_ms']),
        reverse=True)
    selected = gaps[:20]
    remainder = [case_id for case_id in sorted(by_id) if case_id not in selected]
    selected += random.Random(117).sample(remainder, 20)
    records = [by_id[case_id] for case_id in selected]
    args.output.mkdir(parents=True)
    manifest = {
        'kind': 'post_hoc_latency_mechanism_diagnostic',
        'selection': 'top 20 positive 1.1.7-minus-1.1.5 gaps in full same-host rerun, plus 20 seeded random other cases',
        'random_seed': 117,
        'case_ids': selected,
        'source_dataset_sha256': digest(args.dataset),
        'sample_sha256': digest(args.sample),
        'prior_results_sha256': {'1.1.5': digest(args.prior_older), '1.1.7': digest(args.prior_newer)},
        'binaries': {version: {'version': version, 'sha256': digest(binary)}
                     for version, binary in binaries.items()},
        'command': 'sandbox-exec loopback-only; moli fetch --dump html|markdown --wait-until done --ca-cert CA --timeout 45000 --http-timeout 45000 URL',
    }
    (args.output / 'selection.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with fixture_server(records) as (base, ca_cert):
        verify_loopback_sandbox(base, ca_cert, records[0])
        with (args.output / 'calls.jsonl').open('w') as stream:
            for index, record in enumerate(records):
                # Alternate both version and output-mode order to limit systematic warm-up bias.
                versions = ['1.1.5', '1.1.7'] if index % 2 == 0 else ['1.1.7', '1.1.5']
                modes = ['html', 'markdown'] if index % 4 < 2 else ['markdown', 'html']
                for version in versions:
                    for mode in modes:
                        command = [
                            '/usr/bin/sandbox-exec', '-p', LOOPBACK_SANDBOX_PROFILE,
                            str(binaries[version]), 'fetch', '--dump', mode,
                            '--wait-until', 'done', '--ca-cert', str(ca_cert),
                            '--timeout', '45000', '--http-timeout', '45000',
                            f'{base}/case/{record["track_id"]}',
                        ]
                        started = time.monotonic()
                        call = subprocess.run(command, capture_output=True, timeout=50, check=False)
                        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                        artifact = args.output / 'outputs' / version / mode / (record['track_id'] + '.txt')
                        artifact.parent.mkdir(parents=True, exist_ok=True)
                        artifact.write_bytes(call.stdout)
                        row = {
                            'case_id': record['track_id'], 'version': version, 'mode': mode,
                            'elapsed_ms': elapsed_ms, 'exit_code': call.returncode,
                            'output_sha256': digest(artifact), 'output_bytes': artifact.stat().st_size,
                            'stderr': call.stderr.decode('utf-8', 'replace')[-500:],
                            'input_html_sha256': hashlib.sha256(record['html'].encode()).hexdigest(),
                        }
                        stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                        stream.flush()
                print(f'[{index + 1}/{len(records)}] {record["track_id"]}', flush=True)


if __name__ == '__main__':
    main()
