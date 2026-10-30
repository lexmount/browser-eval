"""Bind a diagnostic replay to an externally reset WebArena deployment.

The URL map and reset receipt are explicit inputs. Merely reaching a remote
site is not evidence that its writable fixture was reset for a task.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from webarena_verified import WebArenaVerified
from webarena_verified.types.config import EnvironmentConfig, WebArenaVerifiedConfig
from webarena_verified.types.task import WebArenaSite

from harness.distribution import repository_root
from harness.environments.webarena.topology import (
    LOCAL_RECORDED_HOSTS,
    READ_ONLY_PORTS,
    SITE_CREDENTIALS,
    SITE_PORTS,
)
from harness.trajectory_replay.retrieval_acceptance import digest as retrieval_digest
from harness.trajectory_replay.retrieval_acceptance import validate_contract
from harness.web_fetch.safety import unlinked_path, write_new_file
from lexbench_webfetch.paths import contained_path

COHORT_RESET_SCOPE = "once_before_method_cohort"
METHOD_ID = re.compile(r"[a-z0-9][a-z0-9._-]{1,79}\Z")
SUPPORTED_REMOTE_SITES = frozenset(SITE_PORTS) | frozenset(READ_ONLY_PORTS.values())


def load_remote_sites(path: Path) -> dict[WebArenaSite, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "lex_webarena_remote_sites/1":
        raise ValueError("unsupported remote site map")
    raw = value.get("sites")
    if (
        not isinstance(raw, dict)
        or not {site.value for site in SITE_PORTS} <= set(raw)
        or not set(raw) <= {site.value for site in SUPPORTED_REMOTE_SITES}
    ):
        raise ValueError(
            "remote site map must cover the four mutable sites and optional read-only sites"
        )
    sites: dict[WebArenaSite, str] = {}
    for site in sorted(SUPPORTED_REMOTE_SITES, key=lambda item: item.value):
        if site.value not in raw:
            continue
        url = raw[site.value]
        if not isinstance(url, str):
            raise ValueError(f"invalid remote URL for {site.value}")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"invalid remote URL for {site.value}")
        if site == WebArenaSite.SHOPPING_ADMIN and parsed.path.rstrip("/") != "/admin":
            raise ValueError("Shopping Admin remote URL must end in /admin")
        if site != WebArenaSite.SHOPPING_ADMIN and parsed.path not in {"", "/"}:
            raise ValueError(f"remote URL for {site.value} must be an origin")
        sites[site] = url.rstrip("/")
    origins = {urlsplit(url).netloc for url in sites.values()}
    if len(origins) != len(sites):
        raise ValueError("mutable sites must use distinct remote origins")
    return sites


def remote_benchmark(sites: dict[WebArenaSite, str]) -> WebArenaVerified:
    return WebArenaVerified(
        config=WebArenaVerifiedConfig(
            environments={
                site: EnvironmentConfig(
                    # Reverse proxies can terminate HTTPS while an application
                    # still emits same-host HTTP requests from its configured
                    # public base URL. Both transports identify the same bound
                    # fixture and must derender to the same site token.
                    urls=list(
                        dict.fromkeys(
                            [
                                url,
                                urlunsplit(
                                    (
                                        "http" if urlsplit(url).scheme == "https" else "https",
                                        urlsplit(url).netloc,
                                        urlsplit(url).path,
                                        urlsplit(url).query,
                                        urlsplit(url).fragment,
                                    )
                                ),
                            ]
                        )
                    ),
                    credentials=SITE_CREDENTIALS.get(site),
                    use_header_login=site == WebArenaSite.SHOPPING_ADMIN,
                )
                for site, url in sites.items()
            }
        )
    )


def rebind_remote_calls(
    calls: list[dict[str, Any]],
    sites: dict[WebArenaSite, str],
    trusted_source_hosts: set[str],
    source_sites: dict[WebArenaSite, str] | None = None,
) -> list[dict[str, Any]]:
    """Bind frozen WebArena URLs while tolerating HTTP-to-HTTPS proxy upgrades.

    An exact same-site href selector may point at either scheme after a reverse
    proxy redirects the document. Preserve its host, path, query and action
    semantics; only the transport scheme becomes an explicit alternative.
    """
    by_port = {port: site for site, ports in SITE_PORTS.items() for port in ports}
    by_port.update(READ_ONLY_PORTS)
    by_source_origin = {urlsplit(url).netloc: site for site, url in (source_sites or {}).items()}

    def rebind_url(value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"}:
            return value
        site = by_source_origin.get(parsed.netloc)
        if site is None and parsed.hostname in trusted_source_hosts:
            site = by_port.get(parsed.port)
        if site is None or site not in sites:
            return value
        target = urlsplit(sites[site])
        return urlunsplit(
            (target.scheme, target.netloc, parsed.path, parsed.query, parsed.fragment)
        )

    href_pattern = re.compile(
        r"(?P<before>\[href\s*=\s*)(?P<quote>['\"])(?P<url>https?://[^'\"]+)"
        r"(?P=quote)(?P<after>\])"
    )
    embedded_url_pattern = re.compile(r"https?://[^\s\"'\])]+")

    def rebind_embedded_urls(value: str) -> str:
        return embedded_url_pattern.sub(lambda match: rebind_url(match.group()), value)

    def rebind_href(match: re.Match[str]) -> str:
        url = match.group("url")
        rebound = rebind_url(url)
        if rebound == url:
            return match.group(0)
        parsed = urlsplit(rebound)
        other_scheme = "https" if parsed.scheme == "http" else "http"
        alternative = urlunsplit(
            (other_scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment)
        )
        quote = match.group("quote")
        return f":is([href={quote}{rebound}{quote}],[href={quote}{alternative}{quote}])"

    rebound: list[dict[str, Any]] = []
    for raw in calls:
        call = dict(raw)
        url = call.get("url")
        if isinstance(url, str):
            call["url"] = rebind_url(url)
        # URL-valued form inputs refer to the same fixture deployment as
        # navigation URLs. Do not rewrite prose or arbitrary external URLs.
        text = call.get("text")
        if (
            call.get("action") == "fill"
            and isinstance(text, str)
            and not any(char.isspace() for char in text)
        ):
            call["text"] = rebind_url(text)
        selector = call.get("selector")
        if isinstance(selector, str):
            call["selector"] = href_pattern.sub(rebind_href, selector)
        target = call.get("target")
        if isinstance(target, dict):
            rebound_target = dict(target)
            for key in ("value", "name"):
                value = rebound_target.get(key)
                if isinstance(value, str):
                    rebound_target[key] = rebind_embedded_urls(value)
            call["target"] = rebound_target
        script = call.get("script")
        if call.get("action") == "evaluate" and isinstance(script, str):
            # Some qualified actions submit site URLs inside a JavaScript
            # request body. Rewrite only origins explicitly bound to a source
            # deployment; leave the rest of the program intact.
            for source_site, source_url in (source_sites or {}).items():
                source_origin = urlsplit(source_url)
                target_origin = urlsplit(sites[source_site])
                old = f"{source_origin.scheme}://{source_origin.netloc}"
                new = f"{target_origin.scheme}://{target_origin.netloc}"
                script = script.replace(old, new)
            call["script"] = script
        rebound.append(call)
    return rebound


def validate_remote_reset_receipt(
    path: Path, *, required_sites: set[WebArenaSite], site_map_sha256: str
) -> dict[str, Any]:
    """Require a fresh, task-bound Pod replacement and application readiness."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("schema") != "lex_webarena_remote_reset/1"
        or value.get("site_map_sha256") != site_map_sha256
        or not isinstance(value.get("task_id"), int)
        or not isinstance(value.get("app_id"), int)
    ):
        raise ValueError("remote reset receipt is not bound to this deployment")
    rows = value.get("sites")
    if not isinstance(rows, dict) or not {site.value for site in required_sites} <= set(rows):
        raise ValueError("remote reset receipt lacks a required site")
    for site in required_sites:
        row = rows[site.value]
        if (
            not isinstance(row, dict)
            or not row.get("image_digest")
            or not row.get("operation_event_id")
            or not row.get("old_pod")
            or not row.get("new_pod")
            or row["old_pod"] == row["new_pod"]
            or row.get("operation_status") != "success"
            or row.get("writable_layer_marker") != "absent"
            or row.get("env_ctrl_ready") is not True
            or row.get("public_http_status") != 200
            or (
                site in {WebArenaSite.SHOPPING, WebArenaSite.SHOPPING_ADMIN}
                and row.get("elasticsearch_status") not in {"yellow", "green"}
            )
        ):
            raise ValueError(f"remote reset is not proven for {site.value}")
    return value


