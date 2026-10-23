from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from .accounts import print_account_checklist
from .config import load_dataset, load_provider_config, load_runtime_env, select_diagnostic_scope, resolve_provider_selection, add_failure_retry_argument, DEFAULT_CALL_TIMEOUT_SECONDS
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.models import TASK_KINDS
from .providers import build_provider
from .report_build import build_report
from .runner import rescore_evaluation, run_evaluation
from .storage import data_home
from harness.report_languages import add_language_argument, primary_report_language

EXAMPLES = Path(__file__).parent / "data"


def default_run_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return data_home() / "runs" / stamp


def command_run(args: argparse.Namespace) -> int:
    if args.output.exists() or args.output.is_symlink():
        raise SystemExit("Output must be a new directory; existing evidence is immutable")
    load_runtime_env(args.env_file)
    dataset_meta, cases = load_dataset(args.dataset)
    provider_configs = load_provider_config(args.provider_config)
    selection = resolve_provider_selection(args.providers, args.provider_config)
    names = [name.strip() for name in selection.split(",") if name.strip()]
    unknown = [name for name in names if name not in provider_configs]
    if unknown:
        raise SystemExit(f"Unknown providers: {', '.join(unknown)}")
    try:
        dataset_meta, cases = select_diagnostic_scope(
            dataset_meta, cases, names, case_ids=args.case_ids, task_kind=args.task_kind)
    except ValueError as error:
        raise SystemExit(str(error)) from error
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
        max_failure_retries=args.max_failure_retries,
        collect_only=getattr(args, "collect_only", False),
    )
    if getattr(args, "collect_only", False):
        print(f"Collected responses and logs: {args.output.resolve()}")
        return 0
    report_path = build_report(args.output, args.output / 'report', diagnostic_preview=True,
                               language=args.language)
    print(f"Report: {report_path.resolve()}")
    kinds={case.id:case.task_kind for case in cases}
    failures = [r for r in records if not task_passed(r['ok'],r['metrics'],kinds[r['case_id']])]
    return 2 if args.fail_on_failure and failures else 0


def command_report(args: argparse.Namespace) -> int:
    language = args.language or primary_report_language(args.run_dir / 'report')
    report_path = build_report(args.run_dir, args.run_dir / 'report', diagnostic_preview=True,
                               language=language)
    print(f"Report: {report_path.resolve()}")
    return 0


def command_rescore(args: argparse.Namespace) -> int:
    dataset, cases = load_dataset(args.dataset)
    records, _ = rescore_evaluation(run_dir=args.run_dir, dataset_meta=dataset, dataset_path=args.dataset, cases=cases)
    language = args.language or primary_report_language(args.run_dir / 'report')
    report_path = build_report(args.run_dir, args.run_dir / 'report', diagnostic_preview=True,
                               language=language)
    print(f"Rescored {len(records)} persisted attempts; report: {report_path.resolve()}")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    load_runtime_env(args.env_file)
    _, cases = load_dataset(args.dataset)
    configs = load_provider_config(args.provider_config)
    selection = resolve_provider_selection(args.providers, args.provider_config)
    names = [name.strip() for name in selection.split(",") if name.strip()]
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
        description="Web Fetch evaluations for Browser Eval. Run retrieval tasks and build reports from saved evidence.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run providers and generate a multi-page report")
    add_language_argument(run, default="en")
    run.add_argument("--dataset", type=Path, default=EXAMPLES / "fixture-dataset.json")
    run.add_argument("--provider-config", type=Path, default=EXAMPLES / "fixture-providers.json")
    run.add_argument("--providers", default="fixture", help="Comma-separated names, or configured for the provider config default cohort")
    run.add_argument("--case-ids", default="", help="Optional comma-separated case IDs")
    run.add_argument("--task-kind", choices=TASK_KINDS,
                     help="Select one task kind; intersects with --case-ids")
    run.add_argument("--runs", type=int, default=1)
    run.add_argument("--timeout", type=float, default=DEFAULT_CALL_TIMEOUT_SECONDS)
    run.add_argument("--collect-only", action="store_true",
                     help="Collect one call per case without scoring, retries or a scored report")
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
    add_failure_retry_argument(run)
    run.set_defaults(handler=command_run)

    report = sub.add_parser("report", help="Rebuild HTML from an existing run directory")
    report.add_argument("run_dir", type=Path)
    add_language_argument(report)
    report.set_defaults(handler=command_report)

    rescore = sub.add_parser("rescore", help="Recompute metrics/report from persisted artifacts and task rules")
    rescore.add_argument("run_dir", type=Path)
    rescore.add_argument("--dataset", type=Path, required=True)
    add_language_argument(rescore)
    rescore.set_defaults(handler=command_rescore)

    validate = sub.add_parser("validate", help="Validate dataset and provider credentials without network calls")
    validate.add_argument("--dataset", type=Path, default=EXAMPLES / "fixture-dataset.json")
    validate.add_argument("--provider-config", type=Path, default=EXAMPLES / "fixture-providers.json")
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
