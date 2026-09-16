"""Run every pre-registered WebArena qualification shard without overwriting evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from harness.trajectory_replay.public_webarena.cli import task_ids_from_scope
from harness.trajectory_replay.public_webarena.har import HAR_CREATOR_NAME, HAR_CREATOR_VERSION
from harness.trajectory_replay.public_webarena.qualification import build_repair_queue
from harness.trajectory_replay.public_webarena.suite import TaskSuite, load_suite


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qualification_shards(scope_path: Path, suite_path: Path) -> tuple[dict[str, Any], ...]:
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    if scope.get("schema") != "lex_browser_use_native_scope/1":
        raise ValueError("native scope schema is invalid")
    suite_sha256 = _sha256(suite_path)
    if scope.get("suite_sha256") != suite_sha256:
        raise ValueError("native scope does not match suite sha256")
    shards: list[dict[str, Any]] = []
    for batch in scope.get("qualification_batches") or []:
        batch_id = str(batch.get("id") or "")
        shard_count = int(batch.get("shard_count") or 0)
        if not batch_id or shard_count < 1:
            raise ValueError("every qualification batch requires id and shard_count")
        for shard_index in range(shard_count):
            task_ids = task_ids_from_scope(
                scope_path,
                suite_path,
                batch_id,
                shard_count=shard_count,
                shard_index=shard_index,
            )
            shards.append(
                {
                    "id": f"{batch_id}-{shard_index}-of-{shard_count}",
                    "batch_id": batch_id,
                    "shard_count": shard_count,
                    "shard_index": shard_index,
                    "task_ids": task_ids,
                }
            )
    included = tuple(str(value) for value in scope.get("included_task_ids") or [])
    flattened = tuple(task_id for shard in shards for task_id in shard["task_ids"])
    if len(flattened) != len(set(flattened)) or set(flattened) != set(included):
        raise ValueError("qualification shards do not partition the included task set exactly")
    return tuple(shards)


def qualification_waves(
    shards: tuple[dict[str, Any], ...],
    scope: dict[str, Any],
    *,
    max_workers: int,
    isolated_instances: int = 1,
) -> tuple[tuple[tuple[dict[str, Any], ...], ...], ...]:
    """Group complete site batches into deterministic, conflict-free parallel waves."""
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    if isolated_instances < 1:
        raise ValueError("isolated_instances must be positive")
    sites_by_batch = {
        str(batch["id"]): frozenset(str(site) for site in batch.get("sites") or [])
        for batch in scope.get("qualification_batches") or []
    }
    shards_by_batch: dict[str, list[dict[str, Any]]] = {}
    for shard in shards:
        batch_id = str(shard["batch_id"])
        if batch_id not in sites_by_batch or not sites_by_batch[batch_id]:
            raise ValueError(f"qualification batch has no declared sites: {batch_id}")
        shards_by_batch.setdefault(batch_id, []).append(shard)
    remaining = list(shards_by_batch)
    waves: list[tuple[tuple[dict[str, Any], ...], ...]] = []
    while remaining:
        occupied: set[str] = set()
        selected: list[str] = []
        for batch_id in remaining:
            sites = sites_by_batch[batch_id]
            capacity = min(max_workers, isolated_instances)
            isolated = isolated_instances > 1
            if len(selected) < capacity and (isolated or not occupied.intersection(sites)):
                selected.append(batch_id)
                occupied.update(sites)
        waves.append(tuple(tuple(shards_by_batch[batch_id]) for batch_id in selected))
        selected_set = set(selected)
        remaining = [batch_id for batch_id in remaining if batch_id not in selected_set]
    return tuple(waves)


def validate_completed_shard(
    output: Path,
    *,
    suite_sha256: str,
    scope_sha256: str,
    shard: dict[str, Any],
    qualification_pass_index: int,
    qualification_required_passes: int = 2,
) -> None:
    required = {name: output / name for name in ("attempts.jsonl", "summary.json", "manifest.json")}
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise ValueError(f"{shard['id']}: incomplete qualification output; missing {missing}")
    manifest = json.loads(required["manifest.json"].read_text(encoding="utf-8"))
    provenance = manifest.get("artifact_provenance") or {}
    if provenance.get("attempts_sha256") != _sha256(required["attempts.jsonl"]) or provenance.get(
        "summary_sha256"
    ) != _sha256(required["summary.json"]):
        raise ValueError(f"{shard['id']}: qualification artifact hash mismatch")
    protocol = manifest.get("protocol") or {}
    selection = protocol.get("task_selection") or {}
    expected_selection = {
        "kind": "native_scope_batch",
        "scope_sha256": scope_sha256,
        "batch_id": shard["batch_id"],
        "shard_count": shard["shard_count"],
        "shard_index": shard["shard_index"],
        "qualification_pass_index": qualification_pass_index,
        "qualification_required_passes": qualification_required_passes,
    }
    expected_ids = list(shard["task_ids"])
    if (
        manifest.get("suite_sha256") != suite_sha256
        or protocol.get("qualification_run") is not True
        or protocol.get("engines") != ["chromium"]
        or protocol.get("max_failure_retries") != 0
        or protocol.get("task_filter") != expected_ids
        or selection != expected_selection
        or protocol.get("qualification_pass_index") != qualification_pass_index
        or protocol.get("qualification_required_passes") != qualification_required_passes
    ):
        raise ValueError(f"{shard['id']}: qualification manifest contract mismatch")
    rows = [
        json.loads(line)
        for line in required["attempts.jsonl"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != len(expected_ids) or {str(row.get("task_id")) for row in rows} != set(
        expected_ids
    ):
        raise ValueError(f"{shard['id']}: qualification attempts do not cover the shard exactly")
    if any(
        row.get("engine") != "chromium"
        or row.get("qualification_run") is not True
        or row.get("is_retry") is not False
        or row.get("attempt") != 1
        or row.get("qualification_pass_index") != qualification_pass_index
        or row.get("qualification_required_passes") != qualification_required_passes
        for row in rows
    ):
        raise ValueError(f"{shard['id']}: qualification attempts violate primary-run policy")
    expected_creator = {"name": HAR_CREATOR_NAME, "version": HAR_CREATOR_VERSION}
    for row in rows:
        evidence_directory = Path(str(row.get("evidence_directory") or "")).resolve()
        try:
            evidence_directory.relative_to(output.resolve())
        except ValueError as exc:
            raise ValueError(
                f"{shard['id']}: attempt evidence directory escapes the shard"
            ) from exc
        har_path = evidence_directory / "network.har"
        if not har_path.is_file():
            raise ValueError(f"{shard['id']}: attempt network HAR is missing")
        har = json.loads(har_path.read_text(encoding="utf-8"))
        if (har.get("log") or {}).get("creator") != expected_creator:
            raise ValueError(
                f"{shard['id']}: attempt network HAR creator must be {expected_creator}"
            )


def build_scoped_repair_queue(
    suite: TaskSuite,
    attempts_paths: list[Path],
    scope: dict[str, Any],
    *,
    scope_sha256: str,
) -> dict[str, Any]:
    included = {str(value) for value in scope.get("included_task_ids") or []}
    excluded_rows = scope.get("excluded_tasks") or []
    excluded = {str(row.get("task_id")) for row in excluded_rows}
    suite_ids = {task.task_id for task in suite.tasks}
    if included & excluded or included | excluded != suite_ids:
        raise ValueError("native scope must partition the complete suite")
    measured_suite = TaskSuite(
        schema=suite.schema,
        tasks=tuple(task for task in suite.tasks if task.task_id in included),
    )
    queue = build_repair_queue(measured_suite, attempts_paths)
    decisions = {str(row["task_id"]): row for row in queue["tasks"]}
    for row in excluded_rows:
        task_id = str(row["task_id"])
        reason = str(row.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"{task_id}: native scope exclusion requires reason")
        decisions[task_id] = {
            "task_id": task_id,
            "disposition": "excluded_by_native_scope",
            "process_status": None,
            "result_status": None,
            "failed_step": None,
            "evidence_directory": None,
            "adjudication": {
                "status": "skipped",
                "reason": reason,
                "scope_exclusion": {
                    "architecture": scope.get("architecture"),
                    "sites": row.get("sites") or [],
                    "incompatible_sites": row.get("incompatible_sites") or [],
                    **({"kind": row["exclusion_kind"]} if row.get("exclusion_kind") else {}),
                    **({"evidence_id": row["evidence_id"]} if row.get("evidence_id") else {}),
                },
            },
        }
    queue["tasks"] = [decisions[task.task_id] for task in suite.tasks]
    queue["native_scope"] = {
        "sha256": scope_sha256,
        "architecture": scope.get("architecture"),
        "included_count": len(included),
        "excluded_count": len(excluded),
    }
    return queue


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"refusing to overwrite different derived evidence: {path}")
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--native-scope", type=Path, required=True)
    parser.add_argument(
        "--webarena-config",
        type=Path,
        action="append",
        required=True,
        help=(
            "runtime config; repeat with independently provisioned Rainbond slice configs "
            "to run overlapping site batches concurrently"
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="dotenv file passed to every qualification shard; defaults to repository .env",
    )
    parser.add_argument(
        "--browser-docker-host",
        help="absolute unix:// socket used for measured browser containers",
    )
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--batch-id", action="append", dest="batch_ids")
    parser.add_argument("--cpus", type=float, default=2.0)
    parser.add_argument("--memory", default="2g")
    parser.add_argument("--sample-interval-ms", type=int, default=50)
    parser.add_argument("--settle-ms", type=int, default=1500)
    parser.add_argument(
        "--task-timeout-ms",
        type=int,
        default=660_000,
        help="full wall-clock budget per task, including the bounded WebArena site reset",
    )
    parser.add_argument(
        "--max-site-workers",
        type=int,
        default=1,
        help="parallel site batches; batches sharing a site always remain serialized",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    suite_path = args.suite.resolve()
    scope_path = args.native_scope.resolve()
    config_paths = tuple(path.resolve() for path in args.webarena_config)
    evidence_root = args.evidence_root.resolve()
    missing_configs = [str(path) for path in config_paths if not path.is_file()]
    if missing_configs:
        raise ValueError(f"WebArena runtime configs are missing: {missing_configs}")
    if len(set(config_paths)) != len(config_paths):
        raise ValueError("WebArena runtime configs must be distinct isolated instances")
    all_shards = qualification_shards(scope_path, suite_path)
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    selected_batches = set(args.batch_ids or [])
    known_batches = {str(shard["batch_id"]) for shard in all_shards}
    if selected_batches - known_batches:
        unknown = sorted(selected_batches - known_batches)
        raise ValueError(f"unknown qualification batches: {unknown}")
    selected_shards = tuple(
        shard
        for shard in all_shards
        if not selected_batches or shard["batch_id"] in selected_batches
    )
    suite_sha256 = _sha256(suite_path)
    scope_sha256 = _sha256(scope_path)
    waves = qualification_waves(
        selected_shards,
        scope,
        max_workers=args.max_site_workers,
        isolated_instances=len(config_paths),
    )

    def run_shard(shard: dict[str, Any], qualification_pass_index: int, config_path: Path) -> Path:
        output = evidence_root / f"pass-{qualification_pass_index}" / str(shard["id"])
        if output.exists():
            try:
                validate_completed_shard(
                    output,
                    suite_sha256=suite_sha256,
                    scope_sha256=scope_sha256,
                    shard=shard,
                    qualification_pass_index=qualification_pass_index,
                )
            except Exception:
                failed = (
                    evidence_root
                    / "_failed"
                    / (
                        f"pass-{qualification_pass_index}-{shard['id']}-"
                        f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
                    )
                )
                failed.parent.mkdir(parents=True, exist_ok=True)
                output.replace(failed)
                print(f"PRESERVE incomplete {shard['id']} at {failed}", flush=True)
            else:
                print(
                    f"SKIP verified pass-{qualification_pass_index} {shard['id']}",
                    flush=True,
                )
                return output / "attempts.jsonl"
        command = [
            sys.executable,
            "-m",
            "harness.trajectory_replay.public_webarena.cli",
            "--suite",
            str(suite_path),
            "--native-scope",
            str(scope_path),
            "--batch-id",
            str(shard["batch_id"]),
            "--batch-shard-count",
            str(shard["shard_count"]),
            "--batch-shard-index",
            str(shard["shard_index"]),
            "--webarena-config",
            str(config_path),
            "--qualification-run",
            "--qualification-pass-index",
            str(qualification_pass_index),
            "--qualification-required-passes",
            "2",
            "--qualification-lock-key",
            str(shard["batch_id"]),
            "--engines",
            "chromium",
            "--failure-retries",
            "0",
            "--cpus",
            str(args.cpus),
            "--memory",
            args.memory,
            "--sample-interval-ms",
            str(args.sample_interval_ms),
            "--settle-ms",
            str(args.settle_ms),
            "--task-timeout-ms",
            str(args.task_timeout_ms),
            "--output",
            str(output),
            "--reports",
            str(evidence_root / "_qualification_reports_unused"),
        ]
        if args.env_file is not None:
            command.extend(["--env-file", str(args.env_file.resolve())])
        if args.browser_docker_host is not None:
            command.extend(["--browser-docker-host", args.browser_docker_host])
        if args.dry_run:
            print(" ".join(command), flush=True)
            return output / "attempts.jsonl"
        subprocess.run(command, check=True, env=os.environ.copy())
        validate_completed_shard(
            output,
            suite_sha256=suite_sha256,
            scope_sha256=scope_sha256,
            shard=shard,
            qualification_pass_index=qualification_pass_index,
        )
        return output / "attempts.jsonl"

    attempts_by_id: dict[tuple[int, str], Path] = {}
    for qualification_pass_index in (1, 2):
        for wave_index, wave in enumerate(waves, start=1):
            print(
                f"PASS {qualification_pass_index}/2 WAVE {wave_index}/{len(waves)}: "
                + ", ".join(str(batch[0]["batch_id"]) for batch in wave),
                flush=True,
            )

            def run_batch(
                batch: tuple[dict[str, Any], ...],
                config_path: Path,
                qualification_pass_index: int = qualification_pass_index,
            ) -> list[tuple[tuple[int, str], Path]]:
                return [
                    (
                        (qualification_pass_index, str(shard["id"])),
                        run_shard(shard, qualification_pass_index, config_path),
                    )
                    for shard in batch
                ]

            # Extra ready slices are allowed; only a shortage is an error.
            assigned = tuple(zip(wave, config_paths, strict=False))
            if len(assigned) != len(wave):
                raise RuntimeError("parallel wave exceeds isolated WebArena slice count")
            if args.dry_run or len(wave) == 1:
                completed = [run_batch(batch, config) for batch, config in assigned]
            else:
                with ThreadPoolExecutor(max_workers=len(wave)) as executor:
                    completed = list(
                        executor.map(
                            lambda item: run_batch(item[0], item[1]),
                            assigned,
                        )
                    )
            for batch_rows in completed:
                attempts_by_id.update(batch_rows)
    attempts_paths = [
        attempts_by_id[(pass_index, str(shard["id"]))]
        for pass_index in (1, 2)
        for shard in selected_shards
    ]
    if not args.dry_run and not selected_batches:
        queue = build_scoped_repair_queue(
            load_suite(suite_path),
            attempts_paths,
            scope,
            scope_sha256=scope_sha256,
        )
        _write_immutable(evidence_root / "qualification-queue.json", queue)


if __name__ == "__main__":
    main()
