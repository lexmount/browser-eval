"""Rule-based and optional LLM-assisted failure diagnosis.

Diagnostics are explanatory only. They never participate in pass/fail scoring.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.resources
import json
import math
import os
import re
import subprocess
import tempfile
import time
import urllib.request
import uuid
import zipfile
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from lexbench_webfetch.paths import contained_path, safe_identifier

from harness.environments.webarena.config import SITE_TOKENS
from harness.record_io import parse_jsonl
from harness.trajectory_replay.diagnostics import (
    _check_diagnostic_snapshot,
    _diagnostic_snapshot,
    diagnostic_output_path,
    write_diagnostic,
)
from harness.web_fetch.safety import unlinked_path

from .qualification import build_probe, task_contract_sha256
from .suite import SUPPORTED_ACTIONS, load_suite


def classify_failure(error: str, trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract a deterministic failure signature from the raw trace."""
    failed = next((item for item in trace if item.get("status") == "fail"), {})
    action = str(failed.get("action") or "unknown")
    arguments = failed.get("arguments") or {}
    selector = str(arguments.get("selector") or "")
    text = f"{failed.get('error', '')}\n{error}".lower()
    if action in {"navigate", "back", "forward"} or "net::" in text:
        category = "导航/网络"
    elif "timeout" in text and ("visible" in text or action == "click"):
        category = "交互目标超时/不可见"
    elif "timeout" in text:
        category = "超时"
    elif "exact mismatch" in text:
        category = "结果不匹配"
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


