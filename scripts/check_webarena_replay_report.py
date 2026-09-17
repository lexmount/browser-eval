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


def _semantic_page_inventory(
    root: Path, core_names: tuple[str, ...]
) -> tuple[list[Path], list[Path], list[Path]]:
    core = [
        *(root / name for name in core_names),
        *(root / "en" / name for name in core_names),
    ]
    chinese_details = [
        *(root / "fix").glob("*.html"),
        *(root / "diagnostic").glob("*.html"),
    ]
    english_details = [
        *(root / "en/fix").glob("*.html"),
        *(root / "en/diagnostic").glob("*.html"),
    ]
    return core, chinese_details, english_details


def _local_target(page: Path, raw: str) -> Path | None:
    parsed = urlsplit(raw)
    if parsed.scheme or raw.startswith(("#", "mailto:", "javascript:")):
        return None
    return (page.parent / unquote(parsed.path)).resolve()


def _check_metric_populations(metrics: dict, results: list[dict], participants: list[str]) -> None:
    """Audit denominators against the result inventory, not renderer claims."""
    if (
        metrics.get("distribution_scope") != "all_declared_tasks"
        or metrics.get("successful_scope") != "per_method_success"
        or metrics.get("passing_intersection_scope") != "diagnostic_only"
    ):
        raise ValueError(
            "metric distributions must preserve all tasks and separate successful subsets"
        )
    methods = metrics.get("methods", [])
    if len(methods) != len(participants) or {row.get("participant_id") for row in methods} != set(
        participants
    ):
        raise ValueError("metric participants differ from the result inventory")
    names = (
        "duration",
        "cpu",
        "memory",
        "memory_peak",
        "network_rx",
        "network_tx",
        "cpu_utilization",
    )
    for method in methods:
        rows = [row for row in results if row["participant_id"] == method["participant_id"]]
        total = len(rows)
        passed = sum(row["status"] == "pass" for row in rows)
        if method.get("total") != total or method.get("passed") != passed:
            raise ValueError("metric outcome counts differ from the result inventory")
        for distributions, population in ((method, total), (method.get("successful", {}), passed)):
            for name in names:
                sample = distributions.get(name, {})
                n, missing = sample.get("n"), sample.get("missing")
                if (
                    type(n) is not int
                    or type(missing) is not int
                    or n < 0
                    or missing < 0
                    or n + missing != population
                    or sample.get("population") != population
                ):
                    raise ValueError(f"metric population or missing count is invalid: {name}")
                for statistic in ("mean", "p50", "p95"):
                    value = sample.get(statistic)
                    if (n == 0 and value is not None) or (
                        n > 0
                        and (
                            type(value) not in (int, float) or not math.isfinite(value) or value < 0
                        )
                    ):
                        raise ValueError(
                            f"missing or nonfinite metric is represented as measured: {name}"
                        )


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
    missing_manifest_fields = [field for field in required_manifest_fields if field not in manifest]
    if missing_manifest_fields:
        raise ValueError(
            "report manifest is missing required fields: " + ", ".join(missing_manifest_fields)
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
        raise ValueError(
            "report must contain the complete manifest-declared participant-task matrix"
        )
    task_ids = {int(item["task_id"]) for item in results}
    result_keys = {(str(item["participant_id"]), int(item["task_id"])) for item in results}
    expected_keys = {(participant, task_id) for participant in participants for task_id in task_ids}
    if len(task_ids) != task_count or result_keys != expected_keys:
        raise ValueError(
            "report results do not match the manifest-declared participant-task matrix"
        )
    _check_metric_populations(
        json.loads((root / "data/metrics.json").read_text(encoding="utf-8")), results, participants
    )
    failures = [item for item in results if item["status"] != "pass"]
    if manifest["failed_results"] != len(failures) or manifest["reviewed_failures"] != len(
        failures
    ):
        raise ValueError("report must contain a review for every final failure")
    if any(not item["reason_zh"] or not item["reason_en"] for item in failures):
        raise ValueError("every failed result needs a bilingual reviewed cause")

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
                    or f'data-standard-url="{relative_root}data/trajectory/{task_id}.json"'
                    not in text
                    or f'data-evidence-url="{relative_root}data/evidence/{key}.json"' not in text
                    or 'id="step-dialog"' not in text
                ):
                    raise ValueError(f"result detail lacks aligned step evidence: {page}")
    core_names = (
        "index.html",
        "dataset.html",
        "results.html",
        "fixes.html",
        "diagnostics.html",
        "environment.html",
        "review.html",
        "evidence-viewer.html",
    )
    core, chinese_details, english_details = _semantic_page_inventory(root, core_names)
    forbidden = (
        "用户提示词",
        "我的思考",
        "assistant reasoning",
        "内部工作日志",
        "中位数",
        "median",
        "后续 179 题",
    )
    for page in [*core, *chinese_details, *english_details]:
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
    for page in [*core[: len(core_names)], *chinese_details, *(root / "result").glob("*.html")]:
        text = page.read_text(encoding="utf-8")
        if any(marker in text for marker in untranslated_zh_markers):
            raise ValueError(f"Chinese report contains untranslated interface text: {page}")
    _check_english_localization(
        [
            *core[len(core_names) :],
            *english_details,
            *(root / "en/result").glob("*.html"),
            *(root / "en/task").glob("*.html"),
        ]
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
    if LOCAL_PATH_RE.search(distributable) or re.search(r"sk-[A-Za-z0-9_-]{12,}", distributable):
        raise ValueError("report contains a local path or credential-like token")
    css = (root / "assets/report.css").read_text(encoding="utf-8")
    if ".inventory{height:" not in css or "overflow:auto" not in css:
        raise ValueError("result inventory must be fixed-height and horizontally scrollable")
    results_html = (root / "results.html").read_text(encoding="utf-8")
    if '<option value="fail">' not in results_html or 'id="diagnosis-dialog"' not in results_html:
        raise ValueError("result inventory must default to failure and open a diagnosis dialog")
    for language_root in (root, root / "en"):
        dataset_html = (language_root / "dataset.html").read_text(encoding="utf-8")
        filters = set(re.findall(r'data-dataset-filter="([^\"]+)"', dataset_html))
        if filters != {"q", "site", "type", "source"}:
            raise ValueError("dataset must support task, site, type and source filters")
        if dataset_html.count('data-task="') != task_count:
            raise ValueError("dataset must contain one filterable row per task")
        for task_id in task_ids:
            if (
                f'href="task/{task_id}.html"' not in dataset_html
                or f'href="results.html?task={task_id}&status="' not in dataset_html
            ):
                raise ValueError(
                    "every dataset task must drill down to its contract and measured outcomes"
                )
        if "data-dataset-count" not in dataset_html or "data-dataset-empty" not in dataset_html:
            raise ValueError("dataset filters must show result count and empty state")
        review_html = (language_root / "review.html").read_text(encoding="utf-8")
        if (
            'class="table-scroll review-table"' not in review_html
            or review_html.count("<tbody><tr>") != 1
        ):
            raise ValueError(
                "review history must describe review rounds, not enumerate per-result audits"
            )
    js = (root / "assets/app.js").read_text(encoding="utf-8")
    if "const task=params.get('task');" not in js or "row.dataset.task===task" not in js:
        raise ValueError("dataset-to-results drilldown must select an exact task ID")
    if (
        "const stepDialog=document.querySelector('#step-dialog');" not in js
        or "Promise.all([standard(),evidence()])" not in js
    ):
        raise ValueError("step dialog must load qualified and actual evidence on demand")
    for page in (root / "results.html", root / "en/results.html"):
        rendered = page.read_text(encoding="utf-8")
        baseline = re.search(r"<select data-matrix-baseline>(.*?)</select>", rendered, re.S)
        if baseline is None:
            raise ValueError("same-task comparison needs a baseline browser selector")
        baseline_ids = re.findall(r'<option value="([^"]+)">', baseline.group(1))
        comparison_ids = re.findall(r'data-matrix-method value="([^"]+)"', rendered)
        if baseline_ids != participants or comparison_ids != participants:
            raise ValueError(
                "same-task comparison controls must come from the complete participant registry"
            )
        if (
            rendered.count('data-pass="') != task_count
            or rendered.count('data-fail="') != task_count
        ):
            raise ValueError("same-task comparison needs one grouped outcome row per task")
    return {"html_pages": len(list(root.rglob("*.html"))), "checked_links": checked_links}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.report_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
