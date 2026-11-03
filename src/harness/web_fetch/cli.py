from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.models import TASK_KINDS

from harness.report_languages import (
    ReportLanguageError,
    add_language_argument,
    resolve_report_language,
)
from harness.web_fetch.accounts import print_account_checklist
from harness.web_fetch.config import (
    DEFAULT_CALL_TIMEOUT_SECONDS,
    add_failure_retry_argument,
    load_dataset,
    load_provider_config,
    load_runtime_env,
    resolve_provider_selection,
    select_diagnostic_scope,
)
from harness.web_fetch.providers import build_provider
from harness.web_fetch.report_build import build_report
from harness.web_fetch.runner import rescore_evaluation, run_evaluation
from harness.data_home import data_home

EXAMPLES = Path(__file__).parent / "data"
# WCXB and WebMainBench stages whose owning module parses its own arguments, including --help.
OWNER_COMMANDS = {
    "wcxb-select": ("harness.web_fetch.wcxb_dataset", "main",
                    "Freeze a WCXB sample manifest"),
    "wcxb-latency": ("harness.web_fetch.wcxb_latency", "main",
                     "Collect or certify (certify) paired WCXB timing"),
    "wcxb-score": ("harness.web_fetch.wcxb_scoring", "main",
                   "Score a certified WCXB cohort against its profile"),
    "wcxb-visual": ("harness.web_fetch.wcxb_report_check", "visual_main",
                    "Check WCXB report interactions, themes and viewports"),
    "wcxb-publish": ("harness.web_fetch.wcxb_publication", "main",
                     "Package or publish a checked WCXB report"),
    "webmainbench-select": ("harness.web_fetch.public_benchmark_sample", "main",
                            "Freeze the WebMainBench-545 sample manifest"),
    "webmainbench-score": ("harness.web_fetch.webmainbench_scoring", "main",
                           "Score one WebMainBench shard with the pinned scorer"),
    "webmainbench-review": ("harness.web_fetch.webmainbench_diagnostics", "sample_review_main",
                            "Audit a declared sample without the official scorer"),
    "webmainbench-assemble": ("harness.web_fetch.webmainbench_assembly", "sample_main",
                              "Assemble a declared sample from reviewed shards"),
    "webmainbench-check": ("harness.web_fetch.webmainbench_report_check", "sample_main",
                           "Check a frozen sample report"),
    "webmainbench-audit": ("harness.web_fetch.webmainbench_diagnostics", "suspicion_main",
                           "Flag frozen evidence anomalies without changing scores"),
    "webmainbench-case-correction": ("harness.web_fetch.webmainbench_case_correction", "main",
                                     "Apply one-case replacement evidence to a new directory"),
    "webmainbench-case-rescore": ("harness.web_fetch.webmainbench_correction", "case_scoring_main",
                                  "Rescore a certified case correction offline"),
    "webmainbench-scoring-correction": ("harness.web_fetch.webmainbench_correction",
                                        "scoring_revision_main",
                                        "Apply a decision-authority manifest to the full population"),
    "webmainbench-teds-correction": ("harness.web_fetch.webmainbench_diagnostics", "correct_teds",
                                     "Apply an offline TEDS-only revision"),
    "webmainbench-teds-review": ("harness.web_fetch.webmainbench_diagnostics", "teds_review_main",
                                 "Verify the scope and unchanged evidence of a TEDS revision"),
    "webmainbench-latency-correction": ("harness.web_fetch.webmainbench_latency_correction", "main",
                                        "Apply a certified latency correction"),
    "webmainbench-content-review": ("harness.web_fetch.review_execution", "content_review_main",
                                    "Prepare or resume a source-grounded content review"),
    "webmainbench-full-run": ("harness.web_fetch.webmainbench_certification", "main",
                              "Run a certified complete cohort"),
    "webmainbench-full-score": ("harness.web_fetch.webmainbench_certification", "main_score",
                                "Score a certified complete cohort"),
    "webmainbench-full-assemble": ("harness.web_fetch.webmainbench_assembly", "main",
                                   "Verify and package the baseline and variant cohorts"),
    "webmainbench-full-check": ("harness.web_fetch.webmainbench_report_check", "main",
                                "Check a complete report"),
}


def default_run_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return data_home() / "runs" / stamp


