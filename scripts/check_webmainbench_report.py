"""Acceptance checks for the generated WebMainBench preselected-200 report."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from bs4 import BeautifulSoup
from harness.web_fetch.report import STYLE, EXTRA, JS


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


def check_report(root: Path, dataset: dict | None = None, records: list[dict] | None = None) -> None:
    dataset = dataset or json.loads((root / "dataset.json").read_text(encoding="utf-8"))
    records = records or read_jsonl(root / "results.jsonl")
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
    failed_count = sum(not row["ok"] for row in records)
    if f"默认先看 {failed_count} 条请求失败" not in analysis.get_text(" ", strip=True):
        raise ValueError("WebMainBench failure-first explanation does not match the measured matrix")
    if len(analysis.select("#wmb-compare-rows > tr")) != 200 or len(analysis.select("[data-wmb-compare-method]")) != 5:
        raise ValueError("WebMainBench same-page group comparison is incomplete")
    if {option.get("value") for option in analysis.select('[data-wmb-filter="failure"] option') if option.get("value")} != {
            row.get("error_type") for row in records if not row["ok"] and row.get("error_type")}:
        raise ValueError("WebMainBench request failure classes are incomplete")
    expected_slices = sum(len(summaries[provider].get("dimensions", {}).get(axis, {}))
                          for provider in PROVIDERS for axis in ("language", "level", "table", "code", "equation"))
    if (len(analysis.select("#wmb-slices tbody > tr")) != expected_slices
            or len(analysis.select("[data-wmb-slice-filter]")) != 3
            or not analysis.select_one(".wmb-slice-table-wrap")):
        raise ValueError("WebMainBench category slices must use one bounded, filterable table")
    guide = BeautifulSoup((root / "guide/index.html").read_text(encoding="utf-8"), "html.parser")
    if {section.get("id") for section in guide.select("section.section[id]")} != {
            "background", "results", "examples", "configuration", "limits", "outlook"}:
        raise ValueError("WebMainBench report interpretation is incomplete")
    if (len(guide.select("#wmb-guide-examples [role=tab]")) < 4
            or len(guide.select("#configuration table tbody > tr")) != 5
            or len(guide.select("#limits table tbody > tr")) != 5):
        raise ValueError("WebMainBench guide lacks same-page examples, routes or failure evidence")
    dataset_page = BeautifulSoup((root / "datasets/index.html").read_text(encoding="utf-8"),"html.parser")
    if len(dataset_page.select('table[data-wmb-table] tbody tr')) != 200:
        raise ValueError("WebMainBench 200-case filtered dataset inventory is incomplete")
    home_page = BeautifulSoup((root / "index.html").read_text(encoding="utf-8"), "html.parser")
    if len(home_page.select("#wmb-home-components [role=tab]")) != 5:
        raise ValueError("WebMainBench five component rankings must be switchable")
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
                    or len(case_page.select("#case-results table tbody > tr")) != 5
                    or not case_page.select_one("pre")):
                raise ValueError("WebMainBench case detail lacks full reference, frozen input or five scores: " + case_id)
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
