"""Acceptance checks for the generated WebMainBench preselected-200 report."""
from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from harness.reporting.presentation import check_presentation
from bs4 import BeautifulSoup
from harness.web_fetch.report import STYLE, EXTRA, JS
from harness.web_fetch.metrics import percentile
from harness.web_fetch.webmainbench_diagnostics import ERROR_PAGE, validate_teds_revision_if_present
from harness.web_fetch.safety import contained_path
from harness.web_fetch.webmainbench_evidence import retry_chain_matches
from harness.web_fetch.webmainbench_latency_correction import prior_results_path, validate_latency_correction
from harness.web_fetch.report_changes import ROOT, webmainbench_history
from harness.report_languages import check_language_contract, generated_languages


PROVIDERS = {"kitesurf", "lightpanda", "obscura", "moli", "moli-1.1.7", "chromium"}
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
    variants = [('', 'overall（待校验）', f'{impossible_teds} 条 TEDS 记录', '评分有效性尚未通过')]
    if 'en' in generated_languages(root):
        variants.append(('en/', 'overall (under review)', f'{impossible_teds} TEDS records',
                         'score validity did not'))
    for prefix, label, count, warning in variants:
        home = BeautifulSoup((root / prefix / 'index.html').read_text(encoding='utf-8'), 'html.parser')
        review = BeautifulSoup((root / prefix / 'review/index.html').read_text(encoding='utf-8'), 'html.parser')
        if (label not in home.get_text(' ', strip=True)
                or count not in review.get_text(' ', strip=True)
                or warning not in review.get_text(' ', strip=True)):
            raise ValueError(prefix + 'Known impossible TEDS distances require a visible validity warning')


