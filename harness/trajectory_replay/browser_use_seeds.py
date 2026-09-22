"""Derive fixed replay candidates from successful autonomous Browser Use evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

SCHEMA = "lex_browser_use_replay_seeds/2"

_REF_ACTIONS = {"click", "fill", "select"}
_NODE_RE = re.compile(r"^(?P<indent>\s*)\[(?P<ref>\d+)\]\s+(?P<tag>[a-zA-Z][\w-]*)(?P<body>.*)$")
_ATTR_RE = re.compile(r'(?P<name>[A-Za-z][\w-]*)="(?P<value>(?:[^"\\]|\\.)*)"')


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _contains_ref(value: Any) -> bool:
    if isinstance(value, dict):
        return "ref" in value or any(_contains_ref(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_ref(item) for item in value)
    return False


def _snapshot_nodes(result: str) -> list[dict[str, Any]]:
    """Parse the stable, reader-visible element facts emitted by BrowserTool snapshots."""

    nodes = []
    for line in result.splitlines():
        match = _NODE_RE.match(line)
        if match is None:
            continue
        body = match.group("body")
        attributes = {
            item.group("name"): json.loads(f'"{item.group("value")}"')
            for item in _ATTR_RE.finditer(body)
        }
        body_without_attributes = _ATTR_RE.sub("", body).strip()
        text = (
            body_without_attributes[1:].strip() if body_without_attributes.startswith(":") else ""
        )
        nodes.append(
            {
                "ref": int(match.group("ref")),
                "tag": match.group("tag").lower(),
                "attributes": attributes,
                "text": text,
            }
        )
    return nodes


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _base_selector(node: dict[str, Any]) -> str:
    tag = str(node["tag"])
    attributes = node["attributes"]
    for snapshot_name, dom_name in (
        ("name", "name"),
        ("href", "href"),
        ("ariaLabel", "aria-label"),
        ("placeholder", "placeholder"),
        ("alt", "alt"),
        ("role", "role"),
    ):
        value = attributes.get(snapshot_name)
        if value and not str(value).endswith("..."):
            return f"{tag}[{dom_name}={_quoted(str(value))}]:visible"
    text = str(node.get("text") or "").strip()
    if text and not text.endswith("..."):
        return f"{tag}:visible:has-text({_quoted(text)})"
    type_value = attributes.get("type")
    if type_value:
        return f"{tag}[type={_quoted(str(type_value))}]:visible"
    return f"{tag}:visible"


def _same_selector_population(target: dict[str, Any], other: dict[str, Any]) -> bool:
    """Mirror the DOM population matched by the target's generated selector."""

    if target["tag"] != other["tag"]:
        return False
    target_attributes = target["attributes"]
    other_attributes = other["attributes"]
    for name in ("name", "href", "ariaLabel", "placeholder", "alt", "role"):
        value = target_attributes.get(name)
        if value and not str(value).endswith("..."):
            return other_attributes.get(name) == value
    text = str(target.get("text") or "").strip()
    if text and not text.endswith("..."):
        return text in str(other.get("text") or "")
    type_value = target_attributes.get("type")
    if type_value:
        return other_attributes.get("type") == type_value
    return True


def _selector_for_ref(nodes: list[dict[str, Any]], ref: int, *, context: str) -> str:
    target = next((node for node in nodes if node["ref"] == ref), None)
    if target is None:
        raise ValueError(f"{context}: ref {ref} is absent from the preceding snapshot")
    base = _base_selector(target)
    matches = [node for node in nodes if _same_selector_population(target, node)]
    if len(matches) == 1:
        return base
    # Shared hrefs often identify unrelated menu entries. Keep the original
    # element (not its text-bearing child), but prefer its complete label when
    # that label uniquely narrows the snapshot population. Playwright has-text
    # normalizes whitespace and matches case-insensitive substrings; use the
    # same conservative population check rather than assuming exact matching.
    text = " ".join(str(target.get("text") or "").split())
    labels = [" ".join(str(node.get("text") or "").split()) for node in matches]
    if (
        target["attributes"].get("href")
        and text
        and not any(label.endswith(("...", "…")) for label in labels)
        and sum(text.lower() in label.lower() for label in labels) == 1
    ):
        return f"{base}:has-text({_quoted(text)})"
    # Missing, truncated or ambiguous labels retain the precise original ref's
    # ordinal. Do not silently replace it with the first matching text node.
    occurrence = next(index for index, node in enumerate(matches, start=1) if node is target)
    return f":nth-match({base}, {occurrence})"


