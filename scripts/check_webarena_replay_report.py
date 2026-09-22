#!/usr/bin/env python3
"""Run deterministic release checks for the WebArena replay report."""

from __future__ import annotations

import argparse
import json
import math
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from harness.trajectory_replay.webarena_report import LOCAL_PATH_RE


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "pre", "code"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "pre", "code"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def _visible_text(html: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(html)
    return " ".join(parser.parts)


def _check_english_localization(pages: list[Path]) -> None:
    cjk = re.compile(r"[\u3400-\u9fff]")
    localized_attribute = re.compile(
        r'(?:aria-label|placeholder|title)=["\']([^"\']*)',
        flags=re.I,
    )
    for page in pages:
        text = page.read_text(encoding="utf-8")
        title = re.search(r"<title>(.*?)</title>", text, flags=re.I | re.S)
        if title and cjk.search(title.group(1)):
            raise ValueError(f"English report title is not localized: {page}")
        if any(cjk.search(value) for value in localized_attribute.findall(text)):
            raise ValueError(f"English report accessibility text is not localized: {page}")
        visible = _visible_text(text).replace("中文", "")
        match = cjk.search(visible)
        if match:
            excerpt = visible[max(0, match.start() - 40) : match.start() + 80]
            raise ValueError(
                f"English reader-facing page contains Chinese prose: {page}: {excerpt}"
            )


def _local_target(page: Path, raw: str) -> Path | None:
    parsed = urlsplit(raw)
    if parsed.scheme or raw.startswith(("#", "mailto:", "javascript:")):
        return None
    return (page.parent / unquote(parsed.path)).resolve()


def _option_values(html: str) -> set[str]:
    return {option["value"] for option in BeautifulSoup(html, "html.parser").select("option[value]")}


def _check_grouped_metrics(html: str, *, minimum_groups: int) -> None:
    soup = BeautifulSoup(html, "html.parser")
    groups = soup.select(".metric-groups")
    if len(groups) < minimum_groups or not soup.select_one(".leader-grid"):
        raise ValueError("missing grouped timing/resource comparisons")
    for group in groups:
        rows = group.select("tbody tr")
        if not rows:
            raise ValueError("empty metric comparison group")
        for row in rows:
            cells = row.select("td")[1:]
            for stat in ("mean", "p50", "p95"):
                nodes = [cell.select_one(f'[data-stat="{stat}"]') for cell in cells]
                if not nodes or any(node is None for node in nodes):
                    raise ValueError("missing metric comparison values")
                directions = {node.get("data-direction") for node in nodes}
                if len(directions) != 1 or not directions <= {"min", "neutral"}:
                    raise ValueError("invalid metric comparison direction")
                values = [float(node["data-value"]) if node.has_attr("data-value") else None
                          for node in nodes]
                numeric = [value for value in values if value is not None]
                if any(not math.isfinite(value) for value in numeric):
                    raise ValueError("non-finite metric comparison value")
                best = min(numeric) if numeric else None
                unique = best is not None and numeric.count(best) == 1
                for node, value in zip(nodes, values):
                    expected = directions == {"min"} and unique and value == best
                    if bool(node.select_one(".winner")) != expected:
                        raise ValueError("metric highlight missing, incorrect, or tied")


def check(report_dir: Path) -> dict[str, int]:
    root = report_dir.resolve()
    manifest = json.loads((root / "report-manifest.json").read_text(encoding="utf-8"))
    results = json.loads((root / "data/results.json").read_text(encoding="utf-8"))
    required_manifest_fields = (
        "task_count",
        "participant_count",
        "participants",
        "logical_results",
        "failed_results",
        "reviewed_failures",
    )
    missing_manifest_fields = [
        field for field in required_manifest_fields if field not in manifest
    ]
    if missing_manifest_fields:
        raise ValueError(
            "report manifest is missing required fields: "
            + ", ".join(missing_manifest_fields)
        )
    participants = manifest.get("participants") or []
    task_count = int(manifest.get("task_count") or 0)
    participant_count = int(manifest.get("participant_count") or 0)
    expected_results = task_count * participant_count
    if (
        task_count < 1
        or participant_count < 1
        or len(participants) != participant_count
        or len(set(participants)) != participant_count
        or manifest["logical_results"] != expected_results
        or len(results) != expected_results
    ):
        raise ValueError("report must contain the complete manifest-declared participant-task matrix")
    task_ids = {int(item["task_id"]) for item in results}
    result_keys = {(str(item["engine"]), int(item["task_id"])) for item in results}
    expected_keys = {(participant, task_id) for participant in participants for task_id in task_ids}
    if len(task_ids) != task_count or result_keys != expected_keys:
        raise ValueError("report results do not match the manifest-declared participant-task matrix")
    failures = [item for item in results if item["status"] != "pass"]
    if (
        manifest["failed_results"] != len(failures)
        or manifest["reviewed_failures"] != len(failures)
    ):
        raise ValueError("report must contain a review for every final failure")
    if any(
        not item["reason_zh"]
        or not item["reason_en"]
        or not item["cause_zh"]
        or not item["cause_en"]
        for item in failures
    ):
        raise ValueError("every failed result needs a bilingual reviewed cause")
    if any(int(item.get("execution_count") or 0) not in {1, 2} for item in results):
        raise ValueError("every result must expose one logical outcome with one or two executions")

    zh_details = {path.name for path in (root / "result").glob("*.html")}
    en_details = {path.name for path in (root / "en/result").glob("*.html")}
    if zh_details != en_details or len(zh_details) != expected_results:
        raise ValueError("Chinese and English result routes must match one-to-one")
    zh_tasks = {path.name for path in (root / "task").glob("*.html")}
    en_tasks = {path.name for path in (root / "en/task").glob("*.html")}
    if zh_tasks != en_tasks or zh_tasks != {f"{task_id}.html" for task_id in task_ids}:
        raise ValueError("every task needs matching Chinese and English contract routes")
    for task_id in task_ids:
        frozen_path = root / "data/trajectory" / f"{task_id}.json"
        if not frozen_path.is_file():
            raise ValueError(f"qualified trajectory is missing for task {task_id}")
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        action_count = len(frozen.get("tool_calls") or [])
        if frozen.get("task_id") != task_id or action_count < 1:
            raise ValueError(f"qualified trajectory is invalid for task {task_id}")
        for participant in participants:
            key = f"{participant}-{task_id}"
            for language_root in (root, root / "en"):
                page = language_root / "result" / f"{key}.html"
                text = page.read_text(encoding="utf-8")
                relative_root = "../" if language_root == root else "../../"
                if (
                    text.count('class="aligned-step ') != action_count
                    or f'data-standard-url="{relative_root}data/trajectory/{task_id}.json"' not in text
                    or f'data-evidence-url="{relative_root}data/evidence/{key}.json"' not in text
                    or 'id="step-dialog"' not in text
                ):
                    raise ValueError(f"result detail lacks aligned step evidence: {page}")
    core_names = (
        "index.html",
        "dataset.html",
        "results.html",
        "interpretation.html",
        "environment.html",
        "review.html",
        "evidence-viewer.html",
    )
    core = [
        *(root / name for name in core_names),
        *(root / "en" / name for name in core_names),
    ]
    forbidden = (
        "用户提示词",
        "我的思考",
        "assistant reasoning",
        "内部工作日志",
        "中位数",
        "median",
        "后续 179 题",
    )
    for page in core:
        text = page.read_text(encoding="utf-8")
        if any(term in text for term in forbidden):
            raise ValueError(f"formal report contains internal or retired prose: {page}")
        if "<svg" in text.lower():
            raise ValueError(f"report source contains a hand-authored SVG: {page}")

    untranslated_zh_markers = (
        "tasks · model calls",
        'aria-label="Close"',
        ">Browser Trajectory Replay</title>",
        ">Dataset</title>",
        ">Results</title>",
        ">Report interpretation</title>",
        ">Environment</title>",
        ">Review history</title>",
        ">Evidence viewer</title>",
        ">Action</th>",
        ">Target</th>",
        ">Time</th>",
        ">Status</th>",
        ">Result</th>",
        " calls</small>",
    )
    for page in [*core[: len(core_names)], *(root / "result").glob("*.html")]:
        text = page.read_text(encoding="utf-8")
        if any(marker in text for marker in untranslated_zh_markers):
            raise ValueError(f"Chinese report contains untranslated interface text: {page}")
    _check_english_localization(
        [*core[len(core_names) :], *(root / "en/result").glob("*.html"), *(root / "en/task").glob("*.html")]
    )

    checked_links = 0
    for page in root.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        for raw in re.findall(r'(?:href|src)=["\']([^"\']+)', text):
            target = _local_target(page, raw)
            if target is None:
                continue
            checked_links += 1
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file() or (target != root and root not in target.parents):
                raise ValueError(f"broken or escaping local link in {page}: {raw}")

    distributable = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".html", ".json", ".js", ".css"}
    )
    if LOCAL_PATH_RE.search(distributable) or re.search(
        r"sk-[A-Za-z0-9_-]{12,}", distributable
    ):
        raise ValueError("report contains a local path or credential-like token")
    css = (root / "assets/report.css").read_text(encoding="utf-8")
    if ".inventory{height:" not in css or "overflow:auto" not in css:
        raise ValueError("result inventory must be fixed-height and horizontally scrollable")
    results_html = (root / "results.html").read_text(encoding="utf-8")
    if (
        '<option value="fail">' not in results_html
        or 'id="diagnosis-dialog"' not in results_html
        or 'data-filter="cause"' not in results_html
        or ">执行次数</th>" not in results_html
    ):
        raise ValueError("result inventory must default to failure and open a diagnosis dialog")
    expected_categories = {item["cause_zh"] for item in failures}
    rendered_categories = _option_values(results_html)
    if not expected_categories.issubset(rendered_categories):
        raise ValueError("result inventory must filter every reviewed failure category")
    for language_root in (root, root / "en"):
        dataset_html = (language_root / "dataset.html").read_text(encoding="utf-8")
        filters = set(re.findall(r'data-dataset-filter="([^\"]+)"', dataset_html))
        if filters != {"q", "site", "type", "source"}:
            raise ValueError("dataset must support task, site, type and source filters")
        if dataset_html.count('data-task="') != task_count:
            raise ValueError("dataset must contain one filterable row per task")
        for task_id in task_ids:
            if f'href="task/{task_id}.html"' not in dataset_html or f'href="results.html?task={task_id}&status="' not in dataset_html:
                raise ValueError("every dataset task must drill down to its contract and measured outcomes")
        if 'data-dataset-count' not in dataset_html or 'data-dataset-empty' not in dataset_html:
            raise ValueError("dataset filters must show result count and empty state")
        review_html = (language_root / "review.html").read_text(encoding="utf-8")
        if 'class="table-scroll review-table"' not in review_html or review_html.count('<tbody><tr>') != 1:
            raise ValueError("review history must describe review rounds, not enumerate per-result audits")
        interpretation_html = (language_root / "interpretation.html").read_text(encoding="utf-8")
        _check_grouped_metrics(interpretation_html, minimum_groups=1)
        home_html = (language_root / "index.html").read_text(encoding="utf-8")
        _check_grouped_metrics(home_html, minimum_groups=2)
    js = (root / "assets/app.js").read_text(encoding="utf-8")
    if "const task=params.get('task');" not in js or "row.dataset.task===task" not in js:
        raise ValueError("dataset-to-results drilldown must select an exact task ID")
    if "const stepDialog=document.querySelector('#step-dialog');" not in js or "Promise.all([standard(),evidence()])" not in js:
        raise ValueError("step dialog must load qualified and actual evidence on demand")
    for page in (root / "results.html", root / "en/results.html"):
        rendered = page.read_text(encoding="utf-8")
        baseline = re.search(r'<select data-matrix-baseline>(.*?)</select>', rendered, re.S)
        if baseline is None:
            raise ValueError("same-task comparison needs a baseline browser selector")
        baseline_ids = re.findall(r'<option value="([^"]+)">', baseline.group(1))
        comparison_ids = re.findall(r'data-matrix-method value="([^"]+)"', rendered)
        if baseline_ids != participants or comparison_ids != participants:
            raise ValueError("same-task comparison controls must come from the complete participant registry")
        if rendered.count('data-pass="') != task_count or rendered.count('data-fail="') != task_count:
            raise ValueError("same-task comparison needs one grouped outcome row per task")
    return {"html_pages": len(list(root.rglob("*.html"))), "checked_links": checked_links}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.report_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
