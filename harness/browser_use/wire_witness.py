"""Reconcile browser HAR request headers with isolated self-hosted access logs.

Only a server-observed header may fill a missing client-side field. The
reconciliation is task- and browser-agnostic and refuses ambiguous matches.
Raw access logs must never be written to the public evidence tree.
"""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_ACCESS_LINE = re.compile(
    r'^.*?"(?P<method>[A-Z]+) (?P<target>\S+) HTTP/\d(?:\.\d)?" '
    r'(?P<status>\d{3}) \S+ "(?P<referer>[^"]*)"(?: |$)'
)
MAX_WIRE_LOG_SEGMENT_BYTES = 32 * 1024 * 1024
RouteKey = tuple[str, str, int, str, str, int]


@dataclass(frozen=True)
class WireWitness:
    method: str
    url: str
    status: int
    referer: str
    line_sha256: str


def parse_access_log(segment: bytes, *, origin: str) -> list[WireWitness]:
    """Read request identity/Referer from WebArena's nginx combined-format log tail.

    Malformed UTF-8 lines and formats outside the observed combined layout are
    ignored rather than reconstructed into evaluator evidence.
    """

    parsed_origin = urlsplit(origin)
    if parsed_origin.scheme not in {"http", "https"} or not parsed_origin.netloc:
        raise ValueError("wire-witness origin must be an absolute HTTP URL")
    root = f"{parsed_origin.scheme}://{parsed_origin.netloc}"
    witnesses: list[WireWitness] = []
    for raw_line in segment.splitlines():
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            continue
        match = _ACCESS_LINE.match(line)
        if not match or not match["target"].startswith("/"):
            continue
        referer = match["referer"]
        if referer in {"", "-"} or "\\" in referer or any(c in referer for c in "\r\n"):
            continue
        parsed_referer = urlsplit(referer)
        if parsed_referer.scheme not in {"http", "https"} or not parsed_referer.netloc:
            continue
        witnesses.append(
            WireWitness(
                method=match["method"],
                url=root + match["target"],
                status=int(match["status"]),
                referer=referer,
                line_sha256=hashlib.sha256(raw_line).hexdigest(),
            )
        )
    return witnesses