def _refine_observed_failure(diagnosis: dict[str, Any], tags: list[str]) -> dict[str, Any]:
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
    state_mismatch = diagnosis.get("category") == "结果不匹配" and diagnosis.get(
        "failed_action"
    ) not in {"navigate", "back", "forward"}
    if "native-control" in tags and state_mismatch:
        diagnosis["category"] = "原生控件默认行为未生效"
        diagnosis["observed_mechanism"] = (
            "点击调用返回成功，但后续读取到的唯一状态值未改变；现有证据不能区分"
            "事件未生效、状态传播延迟、读取陈旧或测试页面因素。"
        )
    elif "target" in tags and state_mismatch:
        diagnosis["category"] = "锚点与 CSS 状态未生效"
        diagnosis["observed_mechanism"] = (
            "点击调用返回成功，但后续读取时 hash/:target 驱动的唯一可见状态未出现；"
            "现有证据不能区分事件、状态传播、读取或测试页面因素。"
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
                    classify_failure(str(failure.get("error") or ""), failure.get("trace") or []),
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
        base = api_base or os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")
        try:
            if not key or not base:
                raise RuntimeError("OPENAI_API_KEY and OPENAI_BASE_URL are required")
            result["llm"] = add_llm_diagnoses(
                groups,
                api_key=key,
                api_base=base,
                model=model,
                reasoning_effort=reasoning_effort,
            )
        except Exception as exc:
            # Optional explanation must not discard deterministic rules or run results.
            result["llm"].update(
                enabled=True,
                status="error",
                model=model,
                error_type=type(exc).__name__,
            )
    summary["failure_diagnostics"] = result
    return summary


PACKET_SCHEMA = "lex_webarena_codex_repair_packet/2"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MODEL = "gpt-6-astra"
REASONING_EFFORT = "low"
RUN_SCHEMA = "lex_webarena_codex_repair_run/1"
MAX_CONCURRENCY = 8


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _file_binding(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError(f"repair evidence file is symlinked: {path}")
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"repair evidence file is missing: {resolved}")
    raw = resolved.read_bytes()
    return {
        "path": str(resolved),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def _contained_evidence_path(directory: Path, name: Any, label: str) -> Path:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"final-page {label} path is missing")
    return contained_path(directory, name, reject_symlinks=True)


def _redacted_evaluator_contract(task: dict[str, Any]) -> tuple[list[Any], list[str]]:
    contracts = deepcopy((task.get("source") or {}).get("verified_evaluators") or [])
    redactions: list[str] = []
    for index, contract in enumerate(contracts):
        if not isinstance(contract, dict):
            continue
        if contract.get("evaluator") != "AgentResponseEvaluator":
            continue
        expected = contract.get("expected")
        if isinstance(expected, dict) and "retrieved_data" in expected:
            expected.pop("retrieved_data")
            redactions.append(f"task.evaluator_contracts[{index}].expected.retrieved_data")
    return contracts, redactions


def build_packet(
    suite_path: str | Path,
    attempt_path: str | Path,
) -> dict[str, Any]:
    """Return one immutable-input repair packet after verifying every binding."""
    suite_file = Path(suite_path)
    suite_binding = _file_binding(suite_file)
    suite_raw = suite_file.read_bytes()
    if hashlib.sha256(suite_raw).hexdigest() != suite_binding["sha256"]:
        raise ValueError("repair suite changed during packet construction")
    return _packet_from_inputs(
        json.loads(suite_raw),
        suite_binding["sha256"],
        Path(attempt_path),
    )


def _packet_from_inputs(suite: dict[str, Any], suite_sha256: str, attempt_file: Path) -> dict:
    if attempt_file.parent.is_symlink():
        raise ValueError("repair evidence directory is symlinked")
    attempt_binding = _file_binding(attempt_file)
    attempt_file = attempt_file.resolve()
    attempt_raw = attempt_file.read_bytes()
    if hashlib.sha256(attempt_raw).hexdigest() != attempt_binding["sha256"]:
        raise ValueError("repair attempt changed during packet construction")
    attempt = json.loads(attempt_raw)
    task_id = str(attempt.get("task_id") or "").strip()
    safe_identifier(task_id)
    matches = [task for task in suite.get("tasks") or [] if str(task.get("id")) == task_id]
    if not task_id or len(matches) != 1:
        raise ValueError("attempt must identify exactly one suite task")
    task = matches[0]
    evidence_directory = Path(str(attempt.get("evidence_directory") or ""))
    if evidence_directory.is_symlink():
        raise ValueError("repair evidence directory is symlinked")
    evidence_directory = evidence_directory.resolve()
    if not evidence_directory.is_dir() or attempt_file.parent != evidence_directory:
        raise ValueError("attempt path must be inside its declared evidence directory")

    final_page = attempt.get("final_page_evidence") or {}
    if final_page.get("status") != "complete":
        raise ValueError("repair packet requires complete final-page evidence")
    final_bindings: dict[str, Any] = {}
    for key in ("screenshot", "page"):
        metadata = final_page.get(key) or {}
        path = _contained_evidence_path(evidence_directory, metadata.get("path"), key)
        binding = _file_binding(path)
        declared = str(metadata.get("sha256") or "")
        if not _SHA256_RE.fullmatch(declared) or binding["sha256"] != declared:
            raise ValueError(f"declared final-page {key} sha256 does not match evidence")
        final_bindings[key] = binding
    har_binding = _file_binding(evidence_directory / "network.har")

    evaluator_contracts, redactions = _redacted_evaluator_contract(task)
    safe_task = {
        "id": task.get("id"),
        "title": task.get("title"),
        "difficulty": task.get("difficulty"),
        "tags": deepcopy(task.get("tags") or []),
        "trajectory": deepcopy(task.get("trajectory")),
        "evaluator": deepcopy(task.get("evaluator")),
        "environment": deepcopy(task.get("environment")),
        "evaluator_contracts": evaluator_contracts,
    }
    # Evaluator diagnostics may echo expected answers.  Keep the process trace
    # (which is browser-observed evidence), but never copy the evaluate_result
    # row, validation payload, or evaluator error into the authoring packet.
    process_trace = [
        deepcopy(row)
        for row in (attempt.get("trace") or [])
        if isinstance(row, dict) and row.get("action") != "evaluate_result"
    ]
    attempt_summary = {
        key: deepcopy(attempt.get(key))
        for key in (
            "engine",
            "task_id",
            "attempt",
            "is_retry",
            "qualification_run",
            "status",
            "process_status",
            "result_status",
            "combined_status",
            "evidence_status",
            "process_validation",
        )
        if key in attempt
    }
    attempt_summary["process_trace"] = process_trace
    return {
        "schema": PACKET_SCHEMA,
        "purpose": "candidate_trajectory_repair_only",
        "qualification_effect": "none_until_independent_chromium_replay_passes",
        "bindings": {
            "suite_sha256": suite_sha256,
            "task_id": task_id,
            "task_sha256": _json_sha256(task),
            "task_contract_sha256": task_contract_sha256(task),
            "attempt_sha256": hashlib.sha256(attempt_raw).hexdigest(),
            "final_page": final_bindings,
            "network_har": har_binding,
        },
        "task": safe_task,
        "attempt": attempt_summary,
        "provenance_references": {"attempt": str(attempt_file)},
        "evidence_references": {
            "screenshot": final_bindings["screenshot"]["path"],
            "final_page": final_bindings["page"]["path"],
            "network_har": har_binding["path"],
        },
        "redactions": {
            "paths": redactions,
            "reason": "withhold AgentResponseEvaluator reference answers from repair author",
        },
        "proposal_contract": {
            "schema": "lex_browser_use_repair_proposal/1",
            "validator": "harness.trajectory_replay.public_webarena.qualification.build_probe",
            "qualification_status": "proposal_only",
        },
    }


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"refusing to overwrite a symlinked repair packet: {path}")
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"refusing to overwrite a different repair packet: {path}")
        return
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def main_repair_packet(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Build a redacted repair authoring packet")
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    _write_immutable(args.output, build_packet(args.suite, args.attempt))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def _load_and_validate_packet(path: Path, suite: dict[str, Any]) -> dict[str, Any]:
    _file_binding(path)
    packet = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(packet, dict):
        raise ValueError("repair packet must be an object")
    if packet.get("schema") != PACKET_SCHEMA:
        raise ValueError(f"invalid repair packet schema: {path}")
    bindings = packet.get("bindings") or {}
    task = packet.get("task") or {}
    task_id = str(bindings.get("task_id") or "")
    safe_identifier(task_id)
    if not task_id or str(task.get("id")) != task_id:
        raise ValueError(f"repair packet task binding is invalid: {path}")
    matches = [row for row in suite.get("tasks") or [] if str(row.get("id")) == task_id]
    if len(matches) != 1:
        raise ValueError(f"suite does not uniquely contain repair packet task: {task_id}")
    source_task = matches[0]
    if bindings.get("task_sha256") != _json_sha256(source_task):
        raise ValueError(f"repair packet task sha256 mismatch: {task_id}")
    if bindings.get("task_contract_sha256") != task_contract_sha256(source_task):
        raise ValueError(f"repair packet task contract sha256 mismatch: {task_id}")
    evaluator_contracts, redactions = _redacted_evaluator_contract(source_task)
    expected_safe_task = {
        "id": source_task.get("id"),
        "title": source_task.get("title"),
        "difficulty": source_task.get("difficulty"),
        "tags": source_task.get("tags") or [],
        "trajectory": source_task.get("trajectory"),
        "evaluator": source_task.get("evaluator"),
        "environment": source_task.get("environment"),
        "evaluator_contracts": evaluator_contracts,
    }
    if task != expected_safe_task:
        raise ValueError(f"repair packet sanitized task payload mismatch: {task_id}")
    if ((packet.get("redactions") or {}).get("paths") or []) != redactions:
        raise ValueError(f"repair packet redaction inventory mismatch: {task_id}")

    references = packet.get("evidence_references") or {}
    expected_files = {
        "screenshot": ((bindings.get("final_page") or {}).get("screenshot") or {}).get("sha256"),
        "final_page": ((bindings.get("final_page") or {}).get("page") or {}).get("sha256"),
        "network_har": (bindings.get("network_har") or {}).get("sha256"),
    }
    for key, expected in expected_files.items():
        evidence_path = Path(str(references.get(key) or ""))
        if (
            evidence_path.is_symlink()
            or not evidence_path.is_file()
            or _sha256(evidence_path) != expected
        ):
            raise ValueError(f"repair packet {key} evidence binding mismatch: {task_id}")
    bound_paths = {
        "screenshot": ((bindings.get("final_page") or {}).get("screenshot") or {}).get("path"),
        "final_page": ((bindings.get("final_page") or {}).get("page") or {}).get("path"),
        "network_har": (bindings.get("network_har") or {}).get("path"),
    }
    for key, bound_path in bound_paths.items():
        if Path(str(references[key])).resolve() != Path(str(bound_path)).resolve():
            raise ValueError(f"repair packet {key} path binding mismatch: {task_id}")
    source_attempt = Path(str((packet.get("provenance_references") or {}).get("attempt") or ""))
    if _file_binding(source_attempt)["sha256"] != bindings.get("attempt_sha256"):
        raise ValueError(f"repair packet attempt evidence binding mismatch: {task_id}")
    expected_packet = _packet_from_inputs(suite, bindings.get("suite_sha256"), source_attempt)
    if packet != expected_packet:
        raise ValueError(f"repair packet sanitized payload mismatch: {task_id}")
    return packet


def _prompt(packet_path: Path) -> str:
    return f"""Produce one candidate deterministic WebArena trajectory repair.

Read only the repair packet at {packet_path} and the evidence files explicitly listed in its
evidence_references (screenshot, final_page, and network_har). Do not open raw attempt files,
suite files, or evaluator result files. The reference answer has intentionally been removed;
do not search the benchmark suite or other files for it. Infer actions and observed response fields
only from the task instruction, recorded trajectory, final visible page, screenshot, and HAR.

Return exactly one JSON object with this top-level contract and no prose or Markdown:
{{
  "schema": "lex_browser_use_repair_proposal/1",
  "task_id": "the unchanged packet task id",
  "reason": "a concrete evidence-based repair reason",
  "replace_actions": ["one or more complete lex_browser_trajectory/1 action objects"],
  "response_builder": {{"kind": "...", "task_type": "...", "...": "..."}}
}}
Use exactly one non-empty action field: either replace_actions for a complete corrected path or
append_actions for observation actions appended to an otherwise valid path. Do not emit trajectory,
actions, rationale, observed_response, or limitations as alternative field names.

For a retrieve task, the corrected actions must save the required browser-observed value and the
response_builder must derive the evaluator response from that saved output. Supported builders are
observed_shape (shape contains $output references and optional regex/group/case/coerce transforms),
observed_json, observed_json_concat, and observed_json_zip. For a navigate or mutate task, use
{{"kind":"null_after_verified_path","task_type":"navigate"}} or the same with task_type
"mutate". Never put a reference answer or an invented value directly in response_builder.

Keep task_id unchanged. Every action name must be one of: navigate, click, double_click, fill,
select, check, uncheck, hover, press, get_text, get_all_text, get_table_column, get_attribute,
get_all_attribute, get_property, snapshot, back, forward, or scroll. There is no evaluate, extract,
wait, JavaScript, API, shell, or filesystem action. navigate requires an HTTP(S) url and save_as.
snapshot/get_text/get_all_text/get_table_column/get_attribute/get_all_attribute/get_property also
require save_as; attribute/property actions require name; get_table_column requires header. Follow
the field shapes already used by the packet's recorded trajectory. Prefer the smallest reproducible
action change. This is only a proposal: do not claim that it passed qualification, and do not edit
files.
"""


def _validate_proposal(
    suite: dict[str, Any], packet: dict[str, Any], proposal_path: Path
) -> dict[str, Any]:
    try:
        if proposal_path.is_symlink():
            raise ValueError("repair proposal must not be symlinked")
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Codex did not return a JSON repair proposal") from exc
    if not isinstance(proposal, dict):
        raise ValueError("repair proposal must be an object")
    task_id = str((packet.get("bindings") or {}).get("task_id") or "")
    if str(proposal.get("task_id") or "") != task_id:
        raise ValueError("Codex repair proposal changed the bound task ID")
    probe = build_probe(suite, proposal)
    author_actions = proposal.get("replace_actions") or proposal.get("append_actions") or []
    allowed_actions = SUPPORTED_ACTIONS - {"evaluate", "wait", "wait_for_url"}
    if any(action.get("action") not in allowed_actions for action in author_actions):
        raise ValueError("Codex repair proposal contains an unsupported authoring action")
    with tempfile.TemporaryDirectory(prefix="webarena-repair-validation-") as directory:
        probe_path = Path(directory) / "probe.json"
        probe_path.write_text(json.dumps(probe, ensure_ascii=False), encoding="utf-8")
        load_suite(probe_path)
    return proposal


def _command(
    codex_binary: str,
    task_directory: Path,
    screenshot: Path,
    output_path: Path,
    *,
    model: str = MODEL,
    reasoning_effort: str = REASONING_EFFORT,
) -> list[str]:
    return [
        codex_binary,
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--json",
        "-s",
        "read-only",
        "-m",
        model,
        "-c",
        f"model_reasoning_effort='{reasoning_effort}'",
        "-C",
        str(task_directory),
        "-i",
        str(screenshot),
        "-o",
        str(output_path),
        "-",
    ]


def _changed_repair_inputs(bindings: dict[Path, str]) -> list[str]:
    return [
        str(path)
        for path, sha in bindings.items()
        if path.is_symlink() or not path.is_file() or _sha256(path) != sha
    ]


def _run_repair_job(
    suite: dict[str, Any],
    packet_path: Path,
    output_root: Path,
    *,
    codex_binary: str = "codex",
    timeout_seconds: int = 900,
    model: str = MODEL,
    reasoning_effort: str = REASONING_EFFORT,
    input_hashes: dict[Path, str],
    run_subprocess: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if _changed_repair_inputs(input_hashes):
        raise ValueError("repair inputs changed before worker setup")
    packet = _load_and_validate_packet(packet_path, suite)
    task_id = str(packet["bindings"]["task_id"])
    safe_identifier(task_id)
    if output_root.is_symlink():
        raise ValueError("repair output root must not be symlinked")
    task_directory = output_root.resolve() / task_id
    task_directory.mkdir(parents=True, exist_ok=False)
    packet_copy = task_directory / "packet.json"
    authoring_packet = deepcopy(packet)
    authoring_packet.pop("provenance_references")
    _write_new(packet_copy, json.dumps(authoring_packet, indent=2, ensure_ascii=False) + "\n")
    raw_output = task_directory / "proposal.raw.json"
    screenshot = Path(packet["evidence_references"]["screenshot"])
    command = _command(
        codex_binary,
        task_directory,
        screenshot,
        raw_output,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    command_record = {
        "binary": codex_binary,
        "argv": command,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "sandbox": "read-only",
        "ephemeral": True,
        "json_events": True,
        "timeout_seconds": timeout_seconds,
        "packet_sha256": _sha256(packet_copy),
        "qualification_effect": "none",
    }
    _write_new(
        task_directory / "command.json",
        json.dumps(command_record, indent=2, ensure_ascii=False) + "\n",
    )
    if _changed_repair_inputs(input_hashes):
        raise ValueError("repair inputs changed before model launch")
    launch_error: str | None = None
    try:
        completed = run_subprocess(
            command,
            input=_prompt(packet_copy),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        returncode = None
    except OSError as exc:
        stdout = ""
        stderr = f"{type(exc).__name__}: {exc}\n"
        returncode = None
        launch_error = str(exc)
    _write_new(task_directory / "events.jsonl", stdout)
    _write_new(task_directory / "stderr.log", stderr)

    status = "codex_failed"
    error: str | None = None
    proposal: dict[str, Any] | None = None
    drift = _changed_repair_inputs(input_hashes)
    if drift:
        status = "input_changed"
        error = "Repair inputs changed during execution"
    elif launch_error is not None:
        status = "codex_failed"
        error = f"Codex could not be started: {launch_error}"
    elif returncode is None:
        status = "timeout"
        error = f"Codex exceeded {timeout_seconds} seconds"
    elif returncode != 0:
        error = f"Codex exited with status {returncode}"
    else:
        try:
            proposal = _validate_proposal(suite, packet, raw_output)
            status = "proposal_valid"
            _write_new(
                task_directory / "proposal.json",
                json.dumps(proposal, indent=2, ensure_ascii=False) + "\n",
            )
        except (ValueError, TypeError, KeyError) as exc:
            status = "proposal_invalid"
            error = str(exc)
    result = {
        "schema": RUN_SCHEMA,
        "changed_inputs": drift,
        "task_id": task_id,
        "status": status,
        "error": error,
        "returncode": returncode,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "packet_sha256": _sha256(packet_copy),
        "proposal_sha256": _json_sha256(proposal) if proposal is not None else None,
        "qualification_effect": "none",
    }
    _write_new(
        task_directory / "result.json",
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
    )
    return result


def run_repairs(
    suite_path: Path,
    packet_paths: list[Path],
    output_root: Path,
    *,
    max_workers: int,
    codex_binary: str = "codex",
    timeout_seconds: int = 900,
    model: str = MODEL,
    reasoning_effort: str = REASONING_EFFORT,
    run_subprocess: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if (
        not isinstance(model, str)
        or not model.strip()
        or any(ord(char) <= 32 or ord(char) == 127 for char in model)
    ):
        raise ValueError("repair model must be an explicit non-empty identifier")
    if reasoning_effort not in {
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    }:
        raise ValueError("unsupported repair reasoning effort")
    if type(max_workers) is not int or not 1 <= max_workers <= MAX_CONCURRENCY:
        raise ValueError(f"max_workers must be between 1 and {MAX_CONCURRENCY}")
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")
    if not packet_paths:
        raise ValueError("at least one repair packet is required")
    suite_binding = _file_binding(suite_path)
    if output_root.is_symlink():
        raise ValueError("repair output root must not be symlinked")
    suite_file = suite_path.resolve()
    suite_raw = suite_file.read_bytes()
    suite_sha256 = suite_binding["sha256"]
    if hashlib.sha256(suite_raw).hexdigest() != suite_sha256:
        raise ValueError("repair suite changed during preflight")
    suite = json.loads(suite_raw)
    packet_hashes = {path: _file_binding(path)["sha256"] for path in packet_paths}
    packets = [(path, _load_and_validate_packet(path, suite)) for path in packet_paths]
    task_ids = [str(packet["bindings"]["task_id"]) for _, packet in packets]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("repair packets must bind unique task IDs")
    if any(packet["bindings"].get("suite_sha256") != suite_sha256 for _, packet in packets):
        raise ValueError("repair packet suite sha256 mismatch")
    frozen_inputs = {suite_file: suite_sha256, **packet_hashes}
    for _, packet in packets:
        bindings = packet["bindings"]
        expected = {
            "screenshot": bindings["final_page"]["screenshot"]["sha256"],
            "final_page": bindings["final_page"]["page"]["sha256"],
            "network_har": bindings["network_har"]["sha256"],
        }
        for key, value in packet["evidence_references"].items():
            frozen_inputs[Path(value)] = expected[key]
        frozen_inputs[Path(packet["provenance_references"]["attempt"])] = bindings["attempt_sha256"]
    if _changed_repair_inputs(frozen_inputs):
        raise ValueError("repair inputs changed during preflight")
    output_root.mkdir(parents=True, exist_ok=False)

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(packets))) as executor:
        futures = {
            executor.submit(
                _run_repair_job,
                suite,
                path,
                output_root,
                codex_binary=codex_binary,
                timeout_seconds=timeout_seconds,
                model=model,
                reasoning_effort=reasoning_effort,
                input_hashes=frozen_inputs,
                run_subprocess=run_subprocess,
            ): str(packet["bindings"]["task_id"])
            for path, packet in packets
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                failure = {
                    "schema": RUN_SCHEMA,
                    "task_id": futures[future],
                    "status": "worker_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "qualification_effect": "none",
                }
                results.append(failure)
                directory = output_root / futures[future]
                directory.mkdir(exist_ok=True)
                _write_new(
                    directory / "result.json",
                    json.dumps(failure, ensure_ascii=False) + "\n",
                )
    results.sort(key=lambda row: task_ids.index(str(row["task_id"])))
    drift = _changed_repair_inputs(frozen_inputs)
    summary = {
        "schema": "lex_webarena_codex_repair_batch/1",
        "changed_inputs": drift,
        "suite_sha256": suite_sha256,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "max_workers": max_workers,
        "qualification_effect": "none",
        "tasks": results,
        "status": (
            "complete"
            if not drift and all(row["status"] == "proposal_valid" for row in results)
            else "incomplete"
        ),
    }
    _write_new(
        output_root / "summary.json",
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
    )
    return summary


def main_codex_repairs(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Run bounded read-only repair proposal jobs")
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--packet", type=Path, action="append", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--codex-binary", default="codex")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--reasoning-effort", default=REASONING_EFFORT)
    args = parser.parse_args(argv)
    summary = run_repairs(
        args.suite,
        args.packet,
        args.output_root,
        max_workers=args.max_workers,
        codex_binary=args.codex_binary,
        timeout_seconds=args.timeout_seconds,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
    )
    print(json.dumps(summary, ensure_ascii=False))
    if summary["status"] != "complete":
        raise SystemExit(1)


SITE_PORTS = {
    "map": (3000,),
    "shopping": (7770,),
    "shopping_admin": (7780,),
    "gitlab": (8023,),
    "wikipedia": (8080, 8888),
    "reddit": (9999,),
}


def _recording_sha256(path: Path) -> str:
    return hashlib.sha256(unlinked_path(path).read_bytes()).hexdigest()


def _resource_sha256(resource: Any) -> str:
    return hashlib.sha256(resource.read_bytes()).hexdigest()


def _recorded_network_requests(path: Path) -> list[dict[str, Any]]:
    return [
        row["snapshot"]["request"]
        for row in _recorded_trace_records(path, "trace.network")
        if row.get("type") == "resource-snapshot"
    ]


def site_origins(requests: list[dict[str, Any]], sites: list[str]) -> dict[str, str]:
    known_ports = {port for ports in SITE_PORTS.values() for port in ports}
    by_port: dict[int, Counter[str]] = {port: Counter() for port in known_ports}
    for request in requests:
        split = urlsplit(str(request.get("url") or ""))
        try:
            port = split.port
        except ValueError:
            continue
        if port in by_port and split.scheme and split.netloc:
            by_port[port][f"{split.scheme}://{split.netloc}"] += 1
    result: dict[str, str] = {}
    for site in sites:
        candidates: Counter[str] = Counter()
        for port in SITE_PORTS[site]:
            candidates.update(by_port[port])
        if not candidates:
            raise ValueError(f"official trace contains no origin for {site}")
        result[site] = candidates.most_common(1)[0][0]
    return result


def _official_dataset() -> list[dict[str, Any]]:
    resource = importlib.resources.files("webarena_verified").joinpath(
        "assets/dataset/webarena-verified.json"
    )
    return json.loads(resource.read_text(encoding="utf-8"))


def audit_recorded_outcomes(
    catalog_path: Path, trace_dir: Path, suite_path: Path
) -> dict[str, Any]:
    from webarena_verified import WebArenaVerified

    catalog_path, trace_dir, suite_path = map(unlinked_path, (catalog_path, trace_dir, suite_path))
    before_inputs = _diagnostic_snapshot([catalog_path, suite_path])
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows = catalog.get("traces") or []
    if (
        type(catalog.get("count")) is not int
        or not rows
        or catalog["count"] != len(rows)
        or len({int(row["task_id"]) for row in rows}) != len(rows)
    ):
        raise ValueError("catalog must contain its complete unique declared trace population")
    dataset = {int(task["task_id"]): task for task in _official_dataset()}
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    suite_tasks = {
        int(str(task["id"]).removeprefix("webarena-")): task for task in suite.get("tasks") or []
    }
    if len(suite_tasks) != len(suite.get("tasks") or []) or set(suite_tasks) != {
        int(row["task_id"]) for row in rows
    }:
        raise ValueError("suite and official trace catalog task IDs differ")
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="webarena-recorded-audit-") as temporary:
        config_path = Path(temporary) / "config.json"
        for row in rows:
            task_id = int(row["task_id"])
            task = dataset[task_id]
            path = contained_path(trace_dir, str(row["file_name"]), reject_symlinks=True)
            if not path.is_file():
                raise FileNotFoundError(f"official trace is missing: {path.name}")
            digest = _recording_sha256(path)
            before_inputs[path] = digest
            expected_digest = suite_tasks[task_id]["trajectory"]["provenance"]["source_sha256"]
            if digest != expected_digest:
                raise ValueError(f"official trace sha256 mismatch: {path.name}")
            origins = site_origins(
                _recorded_network_requests(path), [str(site) for site in task["sites"]]
            )
            config_path.write_text(
                json.dumps(
                    {
                        "environments": {
                            f"__{site.upper()}__": {"urls": [origin]}
                            for site, origin in origins.items()
                        }
                    }
                ),
                encoding="utf-8",
            )
            expected_response = next(
                evaluator["expected"]
                for evaluator in task["eval"]
                if evaluator["evaluator"] == "AgentResponseEvaluator"
            )
            implementation = WebArenaVerified(config=config_path)
            result = implementation.evaluate_task(
                task_id=task_id,
                agent_response=expected_response,
                network_trace=path,
            )
            dumped = result.model_dump(mode="json")
            evaluator_statuses = [
                {
                    "evaluator": str(item.get("evaluator_name") or ""),
                    "status": str(item.get("status") or ""),
                }
                for item in dumped.get("evaluators_results") or []
            ]
            results.append(
                {
                    "task_id": task_id,
                    "source_trace_sha256": digest,
                    "status": str(dumped.get("status") or "error"),
                    "score": float(dumped.get("score") or 0.0),
                    "evaluator_statuses": evaluator_statuses,
                }
            )
    status_counts = Counter(row["status"] for row in results)
    dataset_resource = importlib.resources.files("webarena_verified").joinpath(
        "assets/dataset/webarena-verified.json"
    )
    evaluator_resource = importlib.resources.files("webarena_verified").joinpath(
        "core/evaluation/evaluators/network_event_evaluator.py"
    )
    _check_diagnostic_snapshot(before_inputs)
    return {
        "schema": "lex_webarena_recorded_outcome_audit/1",
        "scope": "official_recording_against_official_programmatic_evaluators",
        "agent_response_policy": "inject_official_expected_response_to_isolate_network_trace",
        "current_environment_replay": False,
        "evaluator": {
            "package": "webarena-verified",
            "version": importlib.metadata.version("webarena-verified"),
            "dataset_sha256": _resource_sha256(dataset_resource),
            "network_evaluator_module_sha256": _resource_sha256(evaluator_resource),
        },
        "catalog_sha256": _recording_sha256(catalog_path),
        "suite_sha256": _recording_sha256(suite_path),
        "task_count": len(results),
        "status_counts": dict(sorted(status_counts.items())),
        "passing_task_ids": [row["task_id"] for row in results if row["status"] == "success"],
        "failing_task_ids": [row["task_id"] for row in results if row["status"] != "success"],
        "tasks": results,
    }


def main_recorded_outcomes(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-human-traces.json"),
    )
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-human-suite.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    diagnostic_output_path(args.output)
    payload = audit_recorded_outcomes(args.catalog, args.trace_dir, args.suite)
    write_diagnostic(args.output, payload)


def _recorded_trace_records(path: Path, name: str) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        matches = [value for value in archive.namelist() if value.endswith(name)]
        if len(matches) != 1:
            raise ValueError(f"{path.name}: expected exactly one {name}")
        return parse_jsonl(archive.read(matches[0]).decode())


def verify_exclusions(trace_dir: Path, suite_path: Path, exclusions_path: Path) -> dict[str, Any]:
    trace_dir, suite_path, exclusions_path = map(
        unlinked_path, (trace_dir, suite_path, exclusions_path)
    )
    before_inputs = _diagnostic_snapshot([suite_path, exclusions_path])
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    exclusions = json.loads(exclusions_path.read_text(encoding="utf-8"))
    if exclusions.get("schema") != "lex_webarena_evaluator_exclusions/1":
        raise ValueError("WebArena evaluator exclusions schema is invalid")
    tasks = {str(row["id"]): row for row in suite.get("tasks") or []}
    results: list[dict[str, Any]] = []
    for exclusion in exclusions.get("excluded_tasks") or []:
        task_id = str(exclusion["task_id"])
        numeric_id = task_id.removeprefix("webarena-")
        safe_identifier(numeric_id)
        path = contained_path(trace_dir, f"{numeric_id}.trace.zip", reject_symlinks=True)
        if not path.is_file():
            raise FileNotFoundError(f"{task_id}: official trace is missing")
        digest = _recording_sha256(path)
        before_inputs[path] = digest
        if digest != exclusion.get("source_trace_sha256"):
            raise ValueError(f"{task_id}: official trace sha256 mismatch")
        task = tasks.get(task_id)
        if task is None:
            raise ValueError(f"{task_id}: task is missing from suite")
        network = _recorded_trace_records(path, "trace.network")
        requests = [
            row["snapshot"]["request"] for row in network if row.get("type") == "resource-snapshot"
        ]
        if task_id == "webarena-678":
            expected_events = [
                request
                for request in requests
                if parse_qs(urlsplit(str(request.get("url") or "")).query).get("namespace")
                == ["sales_order_grid"]
            ]
            passed = not expected_events
            detail = {"sales_order_grid_event_count": len(expected_events)}
        elif task_id == "webarena-760":
            route_paths = sorted(
                {
                    urlsplit(str(request.get("url") or "")).path
                    for request in requests
                    if "/route/v1/" in str(request.get("url") or "")
                }
            )
            trace_records = _recorded_trace_records(path, "trace.trace")
            created_pages = sum(
                row.get("type") == "event"
                and row.get("class") == "Page"
                and row.get("method") == "__create__"
                for row in trace_records
            )
            declared_pages = len((task.get("environment") or {}).get("start_urls") or [])
            actions = (task.get("trajectory") or {}).get("actions") or []
            max_tab_index = max((int(row.get("tab_index") or 0) for row in actions), default=0)
            expected_origin = "-75.4716115,40.6022552"
            passed = (
                bool(route_paths)
                and all(expected_origin not in path for path in route_paths)
                and created_pages > declared_pages
                and max_tab_index >= declared_pages
            )
            detail = {
                "recorded_route_paths": route_paths,
                "expected_origin": expected_origin,
                "created_page_count": created_pages,
                "declared_start_page_count": declared_pages,
                "max_action_tab_index": max_tab_index,
            }
        elif task_id == "webarena-811":
            issue_path = "/a11yproject/a11yproject.com/-/issues/1478.json"
            matching_requests = [
                request
                for request in requests
                if urlsplit(str(request.get("url") or "")).path == issue_path
            ]
            methods = sorted(
                {str(request.get("method") or "").upper() for request in matching_requests}
            )
            expected_put_count = sum(
                str(request.get("method") or "").upper() == "PUT" for request in matching_requests
            )
            recorded_get_count = sum(
                str(request.get("method") or "").upper() == "GET" for request in matching_requests
            )
            passed = expected_put_count == 0 and recorded_get_count >= 1
            detail = {
                "issue_path": issue_path,
                "recorded_methods": methods,
                "expected_put_event_count": expected_put_count,
                "recorded_get_event_count": recorded_get_count,
            }
        else:
            raise ValueError(f"unsupported evaluator exclusion audit: {task_id}")
        results.append(
            {
                "task_id": task_id,
                "evidence_id": exclusion["evidence_id"],
                "source_trace_sha256": digest,
                "passed": passed,
                **detail,
            }
        )
    payload = {
        "schema": "lex_webarena_evaluator_exclusion_audit/1",
        "suite_sha256": _recording_sha256(suite_path),
        "exclusions_sha256": _recording_sha256(exclusions_path),
        "complete": bool(results) and all(row["passed"] for row in results),
        "checks": results,
    }
    if not payload["complete"]:
        failed = [row["task_id"] for row in results if not row["passed"]]
        raise RuntimeError(f"WebArena evaluator exclusions were not reproduced: {failed}")
    _check_diagnostic_snapshot(before_inputs)
    return payload


def main_evaluator_exclusions(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-human-suite.json"),
    )
    parser.add_argument(
        "--exclusions",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-evaluator-exclusions.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    diagnostic_output_path(args.output)
    payload = verify_exclusions(args.trace_dir, args.suite, args.exclusions)
    write_diagnostic(args.output, payload)


def site_urls(config: dict[str, Any], sites: tuple[str, ...]) -> dict[str, str]:
    environments = config.get("environments") or {}
    urls: dict[str, str] = {}
    for site in sites:
        token = SITE_TOKENS[site]
        row = environments.get(token)
        if not isinstance(row, dict):
            raise ValueError(f"runtime config is missing {token}")
        if row.get("credentials") is not None:
            raise ValueError(f"diagnostic runtime config must not persist credentials: {token}")
        values = row.get("urls") or []
        index = int(row.get("active_url_idx") or 0)
        if not values or index < 0 or index >= len(values):
            raise ValueError(f"runtime config has no active URL for {token}")
        urls[site] = str(values[index])
    return urls


def runtime_smoke_main(argv=None) -> None:
    from harness.environments.browser import (
        ENGINE_PORTS,
        cleanup_browser_runtime,
        configured_browser_images,
        published_endpoint,
        start_browser_container,
        validate_resource_controls,
        wait_cdp,
    )
    from harness.web_fetch.safety import unlinked_path

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--site", action="append", choices=tuple(SITE_TOKENS), required=True)
    parser.add_argument(
        "--verify-login",
        action="store_true",
        help="authenticate with credentials_env before starting diagnostic HAR capture",
    )
    parser.add_argument("--engine", action="append", choices=tuple(ENGINE_PORTS))
    parser.add_argument("--cpus", type=float, default=None)
    args = parser.parse_args(argv)
    config_path = unlinked_path(args.config)
    output = unlinked_path(args.output)
    if output.exists():
        raise ValueError(f"refusing to overwrite diagnostic evidence: {output}")
    sites = tuple(dict.fromkeys(args.site))
    frozen_config = config_path.read_bytes()
    config = json.loads(frozen_config)
    urls = site_urls(config, sites)
    if args.cpus is not None and (not math.isfinite(args.cpus) or args.cpus <= 0):
        raise ValueError("--cpus must be finite and positive")
    from types import SimpleNamespace

    import docker
    from playwright.sync_api import sync_playwright

    from harness.environments.webarena.har import CdpHarRecorder

    from .webarena_environment import WebArenaEnvironment

    output.mkdir(parents=True)
    client = docker.from_env()
    if args.cpus is not None:
        if args.cpus <= 0:
            raise ValueError("--cpus must be positive")
        validate_resource_controls(client.info())
    receipt: dict[str, Any] = {
        "schema": "lex_webarena_runtime_smoke/1",
        "diagnostic_only": True,
        "qualification_evidence": False,
        "cpu_limit": args.cpus,
        "memory_limit": "1g",
        "login_verified": args.verify_login,
        "config_sha256": _sha256(config_path),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "engines": {},
    }
    images = configured_browser_images()
    for engine in tuple(dict.fromkeys(args.engine or ENGINE_PORTS)):
        client.images.get(images[engine])
        run_id = f"runtime-smoke-{uuid.uuid4().hex[:10]}"
        network = client.networks.create(
            f"lex-browser-use-eval-{run_id}-{engine}",
            driver="bridge",
            labels={"lex.browser-use-eval.diagnostic": run_id},
        )
        containers: dict[str, Any] = {}
        browsers: dict[str, Any] = {}
        playwright = None
        try:
            playwright = sync_playwright().start()
            container = start_browser_container(
                client,
                image=images[engine], cdp_port=ENGINE_PORTS[engine],
                engine=engine,
                run_id=run_id,
                cpus=args.cpus,
                memory="1g",
                network=network.name,
            )
            containers[engine] = container
            container.reload()
            endpoint = published_endpoint(
                container, engine=engine, cdp_port=ENGINE_PORTS[engine]
            )
            browser = playwright.chromium.connect_over_cdp(
                wait_cdp(endpoint, timeout_seconds=30, container=container)
            )
            browsers[engine] = browser
            page = browser.contexts[0].pages[0]
            if args.verify_login:
                environment = WebArenaEnvironment(config_path)
                page = environment.prepare(
                    SimpleNamespace(
                        task_id="diagnostic-login-smoke",
                        environment={
                            "kind": "webarena_verified",
                            "sites": list(sites),
                            "start_urls": [SITE_TOKENS[site] for site in sites],
                        },
                    ),
                    page,
                )
            recorder = CdpHarRecorder(page)
            pages = []
            for site, url in urls.items():
                response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                status = response.status if response is not None else None
                if status is None or status >= 400:
                    raise RuntimeError(f"{engine} failed to load {site}: HTTP {status}")
                pages.append(
                    {
                        "site": site,
                        "requested_url": url,
                        "final_url": page.url,
                        "http_status": status,
                        "title": page.title(),
                    }
                )
            har_path = output / f"{engine}.har"
            har = recorder.stop(har_path)
            receipt["engines"][engine] = {
                "container_image_id": container.image.id,
                "pages": pages,
                "har": {**har, "sha256": _sha256(har_path)},
            }
        finally:
            cleanup_browser_runtime(browsers, playwright, containers, {engine: network})
    if config_path.read_bytes() != frozen_config:
        raise ValueError("Runtime configuration changed during smoke")
    (output / "receipt.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
