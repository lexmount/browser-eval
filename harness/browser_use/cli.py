"""Command-line entrypoint for isolated browser use evaluation."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path

from .config import configure_browser_docker_host, load_runtime_env
from .runner import run_evaluation


@contextmanager
def qualification_lock(output_dir: str | Path) -> Iterator[None]:
    root = Path(output_dir).resolve().parent
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / ".qualification.lock").open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                f"another qualification process is active under {root}"
            ) from exc
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def task_ids_from_scope(
    scope_path: str | Path,
    suite_path: str | Path,
    batch_id: str,
    *,
    shard_count: int | None = None,
    shard_index: int | None = None,
) -> tuple[str, ...]:
    scope_file = Path(scope_path)
    suite_file = Path(suite_path)
    scope = json.loads(scope_file.read_text(encoding="utf-8"))
    if scope.get("schema") != "lex_browser_use_native_scope/1":
        raise ValueError("native scope schema is invalid")
    if scope.get("suite_sha256") != hashlib.sha256(suite_file.read_bytes()).hexdigest():
        raise ValueError("native scope does not match suite sha256")
    matches = [
        row for row in scope.get("qualification_batches") or [] if row.get("id") == batch_id
    ]
    if len(matches) != 1:
        raise ValueError(f"unknown or duplicate native scope batch: {batch_id}")
    batch = matches[0]
    task_ids = tuple(str(value) for value in batch.get("task_ids") or [])
    if not task_ids or len(set(task_ids)) != len(task_ids):
        raise ValueError(f"native scope batch is empty or contains duplicates: {batch_id}")
    if not set(task_ids) <= set(scope.get("included_task_ids") or []):
        raise ValueError(f"native scope batch contains excluded tasks: {batch_id}")
    if (shard_count is None) != (shard_index is None):
        raise ValueError("batch shard count and index must be provided together")
    if shard_count is not None and shard_index is not None:
        declared_shard_count = int(batch.get("shard_count") or 1)
        if shard_count != declared_shard_count:
            raise ValueError(
                f"batch shard count must match pre-registered value {declared_shard_count}"
            )
        if shard_count < 1 or shard_count > len(task_ids):
            raise ValueError("batch shard count must be between 1 and the batch task count")
        if shard_index < 0 or shard_index >= shard_count:
            raise ValueError("batch shard index must be zero-based and smaller than shard count")
        task_ids = task_ids[shard_index::shard_count]
    return task_ids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="/work/tasks.v2.json")
    parser.add_argument("--output", default="/work/output")
    parser.add_argument("--reports", default="/work/reports")
    parser.add_argument(
        "--ground-truth-evidence",
        default=None,
    )
    parser.add_argument("--failure-retries", type=int, default=1)
    parser.add_argument("--sample-interval-ms", type=int, default=50)
    parser.add_argument("--settle-ms", type=int, default=1500)
    parser.add_argument("--cpus", type=float, default=2.0)
    parser.add_argument("--memory", default="2g")
    parser.add_argument("--llm-diagnostics", action="store_true")
    parser.add_argument("--diagnosis-model", default="gpt-5.6-sol")
    parser.add_argument("--diagnosis-reasoning-effort", default="low")
    parser.add_argument("--webarena-config")
    parser.add_argument(
        "--env-file",
        help="dotenv file; defaults to the repository-local .env when present",
    )
    parser.add_argument(
        "--browser-docker-host",
        help="absolute unix:// socket for measured browser containers",
    )
    parser.add_argument("--engines", default="chromium,moli")
    parser.add_argument("--qualification-run", action="store_true")
    parser.add_argument("--trajectory-authoring-run", action="store_true")
    parser.add_argument(
        "--task-ids",
        help="comma-separated task IDs; used for site-grouped WebArena batches",
    )
    parser.add_argument("--native-scope", help="pre-registered native architecture scope JSON")
    parser.add_argument("--batch-id", help="batch ID inside --native-scope")
    parser.add_argument("--batch-shard-count", type=int)
    parser.add_argument("--batch-shard-index", type=int)
    args = parser.parse_args()
    load_runtime_env(args.env_file)
    configure_browser_docker_host(args.browser_docker_host)
    if bool(args.native_scope) != bool(args.batch_id):
        parser.error("--native-scope and --batch-id must be used together")
    if args.task_ids and args.native_scope:
        parser.error("--task-ids cannot be combined with --native-scope")
    if (args.batch_shard_count is None) != (args.batch_shard_index is None):
        parser.error("--batch-shard-count and --batch-shard-index must be used together")
    if args.batch_shard_count is not None and not args.native_scope:
        parser.error("batch sharding requires --native-scope and --batch-id")
    selected_task_ids = (
        task_ids_from_scope(
            args.native_scope,
            args.suite,
            args.batch_id,
            shard_count=args.batch_shard_count,
            shard_index=args.batch_shard_index,
        )
        if args.native_scope
        else (
            tuple(item.strip() for item in args.task_ids.split(",") if item.strip())
            if args.task_ids
            else None
        )
    )
    lock = (
        qualification_lock(args.output)
        if args.qualification_run or args.trajectory_authoring_run
        else nullcontext()
    )
    with lock:
        run_evaluation(
            suite_path=args.suite,
            output_dir=args.output,
            reports_dir=args.reports,
            ground_truth_evidence_path=args.ground_truth_evidence,
            max_retries=args.failure_retries,
            sample_interval_ms=args.sample_interval_ms,
            settle_ms=args.settle_ms,
            cpus=args.cpus,
            memory=args.memory,
            llm_diagnostics=args.llm_diagnostics,
            diagnosis_model=args.diagnosis_model,
            diagnosis_reasoning_effort=args.diagnosis_reasoning_effort,
            webarena_config_path=args.webarena_config,
            engines=tuple(item.strip() for item in args.engines.split(",") if item.strip()),
            qualification_run=args.qualification_run,
            trajectory_authoring_run=args.trajectory_authoring_run,
            task_ids=selected_task_ids,
            task_selection=(
                {
                    "kind": "native_scope_batch",
                    "scope_sha256": hashlib.sha256(
                        Path(args.native_scope).read_bytes()
                    ).hexdigest(),
                    "batch_id": args.batch_id,
                    "shard_count": args.batch_shard_count or 1,
                    "shard_index": args.batch_shard_index or 0,
                }
                if args.native_scope
                else None
            ),
        )


if __name__ == "__main__":
    main()