def fill_missing_referers(
    har: dict[str, Any], witnesses: list[WireWitness], *, gateway_host: str | None = None
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Fill unique matches; allow the bound gateway's Docker hostname alias in v2."""

    if gateway_host is not None:
        try:
            gateway_address = ipaddress.ip_address(gateway_host)
        except ValueError as exc:
            raise ValueError("gateway host must be a private IP address") from exc
        if not gateway_address.is_private:
            raise ValueError("gateway host must be a private IP address")

    result = copy.deepcopy(har)
    entries = result.get("log", {}).get("entries")
    if not isinstance(entries, list):
        raise ValueError("HAR has no entries list")
    har_by_key: dict[tuple[str, str, int], list[int]] = defaultdict(list)
    witness_by_key: dict[tuple[str, str, int], list[WireWitness]] = defaultdict(list)
    alias_har_by_key: dict[RouteKey, list[int]] = defaultdict(list)
    alias_witness_by_key: dict[RouteKey, list[WireWitness]] = defaultdict(list)

    def route_key(method: str, url: str, status: int) -> RouteKey | None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        if port is None:
            return None
        return method, parsed.scheme, port, parsed.path, parsed.query, status

    for index, entry in enumerate(entries):
        request, response = entry.get("request", {}), entry.get("response", {})
        key = (
            str(request.get("method")),
            str(request.get("url")),
            int(response.get("status") or 0),
        )
        har_by_key[key].append(index)
        parsed = urlsplit(key[1])
        if gateway_host is not None and parsed.hostname == "host.docker.internal":
            alias_key = route_key(*key)
            if alias_key is not None:
                alias_har_by_key[alias_key].append(index)
    for witness in witnesses:
        witness_by_key[(witness.method, witness.url, witness.status)].append(witness)
        parsed = urlsplit(witness.url)
        if gateway_host is None or parsed.hostname != gateway_host:
            continue
        alias_key = route_key(witness.method, witness.url, witness.status)
        if alias_key is not None:
            alias_witness_by_key[alias_key].append(witness)

    additions: list[dict[str, str]] = []
    for key, matches in witness_by_key.items():
        indices = har_by_key.get(key, [])
        if not indices and gateway_host is not None and urlsplit(key[1]).hostname == gateway_host:
            alias_key = route_key(*key)
            if alias_key is not None and len(alias_witness_by_key.get(alias_key, [])) == 1:
                indices = alias_har_by_key.get(alias_key, [])
        if len(matches) != 1 or len(indices) != 1:
            continue
        request = entries[indices[0]]["request"]
        headers = request.get("headers")
        if not isinstance(headers, list) or any(
            str(item.get("name", "")).lower() == "referer" for item in headers
        ):
            continue
        witness = matches[0]
        headers.append({"name": "Referer", "value": witness.referer})
        additions.append(
            {
                "method": witness.method,
                "url_sha256": hashlib.sha256(str(request["url"]).encode()).hexdigest(),
                "referer_sha256": hashlib.sha256(witness.referer.encode()).hexdigest(),
                "source_line_sha256": witness.line_sha256,
            }
        )
    return result, additions


def validate_reconciled_har(
    raw: dict[str, Any], evaluated: dict[str, Any], receipt: dict[str, Any]
) -> bool:
    """Verify that the evaluator HAR differs only by declared Referer additions."""

    raw_entries = raw.get("log", {}).get("entries")
    evaluated_entries = evaluated.get("log", {}).get("entries")
    declared = receipt.get("unique_header_additions")
    if not isinstance(raw_entries, list) or not isinstance(evaluated_entries, list):
        return False
    if len(raw_entries) != len(evaluated_entries) or not isinstance(declared, list):
        return False
    reconstructed = copy.deepcopy(raw)
    found: list[tuple[str, str, str]] = []
    for index, (before, after) in enumerate(zip(raw_entries, evaluated_entries, strict=True)):
        if before == after:
            continue
        request_before = before.get("request", {})
        request_after = after.get("request", {})
        old_headers = request_before.get("headers")
        new_headers = request_after.get("headers")
        if not isinstance(old_headers, list) or not isinstance(new_headers, list):
            return False
        if len(new_headers) != len(old_headers) + 1 or new_headers[:-1] != old_headers:
            return False
        header = new_headers[-1]
        if header.get("name") != "Referer" or not isinstance(header.get("value"), str):
            return False
        reconstructed["log"]["entries"][index]["request"]["headers"].append(header)
        found.append(
            (
                str(request_before.get("method")),
                hashlib.sha256(str(request_before.get("url")).encode()).hexdigest(),
                hashlib.sha256(header["value"].encode()).hexdigest(),
            )
        )
    try:
        stated = sorted(
            (item["method"], item["url_sha256"], item["referer_sha256"])
            for item in declared
        )
    except (KeyError, TypeError):
        return False
    return reconstructed == evaluated and sorted(found) == stated


def validate_wire_source(
    raw: dict[str, Any],
    evaluated: dict[str, Any],
    receipt: dict[str, Any],
    source_archive: Path,
    *,
    gateway_host: str | None = None,
) -> bool:
    """Recompute every addition from the retained private server-log bytes."""

    sources = receipt.get("sources")
    if not isinstance(sources, list) or not sources:
        return False
    try:
        with zipfile.ZipFile(source_archive) as archive:
            members = archive.infolist()
            names = [item.filename for item in members]
            declared = [source["archive_member"] for source in sources]
            if (
                len(names) != len(set(names))
                or len(declared) != len(set(declared))
                or set(names) != set(declared)
                or any(
                    item.compress_type != zipfile.ZIP_STORED
                    or item.file_size > MAX_WIRE_LOG_SEGMENT_BYTES
                    for item in members
                )
            ):
                return False
            witnesses = []
            for source in sources:
                segment = archive.read(source["archive_member"])
                if hashlib.sha256(segment).hexdigest() != source["segment_sha256"]:
                    return False
                if len(segment) != source["segment_bytes"]:
                    return False
                parsed = parse_access_log(segment, origin=source["origin"])
                if len(parsed) != source["parsed_request_count"]:
                    return False
                witnesses.extend(parsed)
        schema = receipt.get("schema")
        if schema == "lex_browser_use_server_wire_witness/2":
            if not gateway_host or receipt.get("gateway_host") != gateway_host:
                return False
            reproduced, additions = fill_missing_referers(
                raw, witnesses, gateway_host=gateway_host
            )
        elif schema in {None, "lex_browser_use_server_wire_witness/1"}:
            reproduced, additions = fill_missing_referers(raw, witnesses)
        else:
            return False
        return (
            reproduced == evaluated
            and additions == receipt.get("unique_header_additions")
            and validate_reconciled_har(raw, evaluated, receipt)
        )
    except (KeyError, OSError, TypeError, ValueError, zipfile.BadZipFile):
        return False
