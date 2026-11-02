"""Provision disposable, fully isolated WebArena slices on Rainbond."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

SCHEMA = "lex_webarena_rainbond_pool/1"
RECEIPT_SCHEMA = "lex_webarena_rainbond_slice/1"
LEASE_SCHEMA = "lex_webarena_rainbond_slice_lease/1"
STATEFUL_SITES = ("shopping_admin", "shopping", "reddit", "gitlab")
SITE_PORTS = {"shopping_admin": 80, "shopping": 80, "reddit": 80, "gitlab": 8023}
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_LOCAL_POOL_LOCKS: dict[Path, threading.Lock] = {}
_LOCAL_POOL_LOCKS_GUARD = threading.Lock()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _image_digest(value: str) -> str:
    marker = "@sha256:"
    if marker not in value:
        raise ValueError(f"container image is not digest pinned: {value!r}")
    return "sha256:" + value.rsplit(marker, 1)[1]


def _validate_pool_id(pool_id: str) -> str:
    normalized = pool_id.strip().lower()
    if not _SAFE_ID.fullmatch(normalized):
        raise ValueError("pool_id must be a lowercase DNS-safe identifier")
    return normalized


def _slot_sort_key(path: Path) -> tuple[int, int | str]:
    suffix = path.name.removeprefix("slice-")
    return (0, int(suffix)) if suffix.isdigit() else (1, path.name)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _attempt_cleanup(original: BaseException, description: str, action: Any) -> bool:
    """Run cleanup without hiding the failure that made cleanup necessary."""
    try:
        action()
    except BaseException as cleanup_error:
        original.add_note(f"cleanup failed ({description}): {cleanup_error}")
        return False
    return True


class RainbondAPI:
    """Small OpenAPI client containing only the calls required by slice lifecycle."""

    def __init__(self, base_url: str, token: str, *, timeout_seconds: float = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def request(self, method: str, path: str, body: Any | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": self.token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise RuntimeError(
                f"Rainbond OpenAPI {method} {path} failed with HTTP {exc.code}: {detail}"
            ) from exc
        if not payload:
            return None
        return json.loads(payload)


@dataclass(frozen=True)
class PoolConfig:
    api_base_url: str
    team_id: str
    team_name: str
    region_name: str
    source_app_id: int
    source_services: dict[str, str]
    initializer_service: str
    initializer_image: str
    app_prefix: str
    domain_suffix: str
    token_env: str
    image_lock: Path
    shared_environments: dict[str, Any]
    readiness_timeout_seconds: int
    max_parallel_provisions: int
    reset_strategy: str

    @classmethod
    def load(cls, path: str | Path) -> tuple[PoolConfig, dict[str, Any], Path]:
        config_path = Path(path).resolve()
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if raw.get("schema") != SCHEMA:
            raise ValueError(f"Rainbond pool config schema must be {SCHEMA}")
        source_services = raw.get("source_services")
        if not isinstance(source_services, dict) or set(source_services) != set(STATEFUL_SITES):
            raise ValueError(f"source_services must contain exactly {list(STATEFUL_SITES)}")
        normalized_services = {
            str(site): str(service_id) for site, service_id in source_services.items()
        }
        if any(not value for value in normalized_services.values()):
            raise ValueError("source_services values must be non-empty service IDs")
        prefix = str(raw.get("app_prefix") or "webarena-slice")
        if not _SAFE_ID.fullmatch(prefix):
            raise ValueError("app_prefix must be a lowercase DNS-safe identifier")
        suffix = str(raw.get("domain_suffix") or "").strip().strip(".")
        if not suffix or "/" in suffix or ":" in suffix:
            raise ValueError("domain_suffix must be a DNS suffix without scheme or port")
        lock_value = Path(str(raw.get("image_lock") or ""))
        lock_path = lock_value if lock_value.is_absolute() else (config_path.parent / lock_value)
        if not lock_path.is_file():
            raise ValueError("Rainbond pool image_lock is missing")
        initializer_service = str(raw.get("initializer_service") or "")
        initializer_image = str(raw.get("initializer_image") or "")
        if not initializer_service or not initializer_image:
            raise ValueError("initializer_service and initializer_image are required")
        _image_digest(initializer_image)
        value = cls(
            api_base_url=str(raw.get("api_base_url") or "").rstrip("/"),
            team_id=str(raw.get("team_id") or ""),
            team_name=str(raw.get("team_name") or ""),
            region_name=str(raw.get("region_name") or ""),
            source_app_id=int(raw.get("source_app_id") or 0),
            source_services=normalized_services,
            initializer_service=initializer_service,
            initializer_image=initializer_image,
            app_prefix=prefix,
            domain_suffix=suffix,
            token_env=str(raw.get("token_env") or "RAINBOND_API_TOKEN"),
            image_lock=lock_path.resolve(),
            shared_environments=dict(raw.get("shared_environments") or {}),
            readiness_timeout_seconds=int(raw.get("readiness_timeout_seconds") or 1800),
            max_parallel_provisions=int(raw.get("max_parallel_provisions") or 2),
            reset_strategy=str(raw.get("reset_strategy") or "replace_before_destroy"),
        )
        if (
            not all((value.api_base_url, value.team_id, value.team_name, value.region_name))
            or value.source_app_id < 1
            or value.max_parallel_provisions < 1
        ):
            raise ValueError("Rainbond pool API, team, region, and source app are required")
        if value.reset_strategy not in {"replace_before_destroy", "stop_before_replace"}:
            raise ValueError("reset_strategy must be replace_before_destroy or stop_before_replace")
        return value, raw, config_path


class RainbondSlicePool:
    """Create and replace isolated four-site applications from one immutable baseline."""

    def __init__(
        self,
        config_path: str | Path,
        *,
        api: RainbondAPI | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.config, self.raw_config, self.config_path = PoolConfig.load(config_path)
        self.config_sha256 = _canonical_sha256(self.raw_config)
        token = os.environ.get(self.config.token_env, "")
        if api is None and not token:
            raise ValueError(
                f"Rainbond API token environment variable is missing: {self.config.token_env}"
            )
        self.api = api or RainbondAPI(self.config.api_base_url, token)
        self.sleep = sleep
        self.image_lock = json.loads(self.config.image_lock.read_text(encoding="utf-8"))

    def _apps_path(self) -> str:
        config = self.config
        return f"/openapi/v1/teams/{quote(config.team_id)}/regions/{quote(config.region_name)}/apps"

    def _app_path(self, app_id: int) -> str:
        return f"{self._apps_path()}/{app_id}"

    @staticmethod
    def _response_rows(response: Any, *keys: str) -> list[dict[str, Any]]:
        if isinstance(response, list):
            return [row for row in response if isinstance(row, dict)]
        if not isinstance(response, dict):
            return []
        for key in (*keys, "items"):
            value = response.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict) and isinstance(value.get("items"), list):
                return [row for row in value["items"] if isinstance(row, dict)]
        return []

    def _domain(self, pool_id: str, slot: int, site: str, app_id: int) -> str:
        label = f"{self.config.app_prefix}-{pool_id}-{slot}-{app_id}-{site.replace('_', '-')}"
        if len(label) > 63:
            label = f"{label[:50].rstrip('-')}-{hashlib.sha256(label.encode()).hexdigest()[:10]}"
        return f"{label}.{self.config.domain_suffix}"

    @staticmethod
    def _validate_pool_id(pool_id: str) -> str:
        return _validate_pool_id(pool_id)

    def _create_app(self, pool_id: str, slot: int) -> int:
        name = f"{self.config.app_prefix}-{pool_id}-{slot}"
        note = f"managed-by=browser-eval;pool={pool_id};slot={slot}"
        result = self.api.request("POST", self._apps_path(), {"app_name": name, "app_note": note})
        app_id = int((result or {}).get("ID") or 0)
        if app_id < 1:
            raise RuntimeError("Rainbond create app response has no ID")
        return app_id

    def _copy_components(self, app_id: int) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        config = self.config
        result = self.api.request(
            "POST",
            f"{self._app_path(config.source_app_id)}/copy",
            {
                "services": [
                    {"service_id": config.source_services[site]} for site in STATEFUL_SITES
                ]
                + [{"service_id": config.initializer_service}],
                "target_team_name": config.team_name,
                "target_region_name": config.region_name,
                "target_app_id": app_id,
            },
        )
        rows = self._response_rows(result, "services")
        by_source_name = {
            site: str(self.raw_config.get("source_component_names", {}).get(site) or site)
            for site in STATEFUL_SITES
        }
        mapped: dict[str, dict[str, Any]] = {}
        for site, component_name in by_source_name.items():
            copied_name = re.compile(rf"^{re.escape(component_name)}-copy(?:-\d+)?$")
            matches = [
                row
                for row in rows
                if str(row.get("service_cname")) == component_name
                or copied_name.fullmatch(str(row.get("service_cname")))
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    f"Rainbond copy returned {len(matches)} components named {component_name!r}"
                )
            mapped[site] = matches[0]
        initializer_name = str(
            self.raw_config.get("initializer_component_name") or "slice-initializer"
        )
        copied_initializer_name = re.compile(rf"^{re.escape(initializer_name)}-copy(?:-\d+)?$")
        initializer_matches = [
            row
            for row in rows
            if str(row.get("service_cname")) == initializer_name
            or copied_initializer_name.fullmatch(str(row.get("service_cname")))
        ]
        if len(initializer_matches) != 1:
            raise RuntimeError(
                f"Rainbond copy returned {len(initializer_matches)} components named "
                f"{initializer_name!r}"
            )
        return mapped, initializer_matches[0]

    def _configure_component(
        self,
        app_id: int,
        *,
        pool_id: str,
        slot: int,
        site: str,
        component: dict[str, Any],
    ) -> dict[str, Any]:
        service_id = str(component.get("service_id") or "")
        if not service_id:
            raise RuntimeError(f"Rainbond copied component has no service_id: {site}")
        self._coalesce_internal_ports(
            app_id,
            service_id,
            site_port=SITE_PORTS[site],
            control_port=8877,
            control_alias=f"{site.upper()}_CTRL",
        )
        domain = self._domain(pool_id, slot, site, app_id)
        site_url = f"http://{domain}"
        magento = site in {"shopping_admin", "shopping"}
        self.api.request(
            "POST",
            f"{self._app_path(app_id)}/domains",
            {
                "protocol": "http",
                "http": {
                    "service_id": service_id,
                    "container_port": SITE_PORTS[site],
                    "certificate_id": 0,
                    "domain_name": domain,
                    "domain_cookie": "",
                    "domain_header": "",
                    "domain_path": "/",
                    "the_weight": 100,
                    "whether_open": True,
                    "auto_ssl": False,
                    "auto_ssl_config": "",
                    "rule_extensions": [],
                    "configuration": {
                        "proxy_body_size": 100 if magento else 0,
                        "proxy_buffer_numbers": 8 if magento else 4,
                        "proxy_buffer_size": 64 if magento else 4,
                        "proxy_buffering": "on" if magento else "off",
                        "proxy_connect_timeout": 75,
                        "proxy_read_timeout": 600,
                        "proxy_send_timeout": 600,
                        "set_headers": [],
                    },
                },
            },
        )
        envs = [
            {
                "note": "public origin for WebArena initialization",
                "name": "WA_ENV_CTRL_EXTERNAL_SITE_URL",
                "value": site_url,
                "is_change": True,
                "scope": "inner",
            }
        ]
        if magento:
            envs.append(
                {
                    "note": "allow Magento initialization on loaded nodes",
                    "name": "WA_ENV_CTRL_CMD_TIMEOUT_SEC",
                    "value": "600",
                    "is_change": True,
                    "scope": "inner",
                }
            )
        if site == "gitlab":
            envs.append(
                {
                    "note": "preserve the image's internal 8023 listener",
                    "name": "WA_ENV_CTRL_SKIP_RECONFIGURE",
                    "value": "true",
                    "is_change": True,
                    "scope": "inner",
                }
            )
        self.api.request(
            "PUT",
            f"{self._app_path(app_id)}/services/{quote(service_id)}/envs",
            {"envs": envs},
        )
        expected_image = str(self.image_lock["images"][site]["pinned"])
        actual_image = str(component.get("image") or "")
        if not actual_image:
            raise RuntimeError(f"Rainbond copied {site} component has no image in response")
        if _image_digest(actual_image) != _image_digest(expected_image):
            raise RuntimeError(
                f"Rainbond copied {site} image mismatch: {actual_image!r} != {expected_image!r}"
            )
        return {
            "service_id": service_id,
            "service_cname": component.get("service_cname"),
            "image": expected_image,
            "runtime_image": actual_image,
            "site_url": site_url,
        }

    def _coalesce_internal_ports(
        self,
        app_id: int,
        service_id: str,
        *,
        site_port: int,
        control_port: int,
        control_alias: str,
    ) -> None:
        path = f"{self._app_path(app_id)}/services/{quote(service_id)}/ports"

        def inner_ports() -> dict[int, dict[str, Any]]:
            response = self.api.request("GET", path)
            return {
                int(row.get("container_port") or 0): row
                for row in self._response_rows(response, "ports")
                if bool(row.get("is_inner_service"))
            }

        ports = inner_ports()
        site_host = str(ports.get(site_port, {}).get("k8s_service_name") or "")
        control_host = str(ports.get(control_port, {}).get("k8s_service_name") or "")
        if not site_host or not control_host:
            raise RuntimeError(
                f"Rainbond copied component {service_id} must expose internal ports "
                f"{site_port} and {control_port}"
            )
        if site_host == control_host:
            return
        control_path = f"{path}/{control_port}"
        self.api.request("PUT", control_path, {"action": "close_inner"})
        self.api.request("DELETE", control_path)
        self.api.request(
            "POST",
            path,
            {
                "port": control_port,
                "protocol": str(ports[control_port].get("protocol") or "http"),
                "port_alias": control_alias,
                "is_inner_service": True,
            },
        )
        # The write may finish before the read model reflects the recreated port.
        # Retry only observation: repeating DELETE/POST could disrupt the service.
        for attempt in range(3):
            ports = inner_ports()
            updated_host = str(ports.get(control_port, {}).get("k8s_service_name") or "")
            if updated_host == site_host:
                return
            if attempt < 2:
                self.sleep(attempt + 1)
        raise RuntimeError(
            f"Rainbond did not attach control port {control_port} to internal "
            f"service {site_host!r}: got {updated_host!r}"
        )

    def _configure_initializer(
        self,
        app_id: int,
        *,
        pool_id: str,
        slot: int,
        initializer: dict[str, Any],
        copied_sites: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        service_id = str(initializer.get("service_id") or "")
        actual_image = str(initializer.get("image") or "")
        if not service_id:
            raise RuntimeError("Rainbond copied initializer has no service_id")
        if not actual_image:
            raise RuntimeError("Rainbond copied initializer component has no image in response")
        if _image_digest(actual_image) != _image_digest(self.config.initializer_image):
            raise RuntimeError("Rainbond copied initializer image mismatch")
        aliases = {
            site: self._internal_service_host(app_id, copied_sites[site], 8877)
            for site in ("reddit", "gitlab")
        }
        self.api.request(
            "PUT",
            f"{self._app_path(app_id)}/services/{quote(service_id)}/envs",
            {
                "envs": [
                    {
                        "name": f"{site.upper()}_CTRL_URL",
                        "value": f"http://{alias}:8877",
                        "is_change": True,
                        "scope": "inner",
                    }
                    for site, alias in aliases.items()
                ]
                + [
                    {
                        "name": "ENV_CTRL_MAX_ATTEMPTS",
                        "value": "360",
                        "is_change": True,
                        "scope": "inner",
                    },
                    {
                        "name": "ENV_CTRL_RETRY_SECONDS",
                        "value": "5",
                        "is_change": True,
                        "scope": "inner",
                    },
                ]
            },
        )
        readiness_url = f"http://{self._domain(pool_id, slot, 'initializer', app_id)}"
        self.api.request(
            "POST",
            f"{self._app_path(app_id)}/domains",
            {
                "protocol": "http",
                "http": {
                    "service_id": service_id,
                    "container_port": 8080,
                    "certificate_id": 0,
                    "domain_name": readiness_url.removeprefix("http://"),
                    "domain_cookie": "",
                    "domain_header": "",
                    "domain_path": "/",
                    "the_weight": 100,
                    "whether_open": True,
                    "auto_ssl": False,
                    "auto_ssl_config": "",
                    "rule_extensions": [],
                    "configuration": {
                        "proxy_body_size": 0,
                        "proxy_buffer_numbers": 4,
                        "proxy_buffer_size": 4,
                        "proxy_buffering": "off",
                        "proxy_connect_timeout": 75,
                        "proxy_read_timeout": 600,
                        "proxy_send_timeout": 600,
                        "set_headers": [],
                    },
                },
            },
        )
        return {"service_id": service_id, "readiness_url": readiness_url}

    def _internal_service_host(self, app_id: int, component: dict[str, Any], port: int) -> str:
        service_id = str(component.get("service_id") or "")
        response = self.api.request(
            "GET",
            f"{self._app_path(app_id)}/services/{quote(service_id)}/ports",
        )
        rows = self._response_rows(response, "ports")
        match = next(
            (
                row
                for row in rows
                if int(row.get("container_port") or 0) == port and bool(row.get("is_inner_service"))
            ),
            None,
        )
        host = str((match or {}).get("k8s_service_name") or "")
        if not host:
            raise RuntimeError(
                f"Rainbond copied component {service_id} has no internal service on port {port}"
            )
        return host

    def _wait_component_running(self, app_id: int, service_id: str) -> None:
        deadline = time.monotonic() + self.config.readiness_timeout_seconds
        last_status = "not found"
        while time.monotonic() < deadline:
            response = self.api.request("GET", f"{self._app_path(app_id)}/services")
            rows = self._response_rows(response, "services")
            match = next((row for row in rows if str(row.get("service_id")) == service_id), None)
            if match is not None:
                last_status = str(match.get("status") or "unknown")
                if last_status == "running":
                    return
            self.sleep(5)
        raise TimeoutError(
            f"Rainbond initializer did not become ready: {service_id}: {last_status}"
        )

    def _wait_site(self, url: str) -> None:
        deadline = time.monotonic() + self.config.readiness_timeout_seconds
        last_error = "not checked"
        while time.monotonic() < deadline:
            try:
                request = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(request, timeout=20) as response:
                    if 200 <= response.status < 500:
                        return
                    last_error = f"HTTP {response.status}"
            except urllib.error.HTTPError as exc:
                # urlopen raises for 4xx/5xx; an application answering 4xx is up.
                if 200 <= exc.code < 500:
                    return
                last_error = f"HTTP {exc.code}"
            except (OSError, urllib.error.URLError) as exc:
                last_error = str(exc)
            self.sleep(5)
        raise TimeoutError(f"Rainbond WebArena site did not become ready: {url}: {last_error}")

    def _operate_existing_app(self, receipt: dict[str, Any], action: str) -> None:
        service_ids = [
            str(component["service_id"]) for component in (receipt.get("components") or {}).values()
        ]
        service_ids.append(str(receipt["initializer"]["service_id"]))
        self.api.request(
            "POST",
            f"{self._app_path(int(receipt['app_id']))}/operations",
            {"action": action, "service_ids": service_ids},
        )

    def _wait_app_stopped(self, receipt: dict[str, Any]) -> None:
        app_id = int(receipt["app_id"])
        expected = {
            str(component["service_id"]) for component in (receipt.get("components") or {}).values()
        }
        expected.add(str(receipt["initializer"]["service_id"]))
        deadline = time.monotonic() + self.config.readiness_timeout_seconds
        last_statuses: dict[str, str] = {}
        while time.monotonic() < deadline:
            response = self.api.request("GET", f"{self._app_path(app_id)}/services")
            rows = self._response_rows(response, "services")
            last_statuses = {
                str(row.get("service_id")): str(row.get("status") or "unknown")
                for row in rows
                if str(row.get("service_id")) in expected
            }
            if set(last_statuses) == expected and all(
                status in {"closed", "undeploy"} for status in last_statuses.values()
            ):
                return
            self.sleep(5)
        raise TimeoutError(
            f"Rainbond app did not stop before replacement: {app_id}: {last_statuses}"
        )

    def _stop_existing_app(self, receipt: dict[str, Any]) -> None:
        self._operate_existing_app(receipt, "stop")
        self._wait_app_stopped(receipt)

    def _delete_app(self, app_id: int) -> None:
        """Stop runtime services before removing their control-plane app record."""
        path = self._app_path(app_id)
        try:
            rows = self._response_rows(self.api.request("GET", f"{path}/services"), "services")
        except RuntimeError as exc:
            if isinstance(exc.__cause__, urllib.error.HTTPError) and exc.__cause__.code == 404:
                return
            raise
        service_ids = {str(row["service_id"]) for row in rows if row.get("service_id")}
        if service_ids:
            self.api.request(
                "POST",
                f"{path}/operations",
                {"action": "stop", "service_ids": sorted(service_ids)},
            )
            deadline = time.monotonic() + self.config.readiness_timeout_seconds
            while True:
                rows = self._response_rows(self.api.request("GET", f"{path}/services"), "services")
                statuses = {str(row.get("service_id")): row.get("status") for row in rows}
                if all(statuses.get(sid) in {"closed", "undeploy"} for sid in service_ids):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Rainbond app did not stop before deletion: {app_id}")
                self.sleep(5)
        self.api.request("DELETE", f"{path}?force=1")

    def provision(self, pool_id: str, slot: int, receipt_path: Path) -> dict[str, Any]:
        started = time.monotonic()
        phase_started = started
        timings: dict[str, float] = {}
        pool_id = self._validate_pool_id(pool_id)
        if slot < 1:
            raise ValueError("slot must be positive")
        app_id = self._create_app(pool_id, slot)
        timings["create_app_seconds"] = round(time.monotonic() - phase_started, 3)
        try:
            phase_started = time.monotonic()
            copied, copied_initializer = self._copy_components(app_id)
            timings["copy_components_seconds"] = round(time.monotonic() - phase_started, 3)
            phase_started = time.monotonic()
            components = {
                site: self._configure_component(
                    app_id,
                    pool_id=pool_id,
                    slot=slot,
                    site=site,
                    component=copied[site],
                )
                for site in STATEFUL_SITES
            }
            initializer = self._configure_initializer(
                app_id,
                pool_id=pool_id,
                slot=slot,
                initializer=copied_initializer,
                copied_sites=copied,
            )
            timings["configure_seconds"] = round(time.monotonic() - phase_started, 3)
            phase_started = time.monotonic()
            self.api.request(
                "POST",
                f"{self._app_path(app_id)}/operations",
                {
                    "action": "deploy",
                    "service_ids": [row["service_id"] for row in components.values()]
                    + [initializer["service_id"]],
                },
            )
            timings["deploy_request_seconds"] = round(time.monotonic() - phase_started, 3)
            phase_started = time.monotonic()
            self._wait_component_running(app_id, str(initializer["service_id"]))
            self._wait_site(str(initializer["readiness_url"]))
            for component in components.values():
                self._wait_site(str(component["site_url"]))
            timings["readiness_seconds"] = round(time.monotonic() - phase_started, 3)
        except BaseException as exc:
            _attempt_cleanup(
                exc,
                f"delete incomplete Rainbond app {app_id}",
                lambda: self._delete_app(app_id),
            )
            raise
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "pool_config_sha256": self.config_sha256,
            "pool_id": pool_id,
            "slot": slot,
            "generation": uuid.uuid4().hex,
            "app_id": app_id,
            "components": components,
            "initializer": initializer,
            "timings": {**timings, "total_seconds": round(time.monotonic() - started, 3)},
            "created_at_unix": int(time.time()),
        }
        _atomic_json(receipt_path, receipt)
        return receipt

    def destroy(self, receipt_path: Path, *, missing_ok: bool = True) -> None:
        if not receipt_path.exists():
            if missing_ok:
                return
            raise FileNotFoundError(receipt_path)
        receipt = self.validate_receipt(receipt_path)
        self._delete_app(int(receipt["app_id"]))
        receipt_path.unlink()

    def reset(self, receipt_path: Path) -> dict[str, Any]:
        previous = self.validate_receipt(receipt_path)
        pool_id = str(previous["pool_id"])
        slot = int(previous["slot"])
        replacement_path = receipt_path.with_name("slice-receipt.replacement.json")
        if replacement_path.exists():
            raise RuntimeError(f"unfinished Rainbond slice replacement: {replacement_path}")
        stopped_previous = self.config.reset_strategy == "stop_before_replace"
        if stopped_previous:
            self._stop_existing_app(previous)
        try:
            replacement = self.provision(pool_id, slot, replacement_path)
        except BaseException as exc:
            if not stopped_previous:
                _attempt_cleanup(
                    exc,
                    f"stop failed Rainbond slice {previous['app_id']}",
                    lambda: self._stop_existing_app(previous),
                )
            raise
        try:
            self._delete_app(int(previous["app_id"]))
        except BaseException as exc:
            replacement_deleted = _attempt_cleanup(
                exc,
                f"delete replacement Rainbond app {replacement['app_id']}",
                lambda: self._delete_app(int(replacement["app_id"])),
            )
            if replacement_deleted:
                _attempt_cleanup(
                    exc,
                    f"remove replacement receipt {replacement_path}",
                    lambda: replacement_path.unlink(missing_ok=True),
                )
            if not stopped_previous:
                _attempt_cleanup(
                    exc,
                    f"stop failed Rainbond slice {previous['app_id']}",
                    lambda: self._stop_existing_app(previous),
                )
            raise
        replacement_path.replace(receipt_path)
        return replacement

    def validate_receipt(self, receipt_path: Path) -> dict[str, Any]:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            receipt.get("schema") != RECEIPT_SCHEMA
            or receipt.get("pool_config_sha256") != self.config_sha256
            or set(receipt.get("components") or {}) != set(STATEFUL_SITES)
            or int(receipt.get("app_id") or 0) < 1
        ):
            raise ValueError("Rainbond slice receipt is invalid or belongs to another pool")
        return receipt

    def runtime_config(self, receipt: dict[str, Any]) -> dict[str, Any]:
        environments = dict(self.config.shared_environments)
        for site, component in receipt["components"].items():
            environments[f"__{site.upper()}__"] = {"urls": [component["site_url"]]}
        return {
            "image_lock": str(self.config.image_lock),
            "environments": environments,
            "rainbond_slice": {
                "pool_config": str(self.config_path),
                "receipt": "slice-receipt.json",
            },
        }

    def persist_runtime_config(
        self,
        receipt: dict[str, Any],
        path: Path,
        *,
        base_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically persist receipt-authoritative URLs for crash recovery."""
        value = {**(base_config or {}), **self.runtime_config(receipt)}
        _atomic_json(path, value)
        return value


