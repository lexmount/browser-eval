"""Run browser Markdown methods against the unchanged canonical Web Fetch cases."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.config import load_dataset, load_provider_config, load_runtime_env
from harness.web_fetch.providers import build_provider
from harness.web_fetch.providers.headless import HEADLESS_CONVERSION
from harness.web_fetch.runner import run_evaluation


EXPECTED_PROVIDER_VERSIONS = {
    'moli-1.0.6': '1.0.6',
    'moli': '1.1.1',
    'moli-1.1.3': '1.1.3',
}


def version_number(value):
    match = re.search(r'\d+(?:\.\d+)+', value or '')
    return match.group(0) if match else value


def require_expected_provider_versions(identities):
    """Fail before measurement if a versioned provider slot is mislabeled."""
    for name, expected in EXPECTED_PROVIDER_VERSIONS.items():
        if name not in identities:
            continue
        reported = version_number(identities[name].get('reported_version'))
        if reported != expected:
            raise ValueError(
                f'{name}: expected version {expected}, reported '
                f'{reported or "unavailable"}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, default=ROOT/'evaluation/dataset.json')
    parser.add_argument('--config', type=Path, default=ROOT/'configs/providers.headless.json')
    parser.add_argument('--providers', default='kitesurf,lightpanda,obscura,moli-1.0.6,moli,moli-1.1.3,chromium')
    parser.add_argument('--case-ids')
    parser.add_argument('--tracks', help='Comma-separated task families; default all')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--timeout', type=float, default=45)
    parser.add_argument('--max-failure-retries', type=int, choices=(0, 1), default=0,
                        help='Retry a failed task at most once; the final call is authoritative')
    parser.add_argument('--env-file', type=Path, help='Explicit dotenv file; defaults to repository .env')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; measured evidence must not be overwritten')
    load_runtime_env(args.env_file)
    dataset, cases = load_dataset(args.dataset)
    if args.tracks:
        cases = [c for c in cases if c.track in args.tracks.split(',')]
    if args.case_ids:
        wanted = set(args.case_ids.split(','))
        cases = [c for c in cases if c.id in wanted]
        if len(cases) != len(wanted):
            parser.error('Unknown case ID')
    names = args.providers.split(',')
    if len(names) != len(set(names)):
        parser.error('Duplicate provider')
    config = load_provider_config(args.config)
    providers = [build_provider(name, config[name]) for name in names]
    for provider in providers:
        if missing := provider.validate():
            parser.error(provider.name + ': ' + ', '.join(missing))
    preflight_identities = {provider.name: provider.implementation_identity()
                            for provider in providers}
    require_expected_provider_versions(preflight_identities)
    dataset = {**dataset, 'id': 'web-fetch-headless-browsers',
               'name': '五类浏览器、七种方法 Web Fetch 对比',
               'cases': [c for c in dataset['cases'] if c['id'] in {x.id for x in cases}],
               'coverage_plan': {c.track: names for c in cases},
               'comparison': {'kind': 'headless_markdown', 'timeout_seconds': args.timeout,
                   'source_dataset_sha256': hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
                   'adapter_input_sha256': hashlib.sha256((ROOT/'scripts/headless_worker.py').read_bytes()).hexdigest(),
               'conversion': HEADLESS_CONVERSION,
                   'concurrency': args.workers, 'runs_per_case': 1,
                   'max_failure_retries': args.max_failure_retries,
                   'retry_selection': 'final_physical_attempt_is_authoritative'}}
    records, _ = run_evaluation(output_dir=args.output, dataset_meta=dataset,
        dataset_path=args.dataset, cases=cases, providers=providers,
        provider_config_path=args.config, runs=1, timeout=args.timeout,
        delay=.5, provider_concurrency=args.workers,
        max_failure_retries=args.max_failure_retries)
    if len(records) != len(cases)*len(providers):
        raise ValueError('Incomplete benchmark call count')
    postflight_identities = {provider.name: provider.implementation_identity(refresh=True)
                             for provider in providers}
    if postflight_identities != preflight_identities:
        changed = sorted(name for name in names
                         if postflight_identities[name] != preflight_identities[name])
        raise ValueError('Implementation identity changed during execution: ' + ', '.join(changed))
    # A mutable path, package name or nightly label is not an implementation
    # identity. Bind call metadata to the preflight identity and fail if an
    # executable changes inside one batch.
    implementation_identities = {}
    for name in names:
        metadata = [r.get('provider_metadata', {}) for r in records if r['provider'] == name]
        hashes = sorted({m['binary_sha256'] for m in metadata if m.get('binary_sha256')})
        versions = sorted({m['browser_version'] for m in metadata if m.get('browser_version')})
        deployments = sorted({m['deployment'] for m in metadata if m.get('deployment')})
        extractors = sorted({m['extractor'] for m in metadata if m.get('extractor')})
        if len(hashes) > 1 or len(versions) > 1:
            raise ValueError(name + ': implementation changed during one execution batch')
        preflight = preflight_identities[name]
        if hashes and hashes != [preflight['binary_sha256']]:
            raise ValueError(name + ': measured executable differs from preflight identity')
        if (versions and preflight['reported_version'] and
                {version_number(value) for value in versions} !=
                {version_number(preflight['reported_version'])}):
            raise ValueError(name + ': measured version differs from preflight identity')
        implementation_identities[name] = {**preflight,
            'identity_source': 'preflight and postflight executable identity; copied into call metadata outside timing',
            'call_metadata_coverage': {
                'binary_sha256': sum(bool(m.get('binary_sha256')) for m in metadata),
                'reported_version': sum(bool(m.get('browser_version')) for m in metadata),
                'calls': len(metadata),
            },
            'observed_deployments': deployments,
            'observed_extractors': extractors,
        }
    for folder in ('sources', 'list-references'):
        if (args.dataset.parent/folder).exists():
            shutil.copytree(args.dataset.parent/folder, args.output/folder)
    manifest = json.loads((args.output/'manifest.json').read_text())
    manifest['dataset']['source'] = 'evaluation/dataset.json'
    manifest['provider_config_source'] = 'configs/providers.headless.json'
    manifest['environment'].pop('hostname', None)
    for provider in manifest['providers']:
        provider['implementation_identity'] = implementation_identities[provider['name']]
    (args.output/'execution.json').write_text(json.dumps({'batches': [manifest]}, ensure_ascii=False, indent=2)+'\n')
    print('Completed:', len(records), 'calls. Evidence:', args.output)


if __name__ == '__main__':
    main()
