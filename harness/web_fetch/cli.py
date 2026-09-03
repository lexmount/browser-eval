from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .accounts import print_account_checklist
from .config import load_dataset, load_runtime_env, load_provider_config
from .providers import build_provider
from .metrics import task_passed
from .report_build import build_report
from .runner import load_records, rescore_evaluation, run_evaluation


ROOT = Path(__file__).resolve().parents[2]


def default_run_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("runs") / stamp


def command_run(args: argparse.Namespace) -> int:
    load_runtime_env(args.env_file)
    dataset_meta, cases = load_dataset(args.dataset)
    if args.case_ids:
        requested = {item.strip() for item in args.case_ids.split(",") if item.strip()}
        known = {case.id for case in cases}
        unknown_cases = requested - known
        if unknown_cases:
            raise SystemExit(f"Unknown cases: {', '.join(sorted(unknown_cases))}")
        cases = [case for case in cases if case.id in requested]
        dataset_meta = {**dataset_meta, "id": f"{dataset_meta['id']}--subset", "name": f"{dataset_meta['name']}（子集）", "cases": [item for item in dataset_meta["cases"] if item["id"] in requested]}
    provider_configs = load_provider_config(args.provider_config)
    names = [name.strip() for name in args.providers.split(",") if name.strip()]
    unknown = [name for name in names if name not in provider_configs]
    if unknown:
        raise SystemExit(f"Unknown providers: {', '.join(unknown)}")
    providers = [build_provider(name, provider_configs[name]) for name in names]
    records, _ = run_evaluation(
        output_dir=args.output,
        dataset_meta=dataset_meta,
        dataset_path=args.dataset,
        cases=cases,
        providers=providers,
        provider_config_path=args.provider_config,
        runs=args.runs,
        timeout=args.timeout,
        delay=args.delay,
        provider_concurrency=args.provider_concurrency,
    )
    report_path = build_report(args.output, args.output / 'report')
    print(f"Report: {report_path.resolve()}")
    kinds={case.id:case.task_kind for case in cases}
    failures = [r for r in records if not task_passed(r['ok'],r['metrics'],kinds[r['case_id']])]
    return 2 if args.fail_on_failure and failures else 0


def command_report(args: argparse.Namespace) -> int:
    report_path = build_report(args.run_dir, args.run_dir / 'report')
    print(f"Report: {report_path.resolve()}")
    return 0


def command_rescore(args: argparse.Namespace) -> int:
    dataset, cases = load_dataset(args.dataset)
    records, _ = rescore_evaluation(run_dir=args.run_dir, dataset_meta=dataset, dataset_path=args.dataset, cases=cases)
    report_path = build_report(args.run_dir, args.run_dir / 'report')
    print(f"Rescored {len(records)} persisted attempts; report: {report_path.resolve()}")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    load_runtime_env(args.env_file)
    _, cases = load_dataset(args.dataset)
    configs = load_provider_config(args.provider_config)
    names = [name.strip() for name in args.providers.split(",") if name.strip()]
    errors = []
    for name in names:
        if name not in configs:
            errors.append(f"{name}: not defined")
            continue
        provider = build_provider(name, configs[name])
        missing = provider.validate()
        state = "OK" if not missing else f"missing {', '.join(missing)}"
        print(f"{name}: type={provider.type} {state}")
        if missing:
            errors.append(f"{name}: {state}")
    print(f"dataset: {len(cases)} cases OK")
    return 1 if errors else 0


def command_accounts(args: argparse.Namespace) -> int:
    load_runtime_env(args.env_file)
    print_account_checklist()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="browser-harness-eval",
        description="Reproducible evaluation for agent browser harness tools.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run providers and generate a multi-page report")
    run.add_argument("--dataset", type=Path, default=ROOT / "evaluation/dataset.json")
    run.add_argument("--provider-config", type=Path, default=ROOT / "configs/providers.example.json")
    run.add_argument("--providers", default="fixture", help="Comma-separated configured provider names")
    run.add_argument("--case-ids", default="", help="Optional comma-separated case IDs")
    run.add_argument("--runs", type=int, default=1)
    run.add_argument("--timeout", type=float, default=45)
    run.add_argument("--delay", type=float, default=0.5)
    run.add_argument(
        "--provider-concurrency",
        type=int,
        default=4,
        help="Run this many providers in parallel; cases stay sequential within each provider",
    )
    run.add_argument("--env-file", type=Path)
    run.add_argument("--output", type=Path, default=None)
    run.add_argument("--fail-on-failure", action="store_true")
    run.set_defaults(handler=command_run)

    report = sub.add_parser("report", help="Rebuild HTML from an existing run directory")
    report.add_argument("run_dir", type=Path)
    report.set_defaults(handler=command_report)

    rescore = sub.add_parser("rescore", help="Recompute metrics/report from persisted artifacts and task rules")
    rescore.add_argument("run_dir", type=Path)
    rescore.add_argument("--dataset", type=Path, default=ROOT / "evaluation/dataset.json")
    rescore.set_defaults(handler=command_rescore)

    validate = sub.add_parser("validate", help="Validate dataset and provider credentials without network calls")
    validate.add_argument("--dataset", type=Path, default=ROOT / "evaluation/dataset.json")
    validate.add_argument("--provider-config", type=Path, default=ROOT / "configs/providers.example.json")
    validate.add_argument("--providers", default="fixture")
    validate.add_argument("--env-file", type=Path)
    validate.set_defaults(handler=command_validate)

    accounts = sub.add_parser(
        "accounts", help="Print registration links, key locations and a dotenv template"
    )
    accounts.add_argument("--env-file", type=Path)
    accounts.set_defaults(handler=command_accounts)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "output", "sentinel") is None:
        args.output = default_run_dir()
    raise SystemExit(args.handler(args))