def validate_remote_cohort_reset_receipt(
    path: Path,
    *,
    site_map_sha256: str,
    app_id: int,
    reset_scope: str,
    site_reset_between_tasks: bool = False,
    admin_https_base_url: str | None = None,
    required_readiness_probes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Bind a Pod replacement and public readiness to its declared reset scope.

    This proves replacement and public readiness for the four mutable sites.
    Task-level acceptance still needs the recorded browser/network evidence.
    """
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("schema") != "webarena_remote_cohort_reset/1"
        or value.get("app_id") != app_id
        or value.get("site_map_sha256") != site_map_sha256
        or value.get("reset_scope") != reset_scope
        or value.get("site_reset_between_tasks") is not site_reset_between_tasks
    ):
        raise ValueError("remote cohort reset receipt is not bound to this deployment")
    sites = value.get("sites")
    if not isinstance(sites, dict) or set(sites) != {site.value for site in SITE_PORTS}:
        raise ValueError("remote cohort reset receipt must cover all mutable sites")
    for site, row in sites.items():
        if (
            not isinstance(row, dict)
            or not row.get("service_id")
            or not row.get("image_digest")
            or not row.get("event_id")
            or row.get("event_status") != "success"
            or not row.get("old_pod")
            or not row.get("new_pod")
            or row["old_pod"] == row["new_pod"]
            or row.get("data_volume_count") != 0
            or row.get("public_http_status") != 200
        ):
            raise ValueError(f"remote cohort reset is not proven for {site}")
    for site in ("gitlab", "reddit"):
        check = sites[site].get("baseline_check")
        if not isinstance(check, dict) or check.get("row_count") != 0:
            raise ValueError(f"remote cohort baseline is not proven for {site}")
    if admin_https_base_url is not None:
        config = sites["shopping_admin"].get("https_admin_config")
        if (
            not isinstance(config, dict)
            or config.get("web_secure_base_url") != admin_https_base_url
            or config.get("web_secure_use_in_adminhtml") != 1
            or config.get("config_cache_cleaned") is not True
            or config.get("public_static_resource_scheme") != "https"
        ):
            raise ValueError("remote cohort admin HTTPS configuration is not proven")
    if required_readiness_probes:
        observed = value.get("site_readiness")
        if not isinstance(observed, list) or len(observed) != len(required_readiness_probes):
            raise ValueError("remote cohort site readiness scope is incomplete")
        expected_by_url = {probe["url"]: probe for probe in required_readiness_probes}
        if len(expected_by_url) != len(required_readiness_probes):
            raise ValueError("duplicate required site readiness URL")
        observed_by_url = {probe.get("url"): probe for probe in observed if isinstance(probe, dict)}
        if len(observed_by_url) != len(observed) or set(observed_by_url) != set(expected_by_url):
            raise ValueError("remote cohort site readiness URL differs")
        for url, expected in expected_by_url.items():
            probe = observed_by_url[url]
            limit = expected["max_stable_ms"]
            if (
                probe.get("site") != expected["site"]
                or probe.get("path") != expected["path"]
                or probe.get("method") != "GET"
                or probe.get("read_only") is not True
                or type(probe.get("cold_ms")) not in {int, float}
                or probe["cold_ms"] <= 0
                or probe.get("warm_http_status") != 200
                or probe.get("stable_http_status") != 200
                or not (
                    probe.get("cold_http_status") == 200
                    or (
                        (
                            (
                                type(probe.get("cold_http_status")) is int
                                and 500 <= probe["cold_http_status"] <= 599
                            )
                            or (
                                probe.get("cold_http_status") == 0
                                and probe.get("cold_error") == "timeout"
                            )
                        )
                        and probe.get("recovery_http_status") == 200
                        and type(probe.get("recovery_ms")) in {int, float}
                        and probe["recovery_ms"] > 0
                    )
                )
                or any(
                    type(probe.get(f"{phase}_ms")) not in {int, float}
                    or not 0 < probe[f"{phase}_ms"] <= limit
                    for phase in ("warm", "stable")
                )
            ):
                raise ValueError(f"remote cohort site readiness is not proven for {url}")
    return value


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_method_identity(method_id: str, engine: str) -> None:
    if not isinstance(method_id, str) or not METHOD_ID.fullmatch(method_id):
        raise ValueError("method ID is not a safe stable identifier")
    if engine not in {"moli", "chromium"}:
        raise ValueError("unsupported browser engine")


def selected_task_ids(path: Path) -> list[int]:
    """Read a complete frozen selection in the upstream numeric execution order."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("task selection is missing or symlinked")
    selection = json.loads(path.read_text(encoding="utf-8"))
    rows = selection.get("tasks")
    if selection.get("schema") != "lex_webarena_verified_pilot/1" or not isinstance(rows, list):
        raise ValueError("selected-task schema changed")
    ids = [row.get("task_id") for row in rows if isinstance(row, dict)]
    if (not ids or len(ids) != len(rows) or any(type(value) is not int for value in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("selected-task set is empty or duplicated")
    return sorted(ids)


def checked_file_sha256(path: Path, label: str = "evidence") -> str:
    path = unlinked_path(path)
    if not path.is_file():
        raise ValueError(f"missing or symlinked {label}: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _read_frozen_input(path: Path) -> dict[str, Any]:
    checked_file_sha256(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _source_calls(
    task_id: int, canonical: dict, affected: dict | None, reference_root: Path
) -> list[dict]:
    if affected is None:
        return canonical["tool_calls"]
    revision = affected.get("source_revision") or {}
    if revision:
        path = Path(revision["path"])
        if not path.is_absolute():
            path = reference_root / path
        if checked_file_sha256(path) != revision["sha256"]:
            raise ValueError(f"task {task_id}: revised source actions changed")
        calls = _read_frozen_input(path)["tool_calls"]
    elif canonical["evaluation_input"]["task_type"] == "RETRIEVE":
        path = (
            (reference_root / "evaluation/trajectory-replay/webarena-verified/retrieval-candidates")
            / f"task-{task_id}.json"
        )
        calls = _read_frozen_input(path)["tool_calls"]
    else:
        # Non-retrieval acceptance is evaluated from current site state. Its
        # already-bound candidate actions are the execution authority here.
        calls = affected["actions"]
    # A source candidate may spell the old local gateway differently from the
    # diagnostic trace. Both must bind to the same frozen remote actions below.
    return calls


def freeze_method_inputs(
    *,
    dataset_path: Path,
    selection_path: Path,
    affected36_path: Path,
    site_map_path: Path,
    binary_path: Path,
    method_id: str,
    engine: str,
    retrieval_catalog: dict[int, Path] | None = None,
    reference_root: Path | None = None,
) -> dict[str, Any]:
    validate_method_identity(method_id, engine)
    reference_root = (reference_root or repository_root()).resolve()
    dataset = _read_frozen_input(dataset_path)
    selection = _read_frozen_input(selection_path)
    affected = _read_frozen_input(affected36_path)
    if (
        dataset.get("schema") != "lex_webarena_verified_trajectory_dataset/1"
        or affected.get("schema") != "original36_affected_cohort_inputs/1"
        or affected.get("promotable") is not True
    ):
        raise ValueError("selected-task dataset or candidate manifest is not frozen")
    canonical = dataset.get("trajectories") or []
    affected_rows = affected.get("candidates") or []
    by_affected = {int(row["task_id"]): row for row in affected_rows}
    by_canonical = {int(row["task_id"]): row for row in canonical}
    task_ids = selected_task_ids(selection_path)
    if (
        selection.get("schema") != "lex_webarena_verified_pilot/1"
        or len(canonical) != len(task_ids)
        or len(by_canonical) != len(task_ids)
        or not task_ids
        or len(set(task_ids)) != len(task_ids)
        or set(by_canonical) != set(task_ids)
        or len(affected_rows) != 36
        or len(by_affected) != 36
        or set(by_affected) != set(affected["candidate_task_ids"])
        or checked_file_sha256(site_map_path) != affected["site_map_sha256"]
    ):
        raise ValueError("task coverage or remote site map changed")
    sites = load_remote_sites(site_map_path)
    retrieval_catalog = retrieval_catalog or {}
    rows = []
    missing_contracts = []
    for task_id in task_ids:
        task = by_canonical[task_id]
        changed = by_affected.get(task_id)
        source_actions = _source_calls(task_id, task, changed, reference_root)
        candidate_path = None
        if task["evaluation_input"]["task_type"] == "RETRIEVE":
            candidate_path = retrieval_catalog.get(task_id)
            if candidate_path is None and changed is not None:
                revision = changed.get("source_revision") or {}
                if "retrieval-candidates/" in str(revision.get("path") or ""):
                    candidate_path = Path(revision["path"])
                    if not candidate_path.is_absolute():
                        candidate_path = reference_root / candidate_path
            if candidate_path is None:
                candidate_path = (
                    reference_root
                    / "evaluation/trajectory-replay/webarena-verified/retrieval-candidates"
                    / f"task-{task_id}.json"
                )
            if changed is None and candidate_path.is_file():
                source_actions = _read_frozen_input(candidate_path)["tool_calls"]
        actions = rebind_remote_calls(source_actions, sites, set(LOCAL_RECORDED_HOSTS))
        if changed is not None and (
            input_digest(actions) != changed["bound_calls_sha256"] or actions != changed["actions"]
        ):
            raise ValueError(f"task {task_id}: qualified candidate remote actions changed")
        row = {
            "task_id": task_id,
            "task_contract_sha256": input_digest(
                {"task": task["task"], "evaluation_input": task["evaluation_input"]}
            ),
            "source_actions": source_actions,
            "source_calls_sha256": input_digest(source_actions),
            "actions": actions,
            "bound_calls_sha256": input_digest(actions),
            "readiness": changed["readiness"] if changed is not None else [],
        }
        if changed is not None:
            row["prior_source_calls_sha256"] = changed["source_calls_sha256"]
        if task["evaluation_input"]["task_type"] == "RETRIEVE":
            assert candidate_path is not None
            if not candidate_path.is_file():
                missing_contracts.append(task_id)
            else:
                candidate = _read_frozen_input(candidate_path)
                if (
                    candidate.get("task_id") != task_id
                    or candidate.get("tool_calls") != source_actions
                ):
                    raise ValueError(f"task {task_id}: retrieval contract uses other actions")
                contract = candidate.get("retrieval_contract")
                if contract is None:
                    missing_contracts.append(task_id)
                else:
                    validate_contract(contract, source_actions)
                    row["retrieval_contract"] = contract
                    row["retrieval_contract_source_sha256"] = checked_file_sha256(candidate_path)
        rows.append(row)
    if missing_contracts:
        raise ValueError(f"current-run retrieval contracts are missing: {missing_contracts}")
    if set(retrieval_catalog) - set(task_ids):
        raise ValueError("retrieval catalog contains an out-of-scope task")
    return {
        "schema": "webarena_three_method_inputs/1",
        "promotable": True,
        "method_id": method_id,
        "engine": engine,
        "browser_binary_sha256": checked_file_sha256(binary_path),
        "site_map_sha256": checked_file_sha256(site_map_path),
        "dataset_sha256": checked_file_sha256(dataset_path),
        "selection_sha256": checked_file_sha256(selection_path),
        "candidate36_manifest_sha256": checked_file_sha256(affected36_path),
        "task_ids": task_ids,
        "tasks": rows,
        "site_readiness": affected.get("site_readiness") or [],
        "run_count": 1,
        "retry_count": 0,
        "site_reset_between_tasks": False,
    }


def main_freeze(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Freeze a complete selected-task remote replay input"
    )
    parser.add_argument("--reference-root", type=Path)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--affected36", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--method-id", required=True)
    parser.add_argument("--engine", choices=("chromium", "moli"), required=True)
    parser.add_argument("--retrieval-catalog", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    catalog = _read_frozen_input(args.retrieval_catalog) if args.retrieval_catalog else {}
    catalog_paths = {int(key): Path(value) for key, value in catalog.items()}
    frozen = freeze_method_inputs(
        dataset_path=args.dataset,
        selection_path=args.selection,
        affected36_path=args.affected36,
        site_map_path=args.site_map,
        binary_path=args.binary,
        method_id=args.method_id,
        engine=args.engine,
        retrieval_catalog=catalog_paths,
        reference_root=args.reference_root,
    )
    if args.output.exists():
        raise ValueError("frozen manifest output exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"task_count": len(frozen["tasks"]),
                      "sha256": checked_file_sha256(args.output)}))


UNREBOUND_SOURCE_HOST = re.compile("|".join(map(re.escape, sorted(LOCAL_RECORDED_HOSTS))))


def _candidate_trace_input(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("source_arguments") or row.get("arguments")
    if not isinstance(value, dict) or not isinstance(value.get("action"), str):
        raise ValueError(f"trace step {row.get('index')} has no executable input")
    return value


def _candidate_bound_trace(task: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = task["action_evidence"]
    path = Path(evidence["path"])
    if path.is_symlink() or not path.is_file() or checked_file_sha256(path) != evidence["sha256"]:
        raise ValueError(f"task {task['task_id']} action evidence is missing or changed")
    trace = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(trace, list):
        raise ValueError(f"task {task['task_id']} action evidence is not a list")
    return trace


def validate_candidate_revision(candidate: dict[str, Any]) -> None:
    if "retrieval_contract" in candidate:
        validate_contract(candidate["retrieval_contract"], candidate["tool_calls"])
    elif (
        set(candidate) - {"repair"}
        != {"schema", "task_id", "canonical_calls_sha256", "tool_calls"}
        or (
            "repair" in candidate
            and (
                not isinstance(candidate["repair"], dict)
                or set(candidate["repair"]) != {"cause", "change"}
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in candidate["repair"].values()
                )
            )
        )
        or candidate["schema"] != "webarena_qualified_trajectory_revision/1"
        or candidate["canonical_calls_sha256"] != input_digest(candidate["tool_calls"])
    ):
        raise ValueError("trajectory revision is unbound")


def validate_candidate_lineage(candidate: dict[str, Any], override: dict[str, Any]) -> str:
    """Prove an explicitly declared stabilization against its qualified parent bytes."""
    parent_sha = override.get("stabilization_parent_sha256", override["sha256"])
    reversions = override.get("stabilization_reversions", [])
    if parent_sha == override["sha256"]:
        if reversions:
            raise ValueError("candidate lineage contains unused reversions")
        return parent_sha
    if (not isinstance(parent_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", parent_sha)
            or not isinstance(reversions, list) or not reversions):
        raise ValueError("candidate lineage requires a bound parent and declared reversions")
    parent = copy.deepcopy(candidate)
    calls = parent.get("tool_calls")
    if not isinstance(calls, list):
        raise ValueError("candidate lineage requires executable calls")
    seen = set()
    for row in reversions:
        if (not isinstance(row, dict)
                or set(row) != {"action_index", "current_action", "parent_call"}):
            raise ValueError("invalid candidate lineage reversion")
        index = row["action_index"]
        prior = row["parent_call"]
        if (type(index) is not int or not 1 <= index <= len(calls) or index in seen
                or not isinstance(prior, dict) or not isinstance(prior.get("action"), str)
                or not prior["action"] or not isinstance(calls[index - 1], dict)
                or calls[index - 1].get("action") != row["current_action"]
                or calls[index - 1] == prior):
            raise ValueError("invalid candidate lineage reversion")
        seen.add(index)
        calls[index - 1] = prior
    if "retrieval_contract" in parent:
        parent["retrieval_contract"]["canonical_calls_sha256"] = input_digest(calls)
    elif parent.get("schema") == "webarena_qualified_trajectory_revision/1":
        parent["canonical_calls_sha256"] = input_digest(calls)
    else:
        raise ValueError("unsupported candidate lineage payload")
    payload = (json.dumps(parent, ensure_ascii=False, indent=2) + "\n").encode()
    if hashlib.sha256(payload).hexdigest() != parent_sha:
        raise ValueError("candidate lineage differs from the qualified parent")
    return parent_sha


def freeze_original_failure_inputs(
    index: dict[str, Any],
    *,
    site_map_path: Path,
    step_roles: dict[str, Any],
    formal: bool = False,
    reference_root: Path | None = None,
) -> dict[str, Any]:
    reference_root = reference_root or repository_root()
    if index.get("schema") != "original36_latest_moli_diagnostic_index/7":
        raise ValueError("unsupported original-failure diagnostic index")
    if step_roles.get("schema") != "webarena_qualified_cohort_step_roles/1":
        raise ValueError("unsupported step-role contract")
    original_ids = index.get("original_failed_task_ids")
    if (
        not isinstance(original_ids, list)
        or len(original_ids) != 36
        or len(set(original_ids)) != 36
    ):
        raise ValueError("the original-failure inventory must contain exactly 36 unique tasks")
    source_tasks = index.get("tasks") or []
    by_task = {item["task_id"]: item for item in source_tasks}
    if len(source_tasks) != 36 or set(by_task) != set(original_ids):
        raise ValueError("diagnostic task inventory differs from the original failures")
    exceptions = step_roles.get("exceptions") or []
    roles = {(row["task_id"], row["trace_index"]): row for row in exceptions}
    if len(roles) != len(exceptions):
        raise ValueError("duplicate supplemental step role")
    used_roles: set[tuple[int, int | str]] = set()
    overrides = step_roles.get("candidate_overrides") or []
    overrides_by_task = {row["task_id"]: row for row in overrides}
    if len(overrides_by_task) != len(overrides):
        raise ValueError("duplicate candidate override")
    used_overrides: set[int] = set()
    sites = load_remote_sites(site_map_path)
    site_readiness = []
    seen_readiness: set[tuple[str, str]] = set()
    for probe in step_roles.get("site_readiness") or []:
        site_name = probe.get("site")
        path = probe.get("path")
        max_stable_ms = probe.get("max_stable_ms")
        if (
            site_name not in {site.value for site in sites}
            or not isinstance(path, str)
            or not path.startswith("/")
            or not isinstance(max_stable_ms, int)
            or not 0 < max_stable_ms <= 30_000
            or (site_name, path) in seen_readiness
        ):
            raise ValueError("invalid or duplicate site readiness probe")
        parsed_path = urlsplit(path)
        if parsed_path.scheme or parsed_path.netloc or parsed_path.fragment:
            raise ValueError("site readiness probe must be a site-local GET path")
        seen_readiness.add((site_name, path))
        target = urlsplit(sites[WebArenaSite(site_name)])
        site_readiness.append({
            "site": site_name,
            "path": path,
            "url": urlunsplit((target.scheme, target.netloc, parsed_path.path,
                               parsed_path.query, "")),
            "max_stable_ms": max_stable_ms,
        })
    candidates = []
    for task_id in original_ids:
        task = by_task[task_id]
        reference = task.get("reference_validity") or {}
        if reference.get("status") == "unverifiable":
            raise ValueError(f"task {task_id} has an unverifiable reference")
        if (
            task.get("evaluation_mode") != "actual_trajectory_output"
            or (task.get("semantic_verification") or {}).get("status") != "verified"
        ):
            raise ValueError(f"task {task_id} lacks verified actual trajectory output")
        trace = _candidate_bound_trace(task)
        count = task.get("qualified_action_count")
        if type(count) is not int or count <= 0:
            raise ValueError(f"task {task_id} has invalid action count")
        while roles.get((task_id, count), {}).get("role") == "diagnostic_post_observer":
            count -= 1
        if count <= 0:
            raise ValueError(f"task {task_id} has no qualified actions after observers")
        numbered: dict[int, dict[str, Any]] = {}
        readiness = []
        omitted_observers = []
        for row in trace:
            index_value = row.get("index")
            if type(index_value) is int and 1 <= index_value <= count:
                if (index_value in numbered
                        or row.get("status", row.get("tool_status")) != "success"):
                    raise ValueError(f"task {task_id} has duplicate or failed action {index_value}")
                numbered[index_value] = _candidate_trace_input(row)
                continue
            role = roles.get((task_id, index_value))
            if role is None:
                raise ValueError(f"task {task_id} has unclassified supplemental step {index_value}")
            used_roles.add((task_id, index_value))
            if role["role"] == "readiness":
                before_action = role.get("before_action")
                if (type(before_action) is not int or not 1 <= before_action <= count
                        or row.get("status") != "success"):
                    raise ValueError(f"task {task_id} has invalid readiness step")
                readiness.append({
                    "before_action": before_action,
                    "input": _candidate_trace_input(row),
                    "input_sha256": input_digest(_candidate_trace_input(row)),
                })
            elif role["role"] == "diagnostic_post_observer":
                omitted_observers.append({
                    "trace_index": index_value,
                    "historical_status": row.get("status"),
                    "excluded_from_replay": True,
                })
            else:
                raise ValueError(f"task {task_id} has unsupported supplemental role")
        if set(numbered) != set(range(1, count + 1)):
            raise ValueError(f"task {task_id} is missing a qualified action")
        source_calls = [numbered[number] for number in range(1, count + 1)]
        source_revision = None
        override = overrides_by_task.get(task_id)
        if override is not None:
            path = contained_path(reference_root, override["path"], reject_symlinks=True)
            if (not path.is_file() or checked_file_sha256(path) != override["sha256"]
                    or not isinstance(override.get("reason"), str) or not override["reason"]):
                raise ValueError(f"task {task_id} candidate override is unbound")
            candidate = json.loads(path.read_text(encoding="utf-8"))
            expected_count = override.get("action_count", count)
            if (
                candidate.get("task_id") != task_id
                or type(expected_count) is not int
                or expected_count <= 0
                or len(candidate.get("tool_calls") or []) != expected_count
            ):
                raise ValueError(f"task {task_id} candidate override changed the action scope")
            validate_candidate_revision(candidate)
            qualified_candidate_sha = validate_candidate_lineage(candidate, override)
            if expected_count != count:
                matrix_path = contained_path(reference_root, (
                    "evaluation/trajectory-replay/webarena-verified/"
                    "retrieval-candidates/qualification-matrix.json"
                ), reject_symlinks=True)
                matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
                matched = [
                    row for row in matrix["rows"] if row["task_id"] == task_id
                ]
                qualification = (
                    matched[0].get("affected_task_qualification") if len(matched) == 1 else None
                )
                paired = override.get("paired_qualification")
                if (
                    not isinstance(qualification, dict)
                    or not isinstance(paired, dict)
                    or qualification.get("candidate_sha256") != qualified_candidate_sha
                    or qualification.get("action_count_per_browser") != expected_count
                    or qualification.get("retrieval_status") != "supported"
                    or qualification.get("chromium_official_score") != 1.0
                    or qualification.get("moli_official_score") != 1.0
                    or any(
                        not isinstance(paired.get(key), str)
                        or re.fullmatch(r"[0-9a-f]{64}", paired[key]) is None
                        or qualification.get(key) != paired[key]
                        for key in ("chromium_receipt_sha256", "moli_receipt_sha256")
                    )
                ):
                    raise ValueError(f"task {task_id} action-scope override lacks paired proof")
            if input_digest(candidate["tool_calls"]) == input_digest(source_calls):
                raise ValueError(f"task {task_id} candidate override has no input revision")
            source_calls = candidate["tool_calls"]
            source_revision = {
                "path": override["path"],
                "sha256": override["sha256"],
                "reason": override["reason"],
                "action_count": expected_count,
                "paired_qualification": override.get("paired_qualification"),
            }
            used_overrides.add(task_id)
        bound_calls = rebind_remote_calls(source_calls, sites, set(LOCAL_RECORDED_HOSTS))
        if UNREBOUND_SOURCE_HOST.search(json.dumps(bound_calls, ensure_ascii=False)):
            raise ValueError(f"task {task_id} retains a local source host")
        for guard in readiness:
            bound_guard = rebind_remote_calls(
                [guard["input"]], sites, set(LOCAL_RECORDED_HOSTS),
            )[0]
            if UNREBOUND_SOURCE_HOST.search(json.dumps(bound_guard, ensure_ascii=False)):
                raise ValueError(f"task {task_id} readiness retains a local source host")
            guard["source_input_sha256"] = guard.pop("input_sha256")
            guard["bound_input_sha256"] = input_digest(bound_guard)
            guard["input"] = bound_guard
        candidates.append({
            "task_id": task_id,
            "source_trace_sha256": task["action_evidence"]["sha256"],
            "source_calls_sha256": input_digest(source_calls),
            "source_revision": source_revision,
            "bound_calls_sha256": input_digest(bound_calls),
            "actions": bound_calls,
            "readiness": readiness,
            "omitted_diagnostic_observers": omitted_observers,
        })
    if used_roles != set(roles):
        raise ValueError("step-role contract contains an unused exception")
    if used_overrides != set(overrides_by_task):
        raise ValueError("step-role contract contains an unused candidate override")
    if len(candidates) != 36 or set(item["task_id"] for item in candidates) != set(original_ids):
        raise ValueError("the original 36-task scope changed")
    return {
        "schema": (
            "original36_affected_cohort_inputs/1"
            if formal else "original36_qualified_candidate_cohort_inputs/1"
        ),
        "promotable": formal,
        "source_index_schema": index["schema"],
        "moli_binary_sha256": index["moli_binary_sha256"],
        "site_map_sha256": checked_file_sha256(site_map_path),
        "original_failed_task_ids": original_ids,
        "candidate_task_ids": [item["task_id"] for item in candidates],
        "quarantined": [],
        "site_readiness": site_readiness,
        "candidates": candidates,
        "input_contract": {
            "site_preparation": "once_before_browser_cohort",
            "task_order": "original_failure_order",
            "retry_failed_task": False,
            "response_policy": "derive_from_current_run_only",
            "historical_scores_and_answers_included": False,
        },
    }


def main_freeze_original_failures(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Freeze the declared original-failure candidate inputs",
    )
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--roles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path)
    parser.add_argument(
        "--formal", action="store_true", help="freeze the final affected-task cohort",
    )
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("cohort input manifest already exists; do not overwrite it")
    bound = {path: checked_file_sha256(path)
             for path in (args.index, args.roles, args.site_map)}
    index = json.loads(args.index.read_text(encoding="utf-8"))
    roles = json.loads(args.roles.read_text(encoding="utf-8"))
    result = freeze_original_failure_inputs(
        index, site_map_path=args.site_map, step_roles=roles, formal=args.formal,
        reference_root=args.reference_root,
    )
    if any(checked_file_sha256(path) != sha for path, sha in bound.items()):
        raise ValueError("candidate freeze input changed during compilation")
    result["source_index_sha256"] = bound[args.index]
    result["step_roles_sha256"] = bound[args.roles]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    previous_umask = os.umask(0o077)
    try:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    finally:
        os.umask(previous_umask)
    print(json.dumps({
        "output": str(args.output),
        "candidate_tasks": len(result["candidates"]),
        "quarantined_tasks": result["quarantined"],
        "sha256": checked_file_sha256(args.output),
    }, ensure_ascii=False))


def revise_method_inputs(prior: dict, revisions: dict, sites: dict) -> dict:
    result = copy.deepcopy(prior)
    rows = {row["task_id"]: row for row in result["tasks"]}
    if not rows or len(rows) != len(result["tasks"]):
        raise ValueError("predecessor task set is empty or duplicated")
    if not revisions or len(set(map(int, revisions))) != len(revisions):
        raise ValueError("revision task set is empty or duplicated")
    if not set(map(int, revisions)) <= set(rows):
        raise ValueError("revision is outside the frozen task set")
    candidates = {}
    for row in rows.values():
        if row["source_calls_sha256"] != retrieval_digest(row["source_actions"]):
            raise ValueError("predecessor source actions differ from their binding")
    for key, revision in revisions.items():
        row = rows[int(key)]
        if (
            not revision.get("cause")
            or revision.get("prior_calls_sha256") != row["source_calls_sha256"]
        ):
            raise ValueError("revision lacks a cause or names different predecessor actions")
        path = unlinked_path(Path(revision["candidate"]))
        candidates[path] = checked_file_sha256(path, "candidate")
        candidate = json.loads(path.read_bytes())
        if candidate["task_id"] != row["task_id"]:
            raise ValueError("candidate task mismatch")
        calls = candidate["tool_calls"]
        if (not isinstance(calls, list) or not calls
                or any(not isinstance(call, dict) for call in calls)):
            raise ValueError("candidate actions must be a nonempty list of calls")
        if "retrieval_contract" in row:
            validate_contract(candidate["retrieval_contract"], calls)
            row["retrieval_contract"] = candidate["retrieval_contract"]
            row["retrieval_contract_source_sha256"] = candidates[path]
        elif "retrieval_contract" in candidate:
            raise ValueError("revision changes task type")
        if row.get("readiness") and len(calls) != len(row["source_actions"]):
            raise ValueError("step-count revision requires explicit readiness migration")
        row["source_actions"] = calls
        row["source_calls_sha256"] = retrieval_digest(calls)
        row["revision"] = {**revision, "candidate_sha256": candidates[path]}
    for row in rows.values():
        row["actions"] = rebind_remote_calls(
            row["source_actions"], sites, set(LOCAL_RECORDED_HOSTS)
        )
        row["bound_calls_sha256"] = retrieval_digest(row["actions"])
    result["task_ids"] = sorted(rows)
    result["tasks"] = [rows[n] for n in result["task_ids"]]
    result["execution_order"] = "ascending_task_id"
    if any(checked_file_sha256(path) != sha for path, sha in candidates.items()):
        raise ValueError("candidate input changed during revision")
    return result


def main_revise(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("prior", "revisions", "site-map", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    args.output = unlinked_path(args.output)
    if args.output.exists():
        raise ValueError("output exists; frozen inputs are append-only")
    bound = {
        path: checked_file_sha256(path) for path in (args.prior, args.revisions, args.site_map)
    }
    prior = json.loads(args.prior.read_text())
    if prior["site_map_sha256"] != bound[args.site_map]:
        raise ValueError("site map changed")
    result = revise_method_inputs(
        prior, json.loads(args.revisions.read_text()), load_remote_sites(args.site_map)
    )
    if any(checked_file_sha256(path) != sha for path, sha in bound.items()):
        raise ValueError("revision input changed during compilation")
    result["predecessor_manifest_sha256"] = bound[args.prior]
    write_new_file(args.output, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({"task_count": len(result["tasks"]), "output": str(args.output)}))


def main_prepared_reset(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method-id", required=True)
    parser.add_argument("--app-id", required=True, type=int)
    parser.add_argument("--site-map", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    output = unlinked_path(args.receipt)
    if output.exists():
        raise ValueError("output already exists")
    source_value = os.environ.get("WEBARENA_PREPARED_COHORT_RESET_RECEIPT")
    if not source_value:
        raise ValueError("prepared Rainbond cohort reset receipt is required")
    source = unlinked_path(Path(source_value))
    source_bytes = source.read_bytes()
    if source.is_symlink() or not source.is_file() or args.receipt.exists():
        raise ValueError("prepared reset source or output is invalid")
    if source.resolve() == args.receipt.resolve():
        raise ValueError("prepared reset receipt cannot be its own output")
    receipt = validate_remote_cohort_reset_receipt(
        source,
        site_map_sha256=file_sha256(args.site_map),
        app_id=args.app_id,
        reset_scope="once_before_method_cohort",
        site_reset_between_tasks=False,
    )
    completed = receipt.get("completed_at_unix")
    if (
        receipt.get("method_id") != args.method_id
        or receipt.get("task_id") is not None
        or type(completed) not in {int, float}
        or not time.time() - 1800 <= completed <= time.time()
    ):
        raise ValueError("prepared reset is stale or belongs to another method")
    if source.read_bytes() != source_bytes:
        raise ValueError("prepared reset receipt changed during validation")
    write_new_file(output, source_bytes)
