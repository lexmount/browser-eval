"""Validated image lock and host-local runtime configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SITES = ("shopping_admin", "shopping", "reddit", "gitlab", "map", "wikipedia")
ARCH_ALIASES = {"aarch64": "arm64", "x86_64": "amd64"}


@dataclass(frozen=True)
class SiteConfig:
    name: str
    image: str
    architecture: str
    port: int
    control_port: int


@dataclass(frozen=True)
class RuntimeConfig:
    path: Path
    hostname: str
    sites: dict[str, SiteConfig]

    @classmethod
    def load(cls, path: str | Path) -> RuntimeConfig:
        config_path = Path(path).resolve()
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if payload.get("schema") != "lexmount_webarena_runtime/1":
            raise ValueError("invalid WebArena runtime schema")
        hostname = str(payload.get("hostname") or "").strip()
        if not hostname or "${" in hostname:
            raise ValueError("runtime hostname must replace the example placeholder")
        lock_path = Path(str(payload.get("image_lock") or ""))
        if not lock_path.is_absolute():
            lock_path = config_path.parent / lock_path
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if lock.get("schema") != "lex_webarena_environment_images/1":
            raise ValueError("invalid WebArena image lock schema")
        if lock.get("webarena_verified_version") != "1.2.3":
            raise ValueError("image lock does not match webarena-verified 1.2.3")
        runtime_sites = payload.get("sites") or {}
        locked_sites = lock.get("images") or {}
        sites: dict[str, SiteConfig] = {}
        used_ports: set[int] = set()
        for name in SITES:
            runtime = runtime_sites.get(name)
            image = locked_sites.get(name)
            if not isinstance(runtime, dict) or not isinstance(image, dict):
                raise ValueError(f"runtime or image lock is missing site {name}")
            port = int(runtime.get("port") or 0)
            control_port = int(runtime.get("control_port") or 0)
            if not 1 <= port <= 65535 or not 1 <= control_port <= 65535:
                raise ValueError(f"site {name} ports must be between 1 and 65535")
            if port == control_port or {port, control_port} & used_ports:
                raise ValueError(f"site {name} ports must be unique")
            used_ports.update((port, control_port))
            pinned = str(image.get("pinned") or "")
            if "@sha256:" not in pinned:
                raise ValueError(f"site {name} image must be pinned by digest")
            architecture = normalized_architecture(image.get("architecture"))
            if architecture not in {"amd64", "arm64"}:
                raise ValueError(f"site {name} architecture must be explicitly amd64 or arm64")
            sites[name] = SiteConfig(
                name=name,
                image=pinned,
                architecture=architecture,
                port=port,
                control_port=control_port,
            )
        return cls(path=config_path, hostname=hostname, sites=sites)


def normalized_architecture(value: Any) -> str:
    architecture = str(value or "")
    return ARCH_ALIASES.get(architecture, architecture)
