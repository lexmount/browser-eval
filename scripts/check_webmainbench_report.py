"""Acceptance checks for the generated WebMainBench preselected-200 report."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from bs4 import BeautifulSoup
from harness.web_fetch.report import STYLE, EXTRA, JS
from harness.web_fetch.webmainbench_diagnostics import ERROR_PAGE, validate_teds_revision_if_present
from harness.web_fetch.safety import contained_path


PROVIDERS = {"kitesurf", "lightpanda", "obscura", "moli", "chromium"}
CORE = ("text_edit", "code_edit", "table_edit", "table_TEDS", "formula_edit")
EXPECTED_STYLE_SHA256 = hashlib.sha256((STYLE + EXTRA).encode("utf-8")).hexdigest()
EXPECTED_SCRIPT_SHA256 = hashlib.sha256(JS.encode("utf-8")).hexdigest()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def close(left: float, right: float) -> bool:
    return abs(left - right) <= 1e-12


def check_teds_warnings(root: Path, impossible_teds: int) -> None:
    if not impossible_teds:
        return
    for prefix, label, count, warning in (
        ('', 'overall（待校验）', f'{impossible_teds} 条 TEDS 记录', '评分有效性尚未通过'),
        ('en/', 'overall (under review)', f'{impossible_teds} TEDS records',
         'score validity did not'),
    ):
        home = BeautifulSoup((root / prefix / 'index.html').read_text(encoding='utf-8'), 'html.parser')
        review = BeautifulSoup((root / prefix / 'review/index.html').read_text(encoding='utf-8'), 'html.parser')
        if (label not in home.get_text(' ', strip=True)
                or count not in review.get_text(' ', strip=True)
                or warning not in review.get_text(' ', strip=True)):
            raise ValueError(prefix + 'Known impossible TEDS distances require a visible validity warning')


def check_report(root: Path, dataset: dict | None = None, records: list[dict] | None = None) -> None:
    dataset = dataset or json.loads((root / "dataset.json").read_text(encoding="utf-8"))
    records = records or read_jsonl(root / "results.jsonl")
    teds_revision = validate_teds_revision_if_present(root, records)
    if dataset.get("id") != "webmainbench-545-preselected-200-five-browsers":
        raise ValueError("Not the current WebMainBench preselected-200 report")
    cases = {row["id"]: row for row in dataset.get("cases", [])}
    if len(cases) != 200 or len(records) != 1000:
        raise ValueError("WebMainBench report matrix size mismatch")
    expected = {(provider, case_id) for provider in PROVIDERS for case_id in cases}
    if {(row.get("provider"), row.get("case_id")) for row in records} != expected:
        raise ValueError("WebMainBench report provider/case matrix mismatch")
    if len({row.get("attempt_id") for row in records}) != len(records):
        raise ValueError("WebMainBench report attempt IDs are not unique")

    sample_path = root / "sample-manifest.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample_sha = digest(sample_path)
    sample_ids = {row.get("track_id") for row in sample.get("cases", [])}
    if (sample_sha != dataset.get("sample_manifest_sha256")
            or sample_ids != set(cases)
            or sample.get("sampling", {}).get("size") != 200):
        raise ValueError("WebMainBench sample binding mismatch")

    review = json.loads((root / "result-review.json").read_text(encoding="utf-8"))
    if (review.get("status") != "passed" or set(review.get("providers", [])) != PROVIDERS
            or review.get("sample_manifest_sha256") != sample_sha
            or review.get("sample_case_count") != 200
            or review.get("failures")):
        raise ValueError("WebMainBench independent review is missing or incomplete")

    anomaly_path = root / "webmainbench-anomaly-review.json"
    anomaly = json.loads(anomaly_path.read_text(encoding="utf-8"))
    scope = anomaly.get("scope", {})
    bindings = anomaly.get("bindings", {})
    triggered = anomaly.get("triggered_attempt_ids", [])
    if (anomaly.get("kind") != "webmainbench_anomaly_root_cause_review"
            or scope.get("signal_triggered_results") != 30
            or scope.get("distinct_cases") != 6
            or scope.get("unresolved_triggered_results") != 0
            or len(triggered) != 30
            or len(set(triggered)) != 30
            or bindings.get("dataset_json_sha256") != digest(root / "dataset.json")
            or bindings.get("results_jsonl_sha256") != digest(root / "results.jsonl")
            or bindings.get("score_summaries_json_sha256") != digest(root / "score-summaries.json")):
        raise ValueError("WebMainBench anomaly review is missing, incomplete, or not bound to this report")

    summaries = json.loads((root / "score-summaries.json").read_text(encoding="utf-8"))
    if set(summaries) != PROVIDERS:
        raise ValueError("WebMainBench score-summary provider set mismatch")
    grouped = defaultdict(list)
    for row in records:
        grouped[row["provider"]].append(row)
        artifact = root / row["artifact_path"]
        if digest(artifact) != row["output_sha256"]:
            raise ValueError("WebMainBench output artifact hash mismatch: " + row["attempt_id"])
        for prefix in ("", "en/"):
            detail = root / prefix / "attempts" / f'{row["attempt_id"]}.html'
            if not detail.is_file():
                raise ValueError("WebMainBench attempt-detail route missing: " + row["attempt_id"])
        case = cases[row["case_id"]]
        if row["input_html_sha256"] != case["input_html_sha256"]:
            raise ValueError("WebMainBench input hash mismatch: " + row["attempt_id"])
    for case_id, case in cases.items():
        if digest(root / "references" / f"{case_id}.md") != case["reference_sha256"]:
            raise ValueError("WebMainBench reference hash mismatch: " + case_id)
        if digest(root / "sources" / f"{case_id}.html.txt") != case["input_html_sha256"]:
            raise ValueError("WebMainBench frozen input HTML hash mismatch: " + case_id)
    for provider, rows in grouped.items():
        totals = {metric: 0.0 for metric in CORE}; denominators = {metric: 0 for metric in CORE}
        for row in rows:
            for metric in CORE:
                value = row.get("metrics", {}).get(metric, {})
                if value.get("success"):
                    totals[metric] += value["score"]; denominators[metric] += 1
        scores = {metric: totals[metric] / denominators[metric] if denominators[metric] else 0.0 for metric in CORE}
        scores["overall"] = sum(scores.values()) / len(CORE)
        observed = summaries[provider]["official_aggregate"]
        if denominators != observed["denominators"] or any(not close(value, observed["scores"][name]) for name, value in scores.items()):
            raise ValueError(provider + ": report score summary mismatch")

    physical = read_jsonl(root / "physical-attempts.jsonl")
    physical_by_result = defaultdict(list)
    for row in physical:
        physical_by_result[(row["provider"], row["track_id"])].append(row)
    if set(physical_by_result) != expected:
        raise ValueError("WebMainBench physical-attempt matrix mismatch")
    for row in records:
        attempts = physical_by_result[(row["provider"], row["case_id"])]
        if (len(attempts) != row["physical_attempt_count"]
                or [item["physical_attempt"] for item in attempts] != list(range(1, len(attempts) + 1))
                or attempts[-1]["output_sha256"] != row["output_sha256"]
                or not close(sum(item["elapsed_ms"] for item in attempts), row["total_elapsed_ms"])):
            raise ValueError("WebMainBench retry chain mismatch: " + row["attempt_id"])

    executions = json.loads((root / "execution.json").read_text(encoding="utf-8")).get("batches", [])
    if {row.get("provider") for row in executions} != PROVIDERS:
        raise ValueError("WebMainBench execution manifest provider set mismatch")

    zh = {path.relative_to(root) for path in root.rglob("*.html") if "en" not in path.relative_to(root).parts}
    en = {Path(*path.relative_to(root).parts[1:]) for path in (root / "en").rglob("*.html")}
    if zh != en or len(zh) != 1215:
        raise ValueError("WebMainBench bilingual route inventory mismatch")
    analysis = BeautifulSoup((root / "analysis/index.html").read_text(encoding="utf-8"),"html.parser")
    if (len(analysis.select('table[data-wmb-table] tbody tr')) != 1000
            or {tag.get('data-wmb-filter') for tag in analysis.select('[data-wmb-filter]')} != {
                'method','axis','category','request','failure','metric','band','query'}):
        raise ValueError("WebMainBench full-matrix inspection table or filters are incomplete")
    request = analysis.select_one('[data-wmb-filter="request"]')
    if request is None or request.get("data-wmb-default") != "failed" or request.select_one("option[selected]").get("value") != "failed":
        raise ValueError("WebMainBench request filter must initially locate failed requests")
    body_states = {}
    output_errors = set()
    inspection_links = {anchor.get('href'): anchor for anchor in analysis.select('#wmb-attempt-matrix a[href]')}
    for row in records:
        output = contained_path(root, row['artifact_path']).read_text(encoding='utf-8', errors='replace')
        error_page = bool(ERROR_PAGE.search(output))
        present = bool(row['ok'] and output.strip() and not error_page)
        body_states[row['attempt_id']] = present
        if not present:
            output_errors.add('error_page' if error_page else 'empty_body' if row['ok'] else row.get('error_type'))
        target = '../attempts/' + row['attempt_id'] + '.html'
        anchor = inspection_links.get(target)
        if anchor is None or anchor.find_parent('tr').get('data-request') != ('success' if present else 'failed'):
            raise ValueError('WebMainBench output filter disagrees with artifact: ' + row['attempt_id'])
    failed_count = sum(not present for present in body_states.values())
    if f"默认先显示 {failed_count} 条没有生成正文" not in analysis.get_text(" ", strip=True):
        raise ValueError("WebMainBench failure-first explanation does not match the measured matrix")
    if not any("响应与正文" in tag.get_text(strip=True) for tag in analysis.select("#wmb-attempt-matrix th")):
        raise ValueError("WebMainBench result rows must expose response and body-text condition")
    if len(analysis.select("#wmb-compare-rows > tr")) != 200 or len(analysis.select("[data-wmb-compare-method]")) != 5:
        raise ValueError("WebMainBench same-page group comparison is incomplete")
    if {option.get("value") for option in analysis.select('[data-wmb-filter="failure"] option') if option.get("value")} != output_errors - {None}:
        raise ValueError("WebMainBench request failure classes are incomplete")
    expected_slices = sum(len(summaries[provider].get("dimensions", {}).get(axis, {}))
                          for provider in PROVIDERS for axis in ("language", "level", "table", "code", "equation"))
    if (len(analysis.select("#wmb-slices tbody > tr")) != expected_slices
            or len(analysis.select("[data-wmb-slice-filter]")) != 3
            or not analysis.select_one(".wmb-slice-table-wrap")):
        raise ValueError("WebMainBench category slices must use one bounded, filterable table")
    guide = BeautifulSoup((root / "guide/index.html").read_text(encoding="utf-8"), "html.parser")
    if {section.get("id") for section in guide.select("section.section[id]")} != {
            "success-layers", "scope-gap", "causes", "denominators", "method-profiles",
            "missing-body", "decisions", "limits"}:
        raise ValueError("WebMainBench report interpretation is incomplete")
    if (len(guide.select("#scope-gap table tbody > tr")) != 5
            or len(guide.select("#causes table tbody > tr")) != 5
            or len(guide.select("#denominators table tbody > tr")) != 5
            or len(guide.select("#method-profiles table tbody > tr")) != 5
            or len(guide.select("#missing-body table tbody > tr")) != 6
            or guide.select_one("#success-layers [data-wmb-flow-fallback]") is None):
        raise ValueError("WebMainBench guide lacks aggregate mechanism or root-cause evidence")
    dataset_page = BeautifulSoup((root / "datasets/index.html").read_text(encoding="utf-8"),"html.parser")
    if len(dataset_page.select('table[data-wmb-table] tbody tr')) != 200:
        raise ValueError("WebMainBench 200-case filtered dataset inventory is incomplete")
    if dataset_page.select('[data-wmb-filter="request"]') or '公式' not in dataset_page.select('#wmb-cases th')[-1].get_text(strip=True):
        raise ValueError("Dataset inventory must describe the frozen pages, not method outcomes")
    if (len(dataset_page.select('#wmb-distribution tbody > tr')) == 0
            or dataset_page.select_one('[data-wmb-distribution-filter]') is None):
        raise ValueError("Dataset axis distributions must use one filterable table")
    home_page = BeautifulSoup((root / "index.html").read_text(encoding="utf-8"), "html.parser")
    for prefix, mean_label in (("", "均值耗时 ↕"), ("en/", "Mean latency ↕")):
        localized_home = BeautifulSoup((root / prefix / "index.html").read_text(encoding="utf-8"), "html.parser")
        home_headers = {tag.get_text(" ", strip=True) for tag in localized_home.select("#wmb-core-results th")}
        if teds_revision is not None:
            correction_label = '本地修正版' if not prefix else 'locally corrected'
            if correction_label not in localized_home.get_text(' ', strip=True):
                raise ValueError(prefix + 'Corrected scores must not be labeled as original official scores')
        if (len(localized_home.select("#wmb-core-results tbody > tr")) != 5
                or not {mean_label, "P50 ↕", "P95 ↕"}.issubset(home_headers)
                or {"非空内容交付率 ↕", "Non-empty delivery rate ↕"} & home_headers):
            raise ValueError(prefix + "WebMainBench home must have one core comparison with mean, P50 and P95")
        methodology = BeautifulSoup((root / prefix / "methodology/index.html").read_text(encoding="utf-8"), "html.parser")
        if methodology.select_one(".wmb-flow .mermaid") is None:
            raise ValueError(prefix + "WebMainBench methodology must lead with an evaluation-flow diagram")
        localized_review = BeautifulSoup((root / prefix / "review/index.html").read_text(encoding="utf-8"), "html.parser")
        review_text = localized_review.get_text(" ", strip=True)
        expected_review = "30 条，实际集中在 6 个页面" if not prefix else "30 of 1,000 results, concentrated in 6 pages"
        if expected_review not in review_text or len(localized_review.select("table.wide-prose-table tbody > tr")) < 6:
            raise ValueError(prefix + "WebMainBench anomaly causes must be visible in the review")
    impossible_teds = sum(
        all(key in detail for key in ("predicted_nodes", "groundtruth_nodes", "edit_distance"))
        and detail["edit_distance"] + 1e-9 < abs(detail["predicted_nodes"] - detail["groundtruth_nodes"])
        for row in records
        for detail in [row.get("metrics", {}).get("table_TEDS", {}).get("details", {})]
    )
    check_teds_warnings(root, impossible_teds)
    adoption_page = BeautifulSoup((root / "adoption/index.html").read_text(encoding="utf-8"), "html.parser")
    for page, required in ((dataset_page, 2), (adoption_page, 1), (guide, 1)):
        wide_tables = page.select(".table-wrap > table.wide-prose-table")
        if len(wide_tables) < required or any(len(table.select("thead th")) < 6 for table in wide_tables):
            raise ValueError("Evidence-rich tables must expand horizontally inside their scroll container")
    if (".wide-prose-table{min-width:1720px}" not in EXTRA
            or ".wmb-slice-table-wrap{max-height:70vh;overflow:auto}" not in EXTRA
            or "function wmbSliceFilters()" not in JS):
        raise ValueError("WebMainBench table width, bounded slice layout or filter logic is missing")
    for case_id in cases:
        for prefix in ("", "en/"):
            case_page = BeautifulSoup((root / prefix / "datasets" / f"{case_id}.html").read_text(encoding="utf-8"), "html.parser")
            hrefs = {link.get("href", "") for link in case_page.select("a[href]")}
            if (not any(f"references/{case_id}.md" in href for href in hrefs)
                    or not any(f"sources/{case_id}.html.txt" in href for href in hrefs)
                    or case_page.select("#case-results")
                    or len(case_page.select("pre.wmb-source-pre")) != 2):
                raise ValueError("WebMainBench case detail lacks independent reference or frozen input: " + case_id)
    for route in ("datasets/index.html", "analysis/index.html"):
        localized = [BeautifulSoup((root / prefix / route).read_text(encoding="utf-8"), "html.parser")
                     for prefix in ("", "en/")]
        for selector in ("[data-wmb-filter]", "[data-wmb-slice-filter]"):
            inputs = [[(control.get("data-wmb-filter") or control.get("id"),
                        tuple(option.get("value") for option in control.select("option")))
                       for control in page.select(selector)] for page in localized]
            if inputs[0] != inputs[1]:
                raise ValueError("WebMainBench bilingual filter fields or values differ: " + route)
    for provider in PROVIDERS:
        method_page = BeautifulSoup((root / "providers" / f'{provider}.html').read_text(encoding="utf-8"),"html.parser")
        if len(method_page.select('table[data-wmb-table] tbody tr')) != 200:
            raise ValueError(provider + ": filtered per-method result inventory is incomplete")
    for relative in zh:
        zh_soup = BeautifulSoup((root / relative).read_text(encoding="utf-8"), "html.parser")
        en_soup = BeautifulSoup((root / "en" / relative).read_text(encoding="utf-8"), "html.parser")
        if Counter(tag.name for tag in zh_soup.find_all()) != Counter(tag.name for tag in en_soup.find_all()):
            raise ValueError("WebMainBench bilingual semantic-shape mismatch: " + str(relative))
        for soup in (zh_soup, en_soup):
            for table in soup.select("table"):
                width = len(table.select("thead th"))
                if any(len(row.find_all("td", recursive=False)) != width for row in table.select("tbody > tr")):
                    raise ValueError("WebMainBench table width mismatch: " + str(relative))
    if digest(root / "assets" / "style.css") != EXPECTED_STYLE_SHA256:
        raise ValueError("Report CSS differs from the reviewed reference-shell extension")
    if digest(root / "assets" / "report.js") != EXPECTED_SCRIPT_SHA256:
        raise ValueError("Report JavaScript differs from the reviewed inspection controls")


def check_revision(root: Path) -> None:
    check_report(root)
    changes = BeautifulSoup((root / "changes" / "index.html").read_text(encoding="utf-8"), "html.parser").get_text(" ", strip=True)
    if "200" not in changes or "Moli" not in changes or "1.1.5" not in changes:
        raise ValueError("WebMainBench release page omits the current scope or Moli version")
