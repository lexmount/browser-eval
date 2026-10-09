"""Rule-based and optional LLM-assisted failure diagnosis.

Diagnostics are explanatory only. They never participate in pass/fail scoring.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from harness.record_io import same_json_record
from harness.web_fetch.safety import unlinked_path, write_new_file


def _diagnostic_digest(path: Path) -> str:
    source = unlinked_path(path)
    if not source.is_file():
        raise ValueError("diagnostic input must be a regular file")
    return hashlib.sha256(source.read_bytes()).hexdigest()


def _diagnostic_snapshot(paths) -> dict[Path, str]:
    return {unlinked_path(path): _diagnostic_digest(path) for path in paths}


def _check_diagnostic_snapshot(before: dict[Path, str]) -> None:
    if any(_diagnostic_digest(path) != digest for path, digest in before.items()):
        raise ValueError("diagnostic input changed during inspection")


def diagnostic_output_path(path: Path) -> Path:
    """Reject occupied outputs before starting an optional evaluator or browser."""
    target = unlinked_path(path)
    if target.exists():
        raise ValueError("diagnostic output already exists")
    return target


def write_diagnostic(path: Path, payload: dict) -> None:
    """Expose a complete diagnostic receipt without replacing an existing file."""
    target = diagnostic_output_path(path)
    write_new_file(target, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())


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


def snapshot_readiness_gaps(trace: list[dict]) -> list[dict]:
    gaps = []
    for previous, step in zip(trace, trace[1:], strict=False):
        call = step.get("arguments", {})
        if call.get("action") != "click":
            continue
        match = re.search(r':has-text\(("(?:[^"\\]|\\.)*")\)', call.get("selector", ""))
        if not match:
            continue
        label = json.loads(match[1])
        lines = [
            line.strip()
            for line in str(previous.get("result", "")).splitlines()
            if "button disabled:" in line and line.rstrip().endswith(": " + label)
        ]
        if lines:
            diagnostics = step.get("action_diagnostics")
            dispatch = (
                diagnostics.get("click_dispatch_state") if isinstance(diagnostics, dict) else None
            )
            gaps.append(
                {
                    "snapshot_step": previous["index"],
                    "click_step": step["index"],
                    "selector": call["selector"],
                    "snapshot_lines": lines,
                    "dispatch_state_known": (
                        isinstance(dispatch, dict) and isinstance(dispatch.get("disabled"), bool)
                    ),
                }
            )
    return gaps


def audit_readiness(results_path: Path) -> dict:
    results_path = unlinked_path(results_path)
    before = _diagnostic_snapshot([results_path])
    content = results_path.read_bytes()
    results = json.loads(content)
    findings = []
    identities = set()
    for attempt in results["attempts"]:
        key = (attempt["participant_id"], attempt["task_id"])
        if key in identities:
            raise ValueError("duplicate method-task attempt")
        identities.add(key)
        reference = attempt["evidence"]["trace"]
        path = unlinked_path(Path(reference["path"]))
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != reference["sha256"]:
            raise ValueError("trace evidence changed")
        before[path] = reference["sha256"]
        for gap in snapshot_readiness_gaps(json.loads(data)):
            findings.append(
                {
                    "participant_id": key[0],
                    "task_id": key[1],
                    "historical_status": attempt["status"],
                    "trace_sha256": reference["sha256"],
                    **gap,
                }
            )
    _check_diagnostic_snapshot(before)
    return {
        "schema": "lex_replay_disabled_snapshot_audit/1",
        "results_sha256": hashlib.sha256(content).hexdigest(),
        "attempts_scanned": len(identities),
        "scope": (
            "adjacent snapshot-disabled button and exact has-text click; "
            "not exhaustive actionability review"
        ),
        "score_effect": "none",
        "findings": findings,
    }


def main_readiness(argv=None):
    parser = argparse.ArgumentParser(
        description="Locate clicks immediately following snapshots that label the target disabled."
    )
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    diagnostic_output_path(args.output)
    report = audit_readiness(args.results)
    write_diagnostic(args.output, report)
    print(
        json.dumps(
            {"attempts_scanned": report["attempts_scanned"], "findings": len(report["findings"])}
        )
    )


def cart_html(quantity="1", *, rows=1, fields=1, name):
    field = f'<input name="cart[1][qty]" value="{html.escape(quantity, quote=True)}">'
    row = (
        '<tbody class="cart item"><tr><td><strong class="product-item-name">'
        + html.escape(name)
        + "</strong>"
        + field * fields
        + "</td></tr></tbody>"
    )
    return '<!doctype html><table id="shopping-cart-table">' + row * rows + "</table>"


def bound_cart_scripts(dataset, candidate):
    dataset, candidate = unlinked_path(dataset), unlinked_path(candidate)
    before_inputs = _diagnostic_snapshot([dataset, candidate])
    payload = json.loads(dataset.read_text())
    revised = json.loads(candidate.read_text())
    if (
        hashlib.sha256(dataset.read_bytes()).hexdigest()
        != revised["repair"]["predecessor_dataset_sha256"]
    ):
        raise ValueError("candidate predecessor changed")
    matches = [row for row in payload["trajectories"] if row["task_id"] == revised["task_id"]]
    if len(matches) != 1:
        raise ValueError("candidate task is missing or duplicated")
    before, after = matches[0]["tool_calls"], revised["tool_calls"]
    index = revised["repair"]["changed_step"] - 1
    if len(before) != len(after) or [i for i in range(len(before)) if before[i] != after[i]] != [
        index
    ]:
        raise ValueError("candidate must change only its declared assertion")
    if before[index]["action"] != "evaluate" or after[index]["action"] != "evaluate":
        raise ValueError("declared correction is not an evaluate assertion")
    _check_diagnostic_snapshot(before_inputs)
    return before[index]["script"], after[index]["script"]


def audit_cart_assertion(dataset, candidate, browser_binary, *, product):
    from playwright.sync_api import Error, sync_playwright

    before_inputs = _diagnostic_snapshot([dataset, candidate])
    scripts = bound_cart_scripts(dataset, candidate)
    cases = [
        ("quantity-one", {}, True),
        ("quantity-two", {"quantity": "2"}, False),
        ("quantity-zero", {"quantity": "0"}, False),
        ("quantity-empty", {"quantity": ""}, False),
        ("quantity-invalid", {"quantity": "NaN"}, False),
        ("quantity-missing", {"fields": 0}, False),
        ("quantity-duplicated", {"fields": 2}, False),
        ("two-product-rows", {"rows": 2}, False),
        ("wrong-product", {"name": "Different product"}, False),
    ]
    observations = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(browser_binary), headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.abort())
            for identity, values, expected in cases:
                outcomes = {}
                for label, script in zip(("frozen", "candidate"), scripts, strict=True):
                    page.set_content(cart_html(**{"name": product, **values}))
                    try:
                        result = page.evaluate(script)
                        outcomes[label] = {"passed": True, "result": result}
                    except Error as exc:
                        outcomes[label] = {"passed": False, "error": str(exc).splitlines()[0]}
                if outcomes["candidate"]["passed"] != expected:
                    raise AssertionError(f"candidate violates quantity contract: {identity}")
                observations.append(
                    {"control": identity, "expected_acceptance": expected, **outcomes}
                )
        finally:
            browser.close()
    _check_diagnostic_snapshot(before_inputs)
    return {
        "schema": "lex_replay_cart_assertion_controls/1",
        "scope": "isolated_dom_not_task_scores",
        "network_allowed": False,
        "task_id": json.loads(candidate.read_text())["task_id"],
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "observations": observations,
    }


def main_cart_assertion(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare frozen and candidate cart assertions on isolated DOM counterexamples."
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument(
        "--product", required=True, help="Expected product name from the task contract"
    )
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    diagnostic_output_path(args.output)
    output = audit_cart_assertion(args.dataset, args.candidate, args.browser, product=args.product)
    write_diagnostic(args.output, output)
    print(json.dumps({"output": str(args.output), "controls": len(output["observations"])}))


def validate_contract_annotations(annotations: dict, audit: dict, bindings: dict) -> None:
    """Require an exact, bilingual inventory of selected contract changes."""
    if annotations.get("schema") != "lex_replay_contract_audit/1":
        raise ValueError("unknown contract audit schema")
    if annotations.get("bindings") != bindings:
        raise ValueError("contract audit bindings differ from inspected inputs")
    fork = {row["task_id"] for row in audit["fork"]["dataset_changes"] if row["selected"]}
    local = {row["task_id"] for row in audit["local_contract_edits"]}
    rows = annotations.get("task_contract_changes", [])
    ids = [row["task_id"] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != fork | local:
        raise ValueError("contract audit must cover exactly the selected changed tasks")
    for row in rows:
        task = row["task_id"]
        layer = "fork_and_local" if task in fork & local else "fork" if task in fork else "local"
        if row.get("layer") != layer:
            raise ValueError("contract audit change layer differs")
        for key in ("kind", "before_zh", "after_zh", "before_en", "after_en"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError("contract audit requires bilingual explanations")


def audit_dataset_changes(before: list[dict], after: list[dict], selected: set[int]) -> list[dict]:
    def index(tasks: list[dict]) -> dict[int, dict]:
        rows = {row["task_id"]: row for row in tasks}
        if len(rows) != len(tasks):
            raise ValueError("duplicate dataset task identity")
        return rows

    old, new = index(before), index(after)
    changes = []
    for task in sorted(old.keys() | new.keys()):
        if old.get(task) == new.get(task):
            continue
        left, right = old.get(task, {}), new.get(task, {})
        fields = {
            key: {"before": left.get(key), "after": right.get(key)}
            for key in sorted(left.keys() | right.keys())
            if left.get(key) != right.get(key)
        }
        changes.append(
            {
                "task_id": task,
                "selected": task in selected,
                "change": "added" if not left else "removed" if not right else "modified",
                "fields": fields,
            }
        )
    return changes


def audit_inputs(results: dict, profile: dict, corrections: dict) -> dict[str, Any]:
    participants = [method["id"] for method in profile["methods"]]
    if not participants or len(set(participants)) != len(participants):
        raise ValueError("participant registry must be unique and nonempty")
    task_count = profile["task_selection"]["task_count"]
    rows: dict[int, dict[str, dict]] = {}
    for row in results["attempts"]:
        task = row["task_id"]
        method = row["participant_id"]
        if method not in participants or method in rows.setdefault(task, {}):
            raise ValueError("unknown participant or duplicate logical result")
        rows[task][method] = row
    if len(rows) != task_count or any(set(row) != set(participants) for row in rows.values()):
        raise ValueError("incomplete declared task-by-method matrix")
    unequal_actions = []
    unequal_contracts = []
    for task, methods in sorted(rows.items()):
        baseline = methods[participants[0]]
        if any(row["planned_actions"] != baseline["planned_actions"] for row in methods.values()):
            unequal_actions.append(task)
        if any(
            row["task_contract_sha256"] != baseline["task_contract_sha256"]
            for row in methods.values()
        ):
            unequal_contracts.append(task)
    edits = []
    for record in corrections["corrections"]:
        if record["task_id"] not in rows:
            continue
        operations = record["operations"]
        edits.append(
            {
                "task_id": record["task_id"],
                "correction_id": record["correction_id"],
                "changes_intent": any(op["path"] == "/intent" for op in operations),
                "removed_evaluators": [
                    op["path"]
                    for op in operations
                    if op["op"] == "remove" and op["path"].startswith("/eval/")
                ],
                "operations": operations,
                "replay_assertions": record.get("replay_assertions", []),
                "network_event_alternatives": record.get("network_event_alternatives", []),
                "has_bound_root_cause_and_regressions": bool(
                    record.get("root_cause") and len(record.get("regression_ids", [])) >= 2
                ),
            }
        )
    return {
        "schema": "lex_replay_input_audit/1",
        "scope": (
            "Input symmetry and declared local edits only; "
            "not causal attribution or fairness approval"
        ),
        "participants": participants,
        "task_count": task_count,
        "logical_result_count": len(results["attempts"]),
        "unequal_actions_task_ids": unequal_actions,
        "unequal_contract_task_ids": unequal_contracts,
        "qualification_reference_engine": profile.get("qualification", {}).get("reference_engine"),
        "seed_inventory_base_kind": profile.get("seed_inventory", {}).get("base_kind"),
        "local_contract_edits": edits,
        "requires_separate_review": [
            "upstream fork changes",
            "task selection and qualification bias",
            "acceptance weakening and counterexamples",
            "source mechanisms and failure attribution",
        ],
    }


def main_comparison_inputs(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Inventory comparison symmetry and declared benchmark edits without rescoring."
    )
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path("evaluation/trajectory-replay/webarena-verified/current-run.json"),
    )
    parser.add_argument(
        "--corrections",
        type=Path,
        default=Path("evaluation/trajectory-replay/webarena-verified/task-corrections.json"),
    )
    parser.add_argument("--fork-repository", type=Path)
    parser.add_argument("--upstream-ref")
    parser.add_argument("--fork-ref")
    parser.add_argument("--annotations", type=Path)
    args = parser.parse_args(argv)

    def read(path: Path) -> dict:
        return json.loads(unlinked_path(path).read_text(encoding="utf-8"))

    paths = [args.results, args.profile, args.corrections]
    if args.annotations:
        paths.append(args.annotations)
        paths.append(Path(read(args.profile)["dataset"]["path"]))
    before = _diagnostic_snapshot(paths)
    results = read(args.results)
    audit = audit_inputs(results, read(args.profile), read(args.corrections))
    fork_args = (args.fork_repository, args.upstream_ref, args.fork_ref)
    if any(fork_args):
        if not all(fork_args):
            parser.error("fork inventory requires repository and both explicit refs")
        datasets = []
        commits = []
        for ref in (args.upstream_ref, args.fork_ref):
            commit = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(args.fork_repository),
                    "rev-parse",
                    "--verify",
                    f"{ref}^{{commit}}",
                ],
                text=True,
            ).strip()
            commits.append(commit)
            datasets.append(
                json.loads(
                    subprocess.check_output(
                        [
                            "git",
                            "-C",
                            str(args.fork_repository),
                            "show",
                            f"{commit}:assets/dataset/webarena-verified.json",
                        ],
                        text=True,
                    )
                )
            )
        selected = {row["task_id"] for row in results["attempts"]}
        audit["fork"] = {
            "upstream_commit": commits[0],
            "fork_commit": commits[1],
            "dataset_changes": audit_dataset_changes(*datasets, selected),
        }
    if args.annotations:
        if not all(fork_args):
            parser.error("annotation validation requires the fork inventory")
        profile = read(args.profile)
        dataset_path = Path(profile["dataset"]["path"])
        bindings = {
            "results_sha256": hashlib.sha256(args.results.read_bytes()).hexdigest(),
            "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            "corrections_sha256": hashlib.sha256(args.corrections.read_bytes()).hexdigest(),
            "upstream_commit": commits[0],
            "fork_commit": commits[1],
        }
        if (
            bindings["dataset_sha256"] != profile["dataset"]["sha256"]
            or results["dataset_sha256"] != bindings["dataset_sha256"]
        ):
            raise ValueError("comparison dataset binding differs")
        annotations = read(args.annotations)
        validate_contract_annotations(annotations, audit, bindings)
        audit["contract_annotations"] = annotations
    _check_diagnostic_snapshot(before)
    print(json.dumps(audit, ensure_ascii=False, indent=2))


def _saved_execution_complete(result: dict, task_id: int) -> bool:
    if type(result.get("task_id")) is not int or result["task_id"] != task_id:
        raise ValueError("saved attempt belongs to another task")
    total, completed = result.get("action_count"), result.get("completed_actions")
    if (
        type(total) is not int
        or total < 1
        or type(completed) is not int
        or not 0 <= completed <= total
    ):
        raise ValueError("saved attempt has missing or invalid action counts")
    return completed == total


def audit_saved_correction(
    *, task_dir: Path, task_id: int, site_map: Path, before_catalog: Path, after_catalog: Path
) -> dict:
    task_dir = unlinked_path(task_dir)
    site_map, before_catalog, after_catalog = map(
        unlinked_path, (site_map, before_catalog, after_catalog)
    )
    receipt_path = task_dir.parent / "receipt.json"
    sources = [receipt_path, site_map, before_catalog, after_catalog]
    if any(
        not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)) for path in sources
    ):
        raise ValueError("correction audit inputs must be regular, non-symlinked files")
    before_inputs = _diagnostic_snapshot(sources)
    receipt = json.loads(receipt_path.read_text())
    if receipt["site_map_sha256"] != _diagnostic_digest(site_map):
        raise ValueError("site map differs from saved execution")
    rows = [row for row in receipt["results"] if row["task_id"] == task_id]
    if len(rows) != 1:
        raise ValueError("expected exactly one saved task result")
    row = rows[0]
    hashes = {}
    names = [("network_har", "network.har"), ("trace", "trace.json")]
    completed = _saved_execution_complete(row, task_id)
    if completed:
        names += [("evaluation", "evaluation.json"), ("actual_response", "actual_response.json")]
    elif row["status"] != "fail" or row.get("score") is not None:
        raise ValueError("incomplete execution has inconsistent verdict")
    for key, name in names:
        path = task_dir / name
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise ValueError("saved evidence is missing or symlinked")
        hashes[key] = _diagnostic_digest(path)
        before_inputs[path] = hashes[key]
        if hashes[key] != row["evidence_sha256"][key]:
            raise ValueError("saved evidence differs from execution receipt")
    if not completed:
        _check_diagnostic_snapshot(before_inputs)
        return {
            "schema": "lex_saved_replay_correction_audit/1",
            "task_id": task_id,
            "method_id": receipt["method_id"],
            "browser_calls": 0,
            "source_receipt_sha256": _diagnostic_digest(receipt_path),
            "evidence_sha256": hashes,
            "site_map_sha256": _diagnostic_digest(site_map),
            "saved_execution_status": "fail",
            "evaluation_reached": False,
            "completed_actions": row["completed_actions"],
            "action_count": row["action_count"],
            "score_changed": False,
            "before_catalog_sha256": _diagnostic_digest(before_catalog),
            "after_catalog_sha256": _diagnostic_digest(after_catalog),
        }
    from harness.trajectory_replay.remote_runtime import load_remote_sites, remote_benchmark
    from harness.trajectory_replay.webarena_corrections import (
        apply_correction,
        evaluate_corrected_contract,
        load_corrections,
    )

    benchmark = remote_benchmark(load_remote_sites(site_map))
    response = json.loads((task_dir / "actual_response.json").read_text())
    evaluations = []
    for source in (before_catalog, after_catalog):
        catalog = load_corrections(source)
        contract = apply_correction(benchmark.get_task(task_id), catalog)
        result = evaluate_corrected_contract(
            benchmark, contract, agent_response=response, network_trace=task_dir / "network.har"
        )
        evaluations.append(
            {
                "catalog_sha256": catalog.source_sha256,
                "record_sha256": catalog.record_sha256s[task_id],
                "correction_id": contract.correction_id,
                "task_sha256": contract.corrected_task_sha256,
                "score": result.score,
                "evaluation": result.model_dump(mode="json"),
            }
        )
    saved = json.loads((task_dir / "evaluation.json").read_text())
    if (
        evaluations[0]["score"] != saved["score"]
        or evaluations[0]["task_sha256"] != saved["corrected_task_sha256"]
    ):
        raise ValueError("predecessor does not reproduce the saved scoring contract and score")
    _check_diagnostic_snapshot(before_inputs)
    return {
        "schema": "lex_saved_replay_correction_audit/1",
        "task_id": task_id,
        "method_id": receipt["method_id"],
        "browser_calls": 0,
        "source_receipt_sha256": _diagnostic_digest(receipt_path),
        "evidence_sha256": hashes,
        "site_map_sha256": _diagnostic_digest(site_map),
        "saved_execution_status": row["status"],
        "before": evaluations[0],
        "after": evaluations[1],
        "score_changed": evaluations[0]["score"] != evaluations[1]["score"],
    }


def main_saved_correction(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare two correction catalogs on immutable saved replay evidence, "
            "without browser calls."
        )
    )
    parser.add_argument("--task-dir", type=Path, required=True)
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--before-catalog", type=Path, required=True)
    parser.add_argument("--after-catalog", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(audit_saved_correction(**vars(args)), ensure_ascii=False, indent=2))


def rescore_saved_retrieval(*, task_dir: Path, task_id: int, dataset: Path, site_map: Path) -> dict:
    task_dir, dataset, site_map = map(unlinked_path, (task_dir, dataset, site_map))
    sources = _diagnostic_snapshot([dataset, site_map])
    records = []
    for path in (task_dir / "result.json", task_dir.parent / "result.json"):
        path = unlinked_path(path)
        if path.exists():
            sources.update(_diagnostic_snapshot([path]))
            records.append((path, json.loads(path.read_bytes())))
    receipt_path = unlinked_path(task_dir.parent / "receipt.json")
    if receipt_path.exists():
        sources.update(_diagnostic_snapshot([receipt_path]))
        receipt = json.loads(receipt_path.read_bytes())
        rows = [row for row in receipt.get("results") or [] if row.get("task_id") == task_id]
        if len(rows) != 1:
            raise ValueError("saved diagnostic receipt lacks one task result")
        if receipt.get("site_map_sha256") != sources[site_map]:
            raise ValueError("site map differs from saved execution")
        records.append((receipt_path, rows[0]))
    if not records or any(not same_json_record(value, records[0][1]) for _, value in records[1:]):
        raise ValueError("saved result is missing or disagrees with its receipt")
    result_path, result = records[0]
    if not _saved_execution_complete(result, task_id):
        raise ValueError("saved attempt is incomplete or belongs to another task")
    hashes = result.get("evidence_sha256") or {}
    names = {
        "actual_response": "actual_response.json",
        "network_har": "network.har",
        "trace": "trace.json",
        "acceptance": "acceptance.json",
        "evaluation": "evaluation.json",
    }
    for key, name in names.items():
        path = task_dir / name
        sources.update(_diagnostic_snapshot([path]))
        if sources[path] != hashes.get(key):
            raise ValueError(f"saved {key} evidence changed")
    actual = json.loads((task_dir / "actual_response.json").read_text(encoding="utf-8"))
    from webarena_verified.api import WebArenaVerified
    from webarena_verified.types.config import WebArenaVerifiedConfig

    from harness.trajectory_replay.remote_runtime import load_remote_sites, remote_benchmark

    sites = load_remote_sites(site_map)
    benchmark = WebArenaVerified(
        config=WebArenaVerifiedConfig(
            test_data_file=dataset,
            environments=remote_benchmark(sites).config.environments,
        )
    )
    official = benchmark.get_task(task_id)
    if not official.is_retrieve_task:
        raise ValueError("saved task is not a retrieval task")
    evaluation = benchmark.evaluate_task(
        task_id=task_id, agent_response=actual, network_trace=task_dir / "network.har"
    ).model_dump(mode="json")
    _check_diagnostic_snapshot(sources)
    return {
        "schema": "webarena_saved_retrieval_rescore/1",
        "task_id": task_id,
        "browser_calls": 0,
        "scope": "diagnostic_only",
        "source_verdict_changed": False,
        "source_result_sha256": sources[result_path],
        "source_evaluation_sha256": hashes["evaluation"],
        "dataset_sha256": sources[dataset],
        "site_map_sha256": sources[site_map],
        "score": evaluation["score"],
        "evaluation": evaluation,
    }


def main_saved_retrieval_rescore(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", type=Path, required=True)
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output = diagnostic_output_path(args.output)
    value = rescore_saved_retrieval(
        task_dir=args.task_dir,
        task_id=args.task_id,
        dataset=args.dataset,
        site_map=args.site_map,
    )
    write_diagnostic(args.output, value)
    print(json.dumps({"task_id": args.task_id, "score": value["score"], "browser_calls": 0}))
