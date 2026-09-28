"""Certify an origin-preserving case correction against a frozen six-method run."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.score_webmainbench_results import load_records, official_calculator, metric_dict
from scripts.correct_webmainbench_teds import corrected_distance
from scripts.run_certified_webmainbench_full import verify_shard
from harness.web_fetch.safety import contained_path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def copy_contained_artifact(source_root, source_relative, output_root, output_relative):
    """Copy one evidence artifact without allowing either path to escape its root."""
    source = contained_path(source_root, source_relative)
    destination = contained_path(output_root, output_relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'dataset', 'scorer', 'output', 'fixed-binary'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--moli-binary', type=Path)
    parser.add_argument('--prior-correction', type=Path)
    parser.add_argument('--case-id', required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError('Correction destination already exists')
    originals = rows(args.source / 'results.jsonl')
    selected = {r['provider']: r for r in originals if r['case_id'] == args.case_id}
    providers = json.loads((args.source / 'dataset.json').read_text())['coverage_plan']['static_html']
    if set(selected) != set(providers) or len(providers) != 6:
        raise ValueError('Correction requires all six methods')
    source_case = load_records(args.dataset)[args.case_id]
    binaries = {'moli-fixed': args.fixed_binary}
    if args.moli_binary:
        binaries['moli'] = args.moli_binary
    if not args.prior_correction and 'moli' not in binaries:
        parser.error('--moli-binary is required without --prior-correction')
    for name, binary in binaries.items():
        if digest(binary) != selected[name]['provider_metadata']['binary_sha256']:
            raise ValueError('Binary differs from frozen cohort: ' + name)
    prior_rows = prior_physical = []
    prior_certificate_sha256 = None
    if args.prior_correction:
        certificate_path = args.prior_correction / 'certificate.json'
        prior_certificate = json.loads(certificate_path.read_text())
        prior_plan = prior_certificate.get('preflight', {})
        if (prior_certificate.get('kind') != 'webmainbench_origin_correction_v1'
                or prior_plan != prior_certificate.get('postflight')
                or prior_plan.get('case_id') != args.case_id
                or prior_plan.get('source_sha256') != digest(args.dataset)
                or set(prior_plan.get('providers', ())) != set(providers)
                or prior_certificate.get('results_sha256') != digest(args.prior_correction / 'results.jsonl')
                or prior_certificate.get('physical_sha256') != digest(args.prior_correction / 'physical-attempts.jsonl')):
            raise ValueError('Prior origin correction certificate mismatch')
        prior_rows = rows(args.prior_correction / 'results.jsonl')
        prior_physical = rows(args.prior_correction / 'physical-attempts.jsonl')
        prior_certificate_sha256 = digest(certificate_path)
        inherited = {row['provider']: row for row in prior_rows if row['provider'] != 'moli-fixed'}
        if set(inherited) != set(providers) - {'moli-fixed'}:
            raise ValueError('Prior origin correction does not cover unchanged methods')
        for name, row in inherited.items():
            old = selected[name]
            if (row.get('supersedes_attempt_id') != old['attempt_id']
                    or any(row.get(key) != old.get(key) for key in ('input_html_sha256', 'reference_sha256'))
                    or any(row['provider_metadata'].get(key) != old['provider_metadata'].get(key)
                           for key in ('binary_sha256', 'browser_version', 'extractor_version', 'converter_sha256'))
                    or digest(contained_path(args.prior_correction, row['artifact_path'])) != row['output_sha256']):
                raise ValueError('Prior origin correction differs from current method: ' + name)
        if any(digest(contained_path(args.prior_correction, call['physical_artifact_path']))
               != call['output_sha256'] for call in prior_physical if call['provider'] != 'moli-fixed'):
            raise ValueError('Prior origin correction physical artifact changed')
    plan = {'kind': 'webmainbench_origin_correction_v1', 'case_id': args.case_id,
            'providers': providers, 'parent_results_sha256': digest(args.source / 'results.jsonl'),
            'source_sha256': digest(args.dataset), 'timeout_seconds': 45, 'max_failure_retries': 1,
            'input_html_sha256': hashlib.sha256(source_case['html'].encode()).hexdigest(),
            'reference_sha256': hashlib.sha256(source_case['groundtruth_content'].encode()).hexdigest(),
            'reason': 'localhost substitution triggers the frozen page canonical-URL redirect; preserve the original URL while serving the same frozen bytes and blocking external resources',
            'selection': ('five unchanged methods inherited from a bound prior certified correction; '
                          'the final physical call for moli-fixed was selected before execution'
                          if args.prior_correction else
                          'final physical call; all six methods selected before execution')}
    if prior_certificate_sha256:
        plan['prior_correction_certificate_sha256'] = prior_certificate_sha256
    args.output.mkdir(parents=True)
    write(args.output / 'preflight.json', plan)
    selection = args.output / 'case-ids.txt'
    selection.write_text(args.case_id + '\n')
    corrected = []
    physical = []
    for name in providers:
        if args.prior_correction and name != 'moli-fixed':
            row = next(row for row in prior_rows if row['provider'] == name)
            corrected.append(row)
            for call in prior_physical:
                if call['provider'] != name:
                    continue
                target = call['physical_artifact_path']
                copy_contained_artifact(args.prior_correction, target, args.output, target)
                physical.append(call)
            target = row['artifact_path']
            copy_contained_artifact(args.prior_correction, target, args.output, target)
            print(name, row['ok'], row['output_chars'], 'inherited', flush=True)
            continue
        provider = 'moli' if name == 'moli-fixed' else name
        shard = args.output / name
        env = dict(os.environ)
        if name in binaries:
            env['MOLI_BINARY'] = str(binaries[name])
        version = selected[name]['provider_metadata'].get('browser_version', '').split()
        version_args = ['--expected-version', version[-1]] if version and provider != 'kitesurf' else []
        subprocess.run([sys.executable, str(ROOT / 'scripts/run_webmainbench_benchmark.py'),
                        '--provider', provider, '--dataset', str(args.dataset), '--output', str(shard),
                        '--case-ids-file', str(selection), '--preserve-original-origin',
                        '--timeout', '45', '--max-failure-retries', '1', *version_args], env=env, cwd=ROOT, check=True)
        verify_shard(shard, provider, {args.case_id}, {'case_count': 1, 'source_dataset_sha256': plan['source_sha256']})
        row = rows(shard / 'results.jsonl')[0]
        old = selected[name]
        for key in ('binary_sha256', 'browser_version', 'extractor_version', 'converter_sha256'):
            if old['provider_metadata'].get(key) != row['provider_metadata'].get(key):
                raise ValueError('Provider identity changed: ' + name + '/' + key)
        for key in ('input_html_sha256', 'reference_sha256'):
            if row[key] != plan[key]:
                raise ValueError('Frozen input changed')
        if not row['ok'] or not row['output_chars']:
            raise ValueError('Correction still fails: ' + name)
        calculator = official_calculator(args.scorer, shard / 'score-cache')
        output = (shard / row['artifact_path']).read_text()
        kwargs = dict(predicted_content=output, groundtruth_content=source_case['groundtruth_content'],
                      predicted_content_list=None, groundtruth_content_list=None)
        row['official_metrics'] = {k: metric_dict(v) for k, v in calculator.calculate_all(**kwargs).items()}
        calculator.metrics['table_TEDS']._tree_edit_distance = types.MethodType(corrected_distance, calculator.metrics['table_TEDS'])
        row['metrics'] = {k: metric_dict(v) for k, v in calculator.calculate_all(**kwargs).items()}
        row['provider'] = name
        row['case_id'] = row['track_id']
        row['attempt_id'] = name + '__' + args.case_id + '__' + row['run_id']
        for call in rows(shard / 'physical-attempts.jsonl'):
            call['provider'] = name
            call['attempt_id'] = name + '__' + call['run_id'] + '__' + call['attempt_id']
            target = 'artifacts/' + name + '/' + call['run_id'] + '-' + Path(call['physical_artifact_path']).name
            copy_contained_artifact(shard, call['physical_artifact_path'], args.output, target)
            call['physical_artifact_path'] = target
            physical.append(call)
        target = 'artifacts/' + name + '/' + row['run_id'] + '.md'
        copy_contained_artifact(shard, row['artifact_path'], args.output, target)
        row['artifact_path'] = target
        row['physical_artifact_path'] = physical[-1]['physical_artifact_path']
        row['supersedes_attempt_id'] = old['attempt_id']
        corrected.append(row)
        print(name, row['ok'], row['output_chars'], flush=True)
    if digest(args.dataset) != plan['source_sha256'] or digest(args.source / 'results.jsonl') != plan['parent_results_sha256']:
        raise ValueError('Evidence changed during correction')
    for name, binary in binaries.items():
        if digest(binary) != selected[name]['provider_metadata']['binary_sha256']:
            raise ValueError('Binary changed during correction')
    for filename, values in [('results.jsonl', corrected), ('physical-attempts.jsonl', physical)]:
        (args.output / filename).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in values))
    write(args.output / 'certificate.json', {'kind': plan['kind'], 'preflight': plan, 'postflight': plan,
          'results_sha256': digest(args.output / 'results.jsonl'),
          'physical_sha256': digest(args.output / 'physical-attempts.jsonl')})


if __name__ == '__main__':
    main()
