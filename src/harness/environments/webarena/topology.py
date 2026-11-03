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
import re
from collections.abc import Sequence, Set
from pathlib import Path
from typing import Any

from harness.env_file import local_setting, repository_credential

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
        if bool(row["mutable"]) != ("credentials_env" in row):
            raise ValueError(f"only mutable WebArena sites carry credentials: {name}")
        if row["mutable"] and "container_port" not in row:
            raise ValueError(f"mutable WebArena sites need a container port: {name}")
        if row["mutable"]:
            password = row["credentials_env"].get("password")
            if not isinstance(password, str) or not password.strip():
                raise ValueError(f"mutable WebArena sites need a password variable: {name}")
    return payload


TOPOLOGY = _load(TOPOLOGY_PATH)
_HOSTS = TOPOLOGY["hosts"]
_SITES = TOPOLOGY["sites"]
_SERVICES = TOPOLOGY["container_services"]

CONTAINER_HOST_ALIAS: str = _HOSTS["container_host_alias"]
HOST_LOOPBACK: str = _HOSTS["host_loopback"]
RECORDING_GATEWAYS_ENV: str = _HOSTS["recording_gateways_env"]
_BARE_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")


def _configured_gateways() -> tuple[str, ...]:
    """Return the entries as written, read on use so importing this module reads no file."""
    hosts = tuple(
        host.strip() for host in local_setting(RECORDING_GATEWAYS_ENV).split(",") if host.strip()
    )
    for host in hosts:
        if not _BARE_HOST.fullmatch(host):
            raise ValueError(
                f"{RECORDING_GATEWAYS_ENV} entries must be bare hostnames or IPv4 addresses"
            )
    return hosts


class _RecordingGateways(Sequence):
    """Canonical lowercase hosts, the form URL parsing gives for membership checks."""

    def _hosts(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(host.lower() for host in _configured_gateways()))

    def __getitem__(self, index):
        return self._hosts()[index]

    def __len__(self) -> int:
        return len(self._hosts())


class _RecordedHosts(Set):
    """Fixed hosts plus the configured gateways at the moment of use."""

    def __init__(self, fixed) -> None:
        self._fixed = frozenset(fixed)

    @classmethod
    def _from_iterable(cls, values):
        return frozenset(values)

    def _hosts(self) -> frozenset[str]:
        return self._fixed | frozenset(RECORDING_GATEWAYS)

    def __contains__(self, host) -> bool:
        return host in self._hosts()

    def __iter__(self):
        return iter(self._hosts())

    def __len__(self) -> int:
        return len(self._hosts())


RECORDING_GATEWAYS = _RecordingGateways()
LOCAL_RECORDED_HOSTS = _RecordedHosts({HOST_LOOPBACK, CONTAINER_HOST_ALIAS})
TRUSTED_RECORDED_HOSTS = _RecordedHosts(
    {*_HOSTS["public_recorded_hosts"], HOST_LOOPBACK, CONTAINER_HOST_ALIAS}
)

MUTABLE_SITE_PORTS: dict[str, tuple[int, int]] = {
    name: (int(row["port"]), int(row["control_port"]))
    for name, row in _SITES.items()
    if row["mutable"]
}
READ_ONLY_SITE_PORTS: dict[int, str] = {
    int(row["port"]): name for name, row in _SITES.items() if not row["mutable"]
}
CREDENTIAL_SITES: tuple[str, ...] = tuple(
    name for name, row in _SITES.items() if row["mutable"]
)
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


def require_recorded_gateway(host: str | None) -> None:
    """Stop when a frozen private host would stay in actions instead of being rebound."""
    if host not in RECORDING_GATEWAYS:
        raise ValueError(
            f"a recorded private host on a WebArena port is not listed in {RECORDING_GATEWAYS_ENV}"
        )


def _redacted_gateways() -> tuple[str, ...]:
    """Redaction also covers the spelling an entry was written in."""
    return tuple(dict.fromkeys((*RECORDING_GATEWAYS, *_configured_gateways())))


def public_origin_replacements() -> dict[str, str]:
    """Map every recorded mutable-site origin to its public site token."""
    replacements: dict[str, str] = {}
    for host in (CONTAINER_HOST_ALIAS, *_redacted_gateways()):
        for site in MUTABLE_SITE_PORTS:
            token = "__" + site.upper() + "__"
            replacements[site_url(site, host)] = token
            replacements[site_origin(site, host)] = token
    return replacements


def public_host_replacements() -> dict[str, str]:
    """Map container-visible WebArena hosts to the public benchmark-host placeholder."""
    hosts = (CONTAINER_HOST_ALIAS, *_redacted_gateways())
    return {host: BENCHMARK_HOST_PLACEHOLDER for host in hosts}


def site_credentials(site: str) -> dict[str, str]:
    """Resolve one site's login from the repository .env, naming anything unset."""
    names = _SITES[site]["credentials_env"]
    values = {field: repository_credential(name) for field, name in names.items()}
    missing = sorted(names[field] for field, value in values.items() if not value.strip())
    if missing:
        raise ValueError(f"WebArena site {site} requires {', '.join(missing)}")
    return values


def require_site_credentials(sites) -> None:
    """Stop a run before any site or browser starts when a login it needs is unset."""
    for site in sites:
        name = getattr(site, "value", site)
        if name in CREDENTIAL_SITES:
            site_credentials(name)


def known_site_secrets() -> tuple[str, ...]:
    """Return the resolved site passwords that public evidence must never contain."""
    return tuple(site_credentials(site)["password"] for site in CREDENTIAL_SITES)


def configured_site_secrets() -> tuple[str, ...]:
    """Return the site passwords set in the repository .env, skipping any that are unset."""
    passwords = (
        repository_credential(_SITES[site]["credentials_env"]["password"])
        for site in CREDENTIAL_SITES
    )
    return tuple(password for password in passwords if password.strip())