def provision_pool(config_path: Path, output_dir: Path, pool_id: str, count: int) -> list[Path]:
    if count < 1:
        raise ValueError("count must be positive")
    pool = RainbondSlicePool(config_path)
    created: list[Path] = []

    def create_slot(slot: int) -> Path:
        slot_dir = output_dir / f"slice-{slot}"
        receipt_path = slot_dir / "slice-receipt.json"
        if receipt_path.exists():
            raise FileExistsError(f"Rainbond slice already exists: {receipt_path}")
        try:
            receipt = pool.provision(pool_id, slot, receipt_path)
            _atomic_json(slot_dir / "webarena.config.json", pool.runtime_config(receipt))
        except BaseException as exc:
            if receipt_path.exists():
                _attempt_cleanup(
                    exc,
                    f"destroy incomplete slot {slot}",
                    lambda: pool.destroy(receipt_path),
                )
            raise
        return slot_dir

    try:
        workers = min(count, getattr(getattr(pool, "config", None), "max_parallel_provisions", 2))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(create_slot, slot): slot for slot in range(1, count + 1)}
            first_error: BaseException | None = None
            for future in as_completed(futures):
                try:
                    created.append(future.result())
                except BaseException as exc:
                    first_error = first_error or exc
            if first_error is not None:
                raise first_error
    except BaseException as exc:
        for slot_dir in reversed(created):
            _attempt_cleanup(
                exc,
                f"destroy rolled-back slot {slot_dir.name}",
                lambda slot_dir=slot_dir: pool.destroy(slot_dir / "slice-receipt.json"),
            )
        raise
    return sorted(created, key=_slot_sort_key)