def compile_replay_calls(events_path: str | Path) -> list[dict[str, Any]]:
    """Compile ephemeral refs into fixed selectors while preserving ref action semantics."""

    calls = extract_browser_calls(events_path)
    latest_nodes: list[dict[str, Any]] = []
    compiled: list[dict[str, Any]] = []
    for index, call in enumerate(calls, start=1):
        arguments = dict(call.get("arguments") or {})
        action = str(arguments.get("action") or "")
        if action in _REF_ACTIONS and "ref" in arguments:
            if arguments.get("selector"):
                arguments.pop("ref", None)
            else:
                arguments["selector"] = _selector_for_ref(
                    latest_nodes,
                    int(arguments.pop("ref")),
                    context=f"{events_path}: call {index}",
                )
                arguments["replay_mode"] = "snapshot_ref_semantics"
        elif "ref" in arguments:
            # BrowserTool ignores refs for actions such as ``press``. Keeping that
            # inert model-supplied field would make the fixed trajectory appear to
            # depend on an ephemeral snapshot identity when it does not.
            arguments.pop("ref")
        if _contains_ref(arguments):
            raise ValueError(f"{events_path}: call {index} contains an unsupported ref")
        compiled.append(arguments)
        result = str(call.get("result") or "")
        snapshot_nodes = _snapshot_nodes(result)
        if snapshot_nodes:
            latest_nodes = snapshot_nodes
    return compiled


def extract_browser_calls(events_path: str | Path) -> list[dict[str, Any]]:
    """Return complete BrowserTool calls in execution order, including call status."""

    starts: dict[str, dict[str, Any]] = {}
    calls: list[dict[str, Any]] = []
    for event in _read_json(Path(events_path)):
        event_type = event.get("type")
        data = event.get("data") or {}
        call_id = str(data.get("tool_call_id") or "")
        if event_type == "tool_execution_start":
            if not call_id or call_id in starts:
                raise ValueError(f"invalid or duplicate tool call id in {events_path}: {call_id!r}")
            starts[call_id] = {
                "tool_call_id": call_id,
                "tool_name": str(data.get("tool_name") or ""),
                "arguments": dict(data.get("arguments") or {}),
                "started_at": event.get("timestamp"),
            }
        elif event_type == "tool_execution_end":
            start = starts.pop(call_id, None)
            if start is None:
                raise ValueError(f"tool end event has no start in {events_path}: {call_id!r}")
            start.update(
                {
                    "status": str(data.get("status") or ""),
                    "duration_seconds": data.get("execution_time"),
                    "error": data.get("error"),
                    "result": data.get("result"),
                    "ended_at": event.get("timestamp"),
                }
            )
            calls.append(start)
    if starts:
        raise ValueError(f"tool calls have no end event in {events_path}: {sorted(starts)}")
    return calls


