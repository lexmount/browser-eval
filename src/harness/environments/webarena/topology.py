"""The single WebArena topology registry: hosts, ports, origins and site credentials.

Every host, port, origin and redaction table used for WebArena sites is derived
from ``topology.json``. Recording gateways are properties of the machines that
recorded frozen catalogs and evidence, so they live in that data file rather than
in code; they remain recognized only to rebind and redact historical inputs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from webarena_verified.types.task import WebArenaSite

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
    for name, row in sites.items():
        WebArenaSite(name)
        ports = (int(row["port"]), int(row["control_port"]))
        if len(set(ports)) != 2 or set(ports) & used:
            raise ValueError(f"WebArena topology ports must be unique: {name}")
        used.update(ports)
        if row["port"] not in row["recorded_ports"] or row["port"] not in row[
            "official_trace_ports"
        ]:
            raise ValueError(f"WebArena recorded ports must include the site port: {name}")
        if bool(row["mutable"]) != ("credentials" in row and "container_port" in row):
            raise ValueError(f"only mutable WebArena sites carry credentials: {name}")
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

SITE_PORTS: dict[WebArenaSite, tuple[int, int]] = {
    WebArenaSite(name): (int(row["port"]), int(row["control_port"]))
    for name, row in _SITES.items()
    if row["mutable"]
}
READ_ONLY_PORTS: dict[int, WebArenaSite] = {
    int(row["port"]): WebArenaSite(name) for name, row in _SITES.items() if not row["mutable"]
}
SITE_CREDENTIALS: dict[WebArenaSite, dict[str, str]] = {
    WebArenaSite(name): dict(row["credentials"]) for name, row in _SITES.items() if row["mutable"]
}
SITE_URL_SUFFIX: dict[WebArenaSite, str] = {
    WebArenaSite(name): row["url_suffix"] for name, row in _SITES.items() if row.get("url_suffix")
}
SITE_CONTAINER_PORTS: dict[str, int] = {
    name: int(row["container_port"]) for name, row in _SITES.items() if row["mutable"]
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


def site_origin(site: WebArenaSite | str, host: str) -> str:
    """Return the scheme, host and port of one site at ``host``."""
    return f"http://{host}:{_SITES[WebArenaSite(site).value]['port']}"


def site_url(site: WebArenaSite | str, host: str) -> str:
    """Return a site's entry URL at ``host``, including its path suffix."""
    site = WebArenaSite(site)
    return site_origin(site, host) + SITE_URL_SUFFIX.get(site, "")


def public_origin_replacements() -> dict[str, str]:
    """Map every recorded mutable-site origin to its public site token."""
    replacements: dict[str, str] = {}
    for host in (CONTAINER_HOST_ALIAS, *RECORDING_GATEWAYS):
        for site in SITE_PORTS:
            token = "__" + site.value.upper() + "__"
            replacements[site_url(site, host)] = token
            replacements[site_origin(site, host)] = token
    return replacements


def public_host_replacements() -> dict[str, str]:
    """Map container-visible WebArena hosts to the public benchmark-host placeholder."""
    hosts = (CONTAINER_HOST_ALIAS, *RECORDING_GATEWAYS)
    return {host: BENCHMARK_HOST_PLACEHOLDER for host in hosts}


def known_site_secrets() -> tuple[str, ...]:
    """Return the WebArena site passwords that public evidence must never contain."""
    return tuple(credentials["password"] for credentials in SITE_CREDENTIALS.values())
