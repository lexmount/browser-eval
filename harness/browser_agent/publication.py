"""Build privacy-safe public projections of browser-agent evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


_ORIGIN_REPLACEMENTS = {
    "http://host.docker.internal:7770": "__SHOPPING__",
    "http://host.docker.internal:7780/admin": "__SHOPPING_ADMIN__",
    "http://host.docker.internal:7780": "__SHOPPING_ADMIN__",
    "http://host.docker.internal:9999": "__REDDIT__",
    "http://host.docker.internal:8023": "__GITLAB__",
    "http://192.168.5.2:7770": "__SHOPPING__",
    "http://192.168.5.2:7780/admin": "__SHOPPING_ADMIN__",
    "http://192.168.5.2:7780": "__SHOPPING_ADMIN__",
    "http://192.168.5.2:9999": "__REDDIT__",
    "http://192.168.5.2:8023": "__GITLAB__",
}
_KNOWN_SECRET_VALUES = (
    "Password.123",
    "admin1234",
    "test1234",
    "hello1234",
)
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|token|storage_state|api[_-]?key)", re.I
)
_LOCAL_PATH = re.compile(r"/(?:Users|home|private|tmp|var|opt)/[^\s\"']+")
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


def sha256_file(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def sanitize_text(value: str) -> str:
    text = value
    for origin, placeholder in sorted(
        _ORIGIN_REPLACEMENTS.items(), key=lambda item: len(item[0]), reverse=True
    ):
        text = text.replace(origin, placeholder)
    for secret in _KNOWN_SECRET_VALUES:
        text = text.replace(secret, "[REDACTED]")
    text = text.replace("host.docker.internal", "__BENCHMARK_HOST__")
    text = text.replace("192.168.5.2", "__BENCHMARK_HOST__")
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _LOCAL_PATH.sub("<LOCAL_PATH>", text)
    return text


def sanitize(value: Any, *, key: str = "") -> Any:
    """Recursively redact credentials while preserving benchmark evidence."""

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
    return sanitize(json.loads(path.read_text(encoding="utf-8")))


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
            "sha256": sha256_file(path),
            "publication": (
                "sanitized_projection"
                if label in {"response", "evaluation", "events"}
                else "private_only_session_sensitive"
            ),
        }
    return {"attempt_id": f"{row['engine']}-{row['task_id']}-a{row['attempt']}", "files": files}
