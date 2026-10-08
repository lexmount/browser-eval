"""Measure two pinned Moli builds on every qualified WCXB page in balanced order.

This is a latency experiment, not a replacement for certified content calls.
The complete call log is private evidence; no fastest-call selection is allowed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.providers import build_provider
from harness.web_fetch.webmainbench_runner import (fetch_record,
                                                  fixture_server, load_wcxb_source)
from harness.web_fetch.wcxb_dataset import validate_manifest


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require_quiet_host() -> float:
    """Fail closed when another benchmark can contaminate wall-clock latency."""
    load = os.getloadavg()[0]
    if load > max(4, (os.cpu_count() or 4) * .8):
        raise RuntimeError(f'host one-minute load {load:.1f} is too high for latency certification')
    snapshot = subprocess.run(['ps', '-axo', 'pid=,pcpu=,command='],
                              capture_output=True, text=True, check=True).stdout
    competing = []
    for line in snapshot.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3:
            continue
        pid, cpu, command = fields
        if (int(pid) != os.getpid() and float(cpu) >= 15
                and ('/.browser-eval/work/' in command or 'run_benchmark.py' in command
                     or 'cargo test' in command or 'cargo build' in command)):
            competing.append(pid)
    if competing:
        raise RuntimeError('concurrent benchmark/build processes are active: ' + ', '.join(competing[:8]))
    return load


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wait-quiet-seconds', type=int, default=0,
                        help='Wait for two clean host checks before starting; zero fails immediately')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output exists; measured evidence is immutable')
    deadline = time.monotonic() + args.wait_quiet_seconds
    clean_checks = []
    while len(clean_checks) < 2:
        try:
            clean_checks.append(require_quiet_host())
        except RuntimeError as error:
            clean_checks.clear()
            if time.monotonic() >= deadline:
                raise
            print(f'waiting for quiet host: {error}', flush=True)
        if len(clean_checks) < 2:
            time.sleep(30)
    host_load = clean_checks
    records, source_before = load_wcxb_source(args.dataset)
    manifest_bytes = args.manifest.read_bytes()
    selected, _ = validate_manifest(records, source_before, json.loads(manifest_bytes))
    if len(selected) != 504:
        parser.error('latency correction requires the entire qualified population')
    binaries = {'release': args.release.resolve(), 'pr': args.candidate.resolve()}
    hashes = {name: sha(path.read_bytes()) for name, path in binaries.items()}
    profile = json.loads((ROOT / 'evaluation/wcxb/current-report.json').read_text())
    if (source_before != profile['source_dataset_sha256']
            or sha(manifest_bytes) != profile['sample_manifest_sha256']
            or hashes != {'release': profile['release_binary_sha256'],
                          'pr': profile['candidate_binary_sha256']}):
        parser.error('source, qualified population or binary identity differs from current report')
    providers = {}
    for name, binary in binaries.items():
        provider = build_provider(name, {'type': 'headless_markdown', 'engine': 'moli',
                                         'binary': str(binary), 'network_policy': 'loopback_only'})
        if problems := provider.validate():
            parser.error(f'{name}: {problems}')
        providers[name] = provider
    args.output.mkdir(parents=True)
    started = datetime.now(timezone.utc).isoformat()
    calls_path = args.output / 'calls.jsonl'
    with fixture_server(selected) as (fixture_base, ca_cert), calls_path.open('w') as calls:
        for provider in providers.values():
            provider.config['ca_cert'] = str(ca_cert)
        for round_number in (0, 1):
            for index, record in enumerate(selected):
                if index % 20 == 0:
                    host_load.append(require_quiet_host())
                # Half of the pages reverse order in each round; every page sees both orders.
                first = 'release' if (index + round_number) % 2 == 0 else 'pr'
                second = 'pr' if first == 'release' else 'release'
                for method in (first, second):
                    t0 = time.monotonic()
                    response = fetch_record(providers[method], None, record,
                                            f"{fixture_base}/case/{record['track_id']}", 45)
                    elapsed_ms = round((time.monotonic() - t0) * 1000, 2)
                    row = {'round': round_number, 'case': record['track_id'],
                           'method': method, 'order': 1 if method == first else 2,
                           'elapsed_ms': elapsed_ms, 'ok': response.ok,
                           'error_type': response.error_type,
                           'error_message': response.error_message,
                           'output_sha256': sha(response.content.encode('utf-8'))}
                    calls.write(json.dumps(row, ensure_ascii=False) + '\n')
                    calls.flush()
            print(f'completed round {round_number + 1}/2', flush=True)
    _, source_after = load_wcxb_source(args.dataset)
    if (source_after != source_before or sha(args.manifest.read_bytes()) != sha(manifest_bytes)
            or {name: sha(path.read_bytes()) for name, path in binaries.items()} != hashes):
        raise ValueError('frozen input or measured binary changed during paired latency cohort')
    summary = {'schema_version': 1, 'kind': 'wcxb_full_paired_latency',
               'started_at': started, 'completed_at': datetime.now(timezone.utc).isoformat(),
               'case_count': len(selected), 'rounds': 2, 'selection': 'final round, all cases',
               'source_sha256': source_before, 'manifest_sha256': sha(manifest_bytes),
               'binary_sha256': hashes, 'calls_sha256': sha(calls_path.read_bytes()),
               'call_count': len(selected) * 4,
               'quiet_host_load_limit': max(4, (os.cpu_count() or 4) * .8),
               'host_load_1m_checks': host_load}
    (args.output / 'receipt.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
