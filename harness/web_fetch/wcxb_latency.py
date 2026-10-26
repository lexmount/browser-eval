"""Validate complete, certified WCXB version timing corrections."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from lexbench_webfetch.paths import contained_path

from harness.record_io import parse_jsonl
from harness.web_fetch import webmainbench_runner
from harness.web_fetch.providers import build_provider
from harness.web_fetch.safety import evidence_tree_identity, reject_evidence_symlinks
from harness.web_fetch.wcxb_dataset import validate_manifest


def load_full_paired_latency(root: Path, profile: dict, valid_cases: set[str]) -> tuple[dict, dict]:
    """Validate a complete, preselected final-round timing correction."""
    reject_evidence_symlinks(root)
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
            or not valid_cases
            or not isinstance(load_checks, list)
            or len(load_checks) < 2 + 2 * math.ceil(len(valid_cases) / 20)
            or type(load_limit) not in (int, float) or not math.isfinite(load_limit) or load_limit <= 0
            or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
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
                        or type(row.get('round')) is not int or type(row.get('order')) is not int
                        or type(elapsed) not in (int, float) or not math.isfinite(elapsed)
                        or elapsed <= 0 or not isinstance(row.get('output_sha256'), str)
                        or not re.fullmatch('[0-9a-f]{64}', row['output_sha256'])):
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


def require_quiet_host() -> float:
    """Reject competing work before and throughout the serial timing cohort."""
    load = os.getloadavg()[0]
    limit = max(4, (os.cpu_count() or 4) * .8)
    if not math.isfinite(load) or load < 0 or load > limit:
        raise RuntimeError(f'host one-minute load {load:.1f} exceeds timing limit {limit:.1f}')
    snapshot = subprocess.run(['ps', '-axo', 'pid=,pcpu=,command='],
                              capture_output=True, text=True, check=True).stdout
    for line in snapshot.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3:
            continue
        pid, cpu, command = fields
        if (int(pid) != os.getpid() and float(cpu) >= 15
                and any(marker in command for marker in (
                    '/.browser-eval/work/', 'run_benchmark.py', 'cargo test', 'cargo build'))):
            raise RuntimeError('concurrent benchmark/build process is active: ' + pid)
    return load


def collect(*, dataset: Path, manifest: Path, profile: Path, output: Path,
            providers: dict, binaries: dict, wait_quiet_seconds: int = 0,
            inputs: tuple[Path, ...] = ()) -> dict:
    """Collect the full declared two-build crossover; never select by outcome."""
    from harness.web_fetch.wcxb_scoring import participant_names

    if type(wait_quiet_seconds) is not int or wait_quiet_seconds < 0:
        raise ValueError('wait-quiet-seconds must be a nonnegative integer')
    if output.exists() or output.is_symlink():
        raise ValueError('Timing evidence already exists')
    if set(providers) != {'release', 'pr'} or set(binaries) != set(providers):
        raise ValueError('Paired timing requires release and pr implementations')
    if output.resolve().is_relative_to(dataset.resolve()):
        raise ValueError('Timing output must stay outside the source dataset')
    watched = [profile, manifest, *binaries.values(), *inputs]
    watched += [dataset / 'test/html', dataset / 'test/ground-truth', dataset / 'evaluate.py']

    def source_tree():
        reject_evidence_symlinks(contained_path(dataset, 'test', reject_symlinks=True))

    source_tree()

    def identity(path):
        reject_evidence_symlinks(path)
        return evidence_tree_identity(path) if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()

    before = {path: identity(path) for path in watched}

    def unchanged():
        source_tree()
        if any(identity(path) != expected for path, expected in before.items()):
            raise ValueError('Paired timing input, profile or binary changed')

    authority = json.loads(profile.read_bytes())
    participant_names(authority)
    if type(authority.get('concurrency', 1)) is not int or authority.get('concurrency', 1) != 1:
        raise ValueError('Paired timing requires serial execution')
    records, source_hash = webmainbench_runner.load_wcxb_source(dataset)
    manifest_bytes = manifest.read_bytes()
    selected, _ = validate_manifest(records, source_hash, json.loads(manifest_bytes))
    hashes = {method: before[path] for method, path in binaries.items()}
    if (not selected or type(authority.get('case_count')) is not int
            or len(selected) != authority.get('case_count')
            or source_hash != authority.get('source_dataset_sha256')
            or before[manifest] != authority.get('sample_manifest_sha256')
            or before[dataset / 'evaluate.py'] != authority.get('official_evaluator_sha256')
            or hashes != {'release': authority.get('release_binary_sha256'),
                          'pr': authority.get('candidate_binary_sha256')}
            or len(set(hashes.values())) != 2):
        raise ValueError('Paired timing inputs differ from the complete declared profile')
    for method, provider in providers.items():
        if problems := provider.validate():
            raise ValueError(f'{method}: {problems}')
        if provider.implementation_identity().get('binary_sha256') != hashes[method]:
            raise ValueError('Configured provider differs from the measured binary: ' + method)
    unchanged()
    deadline = time.monotonic() + wait_quiet_seconds
    checks = []
    while len(checks) < 2:
        try:
            checks.append(require_quiet_host())
        except RuntimeError:
            checks.clear()
            if time.monotonic() >= deadline:
                raise
        if len(checks) < 2:
            time.sleep(30)
    unchanged()
    output.mkdir(parents=True)
    started = datetime.now(timezone.utc).isoformat()
    calls_path = output / 'calls.jsonl'
    with webmainbench_runner.fixture_server(selected) as (base, ca), calls_path.open('x', encoding='utf-8') as sink:
        for provider in providers.values():
            provider.config['ca_cert'] = str(ca)
        for round_number in (0, 1):
            for index, record in enumerate(selected):
                if index % 20 == 0:
                    checks.append(require_quiet_host())
                first = 'release' if (index + round_number) % 2 == 0 else 'pr'
                for order, method in enumerate((first, 'pr' if first == 'release' else 'release'), 1):
                    start = time.monotonic()
                    response = webmainbench_runner.fetch_record(providers[method], None, record,
                                            f"{base}/case/{record['track_id']}",
                                            authority['timeout_seconds'])
                    row = {'round': round_number, 'case': record['track_id'],
                           'method': method, 'order': order,
                           'elapsed_ms': round((time.monotonic() - start) * 1000, 2),
                           'ok': response.ok, 'error_type': response.error_type,
                           'error_message': response.error_message,
                           'output_sha256': hashlib.sha256(response.content.encode()).hexdigest()}
                    sink.write(json.dumps(row, ensure_ascii=False) + '\n')
                    sink.flush()
            print(f'completed round {round_number + 1}/2', flush=True)
    unchanged()
    result = {'schema_version': 1, 'kind': 'wcxb_full_paired_latency',
              'started_at': started, 'completed_at': datetime.now(timezone.utc).isoformat(),
              'case_count': len(selected), 'rounds': 2, 'selection': 'final round, all cases',
              'source_sha256': source_hash, 'manifest_sha256': before[manifest],
              'binary_sha256': hashes, 'calls_sha256': identity(calls_path),
              'call_count': len(selected) * 4,
              'quiet_host_load_limit': max(4, (os.cpu_count() or 4) * .8),
              'host_load_1m_checks': checks}
    # Preserve physical calls on failure, but issue no receipt for an invalid cohort.
    receipt = (json.dumps(result, indent=2) + '\n').encode()
    receipt_path = output / 'receipt.json'
    with receipt_path.open('xb') as stream:
        stream.write(receipt)
    try:
        load_full_paired_latency(output, {
            **authority, 'latency_correction_receipt_sha256': hashlib.sha256(receipt).hexdigest(),
        }, {row['track_id'] for row in selected})
        unchanged()
    except BaseException:
        receipt_path.unlink()
        raise
    return result


def certify(argv=None) -> dict:
    """Bind the complete frozen timing receipt without changing any measured calls."""
    from harness.web_fetch.wcxb_scoring import participant_names

    parser = argparse.ArgumentParser(description='Certify a complete WCXB paired latency cohort')
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--sample-manifest', type=Path, required=True)
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    args = parser.parse_args(argv)
    reject_evidence_symlinks(contained_path(args.dataset, 'test', reject_symlinks=True))
    certificate_path = args.evidence_root / 'certification.json'
    if certificate_path.exists() or certificate_path.is_symlink():
        raise ValueError('Paired timing cohort is already certified')
    watched = [args.profile, args.sample_manifest, args.dataset / 'evaluate.py',
               args.evidence_root / 'receipt.json', args.evidence_root / 'calls.jsonl']
    raw = {}
    for path in watched:
        reject_evidence_symlinks(path)
        raw[path] = path.read_bytes()
    authority = json.loads(raw[args.profile])
    participant_names(authority)
    records, before = webmainbench_runner.load_wcxb_source(args.dataset)
    selected, _ = validate_manifest(records, before, json.loads(raw[args.sample_manifest]))
    if (type(authority.get('case_count')) is not int or len(selected) != authority.get('case_count')
            or hashlib.sha256(raw[args.dataset / 'evaluate.py']).hexdigest()
            != authority.get('official_evaluator_sha256')):
        raise ValueError('Paired timing scope or evaluator differs from profile')
    receipt, rows = load_full_paired_latency(args.evidence_root, authority,
                                            {row['track_id'] for row in selected})
    _, after = webmainbench_runner.load_wcxb_source(args.dataset)
    reject_evidence_symlinks(contained_path(args.dataset, 'test', reject_symlinks=True))
    if (before != after or before != receipt['source_sha256']
            or hashlib.sha256(raw[args.sample_manifest]).hexdigest() != receipt['manifest_sha256']
            or len(rows) != len(selected) * 2):
        raise ValueError('WCXB paired timing input or declared scope changed')
    for path, original in raw.items():
        reject_evidence_symlinks(path)
        if path.read_bytes() != original:
            raise ValueError('WCXB paired timing evidence changed during certification')
    certificate = {'schema_version': 1, 'kind': 'certified_wcxb_paired_latency',
                   'source_sha256_before': before, 'source_sha256_after': after,
                   'sample_sha256_before': receipt['manifest_sha256'],
                   'sample_sha256_after': receipt['manifest_sha256'],
                   'receipt_sha256': hashlib.sha256(raw[args.evidence_root / 'receipt.json']).hexdigest(),
                   'calls_sha256': receipt['calls_sha256'], 'case_count': len(selected),
                   'physical_call_count': receipt['call_count'], 'selected_round': 1, 'complete': True}
    with certificate_path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(certificate, indent=2) + '\n')
    print('Certified WCXB paired latency cohort:', args.evidence_root)
    return certificate

def main(argv=None, *, root: Path | None = None) -> None:
    """Caller-relative CLI; existing binary flags retain their two-build meaning."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['certify']:
        certify(argv[1:])
        return

    parser = argparse.ArgumentParser(description='Collect complete profile-bound WCXB paired timing')
    for name in ('dataset', 'manifest', 'release', 'candidate', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--config', type=Path, help='Provider config with release and pr role entries')
    parser.add_argument('--wait-quiet-seconds', type=int, default=0)
    args = parser.parse_args(argv)
    binaries = {'release': args.release.absolute(), 'pr': args.candidate.absolute()}
    if args.config:
        configs = json.loads(args.config.read_bytes())['providers']
        if set(configs) != set(binaries):
            raise ValueError('Paired timing configuration requires exactly release and pr roles')
    else:
        # The historical command measures Moli; other implementations supply config.
        configs = {role: {'type': 'headless_markdown', 'engine': 'moli',
                          'network_policy': 'loopback_only'} for role in binaries}
    providers = {role: build_provider(role, {**configs[role], 'binary': str(binary)})
                 for role, binary in binaries.items()}
    result = collect(dataset=args.dataset.absolute(), manifest=args.manifest.absolute(),
                     profile=args.profile.absolute(), output=args.output.absolute(),
                     providers=providers, binaries=binaries,
                     wait_quiet_seconds=args.wait_quiet_seconds,
                     inputs=(args.config.absolute(),) if args.config else ())
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