@contextmanager
def _pool_lock(output_dir: Path) -> Any:
    output_dir = output_dir.resolve()
    with _LOCAL_POOL_LOCKS_GUARD:
        local_lock = _LOCAL_POOL_LOCKS.setdefault(output_dir, threading.Lock())
    with local_lock:
        output_dir.mkdir(parents=True, exist_ok=True)
        lock_path = output_dir / ".pool.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def scale_pool(
    config_path: Path,
    output_dir: Path,
    pool_id: str,
    count: int,
    *,
    minimum_ready: int = 1,
) -> list[Path]:
    """Reconcile a persistent warm pool, never scaling below minimum_ready."""
    if minimum_ready < 0 or count < minimum_ready:
        raise ValueError(
            "count must be at least minimum_ready, and minimum_ready must not be negative"
        )
    with _pool_lock(output_dir):
        pool = RainbondSlicePool(config_path)
        pool_id = _validate_pool_id(pool_id)
        existing: dict[int, Path] = {}
        for receipt_path in output_dir.glob("slice-*/slice-receipt.json"):
            receipt = pool.validate_receipt(receipt_path)
            if str(receipt.get("pool_id")) != pool_id:
                raise ValueError(f"slice belongs to another pool: {receipt_path}")
            existing[int(receipt["slot"])] = receipt_path.parent
        missing_slots = [slot for slot in range(1, count + 1) if slot not in existing]
        if missing_slots:
            workers = min(len(missing_slots), pool.config.max_parallel_provisions)

            def create_slot(slot: int) -> tuple[int, Path]:
                slot_dir = output_dir / f"slice-{slot}"
                receipt_path = slot_dir / "slice-receipt.json"
                try:
                    receipt = pool.provision(pool_id, slot, receipt_path)
                    _atomic_json(slot_dir / "webarena.config.json", pool.runtime_config(receipt))
                except BaseException as exc:
                    if receipt_path.exists():
                        _attempt_cleanup(
                            exc,
                            f"destroy incomplete slot {slot}",
                            lambda: pool.destroy(receipt_path),
                        )
                    raise
                return slot, slot_dir

            created: dict[int, Path] = {}
            first_error: BaseException | None = None
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(create_slot, slot) for slot in missing_slots]
                for future in as_completed(futures):
                    try:
                        slot, slot_dir = future.result()
                        created[slot] = slot_dir
                    except BaseException as exc:
                        first_error = first_error or exc
            if first_error is not None:
                for slot, slot_dir in sorted(created.items(), reverse=True):
                    _attempt_cleanup(
                        first_error,
                        f"destroy rolled-back slot {slot}",
                        lambda slot_dir=slot_dir: pool.destroy(slot_dir / "slice-receipt.json"),
                    )
                raise first_error
            existing.update(created)
        excess_slots = sorted((value for value in existing if value > count), reverse=True)
        leased_excess = [
            existing[slot]
            for slot in excess_slots
            if (existing[slot] / "slice-lease.json").exists()
        ]
        if leased_excess:
            raise RuntimeError(
                "cannot remove leased Rainbond slices: "
                + ", ".join(str(path) for path in leased_excess)
            )
        first_destroy_error: BaseException | None = None
        for slot in excess_slots:
            slot_dir = existing[slot]
            try:
                pool.destroy(slot_dir / "slice-receipt.json")
            except BaseException as exc:
                if first_destroy_error is None:
                    first_destroy_error = exc
                else:
                    first_destroy_error.add_note(
                        f"additional scale-down failure for slot {slot}: {exc}"
                    )
                continue
            existing.pop(slot)
            config_file = slot_dir / "webarena.config.json"
            if config_file.exists():
                config_file.unlink()
            try:
                slot_dir.rmdir()
            except OSError:
                pass
        if first_destroy_error is not None:
            raise first_destroy_error
        return [existing[slot] for slot in sorted(existing)]


