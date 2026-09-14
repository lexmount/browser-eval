"""Validate the frozen WebArena trajectory-replay run contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from harness.browser_use.runner import SITE_PORTS
from harness.trajectory_replay.browser_use_seeds import (
    _authoritative_rows,
    _contains_ref,
    compile_replay_calls,
    extract_browser_calls,
)
from harness.trajectory_replay.runner import ENGINE_IMAGES

PROFILE_SCHEMA = "lex_webarena_trajectory_replay_profile/1"
SEED_SCHEMA = "lex_browser_use_replay_seeds/2"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replay_site_hosts(calls: list[dict[str, Any]]) -> set[str]:
    site_ports = {port for pair in SITE_PORTS.values() for port in pair}
    return {
        str(parsed.hostname)
        for call in calls
        if isinstance(call.get("url"), str)
        for parsed in [urlsplit(call["url"])]
        if parsed.scheme in {"http", "https"}
        and parsed.port in site_ports
        and parsed.hostname
    }


def replay_calls_binding_sha256(
    calls: list[dict[str, Any]], *, allowed_hosts: set[str]
) -> str:
    """Hash calls while ignoring only the deployment-specific WebArena gateway host."""

    site_ports = {port for pair in SITE_PORTS.values() for port in pair}
    normalized = []
    for raw_call in calls:
        call = dict(raw_call)
        url = call.get("url")
        if isinstance(url, str):
            parsed = urlsplit(url)
            if (
                parsed.scheme in {"http", "https"}
                and parsed.port in site_ports
                and parsed.hostname in allowed_hosts
            ):
                call["url"] = urlunsplit(
                    (
                        parsed.scheme,
                        f"__WEBARENA_GATEWAY__:{parsed.port}",
                        parsed.path,
                        parsed.query,
                        parsed.fragment,
                    )
                )
        normalized.append(call)
    payload = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _validate_canonical_candidate(
    task: dict[str, Any],
    candidate: dict[str, Any],
    repair_sha256: str,
    repair_record: dict[str, Any] | None,
    authoritative_row: dict[str, Any] | None,
) -> None:
    task_id = int(task["task_id"])
    calls = candidate.get("tool_calls")
    if (
        not isinstance(calls, list)
        or not calls
        or int(candidate.get("action_count") or 0) != len(calls)
        or not isinstance(candidate.get("recorded_response"), dict)
    ):
        raise ValueError(f"task {task_id} has an invalid canonical trajectory payload")
    source = candidate.get("source") or {}
    if candidate.get("candidate_kind") == "expert_repair":
        if (
            repair_record is None
            or source.get("kind") != "expert_repair_spec"
            or source.get("repair_spec_sha256") != repair_sha256
            or candidate.get("id")
            != str(
                repair_record.get("candidate_id") or f"expert__task-{task_id}"
            )
            or calls != repair_record.get("tool_calls")
            or candidate["recorded_response"] != repair_record.get("expected_response")
            or source.get("details") != repair_record.get("source")
            or candidate.get("repair_basis") != repair_record.get("repair_basis")
        ):
            raise ValueError(f"task {task_id} expert trajectory is not bound to the repair spec")
        return

    if candidate.get("official_score") != 1.0 or candidate.get("clean") is not True:
        raise ValueError(f"task {task_id} autonomous trajectory is not a clean official success")
    method = candidate.get("method") or {}
    if (
        authoritative_row is None
        or int(candidate.get("source_execution") or 0)
        != int(authoritative_row.get("attempt") or 0)
        or str(candidate.get("source_run_id") or "")
        != str(authoritative_row.get("run_id") or "")
        or method.get("model") != authoritative_row.get("model")
        or method.get("engine") != authoritative_row.get("engine")
        or float(authoritative_row.get("score") or 0) != 1.0
    ):
        raise ValueError(
            f"task {task_id} autonomous trajectory is not the authoritative final execution"
        )
    paths = {
        kind: Path(str(source.get(f"{kind}_path") or "")).expanduser().resolve()
        for kind in ("events", "response", "evaluation")
    }
    for kind, path in paths.items():
        if not path.is_file() or _sha256(path) != source.get(f"{kind}_sha256"):
            raise ValueError(f"task {task_id} autonomous {kind} evidence is missing or changed")
        if path != Path(
            str((authoritative_row.get("paths") or {}).get(kind) or "")
        ).expanduser().resolve():
            raise ValueError(
                f"task {task_id} autonomous {kind} evidence is not from the final execution"
            )
    source_calls = extract_browser_calls(paths["events"])
    if (
        compile_replay_calls(paths["events"]) != calls
        or any(item["status"] != "success" for item in source_calls)
        or _read_json(paths["response"]) != candidate["recorded_response"]
    ):
        raise ValueError(f"task {task_id} autonomous trajectory differs from source evidence")
    evaluation = _read_json(paths["evaluation"])
    if int(evaluation.get("task_id") or -1) != task_id or float(
        evaluation.get("score") or 0
    ) != 1.0:
        raise ValueError(f"task {task_id} autonomous source evaluation is not passing")


def load_replay_plan(
    profile_path: str | Path, seed_manifest_path: str | Path
) -> dict[str, Any]:
    """Return a validated, exact method and task plan for one formal replay suite."""

    profile_file = Path(profile_path).expanduser().resolve()
    repo_root = profile_file.parents[3]
    profile = _read_json(profile_file)
    if profile.get("schema") != PROFILE_SCHEMA:
        raise ValueError("unsupported trajectory replay profile")

    selection = profile.get("task_selection") or {}
    selection_file = (repo_root / str(selection.get("path") or "")).resolve()
    if repo_root not in selection_file.parents:
        raise ValueError("task selection must stay inside the repository")
    if not selection_file.is_file() or _sha256(selection_file) != selection.get("sha256"):
        raise ValueError("trajectory replay task selection does not match the frozen profile")
    selection_payload = _read_json(selection_file)
    task_ids = [int(item["task_id"]) for item in selection_payload.get("tasks") or []]
    if len(task_ids) != int(selection.get("task_count") or 0) or len(task_ids) != len(
        set(task_ids)
    ):
        raise ValueError("trajectory replay task selection cardinality is invalid")

    repair = profile.get("seed_inventory") or {}
    repair_file = (repo_root / str(repair.get("expert_repairs_path") or "")).resolve()
    if repo_root not in repair_file.parents:
        raise ValueError("expert repairs must stay inside the repository")
    if not repair_file.is_file() or _sha256(repair_file) != repair.get(
        "expert_repairs_sha256"
    ):
        raise ValueError("expert repairs do not match the frozen replay profile")
    repair_payload = _read_json(repair_file)
    repair_records = {
        int(item["task_id"]): item for item in repair_payload.get("repairs") or []
    }
    if len(repair_records) != len(repair_payload.get("repairs") or []):
        raise ValueError("expert repairs contain duplicate task IDs")

    seed_file = Path(seed_manifest_path).expanduser().resolve()
    seeds = _read_json(seed_file)
    if seeds.get("schema") != SEED_SCHEMA:
        raise ValueError("unsupported replay seed manifest")
    seed_source = seeds.get("source") or {}
    if seed_source.get("selection_sha256") != selection.get("sha256"):
        raise ValueError("replay seeds are not bound to the frozen task selection")
    seed_repairs = seeds.get("expert_repairs") or {}
    if seed_repairs.get("repair_spec_sha256") != repair.get("expert_repairs_sha256"):
        raise ValueError("replay seeds are not bound to the frozen expert repairs")
    authoritative: dict[tuple[str, str, int], dict[str, Any]] = {}
    for declared_model, declared in (seed_source.get("evidence") or {}).items():
        root = Path(str((declared or {}).get("root") or "")).expanduser().resolve()
        attempts_file = root / "attempts.jsonl"
        manifest_file = root / "manifest.json"
        if (
            not attempts_file.is_file()
            or not manifest_file.is_file()
            or _sha256(attempts_file) != declared.get("attempts_sha256")
            or _sha256(manifest_file) != declared.get("manifest_sha256")
        ):
            raise ValueError("replay seed authority manifest is missing or changed")
        manifest, rows = _authoritative_rows(root)
        model = str((manifest.get("protocol") or {}).get("model") or "")
        if model != declared_model or manifest.get("selection_sha256") != selection.get(
            "sha256"
        ):
            raise ValueError("replay seed authority differs from the frozen selection")
        for row in rows:
            key = (model, str(row["engine"]), int(row["task_id"]))
            if key in authoritative:
                raise ValueError("replay seed authority contains duplicate final executions")
            authoritative[key] = row
    seed_tasks = seeds.get("tasks") or []
    seed_task_ids = [int(item["task_id"]) for item in seed_tasks]
    if len(seed_task_ids) != len(set(seed_task_ids)) or set(seed_task_ids) != set(task_ids):
        raise ValueError("replay seeds do not match the frozen task selection")
    ready = [item for item in seed_tasks if item.get("replay_status") == "ready"]
    if len(ready) != int(repair.get("required_ready_tasks") or 0):
        raise ValueError("replay seeds do not provide the required ready-task coverage")
    for item in seed_tasks:
        candidate_ids = [
            str(candidate.get("id") or "") for candidate in item.get("candidates") or []
        ]
        canonical_id = str(item.get("canonical_candidate_id") or "")
        if (
            not canonical_id
            or candidate_ids.count(canonical_id) != 1
            or len(candidate_ids) != len(set(candidate_ids))
        ):
            raise ValueError("every replay task must identify one unique canonical trajectory")
        canonical = next(
            candidate
            for candidate in item.get("candidates") or []
            if candidate.get("id") == canonical_id
        )
        if _contains_ref(canonical["tool_calls"]):
            raise ValueError(
                f"task {item['task_id']} canonical trajectory contains an ephemeral ref"
            )
        _validate_canonical_candidate(
            item,
            canonical,
            str(repair["expert_repairs_sha256"]),
            repair_records.get(int(item["task_id"])),
            authoritative.get(
                (
                    str((canonical.get("method") or {}).get("model") or ""),
                    str((canonical.get("method") or {}).get("engine") or ""),
                    int(item["task_id"]),
                )
            ),
        )

    methods = profile.get("methods") or []
    method_ids = [str(item.get("id") or "") for item in methods]
    engines = [str(item.get("engine") or "") for item in methods]
    if (
        not methods
        or len(method_ids) != len(set(method_ids))
        or len(engines) != len(set(engines))
        or any(engine not in ENGINE_IMAGES for engine in engines)
    ):
        raise ValueError("trajectory replay methods must be unique registered engines")

    execution = profile.get("execution") or {}
    if execution.get("environment_pool_count") != 1:
        raise ValueError("the fixed-port WebArena stack supports exactly one environment pool")
    if execution.get("method_parallelism") != 1 or execution.get("task_parallelism") != 1:
        raise ValueError("one environment pool requires serial methods and serial tasks")
    if not execution.get("reset_before_every_task") or not execution.get(
        "teardown_between_methods"
    ):
        raise ValueError("formal replay requires task resets and method teardown")
    trusted_source_site_hosts = {
        str(host) for host in execution.get("trusted_source_site_hosts") or []
    }
    if not trusted_source_site_hosts or any(
        not host or ":" in host or "/" in host for host in trusted_source_site_hosts
    ):
        raise ValueError("formal replay requires explicit trusted source site hosts")
    for task in seed_tasks:
        canonical = next(
            candidate
            for candidate in task.get("candidates") or []
            if candidate.get("id") == task.get("canonical_candidate_id")
        )
        untrusted = replay_site_hosts(canonical["tool_calls"]) - trusted_source_site_hosts
        if untrusted:
            raise ValueError(
                f"task {task['task_id']} canonical trajectory uses untrusted site hosts: "
                f"{sorted(untrusted)}"
            )
    if profile.get("runs_per_task") != 1:
        raise ValueError("formal replay requires exactly one logical run per task and method")
    if repair.get("canonical_trajectories_per_task") != 1:
        raise ValueError("formal replay requires exactly one canonical trajectory per task")
    if (profile.get("scoring") or {}).get("model_calls") != 0:
        raise ValueError("trajectory replay must not call a model")

    return {
        "profile": profile,
        "profile_path": str(profile_file),
        "profile_sha256": _sha256(profile_file),
        "seed_manifest": seeds,
        "seed_manifest_path": str(seed_file),
        "seed_manifest_sha256": _sha256(seed_file),
        "task_ids": task_ids,
        "methods": methods,
        "engines": engines,
        "trusted_source_site_hosts": trusted_source_site_hosts,
    }
