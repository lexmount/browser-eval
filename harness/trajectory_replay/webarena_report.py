"""Build the evidence-backed WebArena Browser Trajectory Replay report."""

from __future__ import annotations

import hashlib
import html
import importlib.resources
import json
import math
import re
import shutil
import statistics
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from harness.trajectory_replay.catalog_contracts import (
    LEGACY_CATALOG_CONTRACTS_SCHEMA,
    LEGACY_CATALOG_REPORT_SCHEMA,
)
from harness.trajectory_replay.evaluator_transition import json_sha256, reevaluate_pass
from harness.trajectory_replay.failure_matrix import failure_category_matrix
from harness.trajectory_replay.interpretation import render_interpretation, standalone_document
from harness.trajectory_replay.participants import (
    bind_participant,
    participant_registry,
    resolve_participant_id,
)
from harness.trajectory_replay.remote_runtime import load_remote_sites, remote_benchmark
from harness.trajectory_replay.runtime_identity import request_bridge_identity
from harness.trajectory_replay.webarena_corrections import load_corrections
from harness.trajectory_replay.webarena_review import (
    load_validated_failure_review,
    programmatic_failure,
)

LOCAL_PATH_RE = re.compile(r"/(?:Users|home|root|private|tmp|var|opt)/[^\s\"']+")
WEB_URL_RE = re.compile(r"https?://[^\s\"']+")

# This notice describes one retained historical cohort, not trajectory replay in
# general.  Keying all three immutable inputs prevents a future requalified run
# from inheriting legacy limitations merely because it also contains 90 tasks.
LEGACY_REPLAY_VALIDITY_INPUTS = {
    (
        "abaaa0e90aced74a831093f2d6d7c3371639acb984b3a2c057f3cc39632ea935",
        "61bb38bce552b5c7eea0e1f5fcca1bc898f55e2e39ee6e72498cff346ca1e4e3",
        "3ca5d91cfec570490df202c9cb332957f7e850b70fc5af80b684709e97ef0893",
    )
}


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _legacy_retrieval_provenance(
    results: dict[str, Any], dataset: dict[str, Any]
) -> dict[str, Any]:
    """Prove whether retained retrieval verdicts used saved Agent answers."""
    source = Path(results["source_seed_manifest"])
    if (not source.is_absolute()
            or any(part.is_symlink() for part in (source, *source.parents))
            or not source.is_file()):
        raise ValueError("legacy replay seed manifest must be an absolute file without symlinks")
    if _sha256(source) != results.get("source_seed_manifest_sha256"):
        raise ValueError("legacy replay seed manifest hash mismatch")
    seeds = json.loads(source.read_text(encoding="utf-8"))
    by_id = {int(row["task_id"]): row for row in seeds["tasks"]}
    retrieval_ids = {
        int(row["task_id"])
        for row in dataset["trajectories"]
        if row["evaluation_input"]["task_type"] == "RETRIEVE"
    }
    rows = []
    for attempt in results["attempts"]:
        task_id = int(attempt["task_id"])
        if task_id not in retrieval_ids:
            continue
        seed = by_id[task_id]
        candidates = [
            item for item in seed["candidates"]
            if item["id"] == seed["canonical_candidate_id"]
        ]
        if len(candidates) != 1:
            raise ValueError(f"task {task_id}: canonical replay seed is ambiguous")
        saved_hash = _json_sha256(candidates[0]["recorded_response"])
        actual_hash = attempt.get("evaluation_input_sha256")
        if actual_hash != saved_hash:
            raise ValueError(f"task {task_id}: retrieval input differs from frozen Agent answer")
        rows.append({
            "task_id": task_id, "browser": attempt["engine"],
            "evaluation_input_sha256": actual_hash,
            "source": "frozen_agent_response_not_current_page_observation",
        })
    browsers = {str(browser) for browser in results["engines"]}
    expected = {(browser, task_id) for browser in browsers for task_id in retrieval_ids}
    actual = {(row["browser"], row["task_id"]) for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise ValueError("legacy retrieval provenance lacks the full browser-task matrix")
    return {
        "schema": "trajectory_replay_legacy_retrieval_provenance/1",
        "source_seed_manifest_sha256": results["source_seed_manifest_sha256"],
        "retrieval_tasks": len(retrieval_ids),
        "retrieval_results": len(rows),
        "frozen_answer_matches": len(rows),
        "meaning": "A matching verdict verifies the saved answer against the official evaluator, not an answer reconstructed from this browser run.",
        "results": sorted(rows, key=lambda item: (item["task_id"], item["browser"])),
    }


def _stat(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "mean": None, "p50": None, "p95": None}
    ordered = sorted(values)

    def rank(quantile: float) -> float:
        return ordered[min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1)]
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "p50": rank(0.5),
        "p95": rank(0.95),
    }


