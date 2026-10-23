"""Explain native failures and latency without equating parallel work with wall time."""

from collections import Counter
from statistics import mean


def error_evidence(row, payload):
    """Keep collection errors and nested service failure evidence distinguishable."""
    parts = [row["error"]] if row.get("error") else []
    status = payload.get("status_code") if isinstance(payload, dict) else None
    if type(status) is int and status >= 400:
        parts.append("status_code: " + str(status))
        headers = payload.get("headers", {})
        if isinstance(headers, dict):
            parts.extend(str(k) + ": " + str(v) for k, v in headers.items()
                         if k.lower() in {"x-brd-error", "x-brd-error-code"})
    return "\n".join(parts)


def operational(rows, payloads, methods=None):
    calls = []
    by_method = {m["id"]: m for m in (methods or [])}
    for row in rows:
        payload = payloads.get(row["id"], {})
        from .adapters import adapter_for
        adapter = by_method.get(row["method"], {}).get("adapter")
        if adapter:
            timing = adapter_for(adapter).timing(payload)
            payload = timing
        reported_attempts = payload.get("attempts") if isinstance(payload, dict) else None
        attempts = reported_attempts if isinstance(reported_attempts, list) else []
        network = row.get("network") or {}
        ttfb = row.get("ttfb_ms")
        downloaded = network.get("download_bytes")
        status = row.get("http_status")
        # curl reports zero start-transfer time when no response ever arrived.
        # A real response/partial body also proves arrival if that clock is zero.
        response_started = (
            isinstance(ttfb, (int, float)) and ttfb > 0
            or isinstance(downloaded, (int, float)) and downloaded > 0
            or type(status) is int and 100 <= status <= 599
        )
        valid = [
            a
            for a in attempts
            if isinstance(a, dict)
            and isinstance(a.get("elapsed_ms"), (int, float))
            and isinstance(a.get("started_after_ms"), (int, float))
        ]
        ready = [
            a["started_after_ms"] + a["elapsed_ms"]
            for a in valid
            if a.get("outcome") == "results" and (a.get("parsed_count") or 0) > 0
        ]
        server = (row.get("server_timing") or {}).get("elapsed_ms")
        first = min(ready) if ready else None
        calls.append(
            dict(
                id=row["id"],
                evidence_path=(row.get("evidence") or {}).get("path"),
                case_id=row["case_id"],
                method=row["method"],
                round=row["round"],
                client_ms=row["elapsed_ms"],
                server_ms=server,
                outside_server_ms=row["elapsed_ms"] - server if server is not None else None,
                first_results_ms=first,
                after_first_ms=server - first if server is not None and first is not None else None,
                attempts=len(attempts) if isinstance(reported_attempts, list) else None,
                outcomes=dict(Counter(a.get("outcome", "unknown") for a in attempts if isinstance(a, dict))),
                ttfb_ms=ttfb if response_started else None,
                body_ms=network.get("body_ms") if response_started else None,
                bytes=downloaded,
                cache=(row.get("cache") or {}).get("state", "unknown"),
                usage=row.get("usage") or {},
                stop_reason=payload.get("stop_reason") if isinstance(payload, dict) else None,
                coverage=payload.get("coverage") if isinstance(payload, dict) else None,
                timeline=[
                    dict(
                        engine=a.get("engine"),
                        route=a.get("route"),
                        start=a["started_after_ms"],
                        duration=a["elapsed_ms"],
                        outcome=a.get("outcome"),
                        count=a.get("parsed_count"),
                    )
                    for a in valid
                ],
            )
        )
    methods = {}
    for mid in dict.fromkeys(r["method"] for r in rows):
        group = [c for c in calls if c["method"] == mid]
        stats = {}
        for key in (
            "client_ms",
            "server_ms",
            "outside_server_ms",
            "first_results_ms",
            "after_first_ms",
            "attempts",
            "ttfb_ms",
            "body_ms",
            "bytes",
        ):
            values = [c[key] for c in group if c[key] is not None]
            stats[key] = {"n": len(values), "mean": mean(values) if values else None}
        stats["cache"] = dict(Counter(c["cache"] for c in group))
        stats["outcomes"] = dict(sum((Counter(c["outcomes"]) for c in group), Counter()))
        stats["usage"] = {
            k: sum(c["usage"].get(k, 0) for c in group)
            for k in sorted({k for c in group for k in c["usage"]})
        }
        methods[mid] = stats
    return {"methods": methods, "calls": calls}
