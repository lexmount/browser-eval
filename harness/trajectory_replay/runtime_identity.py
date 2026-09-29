"""Validate the browser runtime identity shared by replay audit and comparison."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit

REMOTE_RUNTIME_FIELDS = (
    "protocolVersion",
    "product",
    "revision",
    "userAgent",
    "jsVersion",
)


def request_bridge_identity(binding: dict[str, Any]) -> dict[str, object] | None:
    bridge = binding.get("request_bridge")
    engine = binding.get("engine")
    if bridge is None:
        if engine == "kitesurf":
            raise ValueError("Kitesurf run is missing its request bridge identity")
        return None
    if engine is not None and engine != "kitesurf":
        raise ValueError("request bridge is only valid for Kitesurf runs")
    origins = bridge.get("origins") if isinstance(bridge, dict) else None
    if (
        not isinstance(bridge, dict)
        or bridge.get("kind") != "local_http_fulfill/1"
        or not isinstance(origins, list)
        or not origins
        or origins != sorted(set(origins))
    ):
        raise ValueError("run request bridge identity is invalid")
    for origin in origins:
        parsed = urlsplit(origin) if isinstance(origin, str) else None
        if (
            parsed is None
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("run request bridge origin is invalid")
    return bridge


def runtime_identity(binding: dict[str, Any]) -> tuple[str, dict[str, object]]:
    """Return one credential-free, hashable local or hosted runtime identity."""

    binary = binding.get("binary_sha256")
    remote = binding.get("remote_browser")
    if isinstance(binary, str) and binary:
        if remote is not None:
            raise ValueError("run mixes local and remote browser identities")
        return f"binary:{binary}", {"binary_sha256": binary}
    if binary is not None:
        raise ValueError("run local browser identity is invalid")
    required = {
        "provider",
        "browser",
        "protocol",
        "endpoint_contract",
        "account_id_sha256",
        "runtime_metadata",
    }
    if not isinstance(remote, dict) or required - set(remote):
        raise ValueError("run browser runtime identity is missing")
    for key in required - {"runtime_metadata"}:
        if not isinstance(remote.get(key), str) or not remote[key]:
            raise ValueError(f"run remote browser identity has invalid {key}")
    account_digest = remote["account_id_sha256"]
    if len(account_digest) != 64 or any(char not in "0123456789abcdef" for char in account_digest):
        raise ValueError("run remote browser account identity is invalid")
    metadata = remote["runtime_metadata"]
    if (
        not isinstance(metadata, dict)
        or set(metadata) != set(REMOTE_RUNTIME_FIELDS)
        or any(
            not isinstance(metadata.get(key), str) or not metadata[key]
            for key in REMOTE_RUNTIME_FIELDS
        )
    ):
        raise ValueError("run remote browser version identity is incomplete")
    serialized = json.dumps(remote, sort_keys=True, separators=(",", ":"))
    if any(marker in serialized.lower() for marker in ("authorization", "bearer ", "token")):
        raise ValueError("run remote browser identity contains credentials")
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    return f"remote:{digest}", {"remote_browser": remote}


def runtime_identities(binding: dict[str, Any]) -> dict[str, dict[str, object]]:
    """Return every runtime allowed by an acceptance participant contract."""

    binaries = binding.get("binary_sha256s")
    if binaries is None:
        key, public = runtime_identity(binding)
        return {key: public}
    if (
        binding.get("binary_sha256") is not None
        or binding.get("remote_browser") is not None
        or not isinstance(binaries, list)
        or not binaries
        or len(set(binaries)) != len(binaries)
        or any(not isinstance(binary, str) or not binary for binary in binaries)
    ):
        raise ValueError("participant browser runtime identities are invalid")
    return {
        f"binary:{binary}": {"binary_sha256": binary}
        for binary in binaries
    }


def assert_observed_runtime(
    public_identity: dict[str, object], observed: object, *, task_id: int
) -> None:
    """Reject a hosted result produced by a runtime other than its frozen identity."""

    remote = public_identity.get("remote_browser")
    if remote is None:
        if observed is not None:
            raise ValueError(f"task {task_id}: local result unexpectedly records remote runtime")
        return
    assert isinstance(remote, dict)
    if observed != remote["runtime_metadata"]:
        raise ValueError(f"task {task_id}: observed remote browser version changed")