def reconcile_pool_capacity(
    config_path: Path,
    output_dir: Path,
    pool_id: str,
    active_count: int,
    *,
    minimum_ready: int = 1,
) -> list[Path]:
    """Keep one or more idle slices in addition to current task demand."""
    if active_count < 0:
        raise ValueError("active_count must not be negative")
    return scale_pool(
        config_path,
        output_dir,
        pool_id,
        active_count + minimum_ready,
        minimum_ready=minimum_ready,
    )


def acquire_slice(output_dir: Path, owner: str) -> dict[str, Any]:
    """Atomically grant one ready slice to one task owner."""
    owner = owner.strip()
    if not owner:
        raise ValueError("lease owner is required")
    with _pool_lock(output_dir):
        for slot_dir in sorted(output_dir.glob("slice-*"), key=_slot_sort_key):
            receipt_path = slot_dir / "slice-receipt.json"
            config_path = slot_dir / "webarena.config.json"
            lease_path = slot_dir / "slice-lease.json"
            if not receipt_path.is_file() or not config_path.is_file() or lease_path.exists():
                continue
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("schema") != RECEIPT_SCHEMA:
                raise ValueError(f"invalid Rainbond slice receipt: {receipt_path}")
            lease = {
                "schema": LEASE_SCHEMA,
                "lease_id": uuid.uuid4().hex,
                "owner": owner,
                "slot": int(receipt["slot"]),
                "app_id": int(receipt["app_id"]),
                "generation": str(receipt["generation"]),
                "config": str(config_path.resolve()),
                "acquired_at_unix": int(time.time()),
            }
            _atomic_json(lease_path, lease)
            return lease
    raise RuntimeError("no unleased Rainbond WebArena slice is ready")


