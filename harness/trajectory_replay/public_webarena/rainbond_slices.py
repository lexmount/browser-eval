"""Provision disposable, fully isolated WebArena slices on Rainbond."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

SCHEMA = "lex_webarena_rainbond_pool/1"
RECEIPT_SCHEMA = "lex_webarena_rainbond_slice/1"
STATEFUL_SITES = ("shopping_admin", "shopping", "reddit", "gitlab")
SITE_PORTS = {"shopping_admin": 80, "shopping": 80, "reddit": 80, "gitlab": 8023}
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _image_digest(value: str) -> str:
    marker = "@sha256:"
    if marker not in value:
        raise ValueError(f"container image is not digest pinned: {value!r}")
    return "sha256:" + value.rsplit(marker, 1)[1]


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


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
    app_prefix: str
    domain_suffix: str
    token_env: str
    image_lock: Path
    source_runtime_images: dict[str, str]
    shared_environments: dict[str, Any]
    readiness_timeout_seconds: int

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
        runtime_images_value = raw.get("source_runtime_images") or {}
        if not isinstance(runtime_images_value, dict) or set(runtime_images_value) - set(
            STATEFUL_SITES
        ):
            raise ValueError("source_runtime_images contains an unknown site")
        runtime_images = {str(site): str(image) for site, image in runtime_images_value.items()}
        for image in runtime_images.values():
            _image_digest(image)
        value = cls(
            api_base_url=str(raw.get("api_base_url") or "").rstrip("/"),
            team_id=str(raw.get("team_id") or ""),
            team_name=str(raw.get("team_name") or ""),
            region_name=str(raw.get("region_name") or ""),
            source_app_id=int(raw.get("source_app_id") or 0),
            source_services=normalized_services,
            app_prefix=prefix,
            domain_suffix=suffix,
            token_env=str(raw.get("token_env") or "RAINBOND_API_TOKEN"),
            image_lock=lock_path.resolve(),
            source_runtime_images=runtime_images,
            shared_environments=dict(raw.get("shared_environments") or {}),
            readiness_timeout_seconds=int(raw.get("readiness_timeout_seconds") or 1800),
        )
        if (
            not all((value.api_base_url, value.team_id, value.team_name, value.region_name))
            or value.source_app_id < 1
        ):
            raise ValueError("Rainbond pool API, team, region, and source app are required")
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

    def _domain(self, pool_id: str, slot: int, site: str, app_id: int) -> str:
        label = (
            f"{self.config.app_prefix}-{pool_id}-{slot}-{app_id}-{site.replace('_', '-')}"
        )
        if len(label) > 63:
            label = f"{label[:50].rstrip('-')}-{hashlib.sha256(label.encode()).hexdigest()[:10]}"
        return f"{label}.{self.config.domain_suffix}"

    @staticmethod
    def _validate_pool_id(pool_id: str) -> str:
        normalized = pool_id.strip().lower()
        if not _SAFE_ID.fullmatch(normalized):
            raise ValueError("pool_id must be a lowercase DNS-safe identifier")
        return normalized

    def _create_app(self, pool_id: str, slot: int) -> int:
        name = f"{self.config.app_prefix}-{pool_id}-{slot}"
        note = f"managed-by=browser-eval;pool={pool_id};slot={slot}"
        result = self.api.request("POST", self._apps_path(), {"app_name": name, "app_note": note})
        app_id = int((result or {}).get("ID") or 0)
        if app_id < 1:
            raise RuntimeError("Rainbond create app response has no ID")
        return app_id

    def _copy_components(self, app_id: int) -> dict[str, dict[str, Any]]:
        config = self.config
        result = self.api.request(
            "POST",
            f"{self._app_path(config.source_app_id)}/copy",
            {
                "services": [
                    {"service_id": config.source_services[site]} for site in STATEFUL_SITES
                ],
                "target_team_name": config.team_name,
                "target_region_name": config.region_name,
                "target_app_id": app_id,
            },
        )
        rows = list((result or {}).get("services") or [])
        by_source_name = {
            site: str(self.raw_config.get("source_component_names", {}).get(site) or site)
            for site in STATEFUL_SITES
        }
        mapped: dict[str, dict[str, Any]] = {}
        for site, component_name in by_source_name.items():
            matches = [row for row in rows if str(row.get("service_cname")) == component_name]
            if len(matches) != 1:
                raise RuntimeError(
                    f"Rainbond copy returned {len(matches)} components named {component_name!r}"
                )
            mapped[site] = matches[0]
        return mapped

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
        expected_runtime_image = self.config.source_runtime_images.get(site, expected_image)
        actual_image = str(component.get("image") or "")
        if actual_image and _image_digest(actual_image) != _image_digest(expected_runtime_image):
            raise RuntimeError(
                f"Rainbond copied {site} runtime image mismatch: "
                f"{actual_image!r} != {expected_runtime_image!r}"
            )
        return {
            "service_id": service_id,
            "service_cname": component.get("service_cname"),
            "image": expected_image,
            "runtime_image": actual_image or expected_image,
            "site_url": site_url,
        }

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
            except (OSError, urllib.error.URLError) as exc:
                last_error = str(exc)
            self.sleep(5)
        raise TimeoutError(f"Rainbond WebArena site did not become ready: {url}: {last_error}")

    def provision(self, pool_id: str, slot: int, receipt_path: Path) -> dict[str, Any]:
        pool_id = self._validate_pool_id(pool_id)
        if slot < 1:
            raise ValueError("slot must be positive")
        app_id = self._create_app(pool_id, slot)
        try:
            copied = self._copy_components(app_id)
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
            self.api.request(
                "POST",
                f"{self._app_path(app_id)}/operations",
                {
                    "action": "deploy",
                    "service_ids": [row["service_id"] for row in components.values()],
                },
            )
            for component in components.values():
                self._wait_site(str(component["site_url"]))
        except BaseException:
            self.api.request("DELETE", f"{self._app_path(app_id)}?force=1")
            raise
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "pool_config_sha256": self.config_sha256,
            "pool_id": pool_id,
            "slot": slot,
            "generation": uuid.uuid4().hex,
            "app_id": app_id,
            "components": components,
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
        self.api.request("DELETE", f"{self._app_path(int(receipt['app_id']))}?force=1")
        receipt_path.unlink()

    def reset(self, receipt_path: Path) -> dict[str, Any]:
        previous = self.validate_receipt(receipt_path)
        pool_id = str(previous["pool_id"])
        slot = int(previous["slot"])
        self.destroy(receipt_path, missing_ok=False)
        return self.provision(pool_id, slot, receipt_path)

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


def provision_pool(config_path: Path, output_dir: Path, pool_id: str, count: int) -> list[Path]:
    if count < 1:
        raise ValueError("count must be positive")
    pool = RainbondSlicePool(config_path)
    created: list[Path] = []
    try:
        for slot in range(1, count + 1):
            slot_dir = output_dir / f"slice-{slot}"
            receipt_path = slot_dir / "slice-receipt.json"
            receipt = pool.provision(pool_id, slot, receipt_path)
            _atomic_json(slot_dir / "webarena.config.json", pool.runtime_config(receipt))
            created.append(slot_dir)
    except BaseException:
        for slot_dir in reversed(created):
            pool.destroy(slot_dir / "slice-receipt.json")
        raise
    return created
