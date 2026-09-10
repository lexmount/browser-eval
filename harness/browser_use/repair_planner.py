"""Evidence-bounded Astra proposal generation for WebArena trajectory repair.

The model proposes candidate actions only. The deterministic Chromium replay and
the official WebArena evaluator remain the sole acceptance gate.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from .suite import SUPPORTED_ACTIONS
from .targets import TARGET_KINDS

REPAIR_SCHEMA = "lex_browser_use_repair_proposal/1"
PLANNER_RECORD_SCHEMA = "lex_browser_use_repair_planner_record/1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _task_by_id(suite: dict[str, Any], task_id: str) -> dict[str, Any]:
    matches = [item for item in suite.get("tasks") or [] if str(item.get("id")) == task_id]
    if len(matches) != 1:
        raise ValueError(f"suite must contain exactly one task {task_id!r}")
    return matches[0]


def _evaluator_schema(task: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose output shape, never the evaluator's expected answer."""
    return [
        {
            "evaluator": item.get("evaluator"),
            "results_schema": item.get("results_schema"),
        }
        for item in (task.get("source") or {}).get("verified_evaluators") or []
        if isinstance(item, dict)
    ]


def build_repair_packet(
    suite: dict[str, Any], attempt: dict[str, Any], attempt_dir: Path
) -> dict[str, Any]:
    task_id = str(attempt.get("task_id") or "")
    task = _task_by_id(suite, task_id)
    if attempt.get("engine") != "chromium" or attempt.get("is_retry") is not False:
        raise ValueError("repair evidence must be a primary Chromium attempt")
    final_page_path = attempt_dir / "final-page.json"
    final_page = (
        json.loads(final_page_path.read_text(encoding="utf-8"))
        if final_page_path.is_file()
        else None
    )
    trace = []
    for item in attempt.get("trace") or []:
        if not isinstance(item, dict):
            continue
        trace.append(
            {
                key: item.get(key)
                for key in (
                    "index",
                    "action",
                    "arguments",
                    "save_as",
                    "status",
                    "output",
                    "error",
                    "actionability_probe",
                )
                if item.get(key) not in (None, "")
            }
        )
    return {
        "task_id": task_id,
        "task": {
            "intent": task.get("title"),
            "environment": task.get("environment"),
            "original_actions": (task.get("trajectory") or {}).get("actions") or [],
            "evaluator_output_schema": _evaluator_schema(task),
        },
        "attempt": {
            "execution_status": attempt.get("status"),
            "process_status": attempt.get("process_status"),
            "result_status": attempt.get("result_status"),
            "error": str(attempt.get("error") or "")[-8_000:],
            "trace": trace,
            "final_page": final_page,
        },
        "action_contract": {
            "supported_actions": sorted(SUPPORTED_ACTIONS),
            "target_kinds": sorted(TARGET_KINDS),
            "notes": [
                "Every observation action requires save_as.",
                "Use target objects when practical; they are strict unless allow_many is true.",
                "Do not use ref targets or benchmark expected answers.",
            ],
        },
    }


def _format_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "browser_trajectory_repair",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["mode", "actions_json", "response_builder_json", "reason"],
            "properties": {
                "mode": {"type": "string", "enum": ["replace_actions", "append_actions"]},
                "actions_json": {
                    "type": "string",
                    "description": "JSON array containing executable action objects",
                },
                "response_builder_json": {
                    "type": "string",
                    "description": (
                        "JSON object that builds evaluator data only from save_as outputs"
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Evidence-based explanation of the minimal correction",
                },
            },
        },
    }


def build_responses_request(
    packet: dict[str, Any], *, model: str = "gpt-6-astra", screenshot_path: Path | None = None
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": (
                "修复一条 WebArena 官方人工轨迹。只根据任务意图、失败轨迹和可见页面证据"
                "提出最小候选轨迹；不得推测或使用 benchmark 预期答案，不得改变任务、环境或"
                "evaluator。动作必须符合给定 action contract。response builder 只能引用候选"
                "动作通过 save_as 实际观察到的输出；不能嵌入答案字面量。若原轨迹整体不可靠，"
                "使用 replace_actions；仅缺最终读取动作时才用 append_actions。候选随后会由"
                "全新 Chromium 单次重放和官方 evaluator 验证，模型输出本身不算通过。\n\n"
                + json.dumps(packet, ensure_ascii=False, indent=2)
            ),
        }
    ]
    if screenshot_path is not None:
        if not screenshot_path.is_file():
            raise ValueError(f"screenshot evidence is missing: {screenshot_path}")
        encoded = base64.b64encode(screenshot_path.read_bytes()).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{encoded}",
                "detail": "high",
            }
        )
    return {
        "model": model,
        "reasoning": {"effort": "low"},
        "store": False,
        "instructions": (
            "你是浏览器自动化轨迹修复器，不是成功判分器。输出只描述一个可验证候选。"
        ),
        "input": [{"role": "user", "content": content}],
        "text": {"format": _format_schema()},
    }


def _response_output_text(response: dict[str, Any]) -> str:
    parts = [
        content.get("text")
        for item in response.get("output") or []
        if isinstance(item, dict) and item.get("type") == "message"
        for content in item.get("content") or []
        if isinstance(content, dict) and content.get("type") == "output_text"
    ]
    if len(parts) != 1 or not isinstance(parts[0], str):
        raise ValueError("Responses API did not return exactly one output_text item")
    return parts[0]


def candidate_to_proposal(task_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    mode = candidate.get("mode")
    if mode not in {"replace_actions", "append_actions"}:
        raise ValueError("candidate repair mode is invalid")
    actions = json.loads(str(candidate.get("actions_json") or ""))
    response_builder = json.loads(str(candidate.get("response_builder_json") or ""))
    if not isinstance(actions, list) or not actions:
        raise ValueError("candidate actions_json must contain a non-empty JSON array")
    if not isinstance(response_builder, dict):
        raise ValueError("candidate response_builder_json must contain a JSON object")
    reason = str(candidate.get("reason") or "").strip()
    if not reason:
        raise ValueError("candidate repair reason is required")
    return {
        "schema": REPAIR_SCHEMA,
        "task_id": task_id,
        mode: actions,
        "response_builder": response_builder,
        "reason": reason,
    }


def request_repair_candidate(
    request_body: dict[str, Any], *, api_key: str, api_base: str, timeout_seconds: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    endpoint = f"{api_base.rstrip('/')}/responses"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(request_body).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") != "completed":
        raise RuntimeError(f"Responses API did not complete: {payload.get('status')!r}")
    candidate = json.loads(_response_output_text(payload))
    return candidate, payload


def api_configuration() -> tuple[str, str]:
    key = str(os.environ.get("OPENAI_API_KEY") or "").strip()
    base = str(
        os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE")
        or "https://api.openai.com/v1"
    ).strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is required for Astra repair generation")
    return key, base


def planner_record(
    *, packet: dict[str, Any], request_body: dict[str, Any], response: dict[str, Any]
) -> dict[str, Any]:
    request_without_image = json.loads(json.dumps(request_body))
    for item in request_without_image.get("input") or []:
        for content in item.get("content") or []:
            if content.get("type") == "input_image":
                content["image_url"] = "<retained-separately-by-sha256>"
    return {
        "schema": PLANNER_RECORD_SCHEMA,
        "role": "candidate_generation_only",
        "affects_scoring": False,
        "packet": packet,
        "request": request_without_image,
        "response": {
            "id": response.get("id"),
            "status": response.get("status"),
            "model": response.get("model"),
            "usage": response.get("usage") or {},
            "output": response.get("output") or [],
        },
    }