def release_slice(config_path: Path, output_dir: Path, lease_id: str) -> dict[str, Any]:
    """Replace a dirty slice before returning it to the ready pool."""
    with _pool_lock(output_dir):
        matches: list[tuple[Path, dict[str, Any]]] = []
        for lease_path in output_dir.glob("slice-*/slice-lease.json"):
            lease = json.loads(lease_path.read_text(encoding="utf-8"))
            if lease.get("schema") == LEASE_SCHEMA and lease.get("lease_id") == lease_id:
                matches.append((lease_path, lease))
        if len(matches) != 1:
            raise ValueError("lease_id does not identify exactly one active slice")
        lease_path, lease = matches[0]
        slot_dir = lease_path.parent
    # Keep the lease while resetting so acquire/scale skip this slot. The slot
    # lock serializes competing releases without blocking unrelated ready slots.
    with _pool_lock(slot_dir):
        if not lease_path.is_file() or json.loads(lease_path.read_text(encoding="utf-8")) != lease:
            raise ValueError("lease changed before release")
        pool = RainbondSlicePool(config_path)
        receipt_path = slot_dir / "slice-receipt.json"
        runtime_config_path = slot_dir / "webarena.config.json"
        current_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        release_from_app_id = lease.get("release_from_app_id")
        if release_from_app_id is not None and int(current_receipt["app_id"]) != int(
            release_from_app_id
        ):
            current_receipt = pool.validate_receipt(receipt_path)
            _atomic_json(runtime_config_path, pool.runtime_config(current_receipt))
            lease_path.unlink()
            return {
                "lease_id": lease_id,
                "released_app_id": int(lease["app_id"]),
                "ready_app_id": int(current_receipt["app_id"]),
                "config": str(runtime_config_path.resolve()),
            }
        if release_from_app_id is None:
            # Task preparation also replaces instances. Only this release's
            # replacement proves the task's mutations have been removed.
            lease = {**lease, "release_from_app_id": int(current_receipt["app_id"])}
            _atomic_json(lease_path, lease)
        receipt = pool.reset(receipt_path)
        _atomic_json(runtime_config_path, pool.runtime_config(receipt))
        lease_path.unlink()
        return {
            "lease_id": lease_id,
            "released_app_id": int(lease["app_id"]),
            "ready_app_id": int(receipt["app_id"]),
            "config": str((slot_dir / "webarena.config.json").resolve()),
        }