def command_run(args: argparse.Namespace) -> int:
    if args.output.exists() or args.output.is_symlink():
        raise SystemExit("Output must be a new directory; existing evidence is immutable")
    load_runtime_env(args.env_file)
    language = None if args.no_report else resolve_report_language(args.output / "report", args.language)
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
    )
    if args.no_report:
        print(f"Results: {(args.output / 'results.jsonl').resolve()}")
        print(f"Run data: {args.output.resolve()}")
        print("Report not built (--no-report).")
    else:
        report_path = build_report(args.output, args.output / 'report', diagnostic_preview=True,
                                   language=language)
        print(f"Report: {report_path.resolve()}")
    kinds={case.id:case.task_kind for case in cases}
    failures = [r for r in records if not task_passed(r['ok'],r['metrics'],kinds[r['case_id']])]
    return 2 if args.fail_on_failure and failures else 0


def command_report(args: argparse.Namespace) -> int:
    language = resolve_report_language(args.run_dir / 'report', args.language)
    report_path = build_report(args.run_dir, args.run_dir / 'report', diagnostic_preview=True,
                               language=language)
    print(f"Report: {report_path.resolve()}")
    return 0


def command_rescore(args: argparse.Namespace) -> int:
    language = None if args.no_report else resolve_report_language(args.run_dir / "report", args.language)
    dataset, cases = load_dataset(args.dataset)
    records, _ = rescore_evaluation(run_dir=args.run_dir, dataset_meta=dataset, dataset_path=args.dataset, cases=cases)
    if args.no_report:
        print(f"Rescored {len(records)} persisted attempts; results: {(args.run_dir / 'results.jsonl').resolve()}")
        print("Report not rebuilt (--no-report). Any existing HTML is unchanged.")
    else:
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
    add_language_argument(run)
    run.add_argument("--dataset", type=Path, default=EXAMPLES / "fixture-dataset.json")
    run.add_argument("--provider-config", type=Path, default=EXAMPLES / "fixture-providers.json")
    run.add_argument("--providers", default="fixture", help="Comma-separated names, or configured for the provider config default cohort")
    run.add_argument("--case-ids", default="", help="Optional comma-separated case IDs")
    run.add_argument("--task-kind", choices=TASK_KINDS,
                     help="Select one task kind; intersects with --case-ids")
    run.add_argument("--runs", type=int, default=1)
    run.add_argument("--timeout", type=float, default=DEFAULT_CALL_TIMEOUT_SECONDS)
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
    run.add_argument("--no-report", action="store_true",
                     help="Save scored results and evidence without building local HTML")
    add_failure_retry_argument(run)
    run.set_defaults(handler=command_run)

    report = sub.add_parser("report", help="Rebuild HTML from an existing run directory")
    report.add_argument("run_dir", type=Path)
    add_language_argument(report)
    report.set_defaults(handler=command_report)

    rescore = sub.add_parser("rescore", help="Recompute metrics/report from persisted artifacts and task rules")
    rescore.add_argument("run_dir", type=Path)
    rescore.add_argument("--dataset", type=Path, required=True)
    rescore.add_argument("--no-report", action="store_true",
                         help="Rescore saved data without rebuilding HTML; existing reports are unchanged")
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
    for name, (_, _, summary) in OWNER_COMMANDS.items():
        sub.add_parser(name, help=summary, add_help=False)
    return parser


def run_owner_command(prog: str, name: str, arguments: Sequence[str]) -> None:
    module, function, _ = OWNER_COMMANDS[name]
    # Resolve the owner only when selected.
    handler = getattr(importlib.import_module(module), function)
    invoked = sys.argv
    # Owners read sys.argv: several record their own source hash, so their mains stay unchanged.
    sys.argv = [f"{prog} {name}", *arguments]
    try:
        handler()
    finally:
        sys.argv = invoked


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if arguments[:1] and arguments[0] in OWNER_COMMANDS:
        run_owner_command(parser.prog, arguments[0], arguments[1:])
        return
    args = parser.parse_args(arguments)
    if getattr(args, "output", "sentinel") is None:
        args.output = default_run_dir()
    try:
        result = args.handler(args)
    except ReportLanguageError as error:
        parser.error(str(error))
    raise SystemExit(result)
