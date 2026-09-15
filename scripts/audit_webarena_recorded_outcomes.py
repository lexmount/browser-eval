"""Score official human Playwright recordings with the pinned Verified evaluator.

This is a source-recording audit, not a current-environment replay.  It injects
the benchmark's own expected AgentResponse so the result isolates whether the
recorded network trace satisfies every programmatic NetworkEvent evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.resources
import json
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from webarena_verified import WebArenaVerified

SITE_PORTS = {
    "map": (3000,),
    "shopping": (7770,),
    "shopping_admin": (7780,),
    "gitlab": (8023,),
    "wikipedia": (8080, 8888),
    "reddit": (9999,),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resource_sha256(resource: Any) -> str:
    return hashlib.sha256(resource.read_bytes()).hexdigest()


def _network_requests(path: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        matches = [name for name in archive.namelist() if name.endswith("trace.network")]
        if len(matches) != 1:
            raise ValueError(f"{path.name}: expected exactly one trace.network")
        return [
            row["snapshot"]["request"]
            for row in (json.loads(line) for line in archive.read(matches[0]).decode().splitlines())
            if row.get("type") == "resource-snapshot"
        ]


def site_origins(requests: list[dict[str, Any]], sites: list[str]) -> dict[str, str]:
    known_ports = {port for ports in SITE_PORTS.values() for port in ports}
    by_port: dict[int, Counter[str]] = {port: Counter() for port in known_ports}
    for request in requests:
        split = urlsplit(str(request.get("url") or ""))
        try:
            port = split.port
        except ValueError:
            continue
        if port in by_port and split.scheme and split.netloc:
            by_port[port][f"{split.scheme}://{split.netloc}"] += 1
    result: dict[str, str] = {}
    for site in sites:
        candidates: Counter[str] = Counter()
        for port in SITE_PORTS[site]:
            candidates.update(by_port[port])
        if not candidates:
            raise ValueError(f"official trace contains no origin for {site}")
        result[site] = candidates.most_common(1)[0][0]
    return result


def _official_dataset() -> list[dict[str, Any]]:
    resource = importlib.resources.files("webarena_verified").joinpath(
        "assets/dataset/webarena-verified.json"
    )
    return json.loads(resource.read_text(encoding="utf-8"))


def audit_recorded_outcomes(
    catalog_path: Path, trace_dir: Path, suite_path: Path
) -> dict[str, Any]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows = catalog.get("traces") or []
    if catalog.get("count") != 179 or len(rows) != 179:
        raise ValueError("catalog must contain exactly 179 official traces")
    dataset = {int(task["task_id"]): task for task in _official_dataset()}
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    suite_tasks = {
        int(str(task["id"]).removeprefix("webarena-")): task for task in suite.get("tasks") or []
    }
    if set(suite_tasks) != {int(row["task_id"]) for row in rows}:
        raise ValueError("suite and official trace catalog task IDs differ")
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="webarena-recorded-audit-") as temporary:
        config_path = Path(temporary) / "config.json"
        for row in rows:
            task_id = int(row["task_id"])
            task = dataset[task_id]
            path = trace_dir / str(row["file_name"])
            if not path.is_file():
                raise FileNotFoundError(f"official trace is missing: {path.name}")
            digest = _sha256(path)
            expected_digest = suite_tasks[task_id]["trajectory"]["provenance"]["source_sha256"]
            if digest != expected_digest:
                raise ValueError(f"official trace sha256 mismatch: {path.name}")
            origins = site_origins(_network_requests(path), [str(site) for site in task["sites"]])
            config_path.write_text(
                json.dumps(
                    {
                        "environments": {
                            f"__{site.upper()}__": {"urls": [origin]}
                            for site, origin in origins.items()
                        }
                    }
                ),
                encoding="utf-8",
            )
            expected_response = next(
                evaluator["expected"]
                for evaluator in task["eval"]
                if evaluator["evaluator"] == "AgentResponseEvaluator"
            )
            implementation = WebArenaVerified(config=config_path)
            result = implementation.evaluate_task(
                task_id=task_id,
                agent_response=expected_response,
                network_trace=path,
            )
            dumped = result.model_dump(mode="json")
            evaluator_statuses = [
                {
                    "evaluator": str(item.get("evaluator_name") or ""),
                    "status": str(item.get("status") or ""),
                }
                for item in dumped.get("evaluators_results") or []
            ]
            results.append(
                {
                    "task_id": task_id,
                    "source_trace_sha256": digest,
                    "status": str(dumped.get("status") or "error"),
                    "score": float(dumped.get("score") or 0.0),
                    "evaluator_statuses": evaluator_statuses,
                }
            )
    status_counts = Counter(row["status"] for row in results)
    dataset_resource = importlib.resources.files("webarena_verified").joinpath(
        "assets/dataset/webarena-verified.json"
    )
    evaluator_resource = importlib.resources.files("webarena_verified").joinpath(
        "core/evaluation/evaluators/network_event_evaluator.py"
    )
    return {
        "schema": "lex_webarena_recorded_outcome_audit/1",
        "scope": "official_recording_against_official_programmatic_evaluators",
        "agent_response_policy": "inject_official_expected_response_to_isolate_network_trace",
        "current_environment_replay": False,
        "evaluator": {
            "package": "webarena-verified",
            "version": importlib.metadata.version("webarena-verified"),
            "dataset_sha256": _resource_sha256(dataset_resource),
            "network_evaluator_module_sha256": _resource_sha256(evaluator_resource),
        },
        "catalog_sha256": _sha256(catalog_path),
        "suite_sha256": _sha256(suite_path),
        "task_count": len(results),
        "status_counts": dict(sorted(status_counts.items())),
        "passing_task_ids": [row["task_id"] for row in results if row["status"] == "success"],
        "failing_task_ids": [row["task_id"] for row in results if row["status"] != "success"],
        "tasks": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-human-traces.json"),
    )
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-human-suite.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = audit_recorded_outcomes(args.catalog, args.trace_dir, args.suite)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
