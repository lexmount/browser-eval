"""Build privacy-safe public projections of browser-use evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from harness.environments.webarena.topology import (
    RECORDING_GATEWAYS,
    RECORDING_GATEWAYS_ENV,
    known_site_secrets,
    public_host_replacements,
    public_origin_replacements,
)
from harness.record_io import optional_file_sha256, read_json
from harness.reporting.privacy import BROWSER_USE, PRIVATE_IPV4

_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|token|storage_state|api[_-]?key)", re.I
)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


def private_recorded_hosts(text: str) -> set[str]:
    """Return private IPv4 addresses that redaction left in public text."""
    return set(PRIVATE_IPV4.findall(text))


def require_no_private_hosts(text: str, path: Path) -> None:
    """Fail closed before a recording host reaches a public report."""
    if private_recorded_hosts(text):
        raise ValueError(
            f"published report contains a private address: {path}. If it is a recording host, "
            f"list it in {RECORDING_GATEWAYS_ENV} and rebuild. Every listed value, including "
            "page text, is replaced with the host placeholder"
        )


def sanitize_text(value: str) -> str:
    text = value
    # Hostnames are case-insensitive, so any spelling of a listed gateway is redacted.
    for host in RECORDING_GATEWAYS:
        if host != host.upper():
            text = re.sub(re.escape(host), host, text, flags=re.I)
    for origin, placeholder in sorted(
        public_origin_replacements().items(), key=lambda item: len(item[0]), reverse=True
    ):
        text = text.replace(origin, placeholder)
    for secret in known_site_secrets():
        text = text.replace(secret, "[REDACTED]")
    for host, placeholder in public_host_replacements().items():
        text = text.replace(host, placeholder)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = BROWSER_USE.redact(text)
    return text


def sanitize(value: Any, *, key: str = "") -> Any:
    """Recursively redact credentials while preserving benchmark evidence."""

    if value is None or value == [] or value == {}:
        return value
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        return {str(k): sanitize(v, key=str(k)) for k, v in value.items()}
    return value


def read_public_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return sanitize(read_json(path))


def task_contract(task: Any) -> dict[str, Any]:
    raw = task.model_dump(mode="json") if hasattr(task, "model_dump") else dict(task)
    return sanitize(
        {
            "task_id": raw.get("task_id"),
            "intent_template_id": raw.get("intent_template_id"),
            "revision": raw.get("revision"),
            "sites": raw.get("sites") or [],
            "start_urls": raw.get("start_urls") or [],
            "intent": raw.get("intent"),
            "evaluation_contract": raw.get("eval") or [],
        }
    )


def evaluator_summary(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not result:
        return []
    summaries = []
    for index, item in enumerate(result.get("evaluators_results") or [], start=1):
        summaries.append(
            {
                "index": index,
                "name": item.get("evaluator_name") or "UnknownEvaluator",
                "status": item.get("status"),
                "score": item.get("score"),
                "expected": item.get("expected"),
                "actual": item.get("actual_normalized", item.get("actual")),
                "assertions": item.get("assertions"),
                "error": item.get("error_msg"),
                "should_not_exist": item.get("should_not_exist"),
            }
        )
    return summaries


def trajectory(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Project CowAgent events into a readable, complete BrowserTool action sequence."""

    if not events:
        return []
    turn = 0
    starts: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("type")
        data = event.get("data") or {}
        if event_type == "turn_start":
            turn = int(data.get("turn") or turn + 1)
        elif event_type == "tool_execution_start":
            call_id = str(data.get("tool_call_id") or "")
            starts[call_id] = {
                "step": len(rows) + 1,
                "turn": turn,
                "timestamp": event.get("timestamp"),
                "tool": data.get("tool_name"),
                "arguments": data.get("arguments"),
            }
        elif event_type == "tool_execution_end":
            call_id = str(data.get("tool_call_id") or "")
            row = starts.pop(call_id, {"step": len(rows) + 1, "turn": turn})
            row.update(
                {
                    "status": data.get("status"),
                    "duration_seconds": data.get("execution_time"),
                    "result": data.get("result"),
                    "error": data.get("error"),
                }
            )
            rows.append(sanitize(row))
    for row in starts.values():
        row.update({"status": "missing_end_event", "result": None, "error": None})
        rows.append(sanitize(row))
    return rows


def trajectory_label(step: dict[str, Any]) -> str:
    arguments = step.get("arguments") or {}
    action = arguments.get("action") or step.get("tool") or "tool"
    target = arguments.get("url") or arguments.get("ref") or arguments.get("text") or ""
    if isinstance(target, (dict, list)):
        target = json.dumps(target, ensure_ascii=False)
    label = f"{action} {target}".strip()
    return label[:180] + ("…" if len(label) > 180 else "")


def result_excerpt(step: dict[str, Any], limit: int = 900) -> str:
    text = str(step.get("error") or step.get("result") or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…（完整内容见脱敏事件日志）"


def evidence_metadata(row: dict[str, Any]) -> dict[str, Any]:
    files = {}
    for label, raw_path in (row.get("paths") or {}).items():
        path = Path(raw_path)
        files[label] = {
            "present": path.is_file(),
            "bytes": path.stat().st_size if path.is_file() else None,
            "sha256": optional_file_sha256(path),
            "publication": (
                "sanitized_projection"
                if label in {"response", "evaluation", "events"}
                else "private_only_session_sensitive"
            ),
        }
    return {"attempt_id": f"{row['engine']}-{row['task_id']}-a{row['attempt']}", "files": files}
