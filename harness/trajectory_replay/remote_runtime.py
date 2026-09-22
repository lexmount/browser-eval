"""Bind a diagnostic replay to an externally reset WebArena deployment.

The URL map and reset receipt are explicit inputs. Merely reaching a remote
site is not evidence that its writable fixture was reset for a task.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from webarena_verified import WebArenaVerified
from webarena_verified.types.config import EnvironmentConfig, WebArenaVerifiedConfig
from webarena_verified.types.task import WebArenaSite

from harness.browser_use.runner import SITE_CREDENTIALS, SITE_PORTS
from harness.trajectory_replay.webarena_evaluator_fixes import (
    install_webarena_evaluator_fixes,
)

READ_ONLY_PORTS = {8888: WebArenaSite.WIKIPEDIA, 3000: WebArenaSite.MAP}
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
    install_webarena_evaluator_fixes()
    return WebArenaVerified(
        config=WebArenaVerifiedConfig(
            environments={
                site: EnvironmentConfig(
                    urls=[url],
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
) -> list[dict[str, Any]]:
    """Bind frozen WebArena URLs while tolerating HTTP-to-HTTPS proxy upgrades.

    An exact same-site href selector may point at either scheme after a reverse
    proxy redirects the document. Preserve its host, path, query and action
    semantics; only the transport scheme becomes an explicit alternative.
    """
    by_port = {port: site for site, ports in SITE_PORTS.items() for port in ports}
    by_port.update(READ_ONLY_PORTS)

    def rebind_url(value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in trusted_source_hosts:
            return value
        site = by_port.get(parsed.port)
        if site is None:
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
        if call.get("action") == "fill" and isinstance(text, str) and not any(
            char.isspace() for char in text
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
