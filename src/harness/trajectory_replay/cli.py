"""Run, audit, report and publish the curated Browser Trajectory Replay track."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

PROG = "python -m harness.trajectory_replay"
_PACKAGE = "harness.trajectory_replay"
_CLI = f"{_PACKAGE}.cli"
COMMANDS = {
    "evaluate": (_CLI, "evaluate", "Run the isolated curated suite and save its evidence"),
    "replay": (_CLI, "replay", "Run the frozen model-free WebArena trajectory suite"),
    "seeds": (_CLI, "seeds", "Build private replay candidates from Browser Use evidence"),
    "expert-seeds": (_CLI, "expert_seeds", "Merge reviewed expert repairs into a seed manifest"),
    "promote": (_CLI, "promote", "Promote replay-proven trajectories into the dataset"),
    "merge-results": (f"{_PACKAGE}.webarena_results", "main_merge",
                      "Overlay formal correction shards onto the current matrix"),
    "dataset-revision": (f"{_PACKAGE}.webarena_corrections", "main_dataset_revision",
                         "Create a dataset revision from reviewed task corrections"),
    "report": (f"{_PACKAGE}.webarena_report", "build_main",
               "Build the report from measured evidence"),
    "check": (f"{_PACKAGE}.webarena_report_check", "main",
              "Check a built report against its profile"),
    "visual": (f"{_PACKAGE}.webarena_report_visual", "main",
               "Run the visual gate and write its receipt"),
    "package": (f"{_PACKAGE}.webarena_report_package", "main",
                "Package a checked report for its Artifact Site"),
    "publish": (f"{_PACKAGE}.webarena_report_publish", "cli",
                "Publish a packaged report to its bound Artifact Site"),
    "catalog-run": (f"{_PACKAGE}.catalog_runner", "main", "Replay a frozen WebArena catalog"),
    "catalog-report": (_CLI, "catalog_report", "Build the catalog report from accepted evidence"),
    "catalog-release-check": (f"{_PACKAGE}.catalog_release", "main",
                              "Validate a catalog release"),
    "catalog-audit-acceptance": (f"{_PACKAGE}.catalog_acceptance", "main",
                                 "Audit catalog results against the acceptance contract"),
    "catalog-audit-actions": (f"{_PACKAGE}.catalog_action_audit", "main_audit",
                              "Audit evaluate syntax and the catalog's action overrides"),
    "catalog-comparison-inputs": (f"{_PACKAGE}.catalog_comparison", "inputs_main",
                                  "Build shared report inputs from acceptance ledgers"),
    "catalog-consolidate": (f"{_PACKAGE}.catalog_consolidation", "main",
                            "Consolidate accepted catalog evidence"),
    "catalog-rebind": (f"{_PACKAGE}.catalog_binding", "main", "Rebind a catalog to its inputs"),
    "cohort-freeze": (f"{_PACKAGE}.remote_runtime", "main_freeze",
                      "Freeze one complete selected-task remote replay input"),
    "cohort-freeze-original-failures": (f"{_PACKAGE}.remote_runtime",
                                        "main_freeze_original_failures",
                                        "Freeze the original-failure candidate input"),
    "cohort-revise": (f"{_PACKAGE}.remote_runtime", "main_revise",
                      "Freeze explicit trajectory repairs without changing earlier evidence"),
    "cohort-aggregate": (f"{_PACKAGE}.webarena_results", "main_aggregate",
                         "Aggregate certified selected-task remote replay cohorts"),
    "mutation-diagnostic": (_CLI, "mutation_diagnostic",
                            "Run a receipt-bound remote mutation diagnostic"),
    "evaluator-transition": (f"{_PACKAGE}.evaluator_transition", "main",
                             "Prove old passing evidence still passes the pinned evaluator"),
    "audit-readiness": (f"{_PACKAGE}.diagnostics", "main_readiness",
                        "Locate clicks right after snapshots that label the target disabled"),
    "audit-cart-assertion": (f"{_PACKAGE}.diagnostics", "main_cart_assertion",
                             "Compare cart assertions on isolated DOM counterexamples"),
    "audit-comparison-inputs": (f"{_PACKAGE}.diagnostics", "main_comparison_inputs",
                                "Inventory comparison symmetry and declared benchmark edits"),
    "audit-saved-correction": (f"{_PACKAGE}.diagnostics", "main_saved_correction",
                               "Compare correction catalogs on saved replay evidence"),
    "rescore-saved-retrieval": (f"{_PACKAGE}.diagnostics", "main_saved_retrieval_rescore",
                                "Rescore saved retrieval evidence without browser calls"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    for name, (_, _, summary) in COMMANDS.items():
        # The owning module parses the remaining arguments, including its own --help.
        sub.add_parser(name, help=summary, add_help=False)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args, remaining = build_parser().parse_known_args(argv)
    module, function, _ = COMMANDS[args.command]
    # Import on selection so each command and top-level help load only what they use.
    handler = getattr(importlib.import_module(module), function)
    invoked = sys.argv
    # Owners read sys.argv: visual receipts hash webarena_report_visual.py, so main cannot change.
    sys.argv = [f"{PROG} {args.command}", *remaining]
    try:
        handler()
    finally:
        sys.argv = invoked


def evaluate() -> None:
    from harness.trajectory_replay.runner import run_evaluation

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--suite", help="Explicit suite path; defaults to the locked HF curated edition"
    )
    parser.add_argument("--output", required=True, help="New immutable evidence directory")
    parser.add_argument("--reports", help="Defaults to the selected run's report directory")
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Save scores and evidence without building local HTML",
    )
    parser.add_argument(
        "--ground-truth-evidence",
        required=True,
    )
    parser.add_argument(
        "--agent-smoke", help="Explicit historical smoke evidence JSON; never rerun"
    )
    parser.add_argument("--failure-retries", type=int, default=1)
    parser.add_argument("--sample-interval-ms", type=int, default=50)
    parser.add_argument("--settle-ms", type=int, default=1500)
    parser.add_argument("--cpus", type=float, default=2.0)
    parser.add_argument("--memory", default="2g")
    parser.add_argument("--llm-diagnostics", action="store_true")
    parser.add_argument("--diagnosis-model", default="gpt-5.6-sol")
    parser.add_argument("--diagnosis-reasoning-effort", default="low")
    args = parser.parse_args()
    run_evaluation(
        suite_path=args.suite,
        output_dir=args.output,
        reports_dir=Path(args.reports) if args.reports else Path(args.output) / "report",
        generate_report=not args.no_report,
        ground_truth_evidence_path=args.ground_truth_evidence,
        agent_smoke_path=args.agent_smoke,
        max_retries=args.failure_retries,
        sample_interval_ms=args.sample_interval_ms,
        settle_ms=args.settle_ms,
        cpus=args.cpus,
        memory=args.memory,
        llm_diagnostics=args.llm_diagnostics,
        diagnosis_model=args.diagnosis_model,
        diagnosis_reasoning_effort=args.diagnosis_reasoning_effort,
    )


def replay() -> None:
    # webarena_replay.py is hash-bound into cohort certificates, so its parser lives here.
    from harness.trajectory_replay.webarena_replay import run_replay_suite

    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--seeds", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cowagent-repo", required=True)
    parser.add_argument(
        "--diagnostic-task",
        action="append",
        type=int,
        help="run an explicitly non-promotable subset instead of the frozen full suite",
    )
    parser.add_argument(
        "--diagnostic-engine",
        action="append",
        help="run only selected registered browsers as an explicitly non-promotable diagnostic",
    )
    parser.add_argument(
        "--qualification-engine",
        help="qualify the complete frozen trajectory set on the registered reference browser",
    )
    parser.add_argument(
        "--qualification-task",
        action="append",
        type=int,
        help=(
            "qualify a repaired task shard whose passing proofs will be merged "
            "with prior qualification receipts"
        ),
    )
    parser.add_argument(
        "--correction-task",
        action="append",
        type=int,
        help=(
            "rerun a corrected formal task shard across every registered browser; "
            "the receipt supersedes only these task rows"
        ),
    )
    parser.add_argument(
        "--qualified-dataset",
        help="complete qualified trajectory dataset required by a formal comparison",
    )
    args = parser.parse_args()
    run_replay_suite(
        profile_path=args.profile,
        seed_manifest_path=args.seeds,
        output_dir=args.output,
        cowagent_repo=args.cowagent_repo,
        diagnostic_task_ids=args.diagnostic_task,
        diagnostic_engines=args.diagnostic_engine,
        qualification_engine=args.qualification_engine,
        qualification_task_ids=args.qualification_task,
        correction_task_ids=args.correction_task,
        qualified_dataset_path=args.qualified_dataset,
    )


def seeds() -> None:
    from harness.trajectory_replay.browser_use_seeds import build_replay_seed_manifest

    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--evidence", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    manifest = build_replay_seed_manifest(args.evidence, profile)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **manifest["counts"]}, ensure_ascii=False))


def expert_seeds() -> None:
    from harness.trajectory_replay.expert_seeds import build_expert_seed_manifest

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base",
        "--base-seeds",
        dest="base",
        required=True,
        help="Existing Browser Use replay seed manifest",
    )
    parser.add_argument(
        "--repairs",
        "--repair-spec",
        dest="repairs",
        required=True,
        help="Explicit expert repair specification",
    )
    parser.add_argument("--output", required=True, help="Destination merged seed manifest")
    args = parser.parse_args()
    manifest = build_expert_seed_manifest(args.base, args.repairs)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "expert_repair_candidates": manifest["counts"]["expert_repair_candidates"],
                "replay_ready_tasks": manifest["counts"]["replay_ready_tasks"],
            },
            ensure_ascii=False,
        )
    )


def promote() -> None:
    from harness.trajectory_replay.webarena_dataset import build_verified_trajectory_dataset

    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--seeds", required=True)
    parser.add_argument("--receipt", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    data = build_verified_trajectory_dataset(
        args.seeds,
        args.receipt,
        profile_path=args.profile,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data["counts"], ensure_ascii=False))


def catalog_report() -> None:
    from harness.trajectory_replay.webarena_report import build_catalog_acceptance_report

    parser = argparse.ArgumentParser(description="Build the catalog report from accepted evidence.")
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--portable-suite", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    result = build_catalog_acceptance_report(
        evidence_root=args.evidence_root,
        ledger_path=args.ledger,
        scope_path=args.scope,
        catalog_path=args.catalog,
        suite_path=args.portable_suite,
        site_map_path=args.site_map,
        output=args.output,
        archive=args.archive,
    )
    print(json.dumps(result, ensure_ascii=False))


def mutation_diagnostic() -> None:
    from harness.trajectory_replay.remote_execution import main_mutation_diagnostic

    raise SystemExit(main_mutation_diagnostic())
