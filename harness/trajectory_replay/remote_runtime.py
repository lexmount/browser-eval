"""Bind a diagnostic replay to an externally reset WebArena deployment.

The URL map and reset receipt are explicit inputs. Merely reaching a remote
site is not evidence that its writable fixture was reset for a task.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from webarena_verified import WebArenaVerified
from webarena_verified.types.config import EnvironmentConfig, WebArenaVerifiedConfig
from webarena_verified.types.task import WebArenaSite

from harness.environments.webarena.runtime import (
    SITE_CREDENTIALS,
    SITE_PORTS,
)

COHORT_RESET_SCOPE = "once_before_method_cohort"
METHOD_ID = re.compile(r"[a-z0-9][a-z0-9._-]{1,79}\Z")
READ_ONLY_PORTS = {8888: WebArenaSite.WIKIPEDIA, 3000: WebArenaSite.MAP}
LEGACY_LOCAL_SOURCE_HOSTS = frozenset({"127.0.0.1", "192.168.5.2", "host.docker.internal"})
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
    if path.is_symlink() or not path.is_file():
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
    from harness.trajectory_replay.retrieval_acceptance import validate_contract

    validate_method_identity(method_id, engine)
    reference_root = (reference_root or Path(__file__).resolve().parents[2]).resolve()
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
        actions = rebind_remote_calls(source_actions, sites, set(LEGACY_LOCAL_SOURCE_HOSTS))
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
