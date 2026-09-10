#!/usr/bin/env python3
"""Validate the autonomous browser-use report and its measured evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from harness.browser_use.report import _logical_results
from harness.browser_use.selection import load_pilot


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(
    report_dir: Path,
    evidence_dir: Path,
    selection_path: Path,
    *,
    require_complete: bool,
) -> None:
    profile_path = Path("evaluation/browser-use/current-report.json")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    engines = tuple(str(engine) for engine in profile.get("engines") or [])
    if not engines or len(engines) != len(set(engines)):
        raise ValueError("current Browser Use profile must register unique browser engines")
    page_names = (
        "index.html",
        "guide/index.html",
        "guide/article.html",
        "changes/index.html",
        "datasets/index.html",
        "methodology/index.html",
        "analysis/index.html",
        "review/index.html",
        "adoption/index.html",
        "reproduce/index.html",
        "accounts/index.html",
        *(f"engines/{engine}.html" for engine in engines),
    )
    pages = tuple(report_dir / name for name in page_names)
    index = pages[0]
    audit_path = report_dir / "report-data.json"
    attempts_path = evidence_dir / "attempts.jsonl"
    manifest_path = evidence_dir / "manifest.json"
    run_config_path = evidence_dir / "run-config.json"
    task_pages = tuple(
        report_dir / "datasets" / f"{task_id}.html"
        for task_id in sorted(task.task_id for task in load_pilot(selection_path).tasks)
    )
    measured_rows = (
        [
            json.loads(line)
            for line in attempts_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if attempts_path.is_file()
        else []
    )
    manifest_for_projection = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    )
    logical_rows = _logical_results(
        measured_rows, manifest_for_projection.get("protocol") or {}
    )
    result_pages = tuple(
        report_dir
        / "results"
        / f"{row['engine']}-{int(row['task_id'])}.html"
        for row in logical_rows
    )
    chinese_pages = (*pages, *task_pages, *result_pages)
    english_pages = tuple(
        report_dir / "en" / page.relative_to(report_dir) for page in chinese_pages
    )
    data_files = tuple(
        report_dir / "data" / name
        for name in (
            "selection.json",
            "results.jsonl",
            "summary.json",
            "environment.json",
            "review.json",
            "publication-privacy.json",
            "evidence-index.json",
        )
    )
    artifact_files = tuple(
        report_dir / "artifacts" / page.stem / name
        for page in result_pages
        for name in (
            "agent-response.json",
            "evaluation.json",
            "agent-events.json",
            "trajectory.json",
            "evidence-metadata.json",
        )
    )
    if not all(
        path.is_file()
        for path in (
            *chinese_pages,
            *english_pages,
            *data_files,
            *artifact_files,
            audit_path,
        )
    ):
        raise ValueError("report is missing one or more required report pages")
    selection = load_pilot(selection_path)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("selection_sha256") != selection.sha256:
        raise ValueError("report was not built from the current pre-registered selection")
    rows = []
    if attempts_path.exists():
        rows = [
            json.loads(line)
            for line in attempts_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    selected_ids = {task.task_id for task in selection.tasks}
    expected_primary = {(engine, task_id, 1) for engine in engines for task_id in selected_ids}
    by_key: dict[tuple[str, int, int], dict] = {}
    for row in rows:
        key = (str(row["engine"]), int(row["task_id"]), int(row["attempt"]))
        if key in by_key:
            raise ValueError(f"duplicate measured attempt: {key}")
        if key[0] not in set(engines) or key[1] not in selected_ids:
            raise ValueError(f"attempt outside the registered cohort: {key}")
        if key[2] not in {1, 2}:
            raise ValueError(f"more than one retry is not allowed: {key}")
        by_key[key] = row
    for engine, task_id, attempt in by_key:
        if attempt == 2:
            primary = by_key.get((engine, task_id, 1))
            if primary is None or float(primary["score"]) == 1.0:
                raise ValueError(
                    f"retry is allowed only after a failed primary: {(engine, task_id)}"
                )
    rendered_pages = {
        page: page.read_text(encoding="utf-8") for page in (*chinese_pages, *english_pages)
    }
    if any('<html lang="en">' not in rendered_pages[page] for page in english_pages):
        raise ValueError("every Chinese report route must have an English mirror")
    cjk = re.compile(r"[\u3400-\u9fff]")
    localized_attribute = re.compile(
        r'(?:aria-label|placeholder|title)="([^"]*)"', flags=re.I
    )
    for page in english_pages:
        text = rendered_pages[page]
        title = re.search(r"<title>(.*?)</title>", text, flags=re.I | re.S)
        if title and cjk.search(title.group(1)):
            raise ValueError(f"English report title is not localized: {page}")
        if any(cjk.search(value) for value in localized_attribute.findall(text)):
            raise ValueError(f"English report accessibility text is not localized: {page}")
    if task_pages and any(
        "Ground truth · 官方验收合同" not in rendered_pages[page]
        or "查看本题运行结果" not in rendered_pages[page]
        or "最终结果对照" in rendered_pages[page]
        for page in task_pages
    ):
        raise ValueError(
            "task pages must publish frozen contracts without embedding measured comparisons"
        )
    dataset_index = rendered_pages[report_dir / "datasets/index.html"]
    if 'data-result=' in dataset_index or "可完成性证据" in dataset_index:
        raise ValueError("dataset inventory must not mix measured results into task definitions")
    review_index = rendered_pages[report_dir / "review/index.html"]
    if (
        "核验与修订记录" not in review_index
        or "核验轮次" not in review_index
        or "review-result-table" in review_index
        or "逐结果覆盖" in review_index
    ):
        raise ValueError("review page must present review rounds, not a per-result audit table")
    methodology_index = rendered_pages[report_dir / "methodology/index.html"]
    for marker in ("192.168.5.2", "Wikipedia", "Map", "未形成独立绑定的准备回执"):
        if marker not in methodology_index:
            raise ValueError(f"methodology is missing deployment evidence boundary: {marker}")
    analysis_index = rendered_pages[report_dir / "analysis/index.html"]
    for marker in (
        "Agent 链路累计耗时",
        "BrowserTool 累计耗时",
        "均值",
        "P50",
        "P95",
        "comparison-winner",
        "BrowserTool 动作延迟",
        "action-card-grid",
        "浏览器组合对比",
        "data-compare-engine",
    ):
        if marker not in analysis_index:
            raise ValueError(f"analysis is missing timing or scalable comparison: {marker}")
    for forbidden in ("中位数", "失败结果定位", 'id="failure-table"', 'id="pair-table"'):
        if forbidden in analysis_index:
            raise ValueError(f"analysis contains a redundant or retired presentation: {forbidden}")
    participant_index = rendered_pages[report_dir / "adoption/index.html"]
    if (
        "参评浏览器与统一实验条件" not in participant_index
        or "参评选手是浏览器后端，不是 Agent 或 benchmark" in participant_index
    ):
        raise ValueError("participant page must use neutral reader-facing terminology")
    if result_pages and any(
        marker not in rendered_pages[page]
        for page in result_pages
        for marker in (
            "任务要求与验收结果",
            "查看官方期望与本次观察",
            "CowAgent 自主执行轨迹",
            "运行、日志与证据",
            "attempt-summary",
            "查看任务与验收",
        )
    ):
        raise ValueError("every result page must expose result, evaluator, trajectory and logs")
    if any(
        marker in rendered_pages[page]
        for page in result_pages
        for marker in ("Ground truth 与实际最终回答", "官方 evaluator 逐规则判定")
    ):
        raise ValueError("result pages must not duplicate answer and evaluator presentations")
    html = rendered_pages[index]
    standalone = (report_dir / "guide/article.html").read_text(encoding="utf-8")
    if "<script" in standalone or "assets/" in standalone:
        raise ValueError("standalone article must be self-contained and JavaScript-free")
    article_heading = "五个零分包含四类根因"
    if (
        article_heading not in standalone
        or article_heading not in rendered_pages[report_dir / "guide/index.html"]
    ):
        raise ValueError(
            "dashboard guide and standalone article must share the authored interpretation"
        )
    public_results = [
        json.loads(line)
        for line in (report_dir / "data/results.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if len(public_results) != len(logical_rows):
        raise ValueError("public result projection must cover every logical result")
    evidence_index = json.loads(
        (report_dir / "data/evidence-index.json").read_text(encoding="utf-8")
    )
    indexed_results = evidence_index.get("results") or []
    if {item.get("result_id") for item in indexed_results} != {
        page.stem for page in result_pages
    }:
        raise ValueError("public evidence index must cover every logical result exactly once")
    forbidden = {
        "agent_response",
        "paths",
        "model_transport_errors",
    }
    if any(forbidden & set(item) for item in public_results):
        raise ValueError("public result projection contains private evidence fields")
    forbidden_report_terms = (
        "诊断重试",
        "失败重试",
        "首轮试次",
        "assistant reasoning",
        "用户提示词",
        "我的思考",
        "内部工作日志",
        "另一个覆盖更多站点的 179 题",
        "后续 179 题",
        "需要注意：",
        "代理实测返回 team_model_access_denied",
        "证据支持的诊断",
        "共享链路诊断",
        "失败归因：",
    )
    for page, text in rendered_pages.items():
        if any(term in text for term in forbidden_report_terms):
            raise ValueError(f"formal report contains forbidden notebook or execution-label text: {page}")
        if re.search(
            r"<svg\b|<(?:img|object|embed)\b[^>]+(?:src|data)=[\"'][^\"']*\.svg(?:[?#][^\"']*)?[\"']",
            text,
            flags=re.I,
        ):
            raise ValueError(
                f"Browser Use reports must use raster or Mermaid diagrams, not SVG: {page}"
            )
    svg_assets = [
        path for path in report_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".svg"
    ]
    if svg_assets:
        raise ValueError(f"Browser Use report contains SVG assets: {svg_assets[0]}")
    style = (report_dir / "assets/style.css").read_text(encoding="utf-8")
    for required_style in (
        "#task-table{min-width:1500px}",
        "background:var(--code-bg)!important",
        ".method-figure img{min-width:960px",
        ".detail-grid .kv{overflow-wrap:anywhere",
        ".comparison-winner{",
        ".browser-comparison{",
        ".action-card-grid{",
        ".attempt-summary{",
        ".review-round{",
        ".page-main{overflow-x:clip}",
    ):
        if required_style not in style:
            raise ValueError(f"report is missing publication readability guard: {required_style}")
    if require_complete:
        if not manifest_path.is_file() or not run_config_path.is_file():
            raise ValueError("complete evidence requires manifest.json and run-config.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        run_config = json.loads(run_config_path.read_text(encoding="utf-8"))
        official = (manifest.get("environment") or {}).get("official_evaluator") or {}
        expected_profile = {
            "selection_sha256": selection.sha256,
            "model": (manifest.get("protocol") or {}).get("model"),
            "reasoning_effort": (manifest.get("protocol") or {}).get("reasoning_effort"),
            "official_data_checksum": official.get("data_checksum"),
        }
        for key, expected in expected_profile.items():
            if profile.get(key) != expected:
                raise ValueError(f"current report profile mismatch for {key}")
        browser_versions = set(((manifest.get("environment") or {}).get("browser_versions") or {}))
        if set(engines) != browser_versions:
            raise ValueError("current report engines differ from measured browser identities")
        manifest_score_authority = (manifest.get("protocol") or {}).get(
            "official_score_uses"
        ) or "final execution for each browser and task"
        if audit.get("score_authority") != manifest_score_authority:
            raise ValueError("report renderer changed the frozen score authority")
        if not str(profile.get("logical_result") or "").startswith(
            "score authority follows the frozen run manifest"
        ):
            raise ValueError("current report profile must defer score authority to the manifest")
        if manifest.get("selection_sha256") != selection.sha256:
            raise ValueError("manifest selection hash differs from the registered cohort")
        if run_config.get("selection_sha256") != selection.sha256:
            raise ValueError("run configuration differs from the registered cohort")
        if run_config.get("reasoning_effort") != "low":
            raise ValueError("pilot reasoning effort must be low")
        if not run_config.get("browser_eval_commit"):
            raise ValueError("run configuration must identify the browser-eval commit")
        if manifest.get("protocol", {}).get("browser_eval_commit") != run_config.get(
            "browser_eval_commit"
        ):
            raise ValueError("manifest and run configuration disagree on browser-eval commit")
        if manifest.get("protocol", {}).get("model_transport_retries") != 1:
            raise ValueError("pilot allows exactly one model transport retry")
        missing = expected_primary - set(by_key)
        if missing:
            raise ValueError(f"missing {len(missing)} primary attempts")
        missing_retries = {
            (engine, task_id, 2)
            for engine, task_id, _ in expected_primary
            if float(by_key[(engine, task_id, 1)]["score"]) != 1.0
            and (engine, task_id, 2) not in by_key
        }
        if missing_retries:
            raise ValueError(f"missing {len(missing_retries)} permitted failure retries")
        if "测量尚未完整" in html:
            raise ValueError("complete publication cannot contain an incomplete-results notice")
        review_path = evidence_dir / "result-review.json"
        if not review_path.is_file():
            raise ValueError("complete publication requires result-review.json")
        review = json.loads(review_path.read_text(encoding="utf-8"))
        bindings = review.get("bindings") or {}
        if bindings.get("attempts_sha256") != _sha256(attempts_path):
            raise ValueError("result review is not bound to the current attempts")
        if bindings.get("manifest_sha256") != _sha256(manifest_path):
            raise ValueError("result review is not bound to the current manifest")
        if bindings.get("selection_sha256") != selection.sha256:
            raise ValueError("result review is not bound to the registered selection")
        coverage = review.get("coverage") or {}
        if coverage != {"recorded_attempts": len(rows), "reviewed_attempts": len(rows)}:
            raise ValueError("result review does not cover every recorded attempt")
        if review.get("disagreements") or review.get("unresolved"):
            raise ValueError("result review contains disagreements or unresolved checks")
        reviewed_keys = {
            (str(item["engine"]), int(item["task_id"]), int(item["attempt"]))
            for item in review.get("attempts") or []
        }
        if reviewed_keys != set(by_key):
            raise ValueError("result review attempt inventory differs from measured evidence")
        report_review = audit.get("review") or {}
        if report_review.get("bindings") != bindings:
            raise ValueError("rendered report is not bound to the current result review")
        for _page, text in rendered_pages.items():
            if str(evidence_dir.resolve()) in text:
                raise ValueError("published pages must not contain machine-local evidence paths")
        forbidden_text = (
            "Password.123",
            "admin1234",
            "test1234",
            "hello1234",
        )
        for path in report_dir.rglob("*"):
            if path.is_file() and path.suffix in {".html", ".json", ".jsonl", ".js", ".css"}:
                text = path.read_text(encoding="utf-8")
                if any(secret in text for secret in forbidden_text):
                    raise ValueError(
                        f"published evidence contains a secret or local origin: {path}"
                    )
                risky_patterns = (
                    r"/Users/[^/\s<]+/",
                    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
                    r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}",
                    r"\bsk-[A-Za-z0-9_-]{12,}",
                )
                if any(re.search(pattern, text, flags=re.I) for pattern in risky_patterns):
                    raise ValueError(f"published evidence contains sensitive text: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument(
        "--selection",
        type=Path,
        default=Path("evaluation/browser-use/webarena-verified-pilot.json"),
    )
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    check(
        args.report,
        args.evidence,
        args.selection,
        require_complete=args.require_complete,
    )
    print("Browser Use report integrity check passed")


if __name__ == "__main__":
    main()
