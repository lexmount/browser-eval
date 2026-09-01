"""Rule-based and optional LLM-assisted failure diagnosis.

Diagnostics are explanatory only. They never participate in pass/fail scoring.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from collections import Counter
from typing import Any


def classify_failure(error: str, trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract a deterministic failure signature from the raw trace."""
    failed = next((item for item in trace if item.get("status") == "fail"), {})
    action = str(failed.get("action") or "unknown")
    arguments = failed.get("arguments") or {}
    selector = str(arguments.get("selector") or "")
    text = f"{failed.get('error', '')}\n{error}".lower()
    if "timeout" in text and ("visible" in text or action == "click"):
        category = "交互目标超时/不可见"
    elif "timeout" in text:
        category = "超时"
    elif "exact mismatch" in text:
        category = "结果不匹配"
    elif "net::" in text or action in {"navigate", "back", "forward"}:
        category = "导航/网络"
    elif "selector" in text or "locator" in text:
        category = "定位器"
    else:
        category = "其他"
    return {
        "category": category,
        "failed_step": failed.get("index"),
        "failed_action": action,
        "selector": selector,
        "rule": "异常文本 + 失败 BrowserTool 调用字段",
        "signature": "|".join((category, action, selector)),
        "evidence": str(failed.get("error") or error).strip()[:1200],
        "actionability_probe": failed.get("actionability_probe"),
        "actionability_probe_error": failed.get("actionability_probe_error"),
    }


def _refine_observed_failure(
    diagnosis: dict[str, Any], tags: list[str]
) -> dict[str, Any]:
    """Refine broad exceptions into deterministic observed mechanisms."""
    probe = diagnosis.get("actionability_probe")
    if isinstance(probe, dict) and probe.get("found") is True:
        rect = probe.get("rect") or {}
        style_visible = (
            probe.get("display") != "none"
            and probe.get("visibility") != "hidden"
            and str(probe.get("opacity")) != "0"
        )
        if style_visible and rect.get("width") == 0 and rect.get("height") == 0:
            diagnosis["category"] = "DOM 存在但布局矩形为零"
            diagnosis["observed_mechanism"] = (
                "节点已连接、computed style 可见且未禁用，但 5 秒后仍无 client rect，"
                "因此 Playwright 无法执行 actionability 操作。"
            )
    if "native-control" in tags and diagnosis.get("category") != "DOM 存在但布局矩形为零":
        diagnosis["category"] = "原生控件默认行为未生效"
        diagnosis["observed_mechanism"] = (
            "点击调用本身成功，但后续唯一状态值未改变；失败发生在原生默认行为或状态传播。"
        )
    elif "target" in tags and diagnosis.get("category") != "DOM 存在但布局矩形为零":
        diagnosis["category"] = "锚点与 CSS 状态未生效"
        diagnosis["observed_mechanism"] = (
            "点击调用本身成功，但 hash/:target 驱动的唯一可见状态未出现。"
        )
    diagnosis["signature"] = "|".join(
        (
            str(diagnosis.get("category") or "其他"),
            str(diagnosis.get("failed_action") or "unknown"),
            str(diagnosis.get("selector") or ""),
        )
    )
    return diagnosis


def build_failure_groups(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Deduplicate primary/retry failures by task, engine, action and selector."""
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for task_id, task in (summary.get("by_task") or {}).items():
        for engine, failures in (task.get("failures") or {}).items():
            for failure in failures:
                diagnosis = _refine_observed_failure(
                    classify_failure(
                        str(failure.get("error") or ""), failure.get("trace") or []
                    ),
                    [str(tag) for tag in task.get("tags") or []],
                )
                key = (str(task_id), str(engine), diagnosis["signature"])
                bucket = grouped.setdefault(
                    key,
                    {
                        "group_id": f"{task_id}:{engine}:{len(grouped) + 1}",
                        "task_id": str(task_id),
                        "task_title": str(task.get("title") or task_id),
                        "engine": str(engine),
                        "difficulty": str(task.get("complexity") or ""),
                        **diagnosis,
                        "attempts": [],
                    },
                )
                bucket["attempts"].append(
                    {
                        "attempt": failure.get("attempt"),
                        "is_retry": bool(failure.get("is_retry")),
                    }
                )
    return list(grouped.values())


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S)
    return json.loads(cleaned)


def add_llm_diagnoses(
    groups: list[dict[str, Any]],
    *,
    api_key: str,
    api_base: str,
    model: str = "gpt-5.6-sol",
    reasoning_effort: str = "low",
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Ask an OpenAI-compatible model for evidence-bounded root-cause hypotheses."""
    if not groups:
        return {"enabled": True, "model": model, "groups": []}
    cases = [
        {
            key: group.get(key)
            for key in (
                "group_id",
                "task_title",
                "engine",
                "category",
                "failed_step",
                "failed_action",
                "selector",
                "evidence",
                "attempts",
            )
        }
        for group in groups
    ]
    prompt = (
        "你是浏览器自动化兼容性评测的故障分析员。基于给出的失败调用和异常证据，"
        "对每个去重后的失败组给出保守、可验证的根因假设。不要把相关性写成已证实因果；"
        "不要改动成功/失败判定。只返回 JSON 对象，格式为 "
        '{"diagnoses":[{"group_id":"...","summary":"中文一句话",'
        '"likely_layer":"browser-provider|tool-adapter|fixture|environment|unknown",'
        '"confidence":"low|medium|high","why":"引用具体 action/selector/异常",'
        '"next_check":"一个最小验证动作"}]}。\n\n失败组：\n'
        + json.dumps(cases, ensure_ascii=False, indent=2)
    )
    endpoint = f"{api_base.rstrip('/')}/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "reasoning_effort": reasoning_effort,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "只依据提供的评测证据分析，输出严格 JSON。",
                },
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = payload["choices"][0]["message"]["content"]
    parsed = _extract_json(str(content))
    by_id = {
        str(item.get("group_id")): item
        for item in parsed.get("diagnoses") or []
        if isinstance(item, dict)
    }
    for group in groups:
        if group["group_id"] in by_id:
            group["llm"] = by_id[group["group_id"]]
    return {
        "enabled": True,
        "role": "failure_explanation_only",
        "affects_scoring": False,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "usage": payload.get("usage") or {},
        "groups": groups,
    }


def diagnose_summary(
    summary: dict[str, Any],
    *,
    use_llm: bool = False,
    api_key: str | None = None,
    api_base: str | None = None,
    model: str = "gpt-5.6-sol",
    reasoning_effort: str = "low",
) -> dict[str, Any]:
    groups = build_failure_groups(summary)
    rule_counts = Counter(str(group["category"]) for group in groups)
    result: dict[str, Any] = {
        "rule_engine": {
            "enabled": True,
            "method": "异常文本 + 失败 BrowserTool 调用字段",
            "groups": groups,
            "category_counts": dict(rule_counts),
        },
        "llm": {
            "enabled": False,
            "role": "failure_explanation_only",
            "affects_scoring": False,
        },
    }
    if use_llm:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        base = api_base or os.environ.get("OPENAI_BASE_URL") or os.environ.get(
            "OPENAI_API_BASE"
        )
        if not key or not base:
            raise RuntimeError("OPENAI_API_KEY and OPENAI_BASE_URL are required")
        result["llm"] = add_llm_diagnoses(
            groups,
            api_key=key,
            api_base=base,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    summary["failure_diagnostics"] = result
    return summary
