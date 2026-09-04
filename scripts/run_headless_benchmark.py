"""Run the five browsers against the unchanged canonical Web Fetch cases."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.config import load_dataset, load_provider_config, load_runtime_env
from harness.web_fetch.providers import build_provider
from harness.web_fetch.runner import run_evaluation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, default=ROOT/'evaluation/dataset.json')
    parser.add_argument('--config', type=Path, default=ROOT/'configs/providers.headless.json')
    parser.add_argument('--providers', default='kitesurf,lightpanda,obscura,moli,chromium')
    parser.add_argument('--case-ids')
    parser.add_argument('--tracks', help='Comma-separated task families; default all')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--timeout', type=float, default=45)
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
    dataset = {**dataset, 'id': 'web-fetch-headless-browsers',
               'name': '五种浏览器 Web Fetch 对比',
               'cases': [c for c in dataset['cases'] if c['id'] in {x.id for x in cases}],
               'coverage_plan': {c.track: names for c in cases},
               'comparison': {'kind': 'headless_markdown', 'timeout_seconds': args.timeout,
                   'source_dataset_sha256': hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
                   'adapter_input_sha256': hashlib.sha256((ROOT/'scripts/headless_worker.py').read_bytes()).hexdigest(),
                   'conversion': 'Native Markdown for Kitesurf, Lightpanda, Obscura and Moli; Chromium whole-DOM HTML via pyhtml2md 1.7.0',
                   'concurrency': args.workers, 'runs_per_case': 1}}
    records, _ = run_evaluation(output_dir=args.output, dataset_meta=dataset,
        dataset_path=args.dataset, cases=cases, providers=providers,
        provider_config_path=args.config, runs=1, timeout=args.timeout,
        delay=.5, provider_concurrency=args.workers)
    if len(records) != len(cases)*len(providers):
        raise ValueError('Incomplete benchmark call count')
    for folder in ('sources', 'list-references'):
        if (args.dataset.parent/folder).exists():
            shutil.copytree(args.dataset.parent/folder, args.output/folder)
    manifest = json.loads((args.output/'manifest.json').read_text())
    manifest['dataset']['source'] = 'evaluation/dataset.json'
    manifest['provider_config_source'] = 'configs/providers.headless.json'
    manifest['environment'].pop('hostname', None)
    (args.output/'execution.json').write_text(json.dumps({'batches': [manifest]}, ensure_ascii=False, indent=2)+'\n')
    print('Completed:', len(records), 'calls. Evidence:', args.output)


if __name__ == '__main__':
    main()