def pool_main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    provision = subparsers.add_parser("provision")
    provision.add_argument("--pool-id", required=True)
    provision.add_argument("--count", type=int, required=True)
    provision.add_argument("--output", type=Path, required=True)
    scale = subparsers.add_parser("scale")
    scale.add_argument("--pool-id", required=True)
    scale_group = scale.add_mutually_exclusive_group(required=True)
    scale_group.add_argument("--count", type=int)
    scale_group.add_argument("--active", type=int)
    scale.add_argument("--minimum-ready", type=int, default=1)
    scale.add_argument("--output", type=Path, required=True)
    reset = subparsers.add_parser("reset")
    reset.add_argument("--slice-dir", type=Path, required=True)
    destroy = subparsers.add_parser("destroy")
    destroy.add_argument("--slice-dir", type=Path, action="append", required=True)
    acquire = subparsers.add_parser("acquire")
    acquire.add_argument("--output", type=Path, required=True)
    acquire.add_argument("--owner", required=True)
    release = subparsers.add_parser("release")
    release.add_argument("--output", type=Path, required=True)
    release.add_argument("--lease-id", required=True)
    args = parser.parse_args(argv)

    if args.command == "provision":
        paths = provision_pool(
            args.config.resolve(), args.output.resolve(), args.pool_id, args.count
        )
        print(json.dumps([str(path / "webarena.config.json") for path in paths]))
        return

    if args.command == "scale":
        if args.active is not None:
            paths = reconcile_pool_capacity(
                args.config.resolve(),
                args.output.resolve(),
                args.pool_id,
                args.active,
                minimum_ready=args.minimum_ready,
            )
        else:
            paths = scale_pool(
                args.config.resolve(),
                args.output.resolve(),
                args.pool_id,
                args.count,
                minimum_ready=args.minimum_ready,
            )
        print(json.dumps([str(path / "webarena.config.json") for path in paths]))
        return

    if args.command == "acquire":
        print(json.dumps(acquire_slice(args.output.resolve(), args.owner), sort_keys=True))
        return

    if args.command == "release":
        print(
            json.dumps(
                release_slice(args.config.resolve(), args.output.resolve(), args.lease_id),
                sort_keys=True,
            )
        )
        return

    pool = RainbondSlicePool(args.config.resolve())
    if args.command == "reset":
        slot_dir = args.slice_dir.resolve()
        receipt = pool.reset(slot_dir / "slice-receipt.json")
        target = slot_dir / "webarena.config.json"
        pool.persist_runtime_config(receipt, target)
        print(target)
        return

    first_error: BaseException | None = None
    for slot_dir in args.slice_dir:
        try:
            pool.destroy(slot_dir.resolve() / "slice-receipt.json")
        except BaseException as exc:
            if first_error is None:
                first_error = exc
            else:
                first_error.add_note(f"additional destroy failure for {slot_dir}: {exc}")
    if first_error is not None:
        raise first_error