def _outcome_metrics(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Keep the declared population; missing samples never become zero."""
    fields = {"duration": "duration_ms", "cpu": "cpu_ms", "memory": "memory_mean_bytes",
              "memory_peak": "memory_peak_bytes", "network_rx": "network_rx_bytes",
              "network_tx": "network_tx_bytes", "cpu_utilization": "cpu_average_utilization_pct"}
    result = {}
    for name, field in fields.items():
        values = []
        for row in rows:
            source = row if name == "duration" else (row.get("resource") or {})
            value = source.get(field)
            if (source.get("available") is not False and type(value) in (int, float)
                    and math.isfinite(value) and value >= 0):
                values.append(float(value))
        result[name] = {**_stat(values), "population": len(rows), "missing": len(rows) - len(values)}
    return result


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _clean(item)
            for key, item in value.items()
            if key not in {"path", "paths"}
        }
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, str):
        url_spans = [match.span() for match in WEB_URL_RE.finditer(value)]

        def redact_local_path(match: re.Match[str]) -> str:
            if any(start <= match.start() and match.end() <= end for start, end in url_spans):
                return match.group(0)
            return "[local path removed]"

        value = LOCAL_PATH_RE.sub(redact_local_path, value)
        value = re.sub(
            r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{12,}",
            "[secret removed]",
            value,
        )
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_clean(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _planned_actions_digest(actions: list[dict[str, Any]]) -> str:
    payload = json.dumps(actions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validated_planned_actions(attempt: dict[str, Any]) -> list[dict[str, Any]]:
    actions = attempt.get("planned_actions")
    digest = attempt.get("planned_actions_sha256")
    if (
        not isinstance(actions, list)
        or not actions
        or any(not isinstance(action, dict) or not action.get("action") for action in actions)
        or _clean(actions) != actions
        or len(actions) != attempt.get("action_count")
        or not isinstance(digest, str)
        or digest != _planned_actions_digest(actions)
    ):
        raise ValueError(
            f"method-specific planned actions missing or changed for "
            f"{attempt.get('participant_id')}/{attempt.get('task_id')}"
        )
    return actions


def _reference_validity_by_task(attempts: list[dict[str, Any]]) -> dict[int, str]:
    task_validity: dict[int, str] = {}
    for attempt in attempts:
        validity = attempt.get("reference_validity") or {}
        status = validity.get("status")
        if status not in {"valid", "unverifiable"}:
            raise ValueError(f"reference validity is missing for task {attempt['task_id']}")
        task_id = int(attempt["task_id"])
        if task_id in task_validity and task_validity[task_id] != status:
            raise ValueError(f"methods disagree on task {task_id} reference validity")
        task_validity[task_id] = status
    return task_validity


def require_all_reference_validity(
    profile: dict[str, Any], results: dict[str, Any], dataset: dict[str, Any]
) -> None:
    """Reject a formal three-method release with any unverified task reference."""

    if (profile.get("scoring") or {}).get("require_all_reference_validity") is not True:
        return
    method_rows = profile.get("methods") or []
    task_rows = dataset.get("trajectories") or []
    task_ids = [row.get("task_id") for row in task_rows if isinstance(row, dict)]
    declared_count = (profile.get("task_selection") or {}).get("task_count")
    if (
        len(method_rows) < 2
        or not task_ids
        or len(task_ids) != declared_count
        or len(set(task_ids)) != len(task_ids)
    ):
        raise ValueError("formal reference-validity gate requires registered methods and the declared task set")
    methods = participant_registry(method_rows)
    attempts = [bind_participant(row, methods) for row in results.get("attempts") or []]
    expected = {(method_id, task_id) for method_id in methods for task_id in task_ids}
    actual = {(row["participant_id"], row.get("task_id")) for row in attempts}
    if len(attempts) != len(methods) * len(task_ids) or actual != expected:
        raise ValueError("formal reference-validity gate requires every method-task result")
    validity = _reference_validity_by_task(attempts)
    unavailable = sorted(task_id for task_id, status in validity.items() if status != "valid")
    if unavailable:
        raise ValueError(f"formal reference-validity gate rejects unverifiable tasks: {unavailable}")


def _measured_validity_identity(
    results: dict[str, Any], results_source: Path, dataset_source: Path
) -> dict[str, str]:
    # The current publication profile is deliberately not an input: changing
    # host/version cannot change the limitations of an already measured run.
    return {
        "results_sha256": _sha256(results_source),
        "dataset_sha256": _sha256(dataset_source),
        "profile_sha256": results.get("profile_sha256", ""),
    }


def _historical_replay_validity_notice(
    *, results_sha256: str, dataset_sha256: str, profile_sha256: str, lang: str
) -> str:
    """Disclose limitations only for the hash-bound legacy 90-task cohort."""

    identity = (results_sha256, dataset_sha256, profile_sha256)
    if identity not in LEGACY_REPLAY_VALIDITY_INPUTS:
        return ""
    if lang == "zh":
        heading = "历史 90 题成绩保留，但不是当前资格证明"
        body = (
            "Chromium 90/90、Moli 54/90 是这批历史固定轨迹的原始验收结果，不做人工改分。"
            "但旧资格流程曾把返回内容为 [Snapshot error: …] 的 snapshot 动作按工具包装层 success 接受；"
            "37 道 RETRIEVE 题的 74 条结果均直接使用冻结的 recorded_response 验收，没有绑定本轮页面读数；"
            "历史运行还存在 Chromium 780×437/493、Moli 1920×1080 的视口差异。"
            "因此这些分数只能描述旧协议下的固定路径验收，不能证明 90 条轨迹符合当前 fail-closed 资格规则，"
            "也不能作为统一视口下的浏览器成功率。修正后的正式比较需要重新资格验证并完整重跑。"
        )
    else:
        heading = "The historical 90-task scores remain, but they are not current qualification proof"
        body = (
            "Chromium 90/90 and Moli 54/90 remain the original acceptance results for this historical fixed-trajectory cohort; no score is manually changed. "
            "However, the legacy qualification flow accepted snapshot actions as tool-wrapper successes even when their returned content began [Snapshot error: …]. "
            "All 74 results for the 37 RETRIEVE tasks used frozen recorded_response values for acceptance without binding answers to data read from the current page, and the historical runs used mismatched viewports: Chromium 780×437/493 and Moli 1920×1080. "
            "These scores therefore describe fixed-path acceptance under the legacy protocol. They do not prove that all 90 trajectories satisfy the current fail-closed qualification rules or measure browser success at a common viewport. A corrected formal comparison requires renewed qualification and a complete rerun."
        )
    return f'<section class="callout historical-validity"><h2>{_e(heading)}</h2><p>{_e(body)}</p></section>'


def _load_artifact(ref: dict[str, Any] | None) -> Any:
    if not ref or not ref.get("path"):
        return None
    path = Path(str(ref["path"]))
    if (
        not path.is_absolute()
        or any(part.is_symlink() for part in (path, *path.parents))
        or not path.is_file()
        or _sha256(path) != ref.get("sha256")
    ):
        raise ValueError(f"evidence artifact is missing or changed: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _supplemental_diagnostic(review: dict[str, Any] | None, task_id: int) -> Any:
    ref = (review or {}).get("supplemental_diagnostic")
    if not ref:
        return None
    diagnostic = _load_artifact(ref)
    if (diagnostic.get("schema") != "lex_replay_supplemental_diagnostic/1"
            or diagnostic.get("task_id") != task_id
            or diagnostic.get("scope") not in {
                "read_only_prefix_on_current_site_not_a_replacement_score",
                "isolated_local_http_fixture_not_a_replacement_score",
            }
            or diagnostic.get("mutations_submitted") is not False):
        raise ValueError("supplemental diagnostic scope or task differs")
    return diagnostic


def _supplemental_diagnostic_html(diagnostic: dict | None, lang: str) -> str:
    if not diagnostic:
        return ""
    zh = lang == "zh"
    rows = []
    component_rows = []
    readiness_rows = []
    network_rows = []
    header_rows = []
    for method in diagnostic["observations"]:
        label = {"release": "Moli 1.1.8", "pr": "Moli · PR 640"}.get(method["method"], method["method"])
        for sample in method["observations"]:
            for request in sample["state"].get("headerRecording", []):
                cookie_label = ("有" if request["cookie_present"] else "无") if zh else ("Yes" if request["cookie_present"] else "No")
                missing = "缺失" if zh else "Missing"
                values = (label, request["api"], cookie_label, request["status"],
                          request["wire_referer_path"] or missing, request["har_referer_path"] or missing)
                header_rows.append("<tr>" + "".join(f"<td>{_e(value)}</td>" for value in values) + "</tr>")
            for request in sample["state"].get("httpReads", []):
                values = (label, request["path"], request.get("status", "—"),
                          request.get("bytes", "—"), request.get("error", ""))
                network_rows.append("<tr>" + "".join(f"<td>{_e(value)}</td>" for value in values) + "</tr>")
            readiness = sample["state"].get("readiness")
            if readiness:
                values = (label, f'{sample["delay"]} s', readiness["target"],
                          readiness["disabled"], readiness["pendingModules"],
                          readiness["formValidationReady"])
                readiness_rows.append("<tr>" + "".join(f"<td>{_e(value)}</td>" for value in values) + "</tr>")
            for component in sample["state"].get("components", []):
                values = (label, f'{sample["delay"]} s', component["initialized"],
                          ", ".join(component["labels"]), component["wizardMatches"],
                          component["stepMatches"], component["selectedMatches"], component["handlerCount"])
                component_rows.append("<tr>" + "".join(f"<td>{_e(value)}</td>" for value in values) + "</tr>")
            for control in sample["state"].get("buttons", []) + sample["state"].get("dialogs", []):
                rect = " × ".join(str(value) for value in control["rect"])
                offset_parent = control.get("offsetParent")
                values = (label, f'{sample["delay"]} s', control["text"],
                          f'{control.get("display") or "—"} / {control.get("visibility") or "—"}', rect,
                          control["rectCount"] if control.get("rectCount") is not None else "—",
                          "—" if offset_parent is None else "non-null" if offset_parent else "null")
                rows.append("<tr>" + "".join(f"<td>{_e(value)}</td>" for value in values) + "</tr>")
    headers = ("方法", "追加等待", "目标文本", "display / visibility", "宽 × 高（px）", "矩形数", "offsetParent") if zh else (
        "Method", "Additional wait", "Target text", "display / visibility", "Width × height (px)", "Client rects", "offsetParent")
    title = "补充实测：目标状态对照" if zh else "Supplemental probe: target-state comparison"
    if header_rows:
        title = "补充实测：请求发送与记录对照" if zh else "Supplemental probe: sent and recorded headers"
    scope = ("仅打开界面或搜索，没有提交表单或发送邀请。这里记录的是后续只读诊断；原始失败轨迹与成绩保持不变。" if zh else
             "The probe only opened the interface or searched; no form or invitation was submitted. These later read-only observations do not replace the original trace or score.")
    if diagnostic.get("scope") == "isolated_local_http_fixture_not_a_replacement_score":
        scope = ("独立本地 HTTP 实验，没有访问评测站点；使用冻结浏览器和正式 HAR 采集器。此处验证机制，不替换原始轨迹或成绩。" if zh else
                 "An isolated local HTTP fixture, without accessing benchmark sites, uses the frozen browsers and production HAR recorder. It tests a mechanism and does not replace original trajectories or scores.")
    scope_note = (diagnostic.get("scope_note") or {}).get(lang)
    if scope_note:
        scope += " " + str(scope_note)
    if header_rows:
        scope += (" 横向滚动可对照服务器收到的头与 HAR 记录。" if zh else
                  " Scroll horizontally to compare server-observed headers with HAR records.")
    components = ""
    if component_rows:
        component_headers = ("方法", "追加等待", "组件已初始化", "属性选项", "向导匹配", "步骤匹配", "选择匹配", "事件处理器数") if zh else (
            "Method", "Additional wait", "Component initialized", "Attribute options", "Wizard matches", "Step matches", "Selection matches", "Event handlers")
        components = ('<div class="table-scroll"><table data-component-readiness><thead><tr>'
                      + "".join(f"<th>{_e(header)}</th>" for header in component_headers)
                      + '</tr></thead><tbody>' + "".join(component_rows) + '</tbody></table></div>')
    readiness_table = ""
    if readiness_rows:
        readiness_headers = ("方法", "DOM 就绪后等待", "目标", "仍禁用", "待初始化模块", "表单校验已绑定") if zh else (
            "Method", "Wait after DOM ready", "Target", "Disabled", "Pending modules", "Form validation bound")
        readiness_table = ('<div class="table-scroll"><table data-readiness-comparison><thead><tr>'
                           + "".join(f"<th>{_e(header)}</th>" for header in readiness_headers)
                           + '</tr></thead><tbody>' + "".join(readiness_rows) + '</tbody></table></div>')
    geometry_table = (('<div class="table-scroll"><table><thead><tr>'
                       + "".join(f"<th>{_e(header)}</th>" for header in headers)
                       + '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>') if rows else "")
    network_table = ""
    if network_rows:
        network_headers = ("方法", "帖子路径", "HTTP 状态", "正文 UTF-8 字节数", "错误") if zh else (
            "Method", "Post path", "HTTP status", "Body UTF-8 bytes", "Error")
        network_table = ('<div class="table-scroll"><table data-http-read-comparison><thead><tr>'
                         + "".join(f"<th>{_e(header)}</th>" for header in network_headers)
                         + '</tr></thead><tbody>' + "".join(network_rows) + '</tbody></table></div>')
    header_table = ""
    if header_rows:
        header_headers = ("方法", "请求接口", "携带 Cookie", "HTTP 状态", "服务器收到的 Referer 路径", "HAR 中的 Referer 路径") if zh else (
            "Method", "Request API", "Cookie present", "HTTP status", "Referer path received by server", "Referer path in HAR")
        header_table = ('<div class="table-scroll"><table data-header-recording-comparison><thead><tr>'
                        + "".join(f"<th>{_e(header)}</th>" for header in header_headers)
                        + '</tr></thead><tbody>' + "".join(header_rows) + '</tbody></table></div>')
    return (f'<section data-supplemental-diagnostic><details><summary>{title}</summary><p>{_e(scope)}</p>'
            + components + readiness_table + geometry_table + network_table + header_table + '</details></section>')


def _corrected_acceptance_by_task(
    attempts: list[dict[str, Any]], *, declared_revisions: dict | None = None,
) -> dict[int, dict[str, Any]]:
    """Expose corrected evaluator expectations without replacing the frozen dataset."""
    corrected: dict[int, dict[str, Any]] = {}
    declarations = {int(key): value for key, value in (declared_revisions or {}).items()}
    versions: dict[int, list[dict]] = {}
    for attempt in attempts:
        evaluation = _load_artifact((attempt.get("evidence") or {}).get("evaluation"))
        if not evaluation or not evaluation.get("contract_correction_id"):
            continue
        task_id = int(attempt["task_id"])
        if evaluation.get("task_id") != task_id:
            raise ValueError("corrected evaluation belongs to another task")
        row = {
            "correction_id": evaluation["contract_correction_id"],
            "corrected_task_sha256": evaluation.get("corrected_task_sha256"),
            "evaluators": [
                {"evaluator": item["evaluator_name"], "expected": item["expected"]}
                for item in evaluation.get("evaluators_results") or []
                if item.get("expected") is not None
            ],
        }
        if not row["corrected_task_sha256"] or not row["evaluators"]:
            raise ValueError(f"task {task_id}: corrected evaluation lacks acceptance evidence")
        if task_id in declarations:
            method = attempt["participant_id"]
            identity = {key: row[key] for key in ("correction_id", "corrected_task_sha256")}
            if declarations[task_id].get(method) != identity:
                raise ValueError(f"task {task_id}: declared acceptance revision differs for {method}")
            groups = versions.setdefault(task_id, [])
            group = next((item for item in groups if {key: item[key] for key in row} == row), None)
            if group is None:
                groups.append({**row, "methods": [method]})
            else:
                group["methods"].append(method)
            continue
        if task_id in corrected and corrected[task_id] != row:
            raise ValueError(f"task {task_id}: corrected acceptance differs across methods")
        corrected[task_id] = row
    if set(declarations) != set(versions):
        raise ValueError("declared acceptance revisions lack evaluator evidence")
    corrected.update({task: {"versions": rows} for task, rows in versions.items()})
    return corrected


def _format_ms(value: float | None) -> str:
    return "—" if value is None else f"{value / 1000:.1f} 秒"


def _format_mib(value: float | None) -> str:
    return "—" if value is None else f"{value / 1024 / 1024:.1f} MiB"


def _metric_lines(
    metric: dict[str, Any],
    formatter: Any,
    method_id: str,
    winners: dict[str, set[str]],
    *,
    lang: str,
) -> str:
    labels = (("mean", "均值"), ("p50", "P50"), ("p95", "P95"))
    if lang == "en":
        labels = (("mean", "Mean"), ("p50", "P50"), ("p95", "P95"))
    text = "<br>".join(
        f'<span class="metric-label">{label}</span> '
        + _winner(formatter(metric.get(key)), method_id in winners[key], lang=lang)
        for key, label in labels
    )
    if "n" in metric:
        coverage = f'n={metric["n"]}'
        if "population" in metric:
            coverage += f'/{metric["population"]}; ' + ("缺失 " if lang == "zh" else "missing ") + str(metric["missing"])
        text += f"<br><small>{coverage}</small>"
    return text


def _unique_winners(
    rows: list[dict[str, Any]], key: str, *, higher: bool
) -> set[str]:
    numeric = [(row["id"], row.get(key)) for row in rows
               if type(row.get(key)) in (int, float) and math.isfinite(row[key])]
    if len(rows) < 2 or len(numeric) != len(rows):
        return set()
    best = (max if higher else min)(value for _, value in numeric)
    winners = {name for name, value in numeric if value == best}
    return winners if len(winners) == 1 else set()


def _add_interpretation_navigation(language_root: Path, lang: str) -> None:
    """Expose the optional article only after its page has been generated."""
    label = "报告解读" if lang == "zh" else "Interpretation"
    for page in language_root.rglob("*.html"):
        route = page.relative_to(language_root)
        if route.parts[0] == "en" or page.name == "article.html":
            continue
        prefix = "../" * (len(route.parts) - 1)
        current = ' aria-current="page"' if route.as_posix() == "interpretation.html" else ""
        link = f'<a data-report-interpretation href="{prefix}interpretation.html"{current}>{label}</a>'
        content = page.read_text(encoding="utf-8")
        page.write_text(content.replace("<nav>", "<nav>" + link, 1), encoding="utf-8")


def _nav(lang: str, active: str, *, depth: int, current_route: str = "index.html") -> str:
    labels = {
        "zh": (("index", "结论"), ("dataset", "评测集"), ("results", "结果分析"), ("environment", "环境与方法"), ("review", "核验记录")),
        "en": (("index", "Summary"), ("dataset", "Dataset"), ("results", "Results analysis"), ("environment", "Environment & method"), ("review", "Review history")),
    }[lang]
    local_prefix = "../" * depth
    switch = ("../../" if lang == "en" and depth else "../" if lang == "en" else "../en/" if depth else "en/") + current_route
    switch_label = "中文" if lang == "en" else "English"
    brand = "Trajectory Replay" if lang == "en" else "轨迹重放"
    theme_label = "Toggle theme" if lang == "en" else "切换主题"
    links = "".join(
        f'<a href="{local_prefix}{slug}.html"'
        + (' aria-current="page"' if slug == active else "")
        + f">{label}</a>"
        for slug, label in labels
    )
    return f'<header class="top"><a class="brand" href="{local_prefix}index.html">{brand}</a><nav>{links}</nav><a class="language" href="{switch}">{switch_label}</a><button class="theme" type="button" aria-label="{theme_label}">◐</button></header>'


def _shell(lang: str, active: str, title: str, body: str, *, depth: int = 0, current_route: str = "index.html") -> str:
    prefix = "../" * depth
    asset_prefix = prefix if lang == "zh" else "../" + prefix
    footer = (
        "WebArena-Verified 1.2.3 · 浏览器轨迹重放"
        if lang == "zh"
        else "WebArena-Verified 1.2.3 · Browser Trajectory Replay"
    )
    preview_bootstrap = r'''<script>(function(){
const base=document.querySelector('base[href]');
if(base){
  const parts=location.pathname.split('/');
  const at=parts.indexOf('preview');
  if(at>0&&parts[at-1]==='api'){
    const folders=parts.slice(at+2,-1);
    if(folders.length)base.href=new URL(folders.join('/')+'/',base.href).href;
  }
}
const css=document.querySelector('link[data-report-css]');
css.href=css.dataset.src;
})();</script>'''
    body = body.replace('<img src=', '<img data-src=')
    return f'''<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title><link rel="stylesheet" data-report-css data-src="{asset_prefix}assets/report.css">{preview_bootstrap}</head><body>{_nav(lang, active, depth=depth, current_route=current_route)}<main>{body}</main><footer>{footer}</footer><script type="application/x-report-app" data-report-app data-src="{asset_prefix}assets/app.js"></script><script>(function(){{document.querySelectorAll('img[data-src]').forEach(img=>img.src=img.dataset.src);const app=document.querySelector('script[data-report-app]');const loader=document.createElement('script');loader.src=app.dataset.src;app.replaceWith(loader)}})();</script></body></html>'''


def _winner(value: str, won: bool, *, lang: str) -> str:
    badge = "优" if lang == "zh" else "Best"
    return f'<strong class="winner">{value}<span>{badge}</span></strong>' if won else value


def _verdict_label(status: str, semantic_status: str | None, lang: str) -> str:
    if semantic_status == "unverified":
        if status == "pass":
            return "官方通过；答案不可核验" if lang == "zh" else "Official pass; answer unverifiable"
        return "官方失败；答案不可核验" if lang == "zh" else "Official fail; answer unverifiable"
    return (("通过" if status == "pass" else "失败") if lang == "zh" else status.title())


def _metrics_table(
    methods: list[dict[str, Any]], lang: str, *, show_valid_reference: bool = True
) -> str:
    pass_winners = _unique_winners(methods, "pass_rate", higher=True)
    valid_winners = _unique_winners(
        [{"id": row["id"], "value": row["valid_reference_passed"]} for row in methods],
        "value", higher=True,
    )
    metric_winners = {
        metric: {
            stat: _unique_winners(
                [{"id": row["id"], "value": row[metric][stat]} for row in methods],
                "value",
                higher=False,
            ) if metric == "action_latency" else set()
            for stat in ("mean", "p50", "p95")
        }
        for metric in ("duration", "action_latency", "cpu", "memory")
    }
    headers = (
        ("方法", "官方通过", "官方通过率", "可核验题通过", "全部任务端到端耗时", "单次动作耗时", "CPU 时间", "平均内存")
        if lang == "zh"
        else ("Method", "Official passes", "Official pass rate", "Passes on verifiable tasks", "End-to-end time, all tasks", "Per-action latency", "CPU time", "Mean memory")
    )
    if not show_valid_reference:
        headers = headers[:3] + headers[4:]
    body = []

    def time_value(value: float | None) -> str:
        if lang == "zh":
            return _format_ms(value)
        return "—" if value is None else f"{value / 1000:.1f} s"

    for row in methods:
        key = row["id"]
        cells = [
            row["label"],
            _winner(
                f'{row["passed"]}/{row["total"]}',
                key in pass_winners,
                lang=lang,
            ),
            _winner(f'{row["pass_rate"] * 100:.1f}%', key in pass_winners, lang=lang),
            _winner(
                f'{row["valid_reference_passed"]}/{row["valid_reference_total"]}',
                key in valid_winners, lang=lang,
            ),
            _metric_lines(
                row["duration"], time_value, key, metric_winners["duration"], lang=lang
            ),
            _metric_lines(
                row["action_latency"],
                time_value,
                key,
                metric_winners["action_latency"],
                lang=lang,
            ),
            _metric_lines(row["cpu"], time_value, key, metric_winners["cpu"], lang=lang),
            _metric_lines(
                row["memory"], _format_mib, key, metric_winners["memory"], lang=lang
            ),
        ]
        body.append(
            "<tr>"
            + f'<td>{_e(cells[0])}</td>'
            + f'<td data-metric="core-pass" data-metric-direction="higher" data-sort="{row["passed"]}">{cells[1]}</td>'
            + f'<td>{cells[2]}</td>'
            + (
                f'<td data-metric="verifiable-pass" data-metric-direction="higher" data-sort="{row["valid_reference_passed"]}">{cells[3]}</td>'
                if show_valid_reference
                else ""
            )
            + "".join(f"<td>{cell}</td>" for cell in cells[4:])
            + "</tr>"
        )
    return f'<div class="table-scroll"><table class="metrics"><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _resource_table(methods: list[dict[str, Any]], lang: str) -> str:
    zh = lang == "zh"
    metrics = (
        ("memory_peak", "峰值内存", "Peak memory", _format_mib),
        ("network_rx", "接收数据", "Network received", _format_mib),
        ("network_tx", "发送数据", "Network sent", _format_mib),
        ("cpu_utilization", "CPU 平均使用率", "Mean CPU utilization", lambda value: "—" if value is None else f"{value:.1f}%"),
    )
    rows = []
    for key, label_zh, label_en, formatter in metrics:
        cells = [_e(label_zh if zh else label_en)]
        for method in methods:
            cells.append(_metric_lines(method[key], formatter, method["id"], {stat: set() for stat in ("mean", "p50", "p95")}, lang=lang))
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    headers = ("资源指标" if zh else "Resource metric", *(method["label"] for method in methods))
    return f'<div class="table-scroll"><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _successful_distribution_table(methods: list[dict[str, Any]], lang: str) -> str:
    """Method-specific successful populations are descriptive, never ranked."""
    zh = lang == "zh"
    time_value = _format_ms if zh else lambda v: "—" if v is None else f"{v / 1000:.1f} s"
    metrics = (("duration", "端到端耗时", "End-to-end time", time_value),
               ("cpu", "CPU 时间", "CPU time", time_value),
               ("memory", "平均内存", "Mean memory", _format_mib),
               ("memory_peak", "峰值内存", "Peak memory", _format_mib),
               ("network_rx", "接收数据", "Network received", _format_mib),
               ("network_tx", "发送数据", "Network sent", _format_mib),
               ("cpu_utilization", "CPU 平均使用率", "Mean CPU utilization", lambda v: "—" if v is None else f"{v:.1f}%"))
    rows = []
    for name, label_zh, label_en, formatter in metrics:
        cells = [_e(label_zh if zh else label_en)] + [
            _metric_lines(method["successful"][name], formatter, method["id"],
                          {stat: set() for stat in ("mean", "p50", "p95")}, lang=lang)
            for method in methods]
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    headers = ["指标" if zh else "Metric"] + [
        f'{method["label"]} · n={method["passed"]}/{method["total"]}' for method in methods]
    return '<div class="table-scroll successful-distributions"><table><thead><tr>' + ''.join(f'<th>{_e(h)}</th>' for h in headers) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'


def _copy_failure_screenshot(attempt: dict[str, Any], output: Path, key: str) -> str | None:
    ref = (attempt.get("evidence") or {}).get("failure_screenshot")
    if not ref or not ref.get("path"):
        return None
    source = Path(str(ref["path"]))
    if (
        not source.is_absolute()
        or any(part.is_symlink() for part in (source, *source.parents))
        or not source.is_file()
        or _sha256(source) != ref.get("sha256")
    ):
        raise ValueError(f"failure screenshot is missing or changed: {key}")
    target = output / "assets/screenshots" / f"{key}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return f"assets/screenshots/{key}.png"


def _public_evidence(
    attempt: dict[str, Any], review: dict[str, Any] | None, output: Path
) -> dict[str, Any]:
    key = f'{attempt["participant_id"]}-{attempt["task_id"]}'
    trace = _load_artifact((attempt.get("evidence") or {}).get("trace")) or []
    evaluation = _load_artifact((attempt.get("evidence") or {}).get("evaluation"))
    site_reset = _load_artifact((attempt.get("evidence") or {}).get("site_reset"))
    readiness = _load_artifact((attempt.get("evidence") or {}).get("readiness"))
    failed = programmatic_failure(attempt)
    if failed["stage"] == "browser_tool_execution" and failed.get("step") is None:
        failed_trace_step = next(
            (
                step.get("index")
                for step in reversed(trace)
                if step.get("status") in {"fail", "error"}
                and isinstance(step.get("index"), int)
            ),
            None,
        )
        if failed_trace_step is not None:
            failed = {**failed, "step": failed_trace_step}
    if failed["stage"] == "official_evaluation" and evaluation:
        missing_events = [
            result for result in evaluation.get("evaluators_results") or []
            if result.get("evaluator_name") == "NetworkEventEvaluator"
            and any(
                assertion.get("assertion_name") == "missing_navigation_event"
                for assertion in result.get("assertions") or []
            )
        ]
        if missing_events:
            assertion_messages = [
                str(message)
                for result in missing_events
                for assertion in result.get("assertions") or []
                if assertion.get("assertion_name") == "missing_navigation_event"
                for message in assertion.get("assertion_msgs") or []
            ]
            trigger = next(
                (
                    step for step in reversed(trace)
                    if (step.get("arguments") or {}).get("action") in {"click", "press", "evaluate"}
                ),
                None,
            )
            failed = {
                "stage": "side_effect_not_observed",
                "step": None,
                "trigger_step": trigger.get("index") if trigger else None,
                "message": "NetworkEventEvaluator: missing_navigation_event" + (" — " + " | ".join(assertion_messages) if assertion_messages else ""),
                "expected_network": [result.get("expected") for result in missing_events],
            }
    screenshot = _copy_failure_screenshot(attempt, output, key)
    public = {
        "result": {
            "participant_id": attempt["participant_id"],
            "engine": attempt["engine"],
            "task_id": attempt["task_id"],
            "status": attempt["status"],
            "action_count": attempt["action_count"],
            "planned_actions_sha256": attempt.get("planned_actions_sha256"),
            "task_contract_sha256": attempt.get("task_contract_sha256"),
            "completed_action_count": attempt["completed_action_count"],
            "duration_ms": attempt["duration_ms"],
            "timings": attempt.get("timings") or {},
            "resource": attempt.get("resource") or {},
        },
        "programmatic_failure": failed,
        "review": (
            {
                "cause_code": review["cause_group"]["code"],
                "label_zh": review["cause_group"]["label_zh"],
                "label_en": review["cause_group"]["label_en"],
                "reason_zh": review["reason_zh"],
                "reason_en": review["reason_en"],
                "responsibility": review["cause_group"]["responsibility"],
                "remediation_owner": review["cause_group"]["remediation_owner"],
                "remediation_status": review["cause_group"]["remediation_status"],
                **{key: review["cause_group"][key] for key in (
                    "remediation_owner_zh", "remediation_owner_en",
                    "remediation_status_zh", "remediation_status_en",
                ) if key in review["cause_group"]},
                **({"revision_evidence": review["revision_evidence"]}
                   if review.get("revision_evidence") else {}),
            }
            if review
            else None
        ),
        "trajectory": trace,
        "official_evaluation": evaluation,
        "site_reset": site_reset,
        "readiness": readiness,
        "reference_validity": attempt.get("reference_validity"),
        "supplemental_diagnostic": _supplemental_diagnostic(review, int(attempt["task_id"])),
        "screenshot": screenshot,
    }
    public = _clean(public)
    _write_json(output / "data/evidence" / f"{key}.json", public)
    return public




def _detail_page(
    lang: str,
    attempt: dict[str, Any],
    task: dict[str, Any],
    method: dict[str, Any],
    evidence: dict[str, Any],
    peers: list[dict[str, Any]],
    validity_notice: str = "",
) -> str:
    failed = evidence["programmatic_failure"]
    review = evidence.get("review")
    zh = lang == "zh"
    semantic_status = (
        "unverified" if (attempt.get("reference_validity") or {}).get("status") == "unverifiable" else "valid"
    )
    status = _verdict_label(attempt["status"], semantic_status, lang)
    reason = (review.get("reason_zh") if zh else review.get("reason_en")) if review else ""
    label = (review.get("label_zh") if zh else review.get("label_en")) if review else ""
    key = f'{attempt["participant_id"]}-{attempt["task_id"]}'
    report_root = "../" if zh else "../../"
    stage_labels = {
        "browser_session_setup": ("浏览器会话初始化", "Browser session setup"),
        "browser_tool_execution": ("浏览器动作执行", "Browser action execution"),
        "evidence_finalization": ("执行证据收尾", "Evidence finalization"),
        "official_evaluator_execution": ("官方评测器执行", "Official evaluator execution"),
        "side_effect_not_observed": ("网络验收条件未满足", "Network acceptance not satisfied"),
        "official_evaluation": ("官方验收", "Official evaluation"),
        "pass": ("无", "None"),
    }
    stage = stage_labels.get(failed["stage"], (failed["stage"], failed["stage"]))[0 if zh else 1]
    failed_index = failed.get("step")
    trigger_index = failed.get("trigger_step")
    failed_action = next((str((step.get("arguments") or {}).get("action") or "") for step in evidence["trajectory"] if step.get("index") == failed_index), "")
    peer_cells = []
    for peer in peers:
        peer_key = f'{peer["participant_id"]}-{peer["task_id"]}'
        peer_status = _verdict_label(peer["status"], peer.get("semantic_status"), lang)
        peer_href = "../" + peer["detail_url"] if peer.get("detail_url") else f"{peer_key}.html"
        peer_cells.append(
            f'<a class="peer {peer["status"]}" href="{_e(peer_href)}"'
            + (' aria-current="page"' if peer_key == key else '')
            + f'><span>{_e(peer["browser"])}</span><strong>{peer_status}</strong></a>'
        )
    # A watchdog can preserve a task-level timeout when an older runner did not
    # checkpoint the active action. Keep that evidence without pretending it
    # belongs to a numbered trajectory step.
    actual_by_index = {
        index: step
        for step in evidence["trajectory"]
        if isinstance((index := step.get("index")), int) and not isinstance(index, bool)
    }
    steps = []
    for number, planned in enumerate(attempt["planned_actions"], 1):
        actual = actual_by_index.get(number)
        planned_target = planned.get("selector") or planned.get("url") or planned.get("key") or ""
        actual_arguments = (actual or {}).get("arguments") or {}
        actual_target = actual_arguments.get("selector") or actual_arguments.get("url") or actual_arguments.get("key") or ""
        actual_result = str((actual or {}).get("result") or "")
        preview = actual_result.splitlines()[0][:160] if actual_result else ""
        failed_step = failed_index == number
        actual_status = str((actual or {}).get("status") or "")
        status_text = (
            ("未执行", "Not executed") if actual is None
            else ("报错", "Error") if actual_status == "error"
            else ("完成", "Completed")
        )[0 if zh else 1]
        status_class = "status-muted" if actual is None else "status-fail" if actual_status == "error" else "status-pass"
        duration = f'{float(actual.get("duration_ms") or 0) / 1000:.2f} s' if actual else "—"
        steps.append(
            f'<article id="step-{number}" class="aligned-step {"failed-step" if failed_step else ""}">'
            f'<div class="aligned-index"><span class="step-number">{number}</span></div>'
            f'<div class="aligned-standard"><span class="mobile-step-label">{"标准轨迹" if zh else "Fixed input"}</span><strong>{_e(planned.get("action") or "")}</strong>'
            f'<code>{_e(planned_target)}</code><button type="button" data-open-step="{number}">'
            f'{"查看标准参数" if zh else "Fixed arguments"}</button></div>'
            f'<div class="aligned-actual"><span class="mobile-step-label">{"实际执行" if zh else "Actual execution"}</span><div class="aligned-actual-head"><strong>{_e(actual_arguments.get("action") or "—")}</strong>'
            f'<span class="{status_class}">{status_text}</span><span>{_e(duration)}</span></div>'
            f'<code>{_e(actual_target) if actual else "—"}</code>'
            f'<p>{_e(preview) if preview else ("没有工具返回" if zh else "No tool response")}</p>'
            f'<button type="button" data-open-step="{number}">'
            f'{"查看实际参数、日志与报错" if zh else "Actual arguments, logs and error"}</button></div></article>'
        )
    screenshot = (
        f'<figure><a href="{report_root}{evidence["screenshot"]}"><img src="{report_root}{evidence["screenshot"]}" alt="{"失败后的最后页面" if zh else "Last page after failure"}"></a><figcaption>{"失败后保存的最后页面；不等同于错误发生瞬间。点击查看原图。" if zh else "Last page saved after failure, not necessarily the error instant. Open the full image."}</figcaption></figure>'
        if evidence.get("screenshot")
        else ""
    )
    failure_panel = ""
    if attempt["status"] != "pass":
        unknown_active_action = (
            failed_index is None
            and trigger_index is None
            and int(attempt.get("completed_action_count") or 0)
            < int(attempt.get("action_count") or 0)
        )
        step_link = f'<a href="#step-{failed_index}">{"跳到失败动作" if zh else "Go to failed action"} #{failed_index}</a>' if failed_index else f'<a href="#step-{trigger_index}">{"查看触发动作" if zh else "Inspect trigger action"} #{trigger_index}</a>' if trigger_index else f'<a href="#trajectory">{"查看已保存轨迹证据" if zh else "View saved trajectory evidence"}</a>'
        action_locator = f'{failed_action} #{failed_index}' if failed_index else ((f'{("触发动作" if zh else "Trigger action")} #{trigger_index}; {("请求未满足验收条件" if zh else "request does not meet acceptance")}') if trigger_index else (("活动动作未记录" if zh else "Active action not recorded") if unknown_active_action else ("所有动作执行后" if zh else "After all actions")))
        expected_network = f'<h3>{"要求匹配的网络条件" if zh else "Required network match"}</h3><pre>{_e(json.dumps(failed["expected_network"], ensure_ascii=False, indent=2))}</pre>' if failed.get("expected_network") else ""
        failure_panel = f'''<section id="diagnosis"><h2>{"失败定位与处理" if zh else "Failure diagnosis and treatment"}</h2><div class="diagnosis-panel"><div class="diagnosis-facts"><div><span>{"失败环节" if zh else "Failure stage"}</span><strong>{_e(stage)}</strong></div><div><span>{"失败位置" if zh else "Failure location"}</span><strong>{_e(action_locator)}</strong></div><div><span>{"复核分类" if zh else "Reviewed category"}</span><strong>{_e(label)}</strong></div></div><h3>{"程序原始判定" if zh else "Exact programmatic finding"}</h3><pre>{_e(failed["message"])}</pre>{expected_network}<h3>{"复核判断与依据" if zh else "Reviewed cause and basis"}</h3><p>{_e(reason)}</p><p>{"处理方" if zh else "Owner"}: {_e(review.get(f"remediation_owner_{lang}", review.get("remediation_owner")) if review else "—")} · {"状态" if zh else "Status"}: {_e(review.get(f"remediation_status_{lang}", review.get("remediation_status")) if review else "—")}</p>{step_link}</div></section>'''
    failure_panel += _supplemental_diagnostic_html(evidence.get("supplemental_diagnostic"), lang)
    facts = [
        (("完成动作", "Actions completed"), f'{attempt["completed_action_count"]}/{attempt["action_count"]}'),
        (("端到端耗时", "End-to-end time"), _format_ms(attempt.get("duration_ms")) if zh else f'{attempt.get("duration_ms", 0) / 1000:.1f} s'),
        (("CPU 时间", "CPU time"), _format_ms((attempt.get("resource") or {}).get("cpu_ms")) if zh else f'{((attempt.get("resource") or {}).get("cpu_ms") or 0) / 1000:.1f} s'),
        (("平均内存", "Mean memory"), _format_mib((attempt.get("resource") or {}).get("memory_mean_bytes"))),
    ]
    fact_html = "".join(f'<div><span>{_e(label[0 if zh else 1])}</span><strong>{_e(value)}</strong></div>' for label, value in facts)
    validity = attempt.get("reference_validity") or {}
    validity_reason = validity.get("reason_zh" if zh else "reason_en") or validity.get("reason") or ""
    validity_panel = (
        f'<section class="callout"><h2>{"答案可核验性" if zh else "Reference answer validity"}</h2>'
        f'<p>{_e(_clean(validity_reason)) if validity_reason else ("站点证据不能唯一确认参考答案；官方得分不证明题意正确。" if zh else "Site evidence cannot uniquely establish the answer; an official pass does not prove task correctness.")}</p></section>'
        if semantic_status == "unverified" else ""
    )
    viewer_file = ("data/evidence/" if zh else "../data/evidence/") + key + ".json"
    body = f'''
    <section class="detail-hero"><p class="eyebrow">{_e(key)}</p><h1>{_e(method["label"])} · {"任务" if zh else "Task"} #{attempt["task_id"]}</h1><p class="task-intent">{_e(task["task"]["intent"])}</p><div class="detail-links"><a href="../task/{attempt["task_id"]}.html">{"查看任务定义与标准轨迹" if zh else "Task contract and fixed trajectory"}</a><a href="../results.html">{"返回结果筛选" if zh else "Back to results"}</a></div></section>
    <section class="compact-section"><h2>{"同题方法结果" if zh else "All method results for this task"}</h2><div class="peer-list">{''.join(peer_cells)}</div></section>
    <section class="compact-section"><div class="verdict-line {attempt["status"]}"><strong>{status}</strong><span>{_e(stage) if attempt["status"] != "pass" else ("固定轨迹及官方验收均通过" if zh else "Fixed trajectory and official evaluation passed")}</span></div><div class="fact-strip">{fact_html}</div></section>
    {validity_panel}
    {failure_panel}
    <section id="trajectory"><h2>{"本方法轨迹与实际执行" if zh else "This method's trajectory and execution"}</h2><p>{"左侧是本方法的冻结输入，右侧是实际操作。不同方法的合格轨迹可能不同；点击任一步读取完整参数、工具返回、日志和原始报错。" if zh else "The left column is this method's frozen input and the right column its execution. Qualified paths can differ between methods. Open a step for exact arguments, tool response, logs and errors."}</p><div class="trajectory-columns"><span>#</span><strong>{"本方法输入" if zh else "Method input"}</strong><strong>{"实际执行" if zh else "Actual execution"}</strong></div><div class="trace-list">{''.join(steps)}</div></section>
    <dialog id="step-dialog" data-evidence-url="{report_root}data/evidence/{key}.json" data-standard-url="{report_root}data/trajectory/{key}.json"><div class="dialog-head"><strong data-step-title></strong><button type="button" data-close-step aria-label="{'关闭逐步证据' if zh else 'Close step evidence'}">×</button></div><div class="dialog-body"><p data-step-status></p><div class="step-dialog-grid"><div><h3>{"本方法输入参数" if zh else "Method input arguments"}</h3><pre data-step-planned></pre></div><div><h3>{"实际动作参数" if zh else "Actual action arguments"}</h3><pre data-step-actual></pre></div></div><h3>{"工具返回、日志与报错" if zh else "Tool response, logs and error"}</h3><pre data-step-result></pre><div data-step-finding hidden><h3>{"程序原始失败判定" if zh else "Exact programmatic failure"}</h3><pre data-step-error></pre></div><p data-step-image-wrap hidden><a data-step-image target="_blank" rel="noopener">{"查看最后观测页面（并非该步截图）" if zh else "Open last observed page (not a screenshot of this step)"}</a></p><p><a href="../evidence-viewer.html?file={viewer_file}">{"查看完整执行证据" if zh else "Inspect complete execution evidence"}</a></p></div></dialog>
    <section id="evidence"><h2>{"验收、资源与页面证据" if zh else "Evaluation, resources, and page evidence"}</h2><div class="evidence-actions"><a href="../evidence-viewer.html?file={viewer_file}">{"网页查看完整证据" if zh else "Inspect complete evidence in the browser"}</a><a href="{report_root}data/evidence/{key}.json">{"下载净化 JSON" if zh else "Download sanitized JSON"}</a></div>{screenshot}</section>'''
    title = (
        f"{method['label']} · 任务 #{attempt['task_id']}"
        if zh
        else f"{method['label']} · Task #{attempt['task_id']}"
    )
    return _shell(lang, "results", title, validity_notice + body, depth=1, current_route=f"result/{key}.html")


def _task_page(
    lang: str, task: dict[str, Any], peers: list[dict[str, Any]], validity_notice: str = "",
    reference_validity: dict[str, Any] | None = None,
    corrected_acceptance: dict[str, Any] | None = None,
) -> str:
    zh = lang == "zh"
    task_id = int(task["task_id"])
    source_kind = str((task.get("source") or {}).get("kind") or "")
    source_label = (
        ("来自已通过的自主浏览器执行", "From a successful autonomous browser run")
        if source_kind == "authoritative_successful_browser_use_execution"
        else ("修复后按记录的协议在基准浏览器验证", "Repaired and checked on the reference browser under the recorded protocol")
    )[0 if zh else 1]
    peer_rows = "".join(
        f'<tr><td><a href="../{_e(peer.get("detail_url") or "result/" + peer["participant_id"] + "-" + str(task_id) + ".html")}">{_e(peer["browser"])}</a></td>'
        f'<td class="status-{peer["status"]}">{_verdict_label(peer["status"], peer.get("semantic_status"), lang)}</td>'
        f'<td>{peer["completed_actions"]}/{peer["action_count"]}</td>'
        f'<td><a href="../{_e(peer.get("detail_url") or "result/" + peer["participant_id"] + "-" + str(task_id) + ".html")}{("#diagnosis" if peer["status"] != "pass" else "#trajectory")}">{"查看诊断与执行轨迹" if zh else "Diagnosis and executed actions"}</a></td></tr>'
        for peer in peers
    )
    planned_rows = []
    for index, call in enumerate(task.get("tool_calls") or [], 1):
        action = str(call.get("action") or "")
        target = str(call.get("selector") or call.get("url") or call.get("key") or "")
        planned_rows.append(
            f'<tr><td>{index}</td><td><code>{_e(action)}</code></td><td><code>{_e(target)}</code></td>'
            f'<td><details><summary>{"查看完整参数" if zh else "Full arguments"}</summary><pre>{_e(json.dumps(_clean(call), ensure_ascii=False, indent=2))}</pre></details></td></tr>'
        )
    contract = task.get("evaluation_input") or {}
    correction_panel = ""
    if corrected_acceptance:
        correction_panel = (
            '<section data-corrected-acceptance><h2>'
            + ("本轮实际验收依据" if zh else "Acceptance used in this comparison")
            + '</h2><p>'
            + ("以下为保存的评分器证据中的修正验收字段，已按评分器规则标准化；下方保留历史数据集输入供对照。"
               if zh else "These corrected acceptance fields come from the saved evaluator evidence, normalized by the evaluator. The historical dataset input is retained below for comparison.")
            + '</p><pre>'
            + _e(json.dumps(_clean(corrected_acceptance), ensure_ascii=False, indent=2))
            + '</pre></section>'
        )
    contract_type = str(contract.get("task_type") or "")
    contract_summary = ({"RETRIEVE": "信息提取", "NAVIGATE": "页面导航", "MUTATE": "页面操作"}.get(contract_type, contract_type) if zh else contract_type) or "—"
    validity_panel = (
        f'<section class="callout"><h2>{"答案无法独立核验" if zh else "Answer cannot be independently verified"}</h2>'
        f'<p>{"站点证据无法唯一确认本题参考答案；官方验收得分与题意正确性分开解读。" if zh else "Site evidence does not uniquely establish this task’s reference answer. Official evaluator scores are separate from answer validity."}</p></section>'
        if (reference_validity or {}).get("status") == "unverifiable" else ""
    )
    body = f'''<section class="detail-hero"><p class="eyebrow">WebArena-Verified · #{task_id}</p><h1>{"任务定义与标准轨迹" if zh else "Task contract and fixed trajectory"}</h1><p class="task-intent">{_e(task["task"]["intent"])}</p><div class="detail-links"><a href="../dataset.html">{"返回评测集" if zh else "Back to dataset"}</a><a href="../results.html">{"筛选实测结果" if zh else "Explore measured results"}</a></div></section>
        {validity_panel}
        {correction_panel}
        <section class="compact-section"><div class="fact-strip"><div><span>{"站点" if zh else "Sites"}</span><strong>{_e(", ".join(task["task"].get("sites") or []))}</strong></div><div><span>{"轨迹来源" if zh else "Trajectory source"}</span><strong>{source_label}</strong></div><div><span>{"计划动作" if zh else "Planned actions"}</span><strong>{len(task.get("tool_calls") or [])}</strong></div><div><span>{"任务类型" if zh else "Task type"}</span><strong>{contract_summary}</strong></div></div></section>
    <section><h2>{"同题所有方法结果" if zh else "All method outcomes on this task"}</h2><div class="table-scroll"><table class="task-peer-table"><thead><tr><th>{"方法" if zh else "Method"}</th><th>{"结果" if zh else "Verdict"}</th><th>{"完成动作" if zh else "Completed actions"}</th><th>{"证据" if zh else "Evidence"}</th></tr></thead><tbody>{peer_rows}</tbody></table></div></section>
    <section><h2>{"评测集基准轨迹" if zh else "Dataset reference path"}</h2><p>{"这里保留评测集的基准动作；各方法实际采用的冻结输入及执行结果见对应结果页。" if zh else "This is the dataset reference path. Each method's frozen input and actual execution appear on its result page."}</p><div class="table-scroll"><table class="planned-table"><thead><tr><th>#</th><th>{"动作" if zh else "Action"}</th><th>{"目标" if zh else "Target"}</th><th>{"参数" if zh else "Arguments"}</th></tr></thead><tbody>{''.join(planned_rows)}</tbody></table></div></section>
    <section><h2>{("历史数据集验收输入" if zh else "Historical dataset evaluation input") if corrected_acceptance else ("验收输入" if zh else "Evaluation input")}</h2><p>{("修正前保留的字段；本轮实际评分采用上方列出的修正依据。" if zh else "Retained fields before correction; this comparison uses the corrected acceptance above.") if corrected_acceptance else ("供官方 evaluator 判分；不是 Agent 提示词，也不是唯一正确路径。" if zh else "Used by the official evaluator, not an Agent prompt or the only correct path.")}</p><details><summary>{"查看答案与验收字段" if zh else "Inspect answer and acceptance fields"}</summary><pre>{_e(json.dumps(_clean(contract), ensure_ascii=False, indent=2))}</pre></details></section>'''
    title = f"任务 #{task_id} · 评测集" if zh else f"Task #{task_id} · Dataset"
    return _shell(lang, "dataset", title, validity_notice + body, depth=1, current_route=f"task/{task_id}.html")


def _assets(output: Path) -> None:
    css = r''':root{--bg:#f4f7fb;--panel:#fff;--ink:#102235;--muted:#60748a;--line:#d4e0ec;--accent:#0969da;--good:#087f4f;--bad:#c52c45;--wash:#eaf3ff}html[data-theme=dark]{--bg:#0c1520;--panel:#121f2d;--ink:#eef5fb;--muted:#9db0c2;--line:#2b4054;--accent:#62a8ff;--good:#4ed29b;--bad:#ff7d91;--wash:#142d49}*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 Inter,ui-sans-serif,system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}.top{height:70px;display:flex;align-items:center;gap:28px;padding:0 max(24px,calc((100% - 1440px)/2));background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:10}.brand{font-weight:900;color:var(--ink);text-decoration:none;white-space:nowrap}.top nav{display:flex;gap:22px;overflow:auto;flex:1}.top nav a,.language{color:var(--accent);text-decoration:none;white-space:nowrap}.top nav a[aria-current=page]{color:var(--ink);font-weight:800;border-bottom:3px solid var(--ink)}.theme{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:50%;width:38px;height:38px}main,footer{width:min(1440px,calc(100% - 48px));margin:auto}footer{padding:36px 0;color:var(--muted);border-top:1px solid var(--line)}section{padding:34px 0}.hero{padding:46px 0 28px}.hero h1,.detail-hero h1{font-size:clamp(36px,5vw,68px);line-height:1.08;margin:8px 0 14px;letter-spacing:-.04em}.hero p,.detail-hero p{max-width:880px;color:var(--muted);font-size:18px}.eyebrow{text-transform:uppercase;letter-spacing:.14em;color:var(--accent)!important;font-size:13px!important;font-weight:800}h2{font-size:30px;margin:0 0 16px}.table-scroll{overflow:auto;border:1px solid var(--line);border-radius:16px;background:var(--panel)}table{border-collapse:collapse;width:100%;min-width:900px}th,td{text-align:left;vertical-align:top;padding:14px 16px;border-bottom:1px solid var(--line)}th{background:color-mix(in srgb,var(--wash) 70%,var(--panel));font-size:14px;position:sticky;top:0}td{overflow-wrap:anywhere}.metrics td{min-width:150px}.metric-label{display:inline-block;width:42px;color:var(--muted);font-size:13px}.winner{color:var(--good)}.winner>span{display:inline-grid;place-items:center;margin-left:7px;min-width:24px;height:24px;padding:0 6px;border-radius:12px;background:var(--good);color:white;font-size:12px}.callout{padding:16px 18px;border-left:4px solid var(--accent);background:var(--panel);border-radius:0 12px 12px 0}.summary-grid,.diagnosis-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}.summary-grid article,.diagnosis-grid article{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;min-height:150px}.summary-grid strong,.diagnosis-grid strong{display:block;font-size:24px;margin:8px 0}.summary-grid span,.diagnosis-grid span{color:var(--muted);font-size:13px}.verdict.fail{border-left:5px solid var(--bad)}.verdict.pass{border-left:5px solid var(--good)}.filters{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}.filters input,.filters select{padding:10px 12px;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:9px;min-width:170px}.inventory{height:620px;overflow:auto}.inventory table{min-width:1500px}.inventory tbody tr{cursor:pointer}.inventory tbody tr:hover{background:var(--wash)}.status-pass{color:var(--good);font-weight:800}.status-fail{color:var(--bad);font-weight:800}.failed-step{background:color-mix(in srgb,var(--bad) 10%,var(--panel))}.failed-step td:first-child{border-left:4px solid var(--bad)}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto;background:#071923;color:#dff4ff;padding:14px;border-radius:10px;font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}figure{margin:22px 0}figure img{display:block;max-width:100%;border:1px solid var(--line);border-radius:12px}figcaption{color:var(--muted);font-size:13px}.evidence-actions{display:flex;gap:12px;flex-wrap:wrap}.evidence-actions a{padding:10px 14px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--accent);text-decoration:none}.detail-hero{padding-top:40px}.viewer{height:75vh}.viewer pre{height:100%;max-height:none}dialog{width:min(850px,calc(100% - 32px));border:1px solid var(--line);border-radius:16px;background:var(--panel);color:var(--ink);padding:0}dialog::backdrop{background:#07111bbf}.dialog-head{display:flex;justify-content:space-between;padding:18px;border-bottom:1px solid var(--line)}.dialog-body{padding:18px}.dialog-head button{border:0;background:none;color:var(--ink);font-size:24px}.mermaid{width:100%;max-width:100%;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:24px;overflow:auto}.flow-fallback{display:flex;align-items:stretch;gap:10px;overflow:auto;padding:18px;background:var(--panel);border:1px solid var(--line);border-radius:14px}.flow-node{min-width:150px;padding:14px;border-radius:10px;background:var(--wash)}.flow-node strong{display:block;color:var(--accent);margin-bottom:6px}.flow-arrow{display:grid;place-items:center;color:var(--muted);font-size:24px}@media(max-width:720px){.top{padding:0 16px;gap:14px}.top nav{gap:14px}main,footer{width:calc(100% - 28px)}section{padding:26px 0}.hero h1,.detail-hero h1{font-size:36px}.inventory{height:560px}.language{display:none}}'''
    css = css.replace(".language{display:none}", "")
    css += r'''
html{scroll-padding-top:88px}a{color:var(--accent)}button,summary{cursor:pointer}
.top nav a{padding:20px 0 15px}.top nav a:hover{text-decoration:underline}
.hero{padding:54px 0 38px;border-bottom:1px solid var(--line)}.hero h1{max-width:1100px}
.compact-section{padding:22px 0}.detail-hero{padding:40px 0 28px;border-bottom:1px solid var(--line)}
.detail-hero h1{font-size:clamp(34px,4vw,56px)}.task-intent{max-width:1040px!important;color:var(--ink)!important;line-height:1.55}
.detail-links,.evidence-actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:16px}
.detail-links a,.evidence-actions a,.dialog-body .evidence-actions a{display:inline-flex;align-items:center;min-height:42px;padding:9px 13px;border:1px solid var(--line);background:var(--panel);border-radius:9px;text-decoration:none;font-weight:700}
.detail-links a:hover,.evidence-actions a:hover{border-color:var(--accent);background:var(--wash)}
.peer-list{display:flex;flex-wrap:wrap;gap:12px;padding:3px 1px}.peer{display:flex;flex:1 1 170px;flex-direction:column;gap:3px;min-width:170px;padding:12px 16px;border:1px solid var(--line);border-radius:12px;background:var(--panel);text-decoration:none;color:var(--ink)}
.peer strong{color:var(--good)}.peer.fail strong{color:var(--bad)}.peer[aria-current=page]{border:2px solid var(--accent);background:var(--wash)}
.verdict-line{display:flex;align-items:center;gap:14px;padding:15px 18px;border-radius:12px;background:color-mix(in srgb,var(--good) 8%,var(--panel));border-left:5px solid var(--good)}
.verdict-line.fail{background:color-mix(in srgb,var(--bad) 9%,var(--panel));border-color:var(--bad)}.verdict-line strong{font-size:25px;color:var(--good)}.verdict-line.fail strong{color:var(--bad)}
.fact-strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:12px}.fact-strip>div{padding:13px 16px;background:var(--panel);border:1px solid var(--line);border-radius:11px;min-height:84px}
.fact-strip span,.diagnosis-facts span{display:block;color:var(--muted);font-size:13px}.fact-strip strong,.diagnosis-facts strong{display:block;margin-top:7px;font-size:16px;line-height:1.35;overflow-wrap:anywhere}
.diagnosis-panel{background:var(--panel);border:1px solid var(--line);border-left:5px solid var(--bad);border-radius:14px;padding:20px 24px;max-width:1120px}
.diagnosis-panel h3{margin:20px 0 8px}.diagnosis-panel p{max-width:1000px}.diagnosis-panel pre,.dialog-body pre{max-height:360px;white-space:pre-wrap}
.diagnosis-facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}.diagnosis-facts>div{padding:12px;background:var(--wash);border-radius:10px}
.trace-list{display:grid;grid-template-columns:minmax(0,1fr);gap:9px;max-width:1220px}.trace-step{min-width:0;padding:13px 18px;background:var(--panel);border:1px solid var(--line);border-radius:12px}
.trace-step.failed-step{border:2px solid var(--bad);background:color-mix(in srgb,var(--bad) 6%,var(--panel))}.step-heading{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.step-number{display:grid;place-items:center;width:29px;height:29px;background:var(--wash);border-radius:50%;font-weight:800}.step-heading strong{min-width:90px}.step-heading>span:last-child{margin-left:auto;color:var(--muted)}
.step-target{margin:7px 0 0 41px;color:var(--muted);font:13px/1.55 ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere}.step-preview{margin:6px 0 0 41px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.trace-step details{margin:10px 0 0 41px}.trace-step summary,.matrix-disclosure summary{color:var(--accent);font-weight:700}.step-data{display:grid;grid-template-columns:minmax(260px,1fr) minmax(340px,2fr);gap:12px;min-width:0}.step-data>div{min-width:0}.step-data pre{max-width:100%;max-height:380px}
.dataset-table table{min-width:600px}.dataset-table td,.dataset-table th{overflow-wrap:normal;white-space:nowrap}.task-peer-table{min-width:650px}.planned-table{min-width:900px}.planned-table td:nth-child(3){min-width:320px;max-width:500px;overflow-wrap:anywhere}
.matrix-disclosure{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 18px}.matrix-disclosure[open] summary{margin-bottom:13px}.matrix-table{max-height:570px}.matrix-table table{min-width:700px}
.failure-category-matrix table{width:max-content;min-width:100%}.failure-category-matrix th,.failure-category-matrix td{text-align:center;white-space:nowrap}.failure-category-matrix th:first-child{text-align:left}.failure-category-matrix thead tr:nth-child(2) th{white-space:normal;min-width:155px;max-width:190px;line-height:1.5;word-break:normal}.failure-category-matrix a{display:inline-block;min-width:36px;padding:6px;border:1px solid var(--line);border-radius:7px;font-weight:800}#result-inventory{scroll-margin-top:80px}
.filters{align-items:end;gap:12px}.filters label{display:flex;flex-direction:column;gap:5px;font-size:13px;font-weight:700;color:var(--muted)}.filters input,.filters select{min-width:140px;max-width:260px;font-size:15px}
.inventory{height:610px}.inventory table{min-width:1060px}.inventory th,.inventory td{white-space:nowrap;overflow-wrap:normal;vertical-align:middle}.inventory th:nth-child(7),.inventory td:nth-child(7){min-width:230px;max-width:280px;white-space:normal;overflow-wrap:anywhere}.inventory tbody tr:hover{background:var(--wash)}
.inventory button{border:1px solid var(--accent);color:var(--accent);background:var(--panel);padding:6px 10px;border-radius:7px;font-weight:700;margin-left:10px}.inventory button:hover{background:var(--wash)}.result-count{color:var(--muted);font-size:14px}
dialog{max-height:90vh;overflow:auto}.dialog-head{position:sticky;top:0;background:var(--panel);z-index:1}.dialog-body{padding:20px 24px}.dialog-body h3{margin:18px 0 8px}.dialog-body .evidence-actions{margin-bottom:4px}
.viewer-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.viewer-tabs button{padding:9px 13px;border:1px solid var(--line);background:var(--panel);color:var(--accent);border-radius:8px}.viewer-tabs button[aria-selected=true]{background:var(--accent);color:white}.viewer{height:auto;min-height:50vh}.viewer pre{min-height:45vh;max-height:65vh}
@media(max-width:720px){.hero{padding:30px 0 24px}.top{height:60px}.top nav a{padding:15px 0}.detail-hero h1{font-size:34px}.step-data{grid-template-columns:minmax(0,1fr)}.step-heading>span:last-child{margin-left:0}.trace-step details,.step-target,.step-preview{margin-left:0}.filters label{flex:1 1 145px}.filters input,.filters select{min-width:0;width:100%}.diagnosis-panel{padding:16px}.fact-strip{grid-template-columns:repeat(2,minmax(0,1fr))}}
:root{--bg:#f6f8fb;--ink:#17212b;--muted:#5d6b78;--line:#d9e2ea;--accent:#1268d8;--good:#117a47;--bad:#c22e43;--wash:#e9f2ff;--panel2:#eef3f8;--shadow:0 8px 28px rgba(30,55,80,.08)}html[data-theme=dark]{--bg:#071015;--panel:#0d1a21;--ink:#edf6f7;--muted:#9aadb4;--line:#263c46;--accent:#49d6ed;--good:#67df9f;--bad:#ff6b7a;--wash:#10303a;--panel2:#122630;--shadow:none}
body{background:linear-gradient(180deg,var(--wash) 0,transparent 240px),var(--bg);font-size:15px}.top{height:58px;gap:17px;padding:0 max(20px,calc((100% - 1180px)/2));background:color-mix(in srgb,var(--panel) 92%,transparent);backdrop-filter:blur(14px)}.top nav{gap:17px}.top nav a,.language{font-size:14px}.theme{border-radius:999px;width:auto;height:auto;padding:6px 12px}main,footer{width:min(1180px,calc(100% - 40px))}section{padding:38px 0;border-top:1px solid var(--line)}.hero{padding:38px 0 30px;border:0}.hero h1{font-size:clamp(34px,5vw,58px)}h2{font-size:clamp(25px,3vw,36px);line-height:1.16}.table-scroll{border-radius:13px;box-shadow:var(--shadow)}th{background:var(--panel2);font-size:12px;white-space:nowrap}tbody tr:hover{background:var(--wash)}
.method-comparison{padding:24px;border:1px solid var(--line);border-radius:18px;background:var(--panel);box-shadow:var(--shadow)}.comparison-heading{display:flex;justify-content:space-between;align-items:start;gap:24px;margin-bottom:20px}.comparison-heading h2{margin-bottom:6px;font-size:25px}.comparison-heading p{margin:0;color:var(--muted)}.comparison-count{white-space:nowrap;padding:7px 11px;border-radius:999px;background:var(--wash);color:var(--accent);font-size:13px}.comparison-controls{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.comparison-controls label{display:grid;gap:5px;font-size:13px;font-weight:700}.comparison-controls select{width:100%;padding:10px 12px;border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:9px;font:inherit}.comparison-methods{border:0;padding:0;margin:18px 0}.comparison-methods legend{font-weight:700}.method-checks,.outcome-list{display:flex;gap:7px;flex-wrap:wrap}.compare-chip{display:flex;align-items:center;gap:6px;padding:7px 11px;border:1px solid var(--line);border-radius:999px;background:var(--bg);cursor:pointer}.compare-chip.disabled{opacity:.45;cursor:not-allowed}.compare-chip input{accent-color:var(--accent)}.outcome-pill{display:inline-block;padding:5px 9px;border-radius:8px;font-size:13px;white-space:nowrap;text-decoration:none}.outcome-pill.pass{background:color-mix(in srgb,var(--good) 12%,var(--panel));color:var(--good)}.outcome-pill.fail{background:color-mix(in srgb,var(--bad) 12%,var(--panel));color:var(--bad)}.matrix-table table{min-width:760px}.matrix-table th:nth-child(n+3),.matrix-table td:nth-child(n+3){min-width:270px}.matrix-table td{vertical-align:middle;overflow-wrap:normal}
.dataset-browser{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:24px;box-shadow:var(--shadow)}.dataset-browser h2{margin-bottom:4px}.dataset-browser>p{margin:0;color:var(--muted)}.dataset-controls{display:grid;grid-template-columns:minmax(220px,2fr) repeat(3,minmax(145px,1fr));gap:12px;margin:22px 0 10px}.dataset-controls label{display:grid;gap:5px;color:var(--muted);font-size:13px;font-weight:700}.dataset-controls input,.dataset-controls select{width:100%;padding:10px 12px;border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:9px;font:inherit}.dataset-count{color:var(--accent);font-weight:700;margin:10px 0}.dataset-table{height:590px}.dataset-table table{min-width:950px}.dataset-table td,.dataset-table th{vertical-align:middle;white-space:normal;overflow-wrap:normal}.dataset-table td:nth-child(2){min-width:190px}.dataset-table td:nth-child(3){min-width:330px;max-width:460px}.intent-preview{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.45}.dataset-table td:last-child{white-space:nowrap}.dataset-table a{font-weight:700}.dataset-actions{display:flex;gap:10px;flex-wrap:wrap}.dataset-actions a{white-space:nowrap}.dataset-table tbody tr:hover{background:var(--wash)}.dataset-empty{color:var(--muted);padding:20px 0}
.mermaid svg{min-width:850px;height:auto!important}.flow-fallback{font-size:14px}.flow-node{min-width:170px}
.review-table table{min-width:1120px}.review-table td:first-child{min-width:145px;font-weight:700}.review-table td:nth-child(n+2){min-width:230px;max-width:320px}.review-table td{overflow-wrap:anywhere}
.successful-distributions table{min-width:1100px}.successful-distributions th:first-child,.successful-distributions td:first-child{min-width:140px;white-space:nowrap}.successful-distributions th:not(:first-child),.successful-distributions td:not(:first-child){min-width:170px}
.trajectory-columns,.aligned-step{display:grid;grid-template-columns:48px minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:start}
.trajectory-columns{padding:0 18px 10px;color:var(--muted);font-size:13px}.aligned-step{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px 18px}
.aligned-step.failed-step{border:2px solid var(--bad);background:color-mix(in srgb,var(--bad) 5%,var(--panel))}.aligned-standard,.aligned-actual{min-width:0;display:grid;gap:7px}.aligned-standard{border-right:1px solid var(--line);padding-right:12px}.aligned-step code{color:var(--muted);font:13px/1.45 ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere}.aligned-step button{justify-self:start;border:0;background:transparent;color:var(--accent);font:inherit;font-weight:700;padding:4px 0;text-align:left}.aligned-actual-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.aligned-actual-head span:last-child{margin-left:auto;color:var(--muted)}.aligned-actual p{margin:0;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.status-muted{color:var(--muted)}
.mobile-step-label{display:none;color:var(--muted);font-size:12px}.step-dialog-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.step-dialog-grid>div{min-width:0}.step-dialog-grid pre{max-height:250px}.step-dialog-grid h3{margin-top:8px}.step-dialog-grid pre,[data-step-result],[data-step-error]{max-width:100%}
@media(max-width:760px){.top{padding:0 14px}.comparison-heading{display:block}.comparison-count{display:inline-block;margin-top:10px}.comparison-controls{grid-template-columns:1fr}.method-comparison{padding:16px}main,footer{width:calc(100% - 28px)}}
@media(max-width:760px){.dataset-browser{padding:16px}.dataset-controls{grid-template-columns:repeat(2,minmax(0,1fr))}.dataset-controls label:first-child{grid-column:1/-1}.dataset-table{height:510px}}
@media(max-width:760px){.trajectory-columns{display:none}.aligned-step{grid-template-columns:34px minmax(0,1fr);padding:12px}.aligned-index{grid-row:1/3}.aligned-standard{border-right:0;border-bottom:1px solid var(--line);padding:0 0 10px}.mobile-step-label{display:block}.step-dialog-grid{grid-template-columns:1fr}}
code{overflow-wrap:anywhere;word-break:break-word}
@media(max-width:720px){.top{height:auto;min-height:104px;padding:10px 16px 0;display:grid;grid-template-columns:minmax(0,1fr) auto auto;grid-template-rows:auto auto;gap:8px 12px}.brand{grid-column:1;grid-row:1;overflow:hidden;text-overflow:ellipsis}.language{grid-column:2;grid-row:1;display:inline}.theme{grid-column:3;grid-row:1}.top nav{grid-column:1/-1;grid-row:2;width:100%;gap:14px;overflow-x:auto}.top nav a{padding:10px 0 12px}}
'''
    js = r'''const lang=document.documentElement.lang;
const zh=lang==='zh';
const base=zh?'':'../';
document.documentElement.dataset.theme=localStorage.getItem('report-theme')||'light';
document.querySelector('.theme')?.addEventListener('click',()=>{
  const root=document.documentElement;
  root.dataset.theme=root.dataset.theme==='dark'?'light':'dark';
  localStorage.setItem('report-theme',root.dataset.theme);
});
const fetchJson=async path=>{const response=await fetch(path);if(!response.ok)throw Error(`HTTP ${response.status}`);return response.json()};
const datasetTable=document.querySelector('[data-dataset-table]');
if(datasetTable){
  const rows=[...datasetTable.querySelectorAll('tbody tr')];
  const controls=[...document.querySelectorAll('[data-dataset-filter]')];
  const params=new URLSearchParams(location.search);
  controls.forEach(control=>{const value=params.get(control.dataset.datasetFilter);if(value!==null)control.value=value});
  const applyDataset=()=>{
    const query=(document.querySelector('[data-dataset-filter=q]')?.value||'').trim().toLocaleLowerCase();
    let shown=0;
    rows.forEach(row=>{
      const matches=controls.every(control=>!control.value||control.dataset.datasetFilter==='q'||(control.dataset.datasetFilter==='site'?(row.dataset.sites||'').split(' ').includes(control.value):row.dataset[control.dataset.datasetFilter]===control.value))&&(!query||(row.dataset.search||row.textContent).toLocaleLowerCase().includes(query));
      row.hidden=!matches;if(matches)shown++;
    });
    document.querySelector('[data-dataset-count]').textContent=zh?`显示 ${shown} / ${rows.length} 个任务`:`Showing ${shown} / ${rows.length} tasks`;
    document.querySelector('[data-dataset-empty]').hidden=shown!==0;
    const next=new URLSearchParams();controls.forEach(control=>{if(control.value)next.set(control.dataset.datasetFilter,control.value)});
    history.replaceState(null,'',location.pathname+(next.size?'?'+next:''));
  };
  controls.forEach(control=>control.addEventListener(control.tagName==='INPUT'?'input':'change',applyDataset));
  applyDataset();
}
const matrix=document.querySelector('[data-task-matrix]');
if(matrix){
  const matrixRows=[...matrix.querySelectorAll('tbody tr')];
  const relation=document.querySelector('[data-matrix-relation]');
  relation.value='';
  const site=document.querySelector('[data-matrix-site]');
  const baseline=document.querySelector('[data-matrix-baseline]');
  const boxes=[...document.querySelectorAll('[data-matrix-method]')];
  const count=document.querySelector('[data-matrix-count]');
  const applyMatrix=()=>{
    boxes.forEach(box=>{box.disabled=box.value===baseline.value;box.closest('label')?.classList.toggle('disabled',box.disabled);if(box.disabled)box.checked=false});
    const others=boxes.filter(box=>box.checked&&!box.disabled).map(box=>box.value);
    const selected=[baseline.value,...others];
    let shown=0;
    matrixRows.forEach(row=>{
      const pass=new Set((row.dataset.pass||'').split(' ').filter(Boolean));
      const fail=new Set((row.dataset.fail||'').split(' ').filter(Boolean));
      row.querySelectorAll('[data-participant]').forEach(link=>{link.hidden=!selected.includes(link.dataset.participant)});
      const allMeasured=selected.every(engine=>pass.has(engine)||fail.has(engine));
      const matchRelation=others.length>0&&(!relation.value||(allMeasured&&((relation.value==='different'&&selected.some(engine=>pass.has(engine))&&selected.some(engine=>fail.has(engine)))||(relation.value==='baseline-pass-others-fail'&&pass.has(baseline.value)&&others.every(engine=>fail.has(engine)))||(relation.value==='baseline-fail-others-pass'&&fail.has(baseline.value)&&others.every(engine=>pass.has(engine)))||(relation.value==='all-pass'&&selected.every(engine=>pass.has(engine)))||(relation.value==='all-fail'&&selected.every(engine=>fail.has(engine)))||(relation.value==='exact-pass'&&selected.every(engine=>pass.has(engine))&&pass.size===selected.length))));
      const matchSite=!site.value||row.dataset.site===site.value;
      row.hidden=!(matchRelation&&matchSite);
      if(!row.hidden)shown++;
    });
    count.textContent=others.length?(zh?`找到 ${shown} / ${matrixRows.length} 个任务`:`Showing ${shown} / ${matrixRows.length} tasks`):(zh?'请至少选择一个对比方法':'Select at least one comparison method');
  };
  baseline.addEventListener('change',()=>{boxes.forEach(box=>box.checked=box.value!==baseline.value);applyMatrix()});
  [...boxes,relation,site].forEach(control=>control.addEventListener('change',applyMatrix));
  applyMatrix();
}
const inventory=document.querySelector('[data-results]');
if(inventory){
  const rows=[...inventory.querySelectorAll('tbody tr')];
  const controls=[...document.querySelectorAll('[data-filter]')];
  const dialog=document.querySelector('#diagnosis-dialog');
  const params=new URLSearchParams(location.search);
  let byKey={};
  controls.forEach(control=>{
    const selected=params.get(control.dataset.filter);
    if(selected!==null)control.value=selected;
  });
  function applyFilters(){
    const query=(document.querySelector('[data-filter=q]')?.value||'').toLowerCase();
    const task=params.get('task');
    let shown=0;
    rows.forEach(row=>{
      const result=byKey[row.dataset.key];
      const haystack=(row.textContent+' '+(result?.intent||'')+' '+(result?.program_error||'')+' '+(result?.reason_zh||'')+' '+(result?.reason_en||'')).toLowerCase();
      const matches=controls.every(control=>!control.value||control.dataset.filter==='q'||row.dataset[control.dataset.filter]===control.value)&&(!task||row.dataset.task===task)&&(!query||haystack.includes(query));
      row.hidden=!matches;
      if(matches)shown++;
    });
    const counter=document.querySelector('[data-result-count]');
    if(counter)counter.textContent=zh?`显示 ${shown} / ${rows.length} 条结果`:`Showing ${shown} / ${rows.length} results`;
  }
  controls.forEach(control=>control.addEventListener('input',applyFilters));
  fetchJson(base+'data/results.json').then(data=>{byKey=Object.fromEntries(data.map(item=>[item.key,item]));inventory.dataset.resultsReady='true';applyFilters()}).catch(()=>applyFilters());
  inventory.addEventListener('click',async event=>{
    if(event.target.closest('a'))return;
    const row=event.target.closest('tr[data-key]');
    if(!row||!dialog)return;
    const result=byKey[row.dataset.key];
    if(!result)return;
    const detail=result.detail_url||'result/'+result.key+'.html';
    dialog.querySelector('[data-dialog-title]').textContent=result.browser+' · '+(zh?'任务':'Task')+' #'+result.task_id;
    dialog.querySelector('[data-dialog-stage]').textContent=result.failure_stage==='browser_session_setup'?(zh?'浏览器会话初始化':'Browser session setup'):result.failure_stage==='browser_tool_execution'?(zh?'浏览器动作':'Browser action'):result.failure_stage==='evidence_finalization'?(zh?'执行证据收尾':'Evidence finalization'):result.failure_stage==='official_evaluator_execution'?(zh?'官方评测器执行':'Official evaluator execution'):result.failure_stage==='side_effect_not_observed'?(zh?'网络验收条件未满足':'Network acceptance not satisfied'):result.failure_stage==='official_evaluation'?(zh?'官方验收':'Official evaluation'):(zh?'无':'None');
    dialog.querySelector('[data-dialog-error]').textContent=zh?'正在读取完整证据…':'Loading complete evidence…';
    delete dialog.dataset.loaded;
    dialog.querySelector('[data-dialog-link]').href=detail;
    dialog.querySelector('[data-dialog-step-link]').href=detail+'#trajectory';
    dialog.showModal();
    try{
      const evidence=await fetchJson(base+'data/evidence/'+result.key+'.json');
      const step=evidence.programmatic_failure?.step;
      const trigger=evidence.programmatic_failure?.trigger_step;
      const failedAction=(evidence.trajectory||[]).find(action=>action.index===step);
      dialog.querySelector('[data-dialog-step]').textContent=step?(failedAction?.arguments?.action||'')+' #'+step:trigger?(zh?'触发动作':'Trigger action')+' #'+trigger:evidence.programmatic_failure?.stage==='browser_session_setup'?(zh?'轨迹开始前':'Before trajectory'):(zh?'动作完成后':'After actions completed');
      dialog.querySelector('[data-dialog-step-link]').href=detail+(step?'#step-'+step:trigger?'#step-'+trigger:'#trajectory');
      dialog.querySelector('[data-dialog-category]').textContent=(zh?evidence.review?.label_zh:evidence.review?.label_en)||(zh?'未复核':'Not reviewed');
      const exact=step?(failedAction?.result||evidence.programmatic_failure?.message||''):evidence.programmatic_failure?.stage==='side_effect_not_observed'?(evidence.programmatic_failure.message+'\n\nExpected network event:\n'+JSON.stringify(evidence.programmatic_failure.expected_network,null,2)):evidence.official_evaluation?JSON.stringify(evidence.official_evaluation,null,2):(evidence.programmatic_failure?.message||'');
      dialog.querySelector('[data-dialog-error]').textContent=exact;
      dialog.querySelector('[data-dialog-cause]').textContent=(zh?evidence.review?.reason_zh:evidence.review?.reason_en)||(zh?'尚无复核判断':'No reviewed cause');
      dialog.querySelector('[data-dialog-remediation]').textContent=(zh?'处理方：':'Owner: ')+(evidence.review?.[zh?'remediation_owner_zh':'remediation_owner_en']||evidence.review?.remediation_owner||'—')+' · '+(zh?'状态：':'Status: ')+(evidence.review?.[zh?'remediation_status_zh':'remediation_status_en']||evidence.review?.remediation_status||'—');
      dialog.dataset.loaded='true';
    }catch(error){dialog.querySelector('[data-dialog-error]').textContent=(zh?'证据读取失败：':'Evidence loading failed: ')+error.message;dialog.dataset.loaded='error'}
  });
  dialog?.querySelector('.dialog-head button')?.addEventListener('click',()=>dialog.close());
}
const stepDialog=document.querySelector('#step-dialog');
if(stepDialog){
  let requestId=0;
  let evidencePromise;
  let standardPromise;
  const evidence=()=>evidencePromise??=fetchJson(stepDialog.dataset.evidenceUrl);
  const standard=()=>standardPromise??=fetchJson(stepDialog.dataset.standardUrl);
  document.querySelector('#trajectory')?.addEventListener('click',async event=>{
    const button=event.target.closest('button[data-open-step]');
    if(!button)return;
    const index=Number(button.dataset.openStep);
    const current=++requestId;
    stepDialog.querySelector('[data-step-title]').textContent=(zh?'第 ':'Step ')+index+(zh?' 步':'');
    stepDialog.querySelector('[data-step-status]').textContent=zh?'正在读取逐步证据…':'Loading step evidence…';
    for(const key of ['planned','actual','result','error'])stepDialog.querySelector('[data-step-'+key+']').textContent='';
    stepDialog.querySelector('[data-step-finding]').hidden=true;
    stepDialog.querySelector('[data-step-image-wrap]').hidden=true;
    if(!stepDialog.open)stepDialog.showModal();
    try{
      const [frozen,measured]=await Promise.all([standard(),evidence()]);
      if(current!==requestId||!stepDialog.open)return;
      const planned=frozen.tool_calls[index-1];
      const actual=(measured.trajectory||[]).find(item=>item.index===index);
      if(!planned)throw Error(zh?'标准轨迹缺少该步':'Fixed trajectory is missing this step');
      stepDialog.querySelector('[data-step-planned]').textContent=JSON.stringify(planned,null,2);
      stepDialog.querySelector('[data-step-actual]').textContent=actual?JSON.stringify(actual.arguments||{},null,2):(zh?'未执行':'Not executed');
      stepDialog.querySelector('[data-step-result]').textContent=actual?(actual.result||''):(zh?'该步没有工具调用或返回。':'No tool call or response for this step.');
      if(actual?.action_diagnostics){
        const label=zh?'动作派发时的控件状态（不改变工具返回或任务成绩）':'Control state at dispatch (does not change tool status or task score)';
        stepDialog.querySelector('[data-step-result]').textContent+='\n\n'+label+'\n'+JSON.stringify(actual.action_diagnostics,null,2);
      }
      if(actual?.failure_ui_state||actual?.failure_ui_state_error){
        const observation=actual.failure_ui_state||{observation_error:actual.failure_ui_state_error};
        const label=zh?'失败后观测的页面状态（补充诊断，不替代上方原始错误）':'Page state observed after failure (supplementary diagnosis, not the original error above)';
        stepDialog.querySelector('[data-step-result]').textContent+='\n\n'+label+'\n'+JSON.stringify(observation,null,2);
      }
      stepDialog.querySelector('[data-step-status]').textContent=actual
        ?(zh?'实际状态：':'Actual status: ')+(actual.status||'—')+' · '+((Number(actual.duration_ms)||0)/1000).toFixed(2)+' s'
        :(zh?'实际状态：未执行':'Actual status: not executed');
      const finding=measured.programmatic_failure;
      if(finding&&(finding.step===index||finding.trigger_step===index)){
        stepDialog.querySelector('[data-step-finding]').hidden=false;
        stepDialog.querySelector('[data-step-error]').textContent=finding.message||'';
        if(finding.expected_network)stepDialog.querySelector('[data-step-error]').textContent+='\n\n'+JSON.stringify(finding.expected_network,null,2);
      }
      if(measured.screenshot){
        stepDialog.querySelector('[data-step-image-wrap]').hidden=false;
        stepDialog.querySelector('[data-step-image]').href=(zh?'../':'../../')+measured.screenshot;
      }
    }catch(error){
      if(current!==requestId||!stepDialog.open)return;
      stepDialog.querySelector('[data-step-status]').textContent=(zh?'逐步证据读取失败：':'Could not load step evidence: ')+error.message;
      evidencePromise=undefined;standardPromise=undefined;
    }
  });
  stepDialog.querySelector('[data-close-step]')?.addEventListener('click',()=>stepDialog.close());
  stepDialog.addEventListener('close',()=>{requestId++});
}
const viewer=document.querySelector('[data-evidence-viewer]');
if(viewer){
  const selected=new URLSearchParams(location.search).get('file');
  const safe=/^(?:\.\.\/)?data\/evidence\/[a-z0-9.-]+\.json$/.test(selected||'');
  const tabs=[...document.querySelectorAll('[data-evidence-tab]')];
  const heading=document.querySelector('[data-evidence-heading]');
  const raw=document.querySelector('[data-evidence-raw]');
  const image=document.querySelector('[data-evidence-image]');
  const render=(evidence,section)=>{
    const labels={result:zh?'结果与资源':'Result and resources',site_reset:zh?'站点重置':'Site reset',readiness:zh?'站点就绪':'Site readiness',programmatic_failure:zh?'程序定位':'Programmatic finding',review:zh?'复核原因':'Reviewed cause',trajectory:zh?'执行轨迹与完整日志':'Trajectory and full logs',official_evaluation:zh?'官方验收':'Official evaluation'};
    heading.textContent=labels[section]||section;
    viewer.textContent=JSON.stringify(evidence[section]??null,null,2);
    tabs.forEach(tab=>tab.setAttribute('aria-selected',String(tab.dataset.evidenceTab===section)));
  };
  if(!safe){viewer.textContent=zh?'没有选中证据文件。':'No evidence file selected.'}
  else fetchJson(selected).then(evidence=>{
    raw.href=selected;
    if(evidence.screenshot){image.href=base+evidence.screenshot;image.textContent=zh?'查看最后页面原图':'Open last observed page';image.hidden=false}
    tabs.forEach(tab=>tab.addEventListener('click',()=>render(evidence,tab.dataset.evidenceTab)));
    render(evidence,'result');
  }).catch(error=>viewer.textContent=(zh?'证据读取失败：':'Evidence loading failed: ')+error.message);
}'''
    (output / "assets").mkdir(parents=True, exist_ok=True)
    css += (
        "\n.diagnostic-observations table{min-width:840px}"
        ".diagnostic-observations td:first-child{white-space:nowrap;min-width:100px}"
        ".diagnostic-observations td code{overflow-wrap:anywhere}"
        ".diagnostic-timeline{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;list-style:none;padding:0}"
        ".diagnostic-timeline li{border:1px solid #888;border-radius:10px;padding:12px;min-width:0}"
        ".diagnostic-timeline strong,.diagnostic-timeline span{display:block}"
        ".fixed36-diagnostic figure img{display:block;max-width:100%;height:auto;border:1px solid var(--line);border-radius:8px}"
    )
    (output / "assets/report.css").write_text(css, encoding="utf-8")
    (output / "assets/app.js").write_text(js, encoding="utf-8")










def build_webarena_replay_report(
    *,
    results_path: str | Path,
    dataset_path: str | Path,
    review_path: str | Path,
    profile_path: str | Path,
    output_dir: str | Path,
    diagnostic_closure_path: str | Path | None = None,
    interpretation_path: str | Path | None = None,
    interpretation_audit_path: str | Path | None = None,
    include_english: bool = False,
) -> dict[str, Any]:
    languages = ("zh", "en") if include_english else ("zh",)
    profile_source = Path(profile_path)
    profile = json.loads(profile_source.read_text(encoding="utf-8"))
    methods = participant_registry(profile["methods"])
    if len(methods) < 2 or profile.get("affected_task_methods"):
        raise ValueError("current report requires complete registered methods")
    execution = profile.get("execution") or {}
    if (
        execution.get("method_parallelism") != 1
        or execution.get("task_parallelism") != 1
        or execution.get("reset_before_every_task") is not False
        or execution.get("reset_before_every_method") is not True
        or execution.get("teardown_between_methods") is not True
        or execution.get("retry_failed_task") is not False
    ):
        raise ValueError("replay report requires the declared common execution protocol")
    if diagnostic_closure_path is not None:
        raise ValueError("diagnostic-only cohorts cannot enter the replay report")
    validated = load_validated_failure_review(results_path, review_path, methods=profile["methods"])
    results = validated["results"]
    reviews = validated["by_result"]
    results_source = Path(results_path)
    dataset_source = Path(dataset_path)
    dataset = json.loads(dataset_source.read_text(encoding="utf-8"))
    if results.get("dataset_sha256") != _sha256(dataset_source):
        raise ValueError("aggregate is not bound to the selected corrected dataset")
    validity_identity = _measured_validity_identity(results, results_source, dataset_source)
    legacy_key = (
        validity_identity["results_sha256"], validity_identity["dataset_sha256"],
        validity_identity["profile_sha256"],
    )
    legacy_provenance = (
        _legacy_retrieval_provenance(results, dataset)
        if legacy_key in LEGACY_REPLAY_VALIDITY_INPUTS else None
    )
    output = Path(output_dir).resolve()
    tasks = {int(item["task_id"]): item for item in dataset["trajectories"]}
    attempts = [bind_participant(row, methods) for row in results["attempts"]]
    results = {**results, "attempts": attempts, "physical_executions": [
        bind_participant(row, methods) for row in results["physical_executions"]
    ]}
    if {(item["participant_id"], int(item["task_id"])) for item in attempts} != {
        (engine, task_id) for engine in methods for task_id in tasks
    } or len(attempts) != len(methods) * len(tasks):
        raise ValueError("report requires the complete participant-task matrix")
    for attempt in attempts:
        task_id = int(attempt["task_id"])
        task = tasks[task_id]
        contract_sha256 = _json_sha256({
            "task": task["task"], "evaluation_input": task["evaluation_input"],
        })
        if attempt.get("task_contract_sha256") != contract_sha256:
            raise ValueError(
                f"task {task_id} result was scored under another task contract"
            )
    task_validity = _reference_validity_by_task(attempts)
    require_all_reference_validity(profile, results, dataset)
    validated_actions = {
        (attempt["participant_id"], int(attempt["task_id"])): _validated_planned_actions(attempt)
        for attempt in attempts
    }
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    _assets(output)
    if legacy_provenance is not None:
        _write_json(output / "data/legacy-retrieval-provenance.json", legacy_provenance)
    corrected_acceptance = _corrected_acceptance_by_task(
        attempts, declared_revisions=profile.get("acceptance_revisions"),
    )
    for task_id, task in tasks.items():
        _write_json(output / "data/trajectory" / f"{task_id}.json", {
            "task_id": task_id,
            "tool_calls": task.get("tool_calls") or [],
        })
    for attempt in attempts:
        actions = validated_actions[(attempt["participant_id"], int(attempt["task_id"]))]
        key = f'{attempt["participant_id"]}-{attempt["task_id"]}'
        _write_json(output / "data/trajectory" / f"{key}.json", {
            "task_id": int(attempt["task_id"]),
            "participant_id": attempt["participant_id"],
            "planned_actions_sha256": attempt["planned_actions_sha256"],
            "tool_calls": actions,
        })
    comparison_methods = methods

    action_by_engine: dict[str, list[float]] = defaultdict(list)
    action_by_name: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for item in results["physical_executions"]:
        key = (str(item["participant_id"]), int(item["task_id"]))
        trace = _load_artifact((item.get("evidence") or {}).get("trace")) or []
        for step in trace:
            value = step.get("duration_ms")
            if isinstance(value, (int, float)):
                action_by_engine[key[0]].append(float(value))
                action = str((step.get("arguments") or {}).get("action") or "unknown")
                action_by_name[action][key[0]].append(float(value))

    passing_tasks = {
        task_id
        for task_id in tasks
        if all(
            next(
                item for item in attempts if item["participant_id"] == engine and item["task_id"] == task_id
            )["status"]
            == "pass"
            for engine in methods
        )
    }
    method_rows = []
    for engine, method in methods.items():
        rows = [item for item in attempts if item["participant_id"] == engine]
        passes = sum(item["status"] == "pass" for item in rows)
        distributions = _outcome_metrics(rows)
        successful = _outcome_metrics([item for item in rows if item["status"] == "pass"])
        duration = distributions["duration"]
        action_latency = _stat(action_by_engine[engine])
        cpu = distributions["cpu"]
        memory = distributions["memory"]
        method_rows.append(
            {
                "id": method["id"],
                "participant_id": engine,
                "engine": method["engine"],
                "label": method["label"],
                "passed": passes,
                "valid_reference_passed": sum(
                    item["status"] == "pass" and task_validity[int(item["task_id"])] == "valid"
                    for item in rows
                ),
                "valid_reference_total": sum(
                    task_validity[int(item["task_id"])] == "valid" for item in rows
                ),
                "total": len(rows),
                "pass_rate": passes / len(rows),
                "duration": duration,
                "duration_mean": duration["mean"],
                "action_latency": action_latency,
                "action_mean": action_latency["mean"],
                "cpu": cpu,
                "cpu_mean": cpu["mean"],
                "memory": memory,
                "memory_mean": memory["mean"],
                "memory_peak": distributions["memory_peak"],
                "network_rx": distributions["network_rx"],
                "network_tx": distributions["network_tx"],
                "cpu_utilization": distributions["cpu_utilization"],
                "successful": successful,
                "physical_executions": len(
                    [item for item in results["physical_executions"] if item["participant_id"] == engine]
                ),
            }
        )

    public_rows = []
    public_evidence: dict[tuple[str, int], dict[str, Any]] = {}
    for attempt in attempts:
        key = (attempt["participant_id"], int(attempt["task_id"]))
        task = tasks[key[1]]
        review = reviews.get(key)
        evidence = _public_evidence(attempt, review, output)
        public_evidence[key] = evidence
        failure = evidence["programmatic_failure"]
        cause = review["cause_group"] if review else {}
        row = {
            "key": f"{key[0]}-{key[1]}",
            "participant_id": key[0],
            "engine": attempt["engine"],
            "browser": methods[key[0]]["label"],
            "task_id": key[1],
            "intent": task["task"]["intent"],
            "sites": task["task"].get("sites") or [],
            "status": attempt["status"],
            "task_contract_sha256": attempt["task_contract_sha256"],
            "semantic_status": (
                "unverified" if task_validity[key[1]] == "unverifiable" else "valid"
            ),
            "duration_ms": attempt["duration_ms"],
            "completed_actions": attempt["completed_action_count"],
            "action_count": attempt["action_count"],
            "failure_stage": failure["stage"],
            "program_error": failure["message"],
            "cause_zh": cause.get("label_zh", ""),
            "cause_en": cause.get("label_en", ""),
            "cause_code": cause.get("code", ""),
            "reason_zh": review.get("reason_zh", "") if review else "",
            "reason_en": review.get("reason_en", "") if review else "",
        }
        public_rows.append(row)
    rows_by_task = defaultdict(list)
    for row in public_rows:
        rows_by_task[int(row["task_id"])].append(row)
    for attempt in attempts:
        key = (str(attempt["participant_id"]), int(attempt["task_id"]))
        row = next(item for item in rows_by_task[key[1]] if item["participant_id"] == key[0])
        for lang in languages:
            target = output / ("result" if lang == "zh" else "en/result") / f"{row['key']}.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                _detail_page(lang, attempt, tasks[key[1]], methods[key[0]], public_evidence[key], rows_by_task[key[1]], _historical_replay_validity_notice(**validity_identity, lang=lang)), encoding="utf-8"
            )
    for task_id, task in tasks.items():
        for lang in languages:
            target = output / ("task" if lang == "zh" else "en/task") / f"{task_id}.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            matching_attempt = next(item for item in attempts if int(item["task_id"]) == task_id)
            target.write_text(_task_page(lang, task, rows_by_task[task_id], _historical_replay_validity_notice(**validity_identity, lang=lang), matching_attempt["reference_validity"], corrected_acceptance.get(task_id)), encoding="utf-8")

    _write_json(output / "data/results.json", public_rows)
    _write_json(output / "data/metrics.json", {
        "methods": method_rows,
        "distribution_scope": "all_declared_tasks",
        "successful_scope": "per_method_success",
        "passing_intersection": len(passing_tasks),
        "passing_intersection_scope": "diagnostic_only",
    })

    source_counts: dict[str, int] = defaultdict(int)
    for task in tasks.values():
        source_counts[str((task.get("source") or {}).get("kind") or "unknown")] += 1
    browser_use_source_count = source_counts["authoritative_successful_browser_use_execution"]
    repaired_source_count = source_counts["expert_repair_spec"]
    reference_engine = str((profile.get("qualification") or {}).get("reference_participant_id") or "")
    if not reference_engine:
        reference_engine = resolve_participant_id(
            {"engine": (profile.get("qualification") or {}).get("reference_engine")}, methods
        )
    reference_method = methods.get(reference_engine)
    reference_row = next(
        (item for item in method_rows if item["participant_id"] == reference_engine),
        None,
    )
    if reference_method is None or reference_row is None:
        raise ValueError("profile qualification reference participant must be registered")
    reference_label = _e(reference_method["label"])
    method_count = len(methods)
    browser_count = len({method["engine"] for method in methods.values()})
    reviewed_failure_count = len(reviews)
    unverifiable_ids = sorted(task_id for task_id, status in task_validity.items() if status == "unverifiable")
    valid_reference_count = len(tasks) - len(unverifiable_ids)

    for lang in languages:
        zh = lang == "zh"
        root = output if zh else output / "en"
        root.mkdir(exist_ok=True)
        action_headers = ["动作" if zh else "Action", *[item["label"] for item in methods.values()]]
        action_rows = []
        for action in sorted(action_by_name):
            action_metrics = {
                engine: _stat(action_by_name[action][engine]) for engine in methods
            }
            action_winners = {
                stat: _unique_winners(
                    [
                        {"id": methods[engine]["id"], "value": metric[stat]}
                        for engine, metric in action_metrics.items()
                    ],
                    "value",
                    higher=False,
                )
                for stat in ("mean", "p50", "p95")
            }
            cells = []
            for engine in methods:
                metric = action_metrics[engine]
                text = _metric_lines(
                    metric,
                    (
                        lambda value, is_zh=zh: _format_ms(value)
                        if is_zh
                        else ("—" if value is None else f"{value / 1000:.1f} s")
                    ),
                    methods[engine]["id"],
                    action_winners,
                    lang=lang,
                )
                calls_label = "次调用" if zh else "calls"
                cells.append(f"<td>{text}<br><small>{metric['n']} {calls_label}</small></td>")
            action_rows.append(f'<tr><td><code>{_e(action)}</code></td>{"".join(cells)}</tr>')
        action_table = f'<div class="table-scroll"><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in action_headers)}</tr></thead><tbody>{"".join(action_rows)}</tbody></table></div>'
        task_count = len(tasks)
        eyebrow = (
            f"WebArena-Verified · {task_count} 个任务 · 模型调用 0 次"
            if zh
            else f"WebArena-Verified · {task_count} tasks · model calls 0"
        )
        stage_rows = []
        for engine, method in methods.items():
            for code, label_pair in (("browser_session_setup", ("浏览器会话初始化", "Browser session setup")), ("browser_tool_execution", ("浏览器动作", "Browser action")), ("evidence_finalization", ("执行证据收尾", "Evidence finalization")), ("official_evaluator_execution", ("官方评测器执行", "Official evaluator execution")), ("side_effect_not_observed", ("网络验收条件未满足", "Network acceptance not satisfied")), ("official_evaluation", ("官方验收", "Official evaluation"))):
                count = sum(row["participant_id"] == engine and row["status"] != "pass" and row["failure_stage"] == code for row in public_rows)
                if count:
                    stage_rows.append(f'<tr><td>{_e(method["label"])}</td><td>{label_pair[0 if zh else 1]}</td><td>{count}</td><td><a href="results.html?participant={_e(engine)}&status=fail&stage={code}">{"下钻到失败结果" if zh else "Inspect failures"} →</a></td></tr>')
        stage_table = f'<div class="table-scroll stage-table"><table><thead><tr><th>{"方法" if zh else "Method"}</th><th>{"失败环节" if zh else "Failure stage"}</th><th>{"结果数" if zh else "Outcomes"}</th><th>{"逐题证据" if zh else "Per-task evidence"}</th></tr></thead><tbody>{"".join(stage_rows)}</tbody></table></div>'
        stage_section = (
            f'<section><h2>{"失败发生在哪个环节" if zh else "Where failures occurred"}</h2>'
            f'<p>{"浏览器会话初始化、轨迹动作报错与网络验收失败分别统计。网络验收失败表示工具调用返回成功，但网络记录中没有满足全部验收条件的请求：可能是请求未发生，也可能是 URL、参数或请求头不匹配。逐题详情给出相关阶段、动作、验收规则和复核原因。" if zh else "Browser-session setup, trajectory action errors, and network-acceptance failures are counted separately. In the latter case the tool call returned success, but no recorded request satisfied all criteria: the request may be absent, or its URL, parameters or headers may not match. Result details show the stage, related actions, evaluator rules, and reviewed causes."}</p>'
            + stage_table + '</section>'
        ) if stage_rows else ''
        validity_notice = _historical_replay_validity_notice(
            **validity_identity, lang=lang
        )
        unverifiable_links = ", ".join(
            f'<a href="task/{task_id}.html">#{task_id}</a>' for task_id in unverifiable_ids
        ) or "—"
        if unverifiable_ids:
            core_metrics_note = (
                f"所有方法均覆盖同一 {task_count} 题。官方通过按 {task_count} 题计；可核验题通过只计参考答案可由站点证据核验的 {valid_reference_count} 题。不可核验题：{unverifiable_links}。"
                if zh else
                f"All methods cover the same {task_count} tasks. Official passes use all {task_count}; verifiable-task passes use the {valid_reference_count} tasks whose answers are supported by site evidence. Unverifiable tasks: {unverifiable_links}. "
            )
        else:
            core_metrics_note = (
                f"所有方法均覆盖同一 {task_count} 题。验收规则及已声明修正见任务详情，独立复核范围见复核说明。"
                if zh else
                f"All methods cover the same {task_count} tasks. Task details identify acceptance rules and declared corrections; the review page states the independent review scope. "
            )
        core_metrics_note += (
            "提前失败会缩短工作量，因此总耗时和资源不按低者判优；单次动作耗时按实际工具调用统计。"
            if zh else
            "Early failures reduce work, so total time and resources are not ranked by lower values; per-action latency uses actual tool calls."
        )
        core_metrics_table = _metrics_table(
            method_rows, lang, show_valid_reference=bool(unverifiable_ids)
        )
        measurement_note = (profile.get("measurement_notes") or {}).get(lang)
        if measurement_note:
            core_metrics_note += " " + _e(measurement_note)
        summary_body = f'''<section class="hero"><p class="eyebrow">{eyebrow}</p><h1>{"浏览器轨迹重放评测" if zh else "Browser Trajectory Replay"}</h1><p>{f"{browser_count} 款浏览器组成 {method_count} 种方法配置，分别覆盖同一 {task_count} 题的冻结轨迹重放。CowAgent 只提供共同的 BrowserTool 操作层，Agent 不规划动作，模型不参与；因此结果比较的是 BrowserTool 与浏览器的组合配置，不能单独归因于浏览器内核。方法版本、轨迹与站点准备见环境说明。" if zh else f"{browser_count} browsers form {method_count} method configurations, each covering the same {task_count} frozen trajectory replays. CowAgent supplies only the shared BrowserTool action layer: no Agent plans actions and no model is called. Results compare BrowserTool–browser combinations, not browser engines in isolation. Environment documents versions, paths and site preparation."}</p><div class="detail-links"><a href="results.html">{"逐题查看结果与报错" if zh else "Explore outcomes and exact errors"}</a><a href="dataset.html">{"查看任务与标准轨迹" if zh else "Task contracts and fixed trajectories"}</a></div></section>
        {validity_notice}
        {f'<p class="evidence-actions"><a href="{"" if zh else "../"}data/legacy-retrieval-provenance.json">{"查看 74 条历史取数验收输入的逐条哈希" if zh else "Inspect hashes for all 74 historical retrieval inputs"}</a></p>' if legacy_provenance is not None else ''}
        <section><h2>{"核心指标" if zh else "Core metrics"}</h2><p class="callout">{core_metrics_note}</p>{core_metrics_table}</section>
        <section><h2>{"资源与网络" if zh else "Resources and network"}</h2><p>{f"下表覆盖全部 {task_count} 题；n 表示有观测值的样本数，缺失不计为零。真实零值保留。网络传输量和 CPU 使用率不直接判优，低数值也可能来自提前失败、缓存或等待。" if zh else f"These per-task samples cover all {task_count} tasks. Each n counts observed values; missing samples are not zero, and measured zeroes are retained. Network bytes and CPU utilization are not ranked: lower values may reflect early failure, caching, or waiting."}</p><details class="matrix-disclosure"><summary>{"展开资源与网络统计" if zh else "Open resource and network statistics"}</summary>{_resource_table(method_rows, lang)}</details></section>
        {stage_section}
        <section><h2>{"浏览器动作耗时" if zh else "Browser action latency"}</h2><p>{"均值、P50、P95 按单次调用统计；调用次数只说明工作量。下钻到结果详情可查看具体动作耗时和工具返回。" if zh else "Mean, P50, and P95 are per-call statistics; call counts only describe workload. Open a task result for the individual action timing and full tool response."}</p><details class="matrix-disclosure"><summary>{"展开各类动作耗时" if zh else "Open latency by action type"}</summary>{action_table}</details></section>'''
        summary_body += f'<section><h2>{"各方法成功任务的条件分布" if zh else "Conditional distributions on each method’s successful tasks"}</h2><p>{"各列仅含该方法成功的任务，任务集合与样本数可能不同，不用于排名，也不替代上方共同任务全集的统计。" if zh else "Each column includes only that method’s successful tasks. Task sets and sample counts may differ; these distributions are not ranked and do not replace the all-task statistics above."}</p>{_successful_distribution_table(method_rows, lang)}</section>'
        (root / "index.html").write_text(_shell(lang, "index", "浏览器轨迹重放" if zh else "Browser Trajectory Replay", summary_body, current_route="index.html"), encoding="utf-8")

        headers = ["任务" if zh else "Task", "站点" if zh else "Site", "任务目标" if zh else "Task goal", "类型" if zh else "Type", "轨迹来源" if zh else "Trajectory source", "标准动作" if zh else "Fixed actions", "下钻" if zh else "Drill down"]
        matrix_rows = []
        for task_id, task in tasks.items():
            source = str((task.get("source") or {}).get("kind") or "")
            source_code = "autonomous" if source == "authoritative_successful_browser_use_execution" else "repaired"
            source_text = ("自主执行成功" if zh else "Autonomous success") if source_code == "autonomous" else ("修复后验证" if zh else "Repaired and checked")
            task_type = str((task.get("evaluation_input") or {}).get("task_type") or "")
            type_text = ({"RETRIEVE": "信息提取", "NAVIGATE": "页面导航", "MUTATE": "页面操作"}.get(task_type, task_type) if zh else task_type) or "—"
            sites = task["task"].get("sites") or []
            intent = str(task["task"].get("intent") or "")
            action_count = len(task.get("tool_calls") or [])
            search = _e(f"{task_id} {', '.join(sites)} {intent} {task_type} {source_text}")
            matrix_rows.append(f'<tr data-task="{task_id}" data-sites="{_e(" ".join(sites))}" data-type="{_e(task_type)}" data-source="{source_code}" data-search="{search}"><td><a href="task/{task_id}.html">#{task_id}</a></td><td>{_e(", ".join(sites))}</td><td><div class="intent-preview">{_e(intent)}</div></td><td>{_e(type_text)}</td><td>{source_text}</td><td>{action_count}</td><td><div class="dataset-actions"><a href="task/{task_id}.html">{"任务与轨迹" if zh else "Task & trajectory"}</a><a href="results.html?task={task_id}&status=">{"实测结果" if zh else "Measured results"}</a></div></td></tr>')
        dataset_sites = sorted({site for task in tasks.values() for site in (task["task"].get("sites") or [])})
        dataset_site_options = "".join(f'<option value="{_e(site)}">{_e(site)}</option>' for site in dataset_sites)
        dataset_type_options = "".join(f'<option value="{code}">{_e(label_zh if zh else label_en)}</option>' for code, label_zh, label_en in (("RETRIEVE", "信息提取", "Retrieve"), ("NAVIGATE", "页面导航", "Navigate"), ("MUTATE", "页面操作", "Mutate")))
        dataset_body = f'''<section class="hero"><p class="eyebrow">WebArena-Verified · {task_count}</p><h1>{"评测集" if zh else "Dataset"}</h1><p>{f"{task_count} 个任务，每题一条固定轨迹。这里展示任务目标和验收类型；实测成败在结果分析中查看。" if zh else f"{task_count} tasks, each with one fixed trajectory. This page contains goals and acceptance types; measured verdicts appear in Results analysis."}</p></section><section><div class="dataset-browser"><h2>{"任务与标准轨迹" if zh else "Tasks and fixed trajectories"}</h2><p>{f"{browser_use_source_count} 条来自成功的自主执行，{repaired_source_count} 条经修复后按记录的协议在 {reference_label} 验证。点开任务可查看验收输入、完整轨迹和各浏览器实测证据。" if zh else f"{browser_use_source_count} came from successful autonomous runs; {repaired_source_count} were repaired and checked under the recorded protocol on {reference_label}. Open a task for its acceptance input, full trajectory, and evidence from every browser."}</p><div class="dataset-controls"><label>{"搜索任务" if zh else "Search tasks"}<input data-dataset-filter="q" type="search" placeholder="{'编号、站点或任务目标' if zh else 'ID, site, or goal'}"></label><label>{"站点" if zh else "Site"}<select data-dataset-filter="site"><option value="">{"全部" if zh else "All"}</option>{dataset_site_options}</select></label><label>{"任务类型" if zh else "Task type"}<select data-dataset-filter="type"><option value="">{"全部" if zh else "All"}</option>{dataset_type_options}</select></label><label>{"轨迹来源" if zh else "Trajectory source"}<select data-dataset-filter="source"><option value="">{"全部" if zh else "All"}</option><option value="autonomous">{"自主执行成功" if zh else "Autonomous success"}</option><option value="repaired">{"修复后验证" if zh else "Repaired and checked"}</option></select></label></div><p class="dataset-count" data-dataset-count></p><div class="table-scroll dataset-table" data-dataset-table><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{"".join(matrix_rows)}</tbody></table></div><p class="dataset-empty" data-dataset-empty hidden>{"没有符合条件的任务，请调整筛选。" if zh else "No matching tasks. Change the filters."}</p></div></section>'''
        dataset_body = dataset_body.replace(
            f"{task_count} 个任务，每题一条固定轨迹。这里展示任务目标和验收类型；实测成败在结果分析中查看。",
            f"{task_count} 个任务，共用任务目标和验收规则。这里保留评测集基准轨迹；各方法的冻结输入和实测成败见结果页。",
        ).replace(
            f"{task_count} tasks, each with one fixed trajectory. This page contains goals and acceptance types; measured verdicts appear in Results analysis.",
            f"{task_count} tasks share goals and acceptance rules. This page retains the dataset reference path; method-specific frozen inputs and measured verdicts appear on result pages.",
        ).replace("任务与标准轨迹", "任务与基准轨迹").replace("Tasks and fixed trajectories", "Tasks and reference paths")
        (root / "dataset.html").write_text(_shell(lang, "dataset", "评测集" if zh else "Dataset", validity_notice + dataset_body, current_route="dataset.html"), encoding="utf-8")

        result_rows = []
        for row in public_rows:
            cause = row["cause_zh"] if zh else row["cause_en"]
            status = _verdict_label(row["status"], row.get("semantic_status"), lang)
            stage_label = {"browser_session_setup": "浏览器会话初始化" if zh else "Browser session setup", "browser_tool_execution": "浏览器动作" if zh else "Browser action", "evidence_finalization": "执行证据收尾" if zh else "Evidence finalization", "official_evaluator_execution": "官方评测器执行" if zh else "Official evaluator execution", "side_effect_not_observed": "网络验收条件未满足" if zh else "Network acceptance not satisfied", "official_evaluation": "官方验收" if zh else "Official evaluation", "pass": "—"}.get(row["failure_stage"], row["failure_stage"])
            detail_url = row.get("detail_url") or f'result/{row["key"]}.html'
            result_rows.append(f'<tr data-key="{row["key"]}" data-task="{row["task_id"]}" data-participant="{_e(row["participant_id"])}" data-status="{row["status"]}" data-site="{_e(row["sites"][0] if row["sites"] else "")}" data-stage="{_e(row["failure_stage"])}" data-cause="{_e(row["cause_zh"] or row["cause_en"])}"><td><a href="task/{row["task_id"]}.html">#{row["task_id"]}</a><button type="button" data-open-diagnosis>{"诊断" if zh else "Diagnose"}</button></td><td><a href="{_e(detail_url)}">{_e(row["browser"])}</a></td><td>{_e(row["sites"][0] if row["sites"] else "")}</td><td class="status-{row["status"]}">{status}</td><td>{row["completed_actions"]}/{row["action_count"]}</td><td>{_e(stage_label)}</td><td>{_e(cause) if cause else "—"}</td><td><a href="{_e(detail_url)}">{"证据页" if zh else "Evidence page"}</a></td></tr>')
        browser_options = "".join(f'<option value="{_e(key)}">{_e(item["label"])}</option>' for key, item in comparison_methods.items())
        site_options = "".join(f'<option value="{_e(site)}">{_e(site)}</option>' for site in sorted({s for row in public_rows for s in row["sites"]}))
        stage_labels = {"browser_session_setup": ("浏览器会话初始化", "Browser session setup"), "browser_tool_execution": ("浏览器动作", "Browser action"), "evidence_finalization": ("执行证据收尾", "Evidence finalization"), "official_evaluator_execution": ("官方评测器执行", "Official evaluator execution"), "side_effect_not_observed": ("网络验收条件未满足", "Network acceptance not satisfied"), "official_evaluation": ("官方验收", "Official evaluation")}
        present_stages = {row["failure_stage"] for row in public_rows if row["status"] == "fail"}
        stage_options = "".join(f'<option value="{code}">{_e(stage_labels[code][0 if zh else 1])}</option>' for code in stage_labels if code in present_stages)
        causes = {row["cause_zh"]: row["cause_zh"] if zh else row["cause_en"] for row in public_rows if row["cause_zh"]}
        cause_options = "".join(f'<option value="{_e(code)}">{_e(label)}</option>' for code, label in sorted(causes.items()))
        result_matrix_rows = []
        for task_id, task in tasks.items():
            pass_links = []
            fail_links = []
            passed_engines = []
            failed_engines = []
            for engine, _method in comparison_methods.items():
                row = next((item for item in rows_by_task[task_id] if item["participant_id"] == engine), None)
                if row is None:
                    raise ValueError(f"same-task method result is missing: {engine}/{task_id}")
                label = _verdict_label(row["status"], row.get("semantic_status"), lang)
                detail_url = row.get("detail_url") or f'result/{row["key"]}.html'
                link = f'<a class="outcome-pill {row["status"]}" href="{_e(detail_url)}" data-participant="{_e(engine)}">{_e(row["browser"])} · {label}</a>'
                if row["status"] == "pass":
                    pass_links.append(link)
                    passed_engines.append(engine)
                else:
                    fail_links.append(link)
                    failed_engines.append(engine)
            site = (task["task"].get("sites") or [""])[0]
            result_matrix_rows.append(f'<tr data-site="{_e(site)}" data-pass="{_e(" ".join(passed_engines))}" data-fail="{_e(" ".join(failed_engines))}"><td><a href="task/{task_id}.html">#{task_id}</a></td><td>{_e(", ".join(task["task"].get("sites") or []))}</td><td><div class="outcome-list">{"".join(pass_links) or "—"}</div></td><td><div class="outcome-list">{"".join(fail_links) or "—"}</div></td></tr>')
        matrix_sites = "".join(f'<option value="{_e(site)}">{_e(site)}</option>' for site in sorted({(task["task"].get("sites") or [""])[0] for task in tasks.values()}))
        matrix_baselines = "".join(f'<option value="{_e(engine)}">{_e(item["label"])}</option>' for engine, item in comparison_methods.items())
        matrix_checks = "".join(f'<label class="compare-chip"><input type="checkbox" data-matrix-method value="{_e(engine)}" checked>{_e(item["label"])}</label>' for engine, item in comparison_methods.items())
        result_matrix = f'<section class="method-comparison"><div class="comparison-heading"><div><h2>{"同题方法对比" if zh else "Same-task method comparison"}</h2><p>{"按官方通过和失败分组展示所有方法；点开任一结果查看该方法的轨迹与执行证据。" if zh else "All methods are grouped by official pass and fail. Open any outcome for its method-specific path and execution evidence."}</p></div><span class="comparison-count" data-matrix-count></span></div><div class="comparison-controls"><label>{"基准方法" if zh else "Baseline method"}<select data-matrix-baseline>{matrix_baselines}</select></label><label>{"成败关系" if zh else "Outcome relation"}<select data-matrix-relation><option value="different">{"所选方法有分歧" if zh else "Selected methods disagree"}</option><option value="baseline-pass-others-fail">{"基准通过，其余失败" if zh else "Baseline passes; others fail"}</option><option value="baseline-fail-others-pass">{"基准失败，其余通过" if zh else "Baseline fails; others pass"}</option><option value="all-pass">{"所选全部通过" if zh else "All selected pass"}</option><option value="all-fail">{"所选全部失败" if zh else "All selected fail"}</option><option value="exact-pass">{"仅所选方法通过" if zh else "Only selected methods pass"}</option><option value="">{"全部任务" if zh else "All tasks"}</option></select></label><label>{"站点" if zh else "Site"}<select data-matrix-site><option value="">{"全部" if zh else "All"}</option>{matrix_sites}</select></label></div><fieldset class="comparison-methods"><legend>{"参与比较的方法（至少选一个）" if zh else "Comparison methods (select at least one)"}</legend><div class="method-checks">{matrix_checks}</div></fieldset><div class="table-scroll matrix-table" data-task-matrix><table><thead><tr><th>{"任务" if zh else "Task"}</th><th>{"站点" if zh else "Site"}</th><th>{"官方通过" if zh else "Official pass"}</th><th>{"失败" if zh else "Fail"}</th></tr></thead><tbody>{"".join(result_matrix_rows)}</tbody></table></div></section>'
        results_body = f'''<section class="hero"><p class="eyebrow">{len(public_rows)} {"条逻辑结果" if zh else "logical outcomes"}</p><h1>{"结果与失败诊断" if zh else "Results and failure diagnosis"}</h1><p>{"同题对比支持选择基准和多个方法；下方筛选表默认只显示失败，点击“诊断”查看原始判定、复核原因与执行证据。" if zh else "Choose a baseline and multiple methods for same-task comparison. The inventory starts with failures; open Diagnose for the exact finding, reviewed cause, and execution evidence."}</p></section><section class="compact-section">{result_matrix}</section><section><div class="filters"><label>{"搜索" if zh else "Search"}<input data-filter="q" placeholder="{"任务编号、站点或错误" if zh else "Task, site, or error"}"></label><label>{"方法" if zh else "Method"}<select data-filter="participant"><option value="">{"全部" if zh else "All"}</option>{browser_options}</select></label><label>{"结果" if zh else "Verdict"}<select data-filter="status"><option value="fail">{"失败" if zh else "Fail"}</option><option value="pass">{"通过" if zh else "Pass"}</option><option value="">{"全部" if zh else "All"}</option></select></label><label>{"站点" if zh else "Site"}<select data-filter="site"><option value="">{"全部" if zh else "All"}</option>{site_options}</select></label><label>{"失败环节" if zh else "Failure stage"}<select data-filter="stage"><option value="">{"全部" if zh else "All"}</option>{stage_options}</select></label><label>{"复核分类" if zh else "Reviewed category"}<select data-filter="cause"><option value="">{"全部" if zh else "All"}</option>{cause_options}</select></label></div><p class="result-count" data-result-count></p><div class="table-scroll inventory" data-results><table><thead><tr><th>{"任务" if zh else "Task"}</th><th>{"方法" if zh else "Method"}</th><th>{"站点" if zh else "Site"}</th><th>{"判定" if zh else "Verdict"}</th><th>{"动作" if zh else "Actions"}</th><th>{"失败环节" if zh else "Failure stage"}</th><th>{"复核分类" if zh else "Reviewed category"}</th><th>{"详情" if zh else "Detail"}</th></tr></thead><tbody>{''.join(result_rows)}</tbody></table></div></section><dialog id="diagnosis-dialog"><div class="dialog-head"><strong data-dialog-title></strong><button type="button" aria-label="{"关闭" if zh else "Close"}">×</button></div><div class="dialog-body"><div class="diagnosis-facts"><div><span>{"失败环节" if zh else "Failure stage"}</span><strong data-dialog-stage></strong></div><div><span>{"动作定位" if zh else "Action locator"}</span><strong data-dialog-step></strong></div><div><span>{"复核分类" if zh else "Reviewed category"}</span><strong data-dialog-category></strong></div></div><h3>{"程序判定与验收规则" if zh else "Programmatic finding and evaluator rule"}</h3><pre data-dialog-error></pre><h3>{"复核原因与处理" if zh else "Reviewed cause and treatment"}</h3><p data-dialog-cause></p><p data-dialog-remediation></p><div class="evidence-actions"><a data-dialog-step-link>{"打开对应动作" if zh else "Open the relevant action"}</a><a data-dialog-link>{"打开完整详情" if zh else "Open full detail"}</a></div></div></dialog>'''
        results_body = results_body.replace(
            '<section class="compact-section">',
            failure_category_matrix(comparison_methods, public_rows, lang) + '<section class="compact-section">',
            1,
        ).replace('<div class="filters">', '<div class="filters" id="result-inventory">', 1)
        results_body = results_body.replace("结果与失败诊断", "结果分析").replace("Results and failure diagnosis", "Results analysis")
        (root / "results.html").write_text(_shell(lang, "results", "结果分析" if zh else "Results analysis", validity_notice + results_body, current_route="results.html"), encoding="utf-8")

        flow_labels = (
            ("重放器", "浏览器容器", "本地站点", "每方法前重置", "证据采集", "官方验收", "完全销毁")
            if zh
            else ("Replay runner", "Browser container", "Local sites", "Reset per method", "Evidence", "Official evaluator", "Full teardown")
        )
        fallback = '<div class="flow-fallback" data-topology-fallback>' + "".join(
            f'<div class="flow-node"><strong>{index}</strong>{_e(label)}</div>'
            + ('<div class="flow-arrow" aria-hidden="true">→</div>' if index < len(flow_labels) else "")
            for index, label in enumerate(flow_labels, 1)
        ) + "</div>"
        topology = f'''<pre class="mermaid" hidden>flowchart LR
  R[{"重放器" if zh else "Replay runner"}] -->|{"每次只运行一个浏览器" if zh else "one browser at a time"}| B[{"浏览器容器" if zh else "Browser container"}]
  B -->|{"Colima 网关 192.168.5.2" if zh else "Colima gateway 192.168.5.2"}| S[{"本地 WebArena 站点" if zh else "Local WebArena sites"}]
  S --> D[{"每方法开始前恢复站点数据" if zh else "Reset site data before each method"}]
  B --> E[{"轨迹 · 网络记录 · 资源采样" if zh else "Trace · HAR · resource samples"}]
  E --> V[{"官方验收" if zh else "Official evaluator"}]
  V --> X[{"绑定证据的结果" if zh else "Evidence-bound result"}]
  X --> T[{"销毁浏览器与站点" if zh else "Teardown browser and sites"}]
  T --> R</pre>'''
        environment_body = f'''<section class="hero"><h1>{"环境与评测流程" if zh else "Environment and evaluation flow"}</h1><p>{"192.168.5.2 是 Colima 虚拟机中的宿主网关：浏览器容器通过它访问运行在同一台 Mac 虚拟化环境里的 WebArena 站点。它不是公网测试网站。" if zh else "192.168.5.2 is the host gateway inside the Colima VM. Browser containers use it to reach the WebArena sites running in the same Mac virtualization environment; it is not a public test website."}</p></section><section><h2>{"执行拓扑" if zh else "Execution topology"}</h2>{fallback}{topology}</section><section><h2>{"隔离规则" if zh else "Isolation rules"}</h2><div class="summary-grid"><article><strong>1</strong><p>{"一套固定端口站点环境，因此只允许一个方法运行。" if zh else "One fixed-port site stack permits only one running method."}</p></article><article><strong>{task_count}</strong><p>{"每种方法覆盖全部任务，方法开始前恢复站点；任务之间不重置。每题只保留一个最终逻辑结果，额外物理执行单独计数，输入未变且已经通过的任务不再执行。" if zh else "Each method covers every task after one site reset, with no reset between tasks. One final logical outcome is retained per task; extra physical calls are counted separately, and an unchanged task is not run again after passing."}</p></article><article><strong>0</strong><p>{"没有 Agent 决策和模型请求；输入是冻结的 BrowserTool 动作。" if zh else "No Agent decisions or model calls; inputs are frozen BrowserTool actions."}</p></article></div></section><script type="module">import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';const node=document.querySelector('.mermaid');try{{mermaid.initialize({{startOnLoad:false,theme:document.documentElement.dataset.theme==='dark'?'dark':'neutral'}});const rendered=await mermaid.render('replay-topology',node.textContent);node.innerHTML=rendered.svg;node.hidden=false;document.querySelector('[data-topology-fallback]').hidden=true}}catch(error){{node.hidden=true}}</script>'''
        environment_body = environment_body.replace("环境与评测流程", "环境与方法").replace("Environment and evaluation flow", "Environment and method")
        if "Rainbond" in str((profile.get("execution") or {}).get("site_stack") or ""):
            diagram_script = environment_body[environment_body.index('<script type="module">'):]
            remote_labels = (
                ("冻结输入", "组前恢复站点", "新建浏览器会话", "顺序重放", "官方验收", "保存证据")
                if zh else
                ("Freeze inputs", "Restore sites", "Fresh browser context", "Sequential replay", "Official evaluation", "Retain evidence")
            )
            remote_fallback = '<div class="flow-fallback" data-topology-fallback>' + ''.join(
                f'<div class="flow-node"><strong>{i}</strong>{_e(label)}</div>'
                + ('<div class="flow-arrow" aria-hidden="true">→</div>' if i < len(remote_labels) else '')
                for i, label in enumerate(remote_labels, 1)
            ) + '</div>'
            remote_diagram = f'''<pre class="mermaid" hidden>flowchart TD
  A["{remote_labels[0]}"] --> B["{remote_labels[1]}"]
  B --> C["{remote_labels[2]}"]
  C --> D["{remote_labels[3]}"]
  D --> S["Rainbond WebArena"]
  S --> E["{remote_labels[4]}"]
  E --> F["{remote_labels[5]}"]
  F -->|{"下一题，不重置站点" if zh else "next task, no site reset"}| C
</pre>'''
            reset_rows = []
            for participant, method in methods.items():
                entry = next((
                    (key, value) for key, value in public_evidence.items()
                    if key[0] == participant and value.get("site_reset")
                ), None)
                if entry is None:
                    continue
                key, value = entry
                evidence_file = ("" if zh else "../") + f"data/evidence/{key[0]}-{key[1]}.json"
                receipt_link = f'evidence-viewer.html?file={evidence_file}'
                for site, identity in sorted(value["site_reset"].get("sites", {}).items()):
                    reset_rows.append(
                        f'<tr><td>{_e(method["label"])}</td><td>{_e(site)}</td>'
                        f'<td><code>sha256:{_e(identity.get("image_digest") or "—")}</code></td>'
                        f'<td><a href="{_e(receipt_link)}">{"恢复与就绪证据" if zh else "Reset and readiness evidence"}</a></td></tr>'
                    )
            reset_table = (
                f'<section><h2>{"站点镜像与恢复证据" if zh else "Site images and reset evidence"}</h2>'
                '<div class="table-scroll"><table><thead><tr>'
                + ''.join(f'<th>{label}</th>' for label in (
                    ("方法", "站点", "镜像标识", "证据") if zh else
                    ("Method", "Site", "Image identity", "Evidence")
                )) + '</tr></thead><tbody>' + ''.join(reset_rows) + '</tbody></table></div></section>'
            ) if reset_rows else ''
            environment_body = (
                f'<section class="hero"><h1>{"环境与方法" if zh else "Environment and method"}</h1>'
                f'<p>{"所有浏览器使用同一套站点映射和题序；验收规则版本见下方说明及任务详情。每个方法开始前恢复站点基线，任务之间不重置站点。" if zh else "All browsers use the same site map and task order; acceptance revisions are identified below and on task pages. The site baseline is restored before each method, not between tasks."}</p></section>'
                f'<section><h2>{"执行流程" if zh else "Execution flow"}</h2>{remote_fallback}{remote_diagram}'
                f'<p>{"每个方法的基线恢复记录绑定 Pod 更换、镜像摘要和站点就绪；逐题记录轨迹、网络和评分证据。方法串行执行；额外物理调用只覆盖从未通过或冻结输入发生变化的任务。" if zh else "Each method-level baseline receipt binds Pod replacement, image digest and site readiness. Per-task traces, network captures and scores remain separate. Methods run serially; extra physical calls cover only never-passing tasks or tasks whose frozen input changed."}</p></section>'
                + f'<p>{"浏览器运行于 macOS arm64 主机，通过网络访问 Rainbond 自托管站点。CowAgent 仅提供 BrowserTool 操作层，不进行 Agent 决策或模型调用。" if zh else "Browsers run on a macOS arm64 host and access self-hosted Rainbond sites over the network. CowAgent supplies only the BrowserTool action layer, without Agent decisions or model calls."}</p>'
                + reset_table + diagram_script
            )
        if measurement_note:
            environment_body += f'<p class="callout">{_e(measurement_note)}</p>'
        (root / "environment.html").write_text(_shell(lang, "environment", "环境与方法" if zh else "Environment and method", environment_body, current_route="environment.html"), encoding="utf-8")

        review_rows = (
            (
                "轨迹资格核验" if zh else "Trajectory qualification",
                f"{task_count} 题；{browser_use_source_count} 条来自自主执行，{repaired_source_count} 条经修复。" if zh else f"{task_count} tasks: {browser_use_source_count} autonomous successes and {repaired_source_count} repaired trajectories.",
                f"{repaired_source_count} 条候选轨迹需要修复后才能作为固定输入。" if zh else f"{repaired_source_count} candidate trajectories required repair before use as frozen inputs.",
                f"记录的逐题验收中，{reference_label} 通过 {reference_row['passed']}/{reference_row['total']} 题；固定轨迹不包含 Agent 或模型决策。" if zh else f"Recorded per-task acceptance yielded {reference_row['passed']}/{reference_row['total']} passes on {reference_label}. Frozen trajectories contain no Agent or model decisions.",
                "逐方法冻结输入及官方验收证据可在结果页查看。" if zh else "Method-specific frozen inputs and official evaluations are available on each result page.",
            ),
            (
                "失败证据复核" if zh else "Failure evidence review",
                f"{reviewed_failure_count} 条最终失败结果。" if zh else f"{reviewed_failure_count} final failed outcomes.",
                f"{sum(row['failure_stage'] == 'browser_session_setup' for row in public_rows)} 条浏览器会话初始化失败；{sum(row['failure_stage'] == 'browser_tool_execution' for row in public_rows)} 条动作执行失败；{sum(row['failure_stage'] == 'evidence_finalization' for row in public_rows)} 条证据收尾失败；{sum(row['failure_stage'] == 'official_evaluator_execution' for row in public_rows)} 条评测器执行失败；{sum(row['failure_stage'] == 'side_effect_not_observed' for row in public_rows)} 条网络验收失败。" if zh else f"{sum(row['failure_stage'] == 'browser_session_setup' for row in public_rows)} browser-session setup failures; {sum(row['failure_stage'] == 'browser_tool_execution' for row in public_rows)} browser-action failures; {sum(row['failure_stage'] == 'evidence_finalization' for row in public_rows)} evidence-finalization failures; {sum(row['failure_stage'] == 'official_evaluator_execution' for row in public_rows)} evaluator-execution failures; {sum(row['failure_stage'] == 'side_effect_not_observed' for row in public_rows)} network-acceptance failures.",
                f"{reviewed_failure_count}/{reviewed_failure_count} 条绑定当前证据哈希；分别展示程序报错、动作位置、复核原因和处理状态，原始分数未改。" if zh else f"{reviewed_failure_count}/{reviewed_failure_count} reviews are bound to the current evidence hash. Program errors, action locators, reviewed causes and treatment states are separate; official scores did not change.",
                "失败根因与处理状态见逐题结果详情。" if zh else "Failure causes and treatment status appear on each failed result page.",
            ),
        )
        review_table_rows = "".join(
            "<tr>" + "".join(f"<td>{_e(cell)}</td>" for cell in (title, scope, finding, correction, remaining)) + "</tr>"
            for title, scope, finding, correction, remaining in review_rows
        )
        review_headers = ("轮次", "范围", "发现", "纠正", "未完成") if zh else ("Round", "Scope", "Finding", "Correction", "Open")
        review_status = (
            f"已收录 {task_count} 个任务、所有方法各一次结果；最终失败 {reviewed_failure_count}/{reviewed_failure_count} 条已完成证据绑定复核。"
            if zh else
            f"The report contains {task_count} tasks with one result per each registered method; {reviewed_failure_count}/{reviewed_failure_count} final failures received evidence-bound review."
        )
        if unverifiable_ids:
            review_status += (
                f"不可核验参考答案 {len(unverifiable_ids)} 题，在核心指标中单列分母。"
                if zh else
                f" The {len(unverifiable_ids)} tasks with unverifiable references are excluded from the separate verifiable-task denominator."
            )
        review_body = f'''<section class="hero"><h1>{"核验记录" if zh else "Review history"}</h1><p>{"只记录影响当前评测集和结果解读的核验轮次；每个失败的完整证据与处理状态在逐题结果页。" if zh else "Rounds that affected the current dataset or result interpretation. Full evidence and treatment status for each failure are on result pages."}</p></section><section><h2>{"当前状态" if zh else "Current status"}</h2><p>{review_status}</p></section><section><h2>{"核验轮次" if zh else "Review rounds"}</h2><div class="table-scroll review-table"><table><thead><tr>{''.join(f'<th>{_e(label)}</th>' for label in review_headers)}</tr></thead><tbody>{review_table_rows}</tbody></table></div></section>'''
        (root / "review.html").write_text(_shell(lang, "review", "核验记录" if zh else "Review history", validity_notice + review_body, current_route="review.html"), encoding="utf-8")

        viewer_tabs = (("result", "结果与资源", "Result and resources"), ("site_reset", "站点重置", "Site reset"), ("readiness", "站点就绪", "Site readiness"), ("programmatic_failure", "程序定位", "Programmatic finding"), ("review", "复核原因", "Reviewed cause"), ("trajectory", "动作与日志", "Actions and logs"), ("official_evaluation", "官方验收", "Official evaluation"))
        tab_buttons = "".join(f'<button type="button" data-evidence-tab="{key}" aria-selected="false">{label_zh if zh else label_en}</button>' for key, label_zh, label_en in viewer_tabs)
        viewer_body = f'<section class="hero"><h1>{"证据查看器" if zh else "Evidence viewer"}</h1><p>{"按结果、动作、程序判定和复核原因查看完整净化证据；原始 JSON 仍可下载。" if zh else "Inspect full sanitized evidence by outcome, action, programmatic finding, and review. The raw JSON remains downloadable."}</p></section><section class="viewer"><div class="viewer-tabs" role="tablist">{tab_buttons}</div><h2 data-evidence-heading></h2><div class="evidence-actions"><a data-evidence-raw href="#">{"下载净化 JSON" if zh else "Download sanitized JSON"}</a><a data-evidence-image hidden></a></div><pre data-evidence-viewer></pre></section>'
        (root / "evidence-viewer.html").write_text(_shell(lang, "results", "证据查看器" if zh else "Evidence viewer", validity_notice + viewer_body, current_route="evidence-viewer.html"), encoding="utf-8")

    if interpretation_path is not None:
        source = Path(interpretation_path)
        illustrations = source.parent / "trajectory-replay-illustrations"
        asset_dir = output / "assets/interpretation"
        asset_dir.mkdir(parents=True, exist_ok=True)
        article = json.loads(source.read_text())
        for section in article["sections"]:
            if section.get("figure"):
                for lang in languages:
                    filename = f'{section["figure"]}-{lang}.png'
                    if Path(filename).name != filename:
                        raise ValueError("invalid article image name")
                    shutil.copyfile(illustrations / filename, asset_dir / filename)
        article_css = (
            ".interpretation{max-width:1100px;margin:auto}.interpretation p{max-width:78ch;line-height:1.85}"
            ".interpretation p,.interpretation figcaption{overflow-wrap:anywhere}"
            ".interpretation h1{font-size:clamp(30px,4vw,54px)}"
            ".interpretation figure{margin:24px 0}.interpretation figure img{width:100%;height:auto;display:block}"
            ".interpretation .diagram-scroll{overflow-x:auto;border:1px solid var(--line);border-radius:12px}"
            ".interpretation .diagram-scroll img{min-width:900px}"
            ".interpretation figcaption{margin-top:8px;color:var(--muted)}"
            ".interpretation .article-specimen table{min-width:560px}"
            "@media(prefers-color-scheme:dark){html:not([data-theme]){--bg:#0c1520;--panel:#121f2d;--panel2:#172b3a;--ink:#eef5fb;--muted:#9db0c2;--line:#2b4054;--accent:#62a8ff;--wash:#142d49;--shadow:none}}"
            "@media print{.interpretation{max-width:none}.interpretation details{display:block}.table-scroll{overflow:visible}table{min-width:0;font-size:9pt}figure{break-inside:avoid}.interpretation .diagram-scroll{overflow:visible}.interpretation .diagram-scroll img,.interpretation .article-specimen table{min-width:0}}"
        )
        css_path = output / "assets/report.css"
        css = css_path.read_text() + article_css
        css_path.write_text(css, encoding="utf-8")
        for lang in languages:
            language_root = output if lang == "zh" else output / "en"
            arguments = dict(source=source, contract_audit=Path(interpretation_audit_path) if interpretation_audit_path is not None else source.parent / "trajectory-replay-contract-audit.json",
                             results_sha256=_sha256(results_source), methods=methods, rows=public_rows,
                             lang=lang, image_dir=illustrations,
                             image_prefix=("assets/" if lang == "zh" else "../assets/") + "interpretation/")
            title, body = render_interpretation(**arguments)
            download = "下载独立阅读版" if lang == "zh" else "Download standalone article"
            body = f'<p><a href="article.html" download>{download}</a></p>' + body
            (language_root / "interpretation.html").write_text(
                _shell(lang, "interpretation", title, body, current_route="interpretation.html"), encoding="utf-8")
            publication = profile["publication"]
            share = urlsplit(publication["share_url"])
            if not share.path.startswith("/v/") or not share.path.removeprefix("/v/"):
                raise ValueError("standalone article requires the bound report share")
            report_url = (
                publication["base_url"].rstrip("/") + "/api/preview/" + publication["site_slug"]
                + ("/en" if lang == "en" else "") + "/interpretation.html?"
                + urlencode({"__artifact_share": share.path.removeprefix("/v/")})
            )
            title, offline = render_interpretation(**arguments, standalone=True, report_url=report_url)
            (language_root / "article.html").write_text(
                standalone_document(title, offline, lang, css), encoding="utf-8")
            home = language_root / "index.html"
            label = "报告解读：失败机制、修复与评测边界" if lang == "zh" else "Interpretation: failure mechanisms, repairs and evaluation limits"
            home.write_text(home.read_text().replace("<main>", f'<main><p><a href="interpretation.html">{label}</a></p>', 1), encoding="utf-8")
            _add_interpretation_navigation(language_root, lang)

    if not include_english:
        # Remove authored translations only; raw task and execution evidence is immutable.
        for evidence_path in (output / "data/evidence").glob("*.json"):
            document = json.loads(evidence_path.read_text(encoding="utf-8"))
            review = document.get("review")
            if isinstance(review, dict):
                document["review"] = {
                    key: value for key, value in review.items()
                    if not (key.endswith("_en") and key[:-3] + "_zh" in review)
                }
            _write_json(evidence_path, document)
        result_path = output / "data/results.json"
        rows = json.loads(result_path.read_text(encoding="utf-8"))
        _write_json(result_path, [
            {key: value for key, value in row.items() if key not in {"cause_en", "reason_en"}}
            for row in rows
        ])
        for page in output.rglob("*.html"):
            if page.name == "article.html":
                continue
            prefix = "../" * (len(page.relative_to(output).parts) - 1)
            content = page.read_text(encoding="utf-8")
            content = re.sub(
                r'(<a class="language" href=")[^"]+(">)',
                lambda match, prefix=prefix: match[1] + prefix + "en/index.html" + match[2],
                content,
            )
            page.write_text(content, encoding="utf-8")
        english_root = output / "en"
        english_root.mkdir()
        (english_root / "index.html").write_text(
            '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<script>(function(){const b=document.querySelector("base[href]");'
            'if(b)b.href=new URL("en/",b.href).href;})();</script>'
            '<title>英文报告未生成</title></head><body>'
            '<main data-english-not-generated><h1>英文报告未生成</h1>'
            '<p>English report has not been generated.</p>'
            '<p>当前报告仅提供中文版。英文版需单独生成。</p>'
            '<a href="../index.html">返回中文报告</a></main></body></html>',
            encoding="utf-8",
        )
    manifest = {
        "schema": "lex_webarena_trajectory_replay_report/1",
        "languages": list(languages),
        "english_status": "generated" if include_english else "not_generated",
        "results_sha256": _sha256(Path(results_path)),
        "dataset_sha256": _sha256(Path(dataset_path)),
        "review_sha256": _sha256(Path(review_path)),
        "task_count": len(tasks),
        "participant_count": len(comparison_methods),
        "browser_count": browser_count,
        "method_count": method_count,
        "participants": list(comparison_methods),
        "logical_results": len(public_rows),
        "failed_results": sum(row["status"] != "pass" for row in public_rows),
        "reviewed_failures": len(reviews),
        "formal_reference_validity": (profile.get("scoring") or {}).get(
            "require_all_reference_validity"
        ) is True,
        "site_reset_between_tasks": (profile.get("execution") or {}).get(
            "reset_before_every_task"
        ) is True,
        "routes": len(list(output.rglob("*.html"))),
        "interpretation": interpretation_path is not None,
        "measurement_notes": {lang: profile["measurement_notes"][lang] for lang in languages}
        if profile.get("measurement_notes") else {},
    }
    _write_json(output / "report-manifest.json", manifest)
    return manifest


CATALOG_REPORT_CSS = """
:root{color-scheme:light dark;font:16px/1.6 system-ui,sans-serif}
body{max-width:1120px;margin:auto;padding:22px;color:#14212b;background:#f7f9fb}
nav{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:24px}a{color:#075fb5}a:focus-visible,input:focus-visible{outline:3px solid #f29221}
h1{line-height:1.25}h2{margin-top:34px}.lead{font-size:1.15rem}.muted{color:#526273}
.panel{background:white;border:1px solid #d9e2e8;border-radius:12px;padding:20px;margin:16px 0}
.scroll{max-height:580px;overflow:auto;border:1px solid #d9e2e8;border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:650px;background:white}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #e3e9ee;padding:9px 12px}th{position:sticky;top:0;background:#edf3f7;z-index:1}
.ok{color:#08734a;font-weight:700}code,pre{font-family:ui-monospace,monospace;overflow-wrap:anywhere}pre{white-space:pre-wrap;max-height:550px;overflow:auto;background:#f0f4f7;padding:14px;border-radius:8px}
input{font:inherit;padding:8px 12px;width:min(100%,420px);border:1px solid #8194a3;border-radius:7px}footer{margin:34px 0;color:#526273;font-size:.9rem}
@media(max-width:650px){body{padding:12px}.panel{padding:14px}.scroll{max-height:65vh}th,td{padding:7px 9px}}
@media(prefers-color-scheme:dark){body{color:#edf3f7;background:#111c25}.panel,table{background:#1b2b36;border-color:#37505f}th{background:#283b48}td{border-color:#37505f}a{color:#85c1ff}.muted,footer{color:#b2c0c9}pre{background:#13232e}input{color:#edf3f7;background:#1b2b36;border-color:#8194a3}}
"""

CATALOG_SITE_LABELS = {
    "gitlab": "GitLab",
    "map": "地图",
    "reddit": "Reddit",
    "shopping": "购物网站",
    "shopping_admin": "购物后台",
    "wikipedia": "维基百科",
}


def _catalog_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _catalog_read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _catalog_write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _catalog_percentile(values: list[float], proportion: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * proportion
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def _catalog_shell(title: str, body: str, participant: str) -> str:
    nav = (
        '<nav><a href="index.html">结果</a>'
        '<a href="dataset.html">数据集</a>'
        '<a href="method.html">方法与边界</a></nav>'
    )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><base href="/">'
        f"<title>{html.escape(title)}</title><style>{CATALOG_REPORT_CSS}</style></head><body>{nav}{body}"
        f"<footer>WebArena-Verified 全量轨迹重放 · {html.escape(participant)}</footer>"
        "</body></html>"
    )


def _catalog_official_tasks() -> list[dict]:
    dataset = importlib.resources.files("webarena_verified").joinpath(
        "assets/dataset/webarena-verified.json"
    )
    tasks = json.loads(dataset.read_text(encoding="utf-8"))
    if not isinstance(tasks, list):
        raise ValueError("WebArena-Verified dataset is not a list")
    return tasks


def _catalog_progress_index(evidence_root: Path) -> tuple[dict[str, Path], dict[str, dict[int, dict]]]:
    directories: dict[str, Path] = {}
    rows_by_digest: dict[str, dict[int, dict]] = {}
    for progress in evidence_root.glob("*/progress.jsonl"):
        digest = _catalog_file_sha256(progress)
        rows = [json.loads(line) for line in progress.read_text(encoding="utf-8").splitlines()]
        directories[digest] = progress.parent
        rows_by_digest[digest] = {int(row["task_id"]): row for row in rows}
    return directories, rows_by_digest


def build_catalog_acceptance_report(
    *,
    evidence_root: Path,
    ledger_path: Path,
    scope_path: Path,
    catalog_path: Path,
    suite_path: Path,
    site_map_path: Path | None,
    output: Path,
    archive: Path,
) -> dict:
    """Build a Chinese-only report from a complete accepted catalog."""

    if output.exists() or archive.exists():
        raise FileExistsError("report output already exists")
    ledger = _catalog_read_json(ledger_path)
    scope = _catalog_read_json(scope_path)
    catalog = _catalog_read_json(catalog_path)
    suite = _catalog_read_json(suite_path)
    official = _catalog_official_tasks()
    task_ids = catalog.get("task_ids")
    expected_data_checksum = scope.get("official_dataset_sha256")
    if (
        ledger.get("counts", {}).get("pass") != len(official)
        or ledger.get("counts", {}).get("unresolved") != 0
        or task_ids != list(range(len(official)))
        or scope.get("task_ids") != task_ids
        or not isinstance(expected_data_checksum, str)
        or len(catalog.get("rows") or []) != len(official)
    ):
        raise ValueError("report inputs are not a complete accepted WebArena catalog")

    participant_data = ledger.get("participant") or {}
    task_count = len(official)
    engine = str(participant_data.get("engine") or "unknown")
    backend = str(participant_data.get("backend") or "unknown")
    request_bridge = request_bridge_identity(participant_data)
    participant = f"{engine.capitalize()} / {backend.capitalize()}"
    output.mkdir(parents=True)
    (output / "case").mkdir()
    (output / "evidence").mkdir()

    directories, rows_by_digest = _catalog_progress_index(evidence_root)
    benchmark = remote_benchmark(load_remote_sites(site_map_path)) if site_map_path else None
    corrections = load_corrections() if site_map_path else None
    accepted: dict[int, tuple[dict, dict, dict, Path]] = {}
    for accepted_row in ledger["rows"]:
        task_id = int(accepted_row["task_id"])
        attempt = accepted_row["attempts"][-1]
        digest = attempt["progress_sha256"]
        try:
            accepted[task_id] = (
                accepted_row,
                attempt,
                rows_by_digest[digest][task_id],
                directories[digest],
            )
        except KeyError as error:
            raise ValueError(f"task {task_id} accepted evidence is missing") from error

    suite_by_id = {
        int(task["id"].removeprefix("webarena-")): task for task in suite["tasks"]
    }
    catalog_by_id = {int(row["task_id"]): row for row in catalog["rows"]}
    contracts = []
    durations = []
    site_counts = {site: 0 for site in CATALOG_SITE_LABELS}
    result_rows = []
    first_statuses: dict[str, int] = {}
    for accepted_row in ledger["rows"]:
        status = accepted_row["attempts"][0]["status"]
        first_statuses[status] = first_statuses.get(status, 0) + 1

    for task_id, task in enumerate(official):
        accepted_row, final_attempt, row, run_dir = accepted[task_id]
        physical_attempt_count = sum(
            item["status"] != "candidate_setup_failure" for item in accepted_row["attempts"]
        )
        seconds = float(row["duration_ms"]) / 1000
        durations.append(seconds)
        sites = [str(site) for site in task["sites"]]
        for site in sites:
            site_counts[site] += 1
        site_text = "、".join(CATALOG_SITE_LABELS[site] for site in sites)
        intent = str(task["intent"])
        result_rows.append(
            f'<tr data-search="{task_id} {html.escape(site_text)} {html.escape(intent)}">'
            f'<td><a href="case/{task_id:03}.html">{task_id}</a></td>'
            f"<td>{html.escape(site_text)}</td><td class=\"ok\">通过</td>"
            f'<td>{len(accepted_row["attempts"])}</td><td>{seconds:.2f}</td></tr>'
        )
        task_dir = run_dir / f"task-{task_id}"
        transition = final_attempt.get("evaluator_transition")
        if transition is not None:
            if benchmark is None or corrections is None:
                raise ValueError("transitioned report evidence requires the bound site map")
            evaluation, binding = reevaluate_pass(
                task_dir,
                task_id=task_id,
                expected_data_checksum=expected_data_checksum,
                benchmark=benchmark,
                corrections=corrections,
            )
            if binding != transition or json_sha256(evaluation) != transition.get(
                "current_evaluation_sha256"
            ):
                raise ValueError(f"task {task_id} transitioned evaluation changed")
        else:
            evaluation = _catalog_read_json(task_dir / "evaluation.json")
        evidence = {
            "schema": "lex_webarena_catalog_public_evidence/1",
            "task_id": task_id,
            "participant": {
                "engine": engine,
                "backend": backend,
                **(
                    {"binary_sha256": final_attempt["binary_sha256"]}
                    if isinstance(final_attempt.get("binary_sha256"), str)
                    else {
                        "remote_browser": (ledger.get("participant") or {}).get(
                            "remote_browser"
                        ),
                        "runtime_metadata": final_attempt.get("runtime_metadata"),
                        **(
                            {"request_bridge": request_bridge}
                            if request_bridge is not None
                            else {}
                        ),
                    }
                ),
            },
            "result": {
                key: row.get(key)
                for key in (
                    "status",
                    "score",
                    "action_count",
                    "completed_actions",
                    "duration_ms",
                    "executed_actions_sha256",
                )
            },
            "attempt_count": len(accepted_row["attempts"]),
            "physical_attempt_count": physical_attempt_count,
            "attempt_statuses": [item["status"] for item in accepted_row["attempts"]],
            "evaluation": evaluation,
            "evaluator_transition": transition,
            "actual_response": _catalog_read_json(task_dir / "actual_response.json"),
            "trace": _catalog_read_json(task_dir / "trace.json"),
        }
        _catalog_write_json(output / "evidence" / f"{task_id:03}.json", evidence)
        case_body = (
            f"<h1>第 {task_id} 题 · {html.escape(participant)} 通过</h1>"
            f'<p class="lead">{html.escape(intent)}</p>'
            '<section class="panel" data-report-detail="passed"><h2>官方判分</h2>'
            f'<p class="ok">通过 · score 1.0</p><p>网站：{html.escape(site_text)}；'
            f"最终执行耗时：{seconds:.2f} 秒；物理执行：{physical_attempt_count} 次。</p>"
            f'<p>最终动作哈希：<code>{html.escape(str(row["executed_actions_sha256"]))}</code></p>'
            '</section><section class="panel"><h2>执行证据</h2>'
            f'<p><a href="evidence.html?task={task_id:03}">查看轨迹与官方评测结果</a> · '
            f'<a href="evidence/{task_id:03}.json">下载 JSON</a></p></section>'
        )
        (output / "case" / f"{task_id:03}.html").write_text(
            _catalog_shell(f"第 {task_id} 题结果", case_body, participant),
            encoding="utf-8",
        )
        catalog_row = catalog_by_id[task_id]
        fixed_actions = (
            suite_by_id[task_id]["trajectory"]["actions"]
            if catalog_row.get("mode") == "portable"
            else catalog_row["source_actions"]
        )
        contracts.append(
            {
                "task_id": task_id,
                "intent": intent,
                "sites": sites,
                "official_evaluators": task["eval"],
                "trajectory_source": catalog_row.get("source_kind"),
                "fixed_actions": fixed_actions,
            }
        )

    site_html = "".join(
        f"<tr><td>{CATALOG_SITE_LABELS[site]}</td><td>{count}</td></tr>"
        for site, count in site_counts.items()
    )
    first_pass = first_statuses.get("pass", 0)
    first_fail = first_statuses.get("fail", 0)
    setup_failure = first_statuses.get("candidate_setup_failure", 0)
    counts = ledger["counts"]
    binary_count = len(ledger.get("binary_sha256s") or [])
    remote_count = len(ledger.get("remote_browsers") or [])
    runtime_count = binary_count + remote_count
    hosted_runtime = bool(remote_count)
    initial_physical = first_pass + first_fail
    index_body = f"""<h1>WebArena-Verified 全量轨迹重放</h1><p class="lead">{html.escape(participant)}：{task_count} / {task_count} 题至少一次获得官方 evaluator 通过（100%）。通过后不再执行该 case。</p>
<section class="panel"><h2>核心结果</h2><div class="scroll"><table><thead><tr><th>浏览器引擎 / 执行后端</th><th>题数</th><th>通过</th><th>Mean</th><th>P50</th><th>P95</th></tr></thead><tbody><tr><td>{html.escape(participant)}</td><td>{task_count}</td><td>{counts["pass"]}</td><td>{statistics.mean(durations):.2f} 秒</td><td>{statistics.median(durations):.2f} 秒</td><td>{_catalog_percentile(durations,.95):.2f} 秒</td></tr></tbody></table></div><p class="muted">耗时统计使用每题最终通过调用。接受审计覆盖 {counts["physical_attempts"]:,} 次物理执行；通过后重复执行 {counts["excluded_duplicate_attempts"]} 次。</p></section>
<section class="panel"><h2>执行收敛</h2><p>初始顺序扫描完成 {initial_physical} 次物理执行：{first_pass} 题通过，{first_fail} 题未通过；其余 {setup_failure} 题因执行环境中断记录为准备失败。恢复环境后继续按官方题号处理未通过项，并逐类修复轨迹、执行器、站点状态和已确认的评测问题，直至 {task_count} 题全部通过。</p><p>接受链包含 {len(ledger["runs"])} 个执行段和 {runtime_count} 个浏览器运行时身份。浏览器引擎、执行后端和站点映射保持一致；每次修复只进入尚未通过的 case。</p></section>
<section class="panel"><h2>网站分布</h2><p>一题可涉及多个网站，计数之和可能大于 {task_count}。</p><div class="scroll"><table><thead><tr><th>网站</th><th>涉及题数</th></tr></thead><tbody>{site_html}</tbody></table></div></section>
<section class="panel"><h2>逐题结果</h2><label for="find">筛选题号、网站或任务内容</label><p><input id="find" type="search" aria-label="筛选逐题结果"></p><div class="scroll"><table id="results"><thead><tr><th>题号</th><th>网站</th><th>最终判分</th><th>接受链记录数</th><th>最终耗时（秒）</th></tr></thead><tbody>{''.join(result_rows)}</tbody></table></div></section><script>const q=document.getElementById("find");q.addEventListener("input",()=>{{for(const row of document.querySelectorAll("#results tbody tr"))row.hidden=!row.dataset.search.toLowerCase().includes(q.value.toLowerCase())}})</script>"""
    (output / "index.html").write_text(
        _catalog_shell("全量轨迹重放结果", index_body, participant), encoding="utf-8"
    )

    runtime_method = (
        "托管浏览器的服务、账户匿名哈希及 Browser.getVersion 版本字段"
        if hosted_runtime
        else "本地浏览器二进制哈希"
    )
    revision_method = (
        "托管浏览器运行时在开始前冻结版本身份，每个物理结果都必须与该身份一致。"
        if hosted_runtime
        else "轨迹修复期间使用多个二进制版本，但每个版本均在接受账本中单独绑定。"
    )
    bridge_method = (
        f"Kitesurf 远端浏览器无法直接访问私网 Rainbond 地址；本轮用 Playwright request bridge 从执行机访问账本绑定的 {len(request_bridge['origins'])} 个站点 origin，并以原 URL、请求方法、请求头和请求体回填响应。桥接身份随每个执行段和公开证据保存。"
        if request_bridge is not None
        else ""
    )
    method_body = f"""<h1>方法与边界</h1><section class="panel"><h2>执行方法</h2><p>评测集按官方题号 {task_ids[0]}–{task_ids[-1]} 排列。每题执行冻结轨迹，由 {html.escape(engine.capitalize())} 浏览器引擎通过 {html.escape(backend.capitalize())} 协议后端完成动作。接受标准是 WebArena-Verified 官方 evaluator 返回 score 1.0。</p><p>初始扫描中断后，后续执行只选择接受账本里尚未通过的题。最终接受账本绑定每个物理调用、{runtime_method}、站点映射、轨迹、实际响应、网络证据和 evaluator 结果。</p>{f'<p>{html.escape(bridge_method)}</p>' if bridge_method else ''}</section><section class="panel"><h2>代码与评测边界</h2><p>确认属于评测集或 evaluator 的通用修复维护在 lexmount/webarena-verified 的 lex-main 分支；轨迹、执行器、站点绑定、证据聚合和报告维护在 browser-eval。评测答案和通过条件没有为浏览器适配而放宽。</p><p>旧 evaluator 版本下的通过证据通过固定实际响应和 HAR 离线重评，浏览器动作没有重跑。{revision_method}</p></section><section class="panel"><h2>结果范围</h2><p>该结果证明当前 {html.escape(participant)} 配置下 {task_count} 题各自至少成功一次。812-case 全量 split 与既有 90-case 抽样 split 数据独立；两者共享轨迹执行和 evaluator 基础模块，并分别绑定自己的证据与发布目标。</p></section>"""
    (output / "method.html").write_text(
        _catalog_shell("方法与边界", method_body, participant), encoding="utf-8"
    )

    _catalog_write_json(
        output / "dataset-contracts.json",
        {"schema": LEGACY_CATALOG_CONTRACTS_SCHEMA, "tasks": contracts},
    )
    dataset_rows = "".join(
        f'<tr><td><a href="case/{task["task_id"]:03}.html">{task["task_id"]}</a></td>'
        f'<td>{html.escape("、".join(CATALOG_SITE_LABELS[site] for site in task["sites"]))}</td>'
        f'<td>{html.escape(task["intent"])}</td></tr>'
        for task in contracts
    )
    dataset_body = f"""<h1>数据集与轨迹合同</h1><p class="lead">{task_count} 题按 WebArena-Verified 官方顺序冻结。此页只展示任务定义；判分和执行证据在逐题结果页。</p><section class="panel"><div class="scroll"><table><thead><tr><th>题号</th><th>网站</th><th>任务</th></tr></thead><tbody>{dataset_rows}</tbody></table></div><p><a href="dataset-contracts.json">下载完整任务、evaluator 与固定动作合同</a></p></section>"""
    (output / "dataset.html").write_text(
        _catalog_shell("数据集与轨迹合同", dataset_body, participant), encoding="utf-8"
    )
    (output / "contract.html").write_text(
        _catalog_shell("评测合同", dataset_body, participant), encoding="utf-8"
    )
    viewer = """<h1>证据查看器</h1><p><a id="back" href="index.html">返回结果</a></p><pre id="payload" role="region" aria-label="完整执行证据">加载中…</pre><script>const id=new URLSearchParams(location.search).get("task");const ok=/^\\d{3}$/.test(id||"");if(ok){document.getElementById("back").href="case/"+id+".html";fetch("evidence/"+id+".json").then(r=>{if(!r.ok)throw Error("证据不存在");return r.json()}).then(x=>document.getElementById("payload").textContent=JSON.stringify(x,null,2)).catch(e=>document.getElementById("payload").textContent=String(e))}else document.getElementById("payload").textContent="无效的题号";</script>"""
    (output / "evidence.html").write_text(
        _catalog_shell("证据查看器", viewer, participant), encoding="utf-8"
    )
    notice = '<h1>English edition not generated</h1><p>This release is Chinese only.</p><p><a href="index.html">返回中文版</a></p>'
    (output / "en").mkdir()
    (output / "en" / "index.html").write_text(
        _catalog_shell("English edition not generated", notice, participant),
        encoding="utf-8",
    )

    manifest = {
        "schema": LEGACY_CATALOG_REPORT_SCHEMA,
        "generated_languages": ["zh"],
        "participant": {
            "engine": engine,
            "backend": backend,
            **(
                {"request_bridge": request_bridge}
                if request_bridge is not None
                else {}
            ),
        },
        "ledger_sha256": _catalog_file_sha256(ledger_path),
        "scope_sha256": _catalog_file_sha256(scope_path),
        "catalog_sha256": _catalog_file_sha256(catalog_path),
        "portable_suite_sha256": _catalog_file_sha256(suite_path),
        "task_count": len(official),
        "pass": counts["pass"],
        "initial_physical_attempts": initial_physical,
        "initial_pass": first_pass,
        "initial_setup_failures": setup_failure,
        "physical_attempts": counts["physical_attempts"],
        "passed_case_reruns": counts["excluded_duplicate_attempts"],
        "binary_count": binary_count,
        "site_counts": site_counts,
        "route_counts": {"case": len(official), "evidence_json": len(official)},
    }
    _catalog_write_json(output / "manifest.json", manifest)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(output))
    validate_catalog_acceptance_report(output, archive)
    return {
        "output": str(output),
        "archive": str(archive),
        "manifest_sha256": _catalog_file_sha256(output / "manifest.json"),
        "archive_sha256": _catalog_file_sha256(archive),
        "archive_bytes": archive.stat().st_size,
    }


def validate_catalog_acceptance_report(
    output: Path, archive: Path, *, profile: dict[str, Any] | None = None
) -> dict:
    """Validate the public boundary of a catalog report package."""

    manifest = _catalog_read_json(output / "manifest.json")
    task_count = int(manifest.get("task_count") or 0)
    if (
        manifest.get("schema") != LEGACY_CATALOG_REPORT_SCHEMA
        or manifest.get("generated_languages") != ["zh"]
        or manifest.get("pass") != task_count
        or task_count <= 0
        or len(list((output / "case").glob("*.html"))) != task_count
        or len(list((output / "evidence").glob("*.json"))) != task_count
    ):
        raise ValueError("catalog report manifest or route inventory is invalid")
    expected_participant = (
        {
            "engine": profile.get("browser"),
            "backend": profile.get("execution_backend"),
            **(
                {"request_bridge": profile["request_bridge"]}
                if profile.get("request_bridge") is not None
                else {}
            ),
        }
        if profile is not None
        else None
    )
    if profile is not None and (
        manifest.get("participant") != expected_participant
        or manifest.get("ledger_sha256") != profile.get("acceptance_ledger_sha256")
        or manifest.get("catalog_sha256")
        != (profile.get("accepted_catalog") or {}).get("sha256")
        or manifest.get("portable_suite_sha256")
        != (profile.get("accepted_portable_suite") or {}).get("sha256")
        or manifest.get("task_count") != (profile.get("accepted_counts") or {}).get("total")
        or manifest.get("physical_attempts")
        != (profile.get("accepted_counts") or {}).get("physical_attempts")
        or manifest.get("passed_case_reruns")
        != (profile.get("accepted_counts") or {}).get("excluded_duplicate_attempts")
    ):
        raise ValueError("catalog report presentation changed its release binding")
    missing = []
    for path in output.rglob("*"):
        if not path.is_file() or path.suffix not in {".html", ".json", ".css", ".js"}:
            continue
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in ("/Users/", "file://", "/private/tmp")):
            raise ValueError(f"catalog report leaks a private path: {path}")
        if path.suffix != ".html":
            continue

        for value in re.findall(
            r'<(?:a|script|img|link)\b[^>]*\b(?:href|src)="([^"]+)"', text
        ):
            if value.startswith(("http:", "https:", "#", "data:", "javascript:")):
                continue
            relative = value.split("?", 1)[0].split("#", 1)[0]
            if relative.startswith("/") or ".." in Path(relative).parts:
                raise ValueError(f"catalog report contains an unsafe asset path: {value}")
            if relative and not (output / relative).resolve().is_file():
                missing.append((str(path.relative_to(output)), value))
    if missing:
        raise ValueError(f"catalog report has missing local links: {missing[:5]}")
    index = (output / "index.html").read_text(encoding="utf-8")
    method = (output / "method.html").read_text(encoding="utf-8")
    if (
        f'{task_count} / {task_count}' not in index
        or f'{manifest["physical_attempts"]:,} 次物理执行' not in index
        or "通过后不再执行" not in index
        or "lexmount/webarena-verified" not in method
        or "评测答案和通过条件没有为浏览器适配而放宽" not in method
    ):
        raise ValueError("catalog report presentation contract is incomplete")
    with zipfile.ZipFile(archive) as bundle:
        corrupt = bundle.testzip()
        if corrupt is not None:
            raise ValueError(f"catalog report archive is corrupt: {corrupt}")
    return manifest