def check_report(root: Path, dataset: dict | None = None, records: list[dict] | None = None) -> None:
    check_language_contract(root)
    language_pairs = (('', 'zh'), ('en/', 'en')) if 'en' in generated_languages(root) else (('', 'zh'),)
    prefixes = tuple(prefix for prefix, _ in language_pairs)
    dataset = dataset or json.loads((root / "dataset.json").read_text(encoding="utf-8"))
    records = records or read_jsonl(root / "results.jsonl")
    latency_correction = validate_latency_correction(root)
    current_profile = json.loads((Path(__file__).resolve().parents[1] / 'evaluation/current-report.json').read_text())
    diagnostic_profile = current_profile.get('latency_diagnostic')
    diagnostic_path = root / 'latency-diagnostic.json'
    if diagnostic_profile and not diagnostic_path.is_file():
        raise ValueError('Current WebMainBench release lacks the declared latency diagnostic')
    latency_diagnostic = json.loads(diagnostic_path.read_text(encoding='utf-8')) if diagnostic_path.is_file() else None
    if latency_diagnostic:
        if (latency_diagnostic.get('kind') != 'same_host_latency_diagnostic'
                or latency_diagnostic.get('official_results_sha256') != digest(prior_results_path(root))
                or len(latency_diagnostic.get('per_case', [])) != 200
                or {row['case_id'] for row in latency_diagnostic['per_case']} !=
                   {row['id'] for row in dataset.get('cases', [])}
                or set(latency_diagnostic.get('runs', {})) != {
                    'moli-115-control', 'moli-117-repeat'}):
            raise ValueError('Published latency diagnostic is incomplete or no longer bound to official evidence')
    source_history = webmainbench_history(json.loads((ROOT / 'research/report-changes.json').read_text(encoding='utf-8')))
    bundled_history = json.loads((root / 'report-changes.json').read_text(encoding='utf-8'))
    if bundled_history.get('releases') != source_history:
        raise ValueError('Bundled WebMainBench release history differs from the reviewed source')
    for prefix, language in language_pairs:
        changes = BeautifulSoup((root / prefix / 'changes/index.html').read_text(encoding='utf-8'), 'html.parser')
        ids = [node.get('data-release-id') for node in changes.select('[data-release-id]')]
        if ids != [row['id'] for row in source_history]:
            raise ValueError(prefix + 'Release page omits a structured release record')
        latest = source_history[0]
        localized = latest['i18n']['en'] if language == 'en' else latest
        current = changes.select_one('#current-release [data-release-id]')
        if (current is None or localized['title'] not in current.get_text(' ', strip=True)
                or localized['summary'] not in changes.select_one('.hero').get_text(' ', strip=True)
                or len(current.select('tbody tr')) != len(localized['changes'])):
            raise ValueError(prefix + 'Current release content differs from structured record')
        for row, cells in zip(localized['changes'], current.select('tbody tr')):
            if [cell.get_text(' ', strip=True) for cell in cells.select('td')[-4:]] != row:
                raise ValueError(prefix + 'Current release effect differs from structured record')
        if latency_correction and not (changes.select_one('a[href$="latency-corrections.json"]')
                                       and changes.select_one('a[href$="prior-latency-results.jsonl"]')):
            raise ValueError(prefix + 'Timing correction evidence links are missing')
    teds_revision = validate_teds_revision_if_present(root, records)
    if dataset.get("id") != "webmainbench-545-preselected-200-six-methods":
        raise ValueError("Not the current WebMainBench preselected-200 report")
    cases = {row["id"]: row for row in dataset.get("cases", [])}
    if len(cases) != 200 or len(records) != 200 * len(PROVIDERS):
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
            or review.get('bindings', {}).get('results_jsonl_sha256') != digest(prior_results_path(root))
            or review.get("failures")):
        raise ValueError("WebMainBench independent review is missing or incomplete")

    anomaly_path = root / "webmainbench-anomaly-review.json"
    anomaly = json.loads(anomaly_path.read_text(encoding="utf-8"))
    scope = anomaly.get("scope", {})
    bindings = anomaly.get("bindings", {})
    triggered = anomaly.get("triggered_attempt_ids", [])
    if (anomaly.get("kind") != "webmainbench_anomaly_root_cause_review"
            or scope.get("signal_triggered_results") != 36
            or scope.get("distinct_cases") != 6
            or scope.get("unresolved_triggered_results") != 0
            or len(triggered) != 36
            or len(set(triggered)) != 36
            or bindings.get("dataset_json_sha256") != digest(root / "dataset.json")
            or bindings.get("results_jsonl_sha256") != digest(prior_results_path(root))
            or bindings.get("score_summaries_json_sha256") != digest(root / "score-summaries.json")):
        raise ValueError("WebMainBench anomaly review is missing, incomplete, or not bound to this report")

    summaries = json.loads((root / "score-summaries.json").read_text(encoding="utf-8"))
    if set(summaries) != PROVIDERS:
        raise ValueError("WebMainBench score-summary provider set mismatch")
    grouped = defaultdict(list)
    for row in records:
        grouped[row["provider"]].append(row)
        artifact = contained_path(root, row["artifact_path"])
        if digest(artifact) != row["output_sha256"]:
            raise ValueError("WebMainBench output artifact hash mismatch: " + row["attempt_id"])
        for prefix in prefixes:
            detail = root / prefix / "attempts" / f'{row["attempt_id"]}.html'
            if detail.is_file():
                detail_soup=check_presentation(detail.read_text())
                if row.get('error_message'):
                    exact=detail_soup.select_one('#result-logs pre[data-kind="error"]')
                    if exact is None or exact.get_text() != row['error_message']:
                        raise ValueError('Detail lost the exact recorded error: '+row['attempt_id'])
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
        if not retry_chain_matches(row, attempts):
            raise ValueError("WebMainBench retry chain mismatch: " + row["attempt_id"])

    executions = json.loads((root / "execution.json").read_text(encoding="utf-8")).get("batches", [])
    if {row.get("provider") for row in executions} != PROVIDERS:
        raise ValueError("WebMainBench execution manifest provider set mismatch")

    zh = {path.relative_to(root) for path in root.rglob("*.html") if "en" not in path.relative_to(root).parts}
    en = {Path(*path.relative_to(root).parts[1:]) for path in (root / "en").rglob("*.html")}
    if zh != en or len(zh) != 16 + len(cases) + len(records):
        raise ValueError("WebMainBench bilingual route inventory mismatch")
    analysis = BeautifulSoup((root / "analysis/index.html").read_text(encoding="utf-8"),"html.parser")
    if (len(analysis.select('table[data-wmb-table] tbody tr')) != len(records)
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
    if len(analysis.select("#wmb-compare-rows > tr")) != 200 or len(analysis.select("[data-wmb-compare-method]")) != len(PROVIDERS):
        raise ValueError("WebMainBench same-page group comparison is incomplete")
    if {option.get("value") for option in analysis.select('[data-wmb-filter="failure"] option') if option.get("value")} != output_errors - {None}:
        raise ValueError("WebMainBench request failure classes are incomplete")
    expected_slices = sum(len(summaries[provider].get("dimensions", {}).get(axis, {}))
                          for provider in PROVIDERS for axis in ("language", "level", "table", "code", "equation"))
    if (len(analysis.select("#wmb-slices tbody > tr")) != expected_slices
            or len(analysis.select("[data-wmb-slice-filter]")) != 3
            or not analysis.select_one(".wmb-slice-table-wrap")):
        raise ValueError("WebMainBench category slices must use one bounded, filterable table")
    expected_guide_sections = {"overall", "scope", "content", "tables", "version-differences", "delivery", "decision"}
    guides = {}
    for prefix, language in tuple((prefix, 'English' if code == 'en' else 'Chinese')
                                  for prefix, code in language_pairs):
        guide = BeautifulSoup((root / prefix / "guide/index.html").read_text(encoding="utf-8"), "html.parser")
        guides[prefix] = guide
        if {section.get("id") for section in guide.select(".wmb-editorial section[id]")} != expected_guide_sections:
            raise ValueError(f"WebMainBench {language} report interpretation is incomplete")
        if (len(guide.select(".wmb-guide-results tbody > tr")) != len(PROVIDERS)
                or len(guide.select(".wmb-guide-toc a")) != len(expected_guide_sections)
                or len(guide.select("#overall .wmb-score-part")) != 5
                or guide.select_one("#overall .wmb-score-result") is None
                or guide.select_one("#overall .wmb-denominator-matrix") is None
                or len(guide.select("#content .wmb-evidence-pair > article")) != 2
                or len(guide.select("#content .wmb-case-stats > span")) != len(PROVIDERS)
                or len(guide.select("#tables .wmb-evidence-pair > article")) != 2
                or len(guide.select("#tables .wmb-case-stats > span")) != len(PROVIDERS)
                or len(guide.select("#version-differences details.wmb-version-case")) != 5
                or len(guide.select("#version-differences .wmb-version-case .actions a")) != 15
                or len(guide.select("#version-differences .wmb-evidence-pair pre")) != 10
                or len(guide.select("#delivery .wmb-guide-latency tbody > tr")) != len(PROVIDERS)):
            raise ValueError(f"WebMainBench {language} guide lacks cohort, scoring, specimens or latency evidence")
        # Fixed representative cases in the reviewed WMB-200 cohort. Replacing
        # the cohort requires re-reviewing the article and its evidence links.
        guide_hrefs = {anchor.get("href") for anchor in guide.select("a[href]")}
        if not {
            "../datasets/c42e7f28-f109-459b-8423-2ce20f651ac9.html",
            "../datasets/686205e4-4549-42ce-bf42-f285f941c868.html",
            "../attempts/chromium__ccb6033c-0a12-4f9c-8c68-794f26129841__r001.html",
        }.issubset(guide_hrefs):
            raise ValueError(f"WebMainBench {language} guide lacks representative evidence links")
    dataset_page = BeautifulSoup((root / "datasets/index.html").read_text(encoding="utf-8"),"html.parser")
    if len(dataset_page.select('table[data-wmb-table] tbody tr')) != 200:
        raise ValueError("WebMainBench 200-case filtered dataset inventory is incomplete")
    if dataset_page.select('[data-wmb-filter="request"]') or '公式' not in dataset_page.select('#wmb-cases th')[-1].get_text(strip=True):
        raise ValueError("Dataset inventory must describe the frozen pages, not method outcomes")
    if (len(dataset_page.select('#wmb-distribution tbody > tr')) == 0
            or dataset_page.select_one('[data-wmb-distribution-filter]') is None):
        raise ValueError("Dataset axis distributions must use one filterable table")
    home_page = BeautifulSoup((root / "index.html").read_text(encoding="utf-8"), "html.parser")
    timing_variants = [('', '均值耗时 ↕')]
    if 'en' in generated_languages(root):
        timing_variants.append(('en/', 'Mean latency ↕'))
    for prefix, mean_label in timing_variants:
        localized_home = BeautifulSoup((root / prefix / "index.html").read_text(encoding="utf-8"), "html.parser")
        check_presentation(localized_home)
        check_presentation((root / prefix / "guide/index.html").read_text())
        if latency_diagnostic:
            guide_page = BeautifulSoup((root / prefix / 'guide/index.html').read_text(encoding='utf-8'), 'html.parser')
            core = localized_home.select_one('#wmb-core-results')
            if (core is None or not core.find_next('p', class_='source-note')
                    or not core.find_next('p', class_='source-note').select_one(
                        f'a[href$="datasets/{latency_diagnostic["failure_retest"]["case_id"]}.html"]')):
                raise ValueError(prefix + 'Core coverage lacks the verified fixture-failure explanation')
            for section in (localized_home.select_one('#moli-latency-rerun'), guide_page.select_one('#delivery .wmb-rerun-diagnostic')):
                if section is None or len(section.select('.wmb-rerun-table tbody tr')) != 2:
                    raise ValueError(prefix + 'Moli latency rerun must show the current two-version comparison')
                context = section.select_one('[data-timing-context="prior-overlap"]')
                context_markers = (('较早', '同时运行', '正式', '无并发', '第二轮') if not prefix
                                   else ('earlier', 'overlapped', 'formal', 'without concurrent', 'second round'))
                if latency_correction and (context is None
                                           or context.get('data-formal-round') != str(latency_correction['selected_round'])
                                           or not all(marker in context.get_text(' ', strip=True) for marker in context_markers)):
                    raise ValueError(prefix + 'Corrected timing must distinguish the earlier diagnostic run')
                if not latency_correction and context is not None:
                    raise ValueError(prefix + 'Uncorrected timing must not claim a later formal run')
                rerun_rows = dict(zip(('1.1.5', '1.1.7'),
                                      section.select('.wmb-rerun-table tbody tr')))
                for name, row in rerun_rows.items():
                    cells = row.find_all('td', recursive=False)
                    run = latency_diagnostic['balanced_200']['runs'][name]
                    if (cells[-4].get_text(strip=True) != f"{run['n']}/{run['calls']}"
                            or any(not close(float(td['data-sort']), run[key])
                                   for td, key in zip(cells[-3:], ('mean', 'p50', 'p95')))):
                        raise ValueError(prefix + 'Moli latency rerun values differ from verified evidence')
                    for key, td in zip(('mean', 'p50', 'p95'), cells[-3:]):
                        other = '1.1.7' if name == '1.1.5' else '1.1.5'
                        expected = run[key] < latency_diagnostic['balanced_200']['runs'][other][key]
                        if bool(td.select('.comparison-winner')) != expected or len(td.select('.comparison-winner')) > 1:
                            raise ValueError(prefix + 'Moli rerun winner marker disagrees with same-host evidence')
                case_rows = section.select('.wmb-rerun-cases tbody tr')
                if len(case_rows) != 20:
                    raise ValueError(prefix + 'Moli slow-page inventory must include all 20 cases')
                outliers = [row for row in latency_diagnostic['tail_repeat']['cases']
                            if abs(row['older_median_ms'] - row['newer_median_ms']) > 20]
                direction = section.select_one('[data-tail-outliers]')
                if (direction is None
                        or direction.get('data-tail-outliers') != str(len(outliers))
                        or direction.get('data-tail-newer-faster') != str(sum(
                            row['newer_median_ms'] < row['older_median_ms'] for row in outliers))
                        or direction.get('data-tail-older-faster') != str(sum(
                            row['older_median_ms'] < row['newer_median_ms'] for row in outliers))
                        or not direction.get_text(' ', strip=True)):
                    raise ValueError(prefix + 'Moli tail conclusion disagrees with per-page medians')
                for rendered, evidence in zip(case_rows, latency_diagnostic['tail_repeat']['cases']):
                    cells = rendered.find_all('td', recursive=False)
                    if (not cells[1].select_one(f'a[href$="datasets/{evidence["case_id"]}.html"]')
                            or any(not close(float(td['data-sort']), evidence[key])
                                   for td, key in zip(cells[2:], ('older_median_ms', 'newer_median_ms',
                                                                   'older_max_ms', 'newer_max_ms')))):
                        raise ValueError(prefix + 'Moli per-case repeat table differs from verified evidence')
                conclusion_markers = (('先请求', '不能保证', '20 页') if not prefix
                                      else ('first call', 'cannot be guaranteed', '20 pages'))
                if not all(marker in section.get_text(' ', strip=True) for marker in conclusion_markers):
                    raise ValueError(prefix + 'Moli repeat conclusion or ordering effect is missing')
                if len(section.select('.wmb-rerun-resource tbody tr')) != 2 or not section.select_one('a[href$="latency-diagnostic.json"]'):
                    raise ValueError(prefix + 'Moli latency rerun lacks resource evidence or per-case download')
                failed_case = latency_diagnostic['failure_retest']['case_id']
                if (not section.select_one(f'a[href$="datasets/{failed_case}.html"]')
                        or not all(marker in section.get_text(' ', strip=True) for marker in
                                   (('重试', '禁用页面脚本') if not prefix else ('retry', 'Disabling page-authored JavaScript')))):
                    raise ValueError(prefix + 'Moli failed-page retest conclusion is missing')
        home_headers = {tag.get_text(" ", strip=True) for tag in localized_home.select("#wmb-core-results th")}
        if teds_revision is not None:
            correction_label = '修正后复算' if not prefix else 'recomputed after correction'
            if correction_label not in localized_home.get_text(' ', strip=True):
                raise ValueError(prefix + 'Corrected scores must not be labeled as original official scores')
        if (len(localized_home.select("#wmb-core-results tbody > tr")) != len(PROVIDERS)
                or not {mean_label, "P50 latency ↕" if prefix else "P50 耗时 ↕", "P95 latency ↕" if prefix else "P95 耗时 ↕"}.issubset(home_headers)
                or localized_home.select_one('#wmb-core-results').get('data-col') != '2'
                or localized_home.select_one('#wmb-core-results').get('data-dir') != 'desc'
                or {"非空内容交付率 ↕", "Non-empty delivery rate ↕"} & home_headers):
            raise ValueError(prefix + "WebMainBench home must have one core comparison with mean, P50 and P95")
        table_population=localized_home.select('.wmb-table-population-table tbody tr')
        if len(table_population)!=len(PROVIDERS):raise ValueError('Table-scoring population breakdown missing')
        for tr in table_population:
            cells=tr.find_all('td',recursive=False)
            name=Path(cells[1].a['href']).stem
            eligible=extra=0
            for row in grouped[name]:
                if not row['ok']:continue
                detail=row.get('metrics',{}).get('table_edit',{}).get('details',{})
                gt=detail.get('groundtruth_table_length',0)>0
                pred=detail.get('predicted_table_length',0)>0
                eligible+=gt
                extra+=pred and not gt
            if [int(td['data-sort']) for td in cells[2:5]] != [eligible,extra,eligible+extra]:
                raise ValueError('Table-scoring breakdown disagrees with evidence: '+name)
        timing_key = localized_home.select_one(".wmb-reading-key")
        if timing_key is None or ("最终成功" if not prefix else "Final successful") not in timing_key.get_text() or "P95" not in timing_key.get_text():
            raise ValueError(prefix + "Home must explain final-success timing separately from scoring populations")
        successes = {name: [row for row in grouped[name]
                            if row['ok'] and (text := contained_path(root, row['artifact_path']).read_text()).strip()
                            and not ERROR_PAGE.search(text)] for name in PROVIDERS}
        shared_cases = set.intersection(*(set(row['case_id'] for row in rows) for rows in successes.values()))
        for tr in localized_home.select('#wmb-core-results tbody tr'):
            cells = tr.find_all('td', recursive=False)
            name = Path(cells[1].a['href']).stem
            values = [row['elapsed_ms'] for row in successes[name]]
            expected_times = (statistics.fmean(values), statistics.median(values), percentile(values, .95))
            if cells[-4].get_text(strip=True) != f'{len(values)}/200' or any(
                    not close(float(td['data-sort']), value) for td, value in zip(cells[-3:], expected_times)):
                raise ValueError(name + ': final successful-fetch timing differs from immutable calls')

        matched = localized_home.select_one('.wmb-matched-latency')
        if matched is None or str(len(shared_cases)) not in matched.get_text() or len(matched.select('tbody tr')) != len(PROVIDERS):
            raise ValueError(prefix + "Matched latency must show the measured shared-success population with scoped minimum markers")
        for tr in matched.select('tbody tr'):
            cells = tr.find_all('td', recursive=False)
            name = Path(cells[1].a['href']).stem
            values = [row['elapsed_ms'] for row in successes[name] if row['case_id'] in shared_cases]
            expected_times = (statistics.fmean(values), statistics.median(values), percentile(values, .95))
            if any(not close(float(td['data-sort']), value) for td, value in zip(cells[-3:], expected_times)):
                raise ValueError(name + ': matched-page timing differs from the common final-call population')
        if (len(localized_home.select(".wmb-denominator-matrix tbody tr")) != 2
                or len(localized_home.select(".wmb-denominator-matrix tbody td")) != 6
                or localized_home.select_one(".wmb-denominator-example .mermaid") is None):
            raise ValueError(prefix + "WebMainBench home lacks the denominator example")
        methodology = BeautifulSoup((root / prefix / "methodology/index.html").read_text(encoding="utf-8"), "html.parser")
        if methodology.select_one(".wmb-flow .mermaid") is None:
            raise ValueError(prefix + "WebMainBench methodology must lead with an evaluation-flow diagram")
        localized_review = BeautifulSoup((root / prefix / "review/index.html").read_text(encoding="utf-8"), "html.parser")
        review_text = localized_review.get_text(" ", strip=True)
        expected_review = "36 条，实际集中在 6 个页面" if not prefix else "36 of 1,200 results, concentrated in 6 pages"
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
    for page, required in ((dataset_page, 2), (adoption_page, 1)):
        wide_tables = page.select(".table-wrap > table.wide-prose-table")
        if len(wide_tables) < required or any(len(table.select("thead th")) < 6 for table in wide_tables):
            raise ValueError("Evidence-rich tables must expand horizontally inside their scroll container")
    if (".wide-prose-table{min-width:1720px}" not in EXTRA
            or ".wmb-choice-table{min-width:760px}" not in EXTRA
            or ".wmb-evidence-pair{display:grid" not in EXTRA
            or ".wmb-slice-table-wrap{max-height:70vh;overflow:auto}" not in EXTRA
            or "function wmbSliceFilters()" not in JS):
        raise ValueError("WebMainBench table width, bounded slice layout or filter logic is missing")
    for case_id in cases:
        for prefix in prefixes:
            case_page = BeautifulSoup((root / prefix / "datasets" / f"{case_id}.html").read_text(encoding="utf-8"), "html.parser")
            hrefs = {link.get("href", "") for link in case_page.select("a[href]")}
            preview = case_page.select_one('#frozen-input iframe.wmb-html-preview')
            if preview is None or preview.get('sandbox') != [] or not preview.get('srcdoc'):
                raise ValueError('Frozen HTML must have an inert in-page reader: ' + case_id)
            if (not any(f"references/{case_id}.md" in href for href in hrefs)
                    or not any(f"sources/{case_id}.html.txt" in href for href in hrefs)
                    or case_page.select("#case-results")
                    or len(case_page.select("pre.wmb-source-pre")) != 2):
                raise ValueError("WebMainBench case detail lacks independent reference or frozen input: " + case_id)
    for route in ("datasets/index.html", "analysis/index.html"):
        localized = [BeautifulSoup((root / prefix / route).read_text(encoding="utf-8"), "html.parser")
                     for prefix in prefixes]
        for selector in ("[data-wmb-filter]", "[data-wmb-slice-filter]"):
            inputs = [[(control.get("data-wmb-filter") or control.get("id"),
                        tuple(option.get("value") for option in control.select("option")))
                       for control in page.select(selector)] for page in localized]
            if len(inputs) == 2 and inputs[0] != inputs[1]:
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
