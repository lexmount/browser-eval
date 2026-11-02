"""The single WebArena topology registry: hosts, ports, origins and site credentials.

Every host, port, origin and redaction table used for WebArena sites is derived
from ``topology.json``. Recording gateways are properties of the machines that
recorded frozen catalogs and evidence, so they live in that data file rather than
in code; they remain recognized only to rebind and redact historical inputs.

The registry is keyed by site name and has no dependency on the optional
WebArena evaluator package; ``runtime`` converts names to ``WebArenaSite``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TOPOLOGY_PATH = Path(__file__).with_name("topology.json")
TOPOLOGY_SCHEMA = "lex_webarena_topology/1"
BENCHMARK_HOST_PLACEHOLDER = "__BENCHMARK_HOST__"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != TOPOLOGY_SCHEMA:
        raise ValueError("invalid WebArena topology schema")
    sites = payload.get("sites")
    if not isinstance(sites, dict) or not sites:
        raise ValueError("WebArena topology must define sites")
    used: set[int] = set()
    attributed: dict[str, set[int]] = {"recorded_ports": set(), "official_trace_ports": set()}
    for name, row in sites.items():
        ports = (int(row["port"]), int(row["control_port"]))
        if len(set(ports)) != 2 or set(ports) & used:
            raise ValueError(f"WebArena topology ports must be unique: {name}")
        used.update(ports)
        for field, seen in attributed.items():
            site_ports = {int(port) for port in row[field]}
            if row["port"] not in site_ports:
                raise ValueError(f"WebArena recorded ports must include the site port: {name}")
            if len(site_ports) != len(row[field]) or site_ports & seen:
                raise ValueError(f"WebArena {field} must attribute each port once: {name}")
            seen.update(site_ports)
        if bool(row["mutable"]) != ("credentials" in row):
            raise ValueError(f"only mutable WebArena sites carry credentials: {name}")
        if row["mutable"] and "container_port" not in row:
            raise ValueError(f"mutable WebArena sites need a container port: {name}")
    return payload


TOPOLOGY = _load(TOPOLOGY_PATH)
_HOSTS = TOPOLOGY["hosts"]
_SITES = TOPOLOGY["sites"]
_SERVICES = TOPOLOGY["container_services"]

CONTAINER_HOST_ALIAS: str = _HOSTS["container_host_alias"]
HOST_LOOPBACK: str = _HOSTS["host_loopback"]
RECORDING_GATEWAYS: tuple[str, ...] = tuple(_HOSTS["recording_gateways"])
LOCAL_RECORDED_HOSTS = frozenset({HOST_LOOPBACK, CONTAINER_HOST_ALIAS, *RECORDING_GATEWAYS})
TRUSTED_RECORDED_HOSTS = frozenset(_HOSTS["public_recorded_hosts"]) | LOCAL_RECORDED_HOSTS

MUTABLE_SITE_PORTS: dict[str, tuple[int, int]] = {
    name: (int(row["port"]), int(row["control_port"]))
    for name, row in _SITES.items()
    if row["mutable"]
}
READ_ONLY_SITE_PORTS: dict[int, str] = {
    int(row["port"]): name for name, row in _SITES.items() if not row["mutable"]
}
DEFAULT_SITE_CREDENTIALS: dict[str, dict[str, str]] = {
    name: dict(row["credentials"]) for name, row in _SITES.items() if row["mutable"]
}
SITE_URL_SUFFIX: dict[str, str] = {
    name: row["url_suffix"] for name, row in _SITES.items() if row.get("url_suffix")
}
SITE_CONTAINER_PORTS: dict[str, int] = {
    name: int(row["container_port"]) for name, row in _SITES.items() if "container_port" in row
}
RECORDED_PORT_SITES: dict[int, str] = {
    port: name for name, row in _SITES.items() for port in row["recorded_ports"]
}
OFFICIAL_TRACE_PORTS: dict[str, tuple[int, ...]] = {
    name: tuple(row["official_trace_ports"]) for name, row in _SITES.items()
}

ENV_CTRL_CONTAINER_PORT: int = int(_SERVICES["env_ctrl_port"])
INITIALIZER_READINESS_PORT: int = int(_SERVICES["initializer_readiness_port"])
MAGENTO_ELASTICSEARCH_PORT: int = int(_SERVICES["magento_elasticsearch_port"])


def site_origin(site: str, host: str) -> str:
    """Return the scheme, host and port of one site (a name or ``WebArenaSite``) at ``host``."""
    return f"http://{host}:{_SITES[str(site)]['port']}"


def site_url(site: str, host: str) -> str:
    """Return a site's entry URL at ``host``, including its path suffix."""
    return site_origin(site, host) + SITE_URL_SUFFIX.get(str(site), "")


def public_origin_replacements() -> dict[str, str]:
    """Map every recorded mutable-site origin to its public site token."""
    replacements: dict[str, str] = {}
    for host in (CONTAINER_HOST_ALIAS, *RECORDING_GATEWAYS):
        for site in MUTABLE_SITE_PORTS:
            token = "__" + site.upper() + "__"
            replacements[site_url(site, host)] = token
            replacements[site_origin(site, host)] = token
    return replacements


def public_host_replacements() -> dict[str, str]:
    """Map container-visible WebArena hosts to the public benchmark-host placeholder."""
    hosts = (CONTAINER_HOST_ALIAS, *RECORDING_GATEWAYS)
    return {host: BENCHMARK_HOST_PLACEHOLDER for host in hosts}


def known_site_secrets() -> tuple[str, ...]:
    """Return the WebArena site passwords that public evidence must never contain."""
    return tuple(credentials["password"] for credentials in DEFAULT_SITE_CREDENTIALS.values())
