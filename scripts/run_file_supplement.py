"""Run the registered file tasks through real providers; never substitute HTTP."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.config import load_runtime_env, load_dataset, load_provider_config
from harness.web_fetch.providers import build_provider
from harness.web_fetch.runner import run_evaluation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT/'.env')
    parser.add_argument('--config', type=Path, default=ROOT/'configs/providers.resources.json')
    parser.add_argument('--providers', nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Existing evidence must not be overwritten; choose a new output directory')
    load_runtime_env(args.env_file)
    config = load_provider_config(args.config)
    meta, cases = load_dataset(ROOT/'evaluation/dataset.json')
    cases = [case for case in cases if case.task_kind == 'file']
    providers = [build_provider(name, config[name]) for name in args.providers]
    for provider in providers:
        if missing := provider.validate():
            parser.error(f'{provider.name}: {", ".join(missing)}')
    run_evaluation(output_dir=args.output, dataset_meta=meta,
        dataset_path=ROOT/'evaluation/dataset.json', cases=cases, providers=providers,
        provider_config_path=args.config, runs=1, timeout=45, delay=0,
        provider_concurrency=4)


if __name__ == '__main__':
    main()