def _authoritative_rows(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = _read_json(root / "manifest.json")
    model = str((manifest.get("protocol") or {}).get("model") or "")
    if not model:
        raise ValueError(f"evidence manifest does not identify a model: {root}")
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for line in (root / "attempts.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            grouped[(str(row["engine"]), int(row["task_id"]))].append(row)
    rows: list[dict[str, Any]] = []
    for (engine, task_id), executions in sorted(grouped.items()):
        executions.sort(key=lambda row: int(row["attempt"]))
        expected = list(range(1, len(executions) + 1))
        if [int(row["attempt"]) for row in executions] != expected:
            raise ValueError(f"non-contiguous executions for {model}/{engine}/{task_id}")
        if len(executions) > 2:
            raise ValueError(f"more than one retry for {model}/{engine}/{task_id}")
        row = dict(executions[-1])
        row["model"] = model
        rows.append(row)
    return manifest, rows


def build_replay_seed_manifest(
    evidence_dirs: list[str | Path], profile: dict[str, Any]
) -> dict[str, Any]:
    """Build a private replay seed inventory without using human demonstration traces."""

    models = [str(value) for value in profile.get("models") or []]
    engines = [str(value) for value in profile.get("engines") or []]
    method_priority = [(model, engine) for model in models for engine in engines]
    if not method_priority:
        raise ValueError("profile must register models and engines")

    manifests: dict[str, dict[str, Any]] = {}
    authoritative: list[dict[str, Any]] = []
    source_roots: dict[str, Path] = {}
    for raw_root in evidence_dirs:
        root = Path(raw_root).expanduser().resolve()
        manifest, rows = _authoritative_rows(root)
        model = str((manifest.get("protocol") or {}).get("model"))
        if model in manifests:
            raise ValueError(f"duplicate evidence cohort for model: {model}")
        manifests[model] = manifest
        source_roots[model] = root
        authoritative.extend(rows)
    if set(manifests) != set(models):
        raise ValueError(
            f"evidence models differ from profile: expected={models}, actual={sorted(manifests)}"
        )
    selection_hashes = {str(item.get("selection_sha256") or "") for item in manifests.values()}
    if len(selection_hashes) != 1 or "" in selection_hashes:
        raise ValueError("evidence cohorts do not share one frozen task selection")

    task_metadata: dict[int, dict[str, Any]] = {}
    for manifest in manifests.values():
        for item in manifest.get("selection") or []:
            task_id = int(item["task_id"])
            if task_id in task_metadata and task_metadata[task_id] != item:
                raise ValueError(f"task metadata differs between evidence cohorts: {task_id}")
            task_metadata[task_id] = dict(item)

    candidates_by_task: dict[int, list[dict[str, Any]]] = defaultdict(list)
    passed_methods_by_task: dict[int, list[str]] = defaultdict(list)
    for row in authoritative:
        task_id = int(row["task_id"])
        if float(row.get("score") or 0) != 1:
            continue
        model, engine = str(row["model"]), str(row["engine"])
        method_id = f"{model}__{engine}"
        passed_methods_by_task[task_id].append(method_id)
        events_path = Path(str((row.get("paths") or {}).get("events") or ""))
        response_path = Path(str((row.get("paths") or {}).get("response") or ""))
        evaluation_path = Path(str((row.get("paths") or {}).get("evaluation") or ""))
        calls = extract_browser_calls(events_path)
        try:
            replay_calls = compile_replay_calls(events_path)
        except (TypeError, ValueError):
            replay_calls = []
        clean = (
            bool(calls)
            and bool(replay_calls)
            and all(call["status"] == "success" for call in calls)
        )
        candidate = {
            "id": f"{method_id}__task-{task_id}",
            "method": {"model": model, "engine": engine},
            "source_execution": int(row["attempt"]),
            "source_run_id": str(row.get("run_id") or ""),
            "official_score": 1.0,
            "clean": clean,
            "action_count": len(calls),
            "actions": [str((call.get("arguments") or {}).get("action") or "") for call in calls],
            "tool_calls": replay_calls,
            "recorded_response": _read_json(response_path),
            "source": {
                "events_path": str(events_path),
                "events_sha256": _sha256(events_path),
                "response_path": str(response_path),
                "response_sha256": _sha256(response_path),
                "evaluation_path": str(evaluation_path),
                "evaluation_sha256": _sha256(evaluation_path),
            },
        }
        candidates_by_task[task_id].append(candidate)

    priority_index = {method: index for index, method in enumerate(method_priority)}
    tasks: list[dict[str, Any]] = []
    for task_id in sorted(task_metadata):
        candidates = sorted(
            candidates_by_task.get(task_id, []),
            key=lambda item: priority_index[(item["method"]["model"], item["method"]["engine"])],
        )
        clean_candidates = [item for item in candidates if item["clean"]]
        tasks.append(
            {
                **task_metadata[task_id],
                "successful_methods": sorted(passed_methods_by_task.get(task_id, [])),
                "candidates": candidates,
                "canonical_candidate_id": clean_candidates[0]["id"] if clean_candidates else None,
                "replay_status": (
                    "ready"
                    if clean_candidates
                    else "success_contains_failed_actions"
                    if candidates
                    else "all_methods_failed"
                ),
            }
        )

    return {
        "schema": SCHEMA,
        "source": {
            "kind": "authoritative successful Browser Use executions",
            "human_demonstrations_used": False,
            "benchmark": str(profile.get("benchmark") or "WebArena-Verified"),
            "benchmark_version": str(profile.get("benchmark_version") or ""),
            "official_data_checksum": str(profile.get("official_data_checksum") or ""),
            "selection_sha256": selection_hashes.pop(),
            "evidence": {
                model: {
                    "root": str(source_roots[model]),
                    "attempts_sha256": _sha256(source_roots[model] / "attempts.jsonl"),
                    "manifest_sha256": _sha256(source_roots[model] / "manifest.json"),
                }
                for model in models
            },
        },
        "selection_policy": {
            "authority": "final execution declared by each frozen Browser Use manifest",
            "eligibility": (
                "official score 1.0, every BrowserTool call completed successfully, and "
                "every ephemeral snapshot ref compiled to a fixed selector"
            ),
            "canonical_priority": [f"{model}__{engine}" for model, engine in method_priority],
            "performance_used_for_selection": False,
        },
        "counts": {
            "tasks": len(tasks),
            "tasks_with_authoritative_success": sum(bool(item["candidates"]) for item in tasks),
            "replay_ready_tasks": sum(item["replay_status"] == "ready" for item in tasks),
            "success_with_failed_actions_tasks": sum(
                item["replay_status"] == "success_contains_failed_actions" for item in tasks
            ),
            "all_methods_failed_tasks": sum(
                item["replay_status"] == "all_methods_failed" for item in tasks
            ),
            "successful_method_trajectories": sum(len(item["candidates"]) for item in tasks),
            "clean_method_trajectories": sum(
                sum(bool(candidate["clean"]) for candidate in item["candidates"]) for item in tasks
            ),
        },
        "tasks": tasks,
    }
