"""Release gates for the frozen sample and complete WebMainBench cohorts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from bs4 import BeautifulSoup

from harness.record_io import load_jsonl
from harness.report_languages import (
    check_language_contract,
    generated_languages,
    localized_report_routes,
)
from harness.reporting.presentation import check_presentation
from lexbench_webfetch.metrics import percentile
from harness.web_fetch.report import ERROR_PAGE, EXTRA, JS, STYLE
from harness.web_fetch.report_changes import webmainbench_history
from lexbench_webfetch.paths import contained_path
from harness.web_fetch.safety import reject_evidence_symlinks
from harness.web_fetch.webmainbench_diagnostics import validate_teds_revision_if_present
from harness.web_fetch.webmainbench_evidence import retry_chain_matches
from harness.web_fetch.webmainbench_latency_correction import (
    prior_results_path,
    validate_latency_correction,
)

ROOT = Path(__file__).resolve().parents[2]


def validate_sample_content(root: Path, *, authority_root: Path, binding_path: Path | None = None) -> None:
    from harness.reporting.assets import load_bound_content

    selected = binding_path or contained_path(authority_root, 'evaluation/webmainbench-content-binding.json', reject_symlinks=True)
    reject_evidence_symlinks(selected)
    expected = selected.read_bytes()
    actual = contained_path(root, 'report-content-binding.json',
                            reject_symlinks=True).read_bytes()
    if actual != expected:
        raise ValueError('Sample report content binding differs from authority')
    content = load_bound_content(root, json.loads(expected), evidence_root=root,
                                 authority_root=authority_root)
    for language in generated_languages(root):
        route = 'en/guide/index.html' if language == 'en' else 'guide/index.html'
        page = contained_path(root, route, reject_symlinks=True).read_text(encoding='utf-8')
        body = re.search(r'<main>(.*)</main>', page, re.S)
        if body is None or body.group(1) != content['guide'][language]:
            raise ValueError('Sample guide differs from bound content: ' + language)


def authority_directory(authority_root: Path | None, profile: str) -> Path:
    if authority_root is None:
        raise ValueError("Select an external authority bundle with --authority-root")
    repository = authority_root.resolve()
    if not (repository / profile).is_file():
        raise ValueError("WebMainBench release authority is missing; pass --authority-root "
                         "pointing to the selected instance bundle")
    return repository


def check_result_payload(row: dict, index: dict, artifact_index: dict, shards: dict) -> None:
    key = row["provider"] + "|" + row["case_id"]
    shard_path = index.get(key)
    if shard_path not in shards:
        raise ValueError("Result shard missing: " + key)
    bundled = shards[shard_path]["results"].get(key)
    if not bundled or {name: value for name, value in bundled.items() if name not in ("diagnosis", "review")} != row:
        raise ValueError("Result shard differs from scored evidence: " + key)
    artifact = row["artifact_path"]
    if artifact_index.get(artifact) != shard_path:
        raise ValueError("Evidence target points to wrong shard: " + artifact)
    digest = shards[shard_path]["artifacts"].get(artifact)
    value = shards[shard_path]["blobs"].get(digest)
    if not isinstance(value, str) or hashlib.sha256(value.encode()).hexdigest() != row["output_sha256"]:
        raise ValueError("Raw output absent or changed: " + artifact)


def check_diagnosis_inventory(analysis: BeautifulSoup) -> None:
    dialog = analysis.select_one('dialog#diagnosis-dialog')
    required = ('#diagnosis-title', '#diagnosis-stage', '#diagnosis-error',
                '#diagnosis-log', '#diagnosis-cause', '#diagnosis-treatment',
                '#diagnosis-result', '#diagnosis-output', '#diagnosis-case',
                '#diagnosis-close')
    if not dialog or any(dialog.select_one(selector) is None for selector in required):
        raise ValueError('Failure diagnosis dialog lacks error, review or evidence targets')
    for row in analysis.select('tr[data-result]'):
        if (row.get('data-kind') != '未触发诊断'
                and (not row.get('data-case-id')
                     or row.select_one('button[data-diagnose][onclick="openDiagnosis(this)"]') is None)):
            raise ValueError('Diagnostic result row cannot open its in-page diagnosis')


def check_interaction_script(script: str) -> None:
    required = ("dialog.addEventListener('close',()=>diagnosisFocus?.focus())",)
    if any(fragment not in script for fragment in required):
        raise ValueError('Report script lacks diagnosis focus restoration')


def check_table_interaction(root: Path, *, provider_count: int, case_count: int) -> None:
    check_interaction_script((root / 'assets/report.js').read_text(encoding='utf-8'))
    home = check_presentation((root / 'index.html').read_text(encoding='utf-8'))
    style_href = home.select_one('link[rel="stylesheet"]')
    script_src = home.select_one('script[src]')
    if (not style_href or not script_src
            or '?v=' not in style_href.get('href', '')
            or style_href['href'].split('?v=', 1)[1] != script_src.get('src', '').split('?v=', 1)[-1]):
        raise ValueError('Report assets lack a shared cache-busting version')
    core = home.select_one('table#core-results')
    if (not core or len(core.select('tbody > tr')) != provider_count
            or len(core.select('th[aria-sort="none"] > button[data-table-sort][onclick="sortReportTable(this)"]')) != 8
            or any(cell.get('data-sort') is None for cell in core.select('tbody > tr > td.number'))):
        raise ValueError('Core comparison lacks complete stable sorting semantics')
    dataset = BeautifulSoup((root / 'dataset/index.html').read_text(encoding='utf-8'), 'html.parser')
    dataset_table = dataset.select_one('table#dataset-inventory')
    if (not dataset.select_one('#dataset-q') or not dataset.select_one('#dataset-language')
            or not dataset.select_one('#dataset-level') or not dataset.select_one('#dataset-count')
            or not dataset_table or len(dataset_table.select('tbody > tr[data-dataset]')) != case_count
            or len(dataset_table.select('button[data-table-sort][onclick="sortReportTable(this)"]')) != 4):
        raise ValueError('Dataset inventory lacks complete search, filters or sorting')
    analysis = BeautifulSoup((root / 'analysis/index.html').read_text(encoding='utf-8'), 'html.parser')
    inventory = analysis.select_one('table#result-inventory')
    if (not inventory or len(inventory.select('tbody > tr[data-result]')) != case_count * provider_count
            or len(inventory.select('button[data-table-sort][onclick="sortReportTable(this)"]')) != 5
            or any(cell.get('data-sort') is None for cell in inventory.select('tbody > tr > td:nth-of-type(4), tbody > tr > td:nth-of-type(5)'))):
        raise ValueError('Result inventory lacks complete filters or stable sorting semantics')


def check_latency_diagnostic(section, evidence: dict, *, formal_round=None) -> None:
    """Check displayed timing against the selected diagnostic, independent of version names."""
    if section is None:
        raise ValueError('Latency diagnostic section is missing')
    runs = evidence['balanced_200']['runs']
    table = section.select_one('.wmb-rerun-table')
    rows = table.select('tbody tr') if table else []
    if len(rows) != len(runs):
        raise ValueError('Latency diagnostic version population differs')
    for rendered, (version, run) in zip(rows, runs.items()):
        cells = rendered.find_all('td', recursive=False)
        if len(cells) != 6 or not cells[1].get_text(strip=True).endswith(version) or cells[2].get_text(strip=True) != f"{run['n']}/{run['calls']}":
            raise ValueError('Latency diagnostic version or call count differs')
        for cell, metric in zip(cells[3:], ('mean', 'p50', 'p95')):
            values = [item[metric] for item in runs.values()]
            won = run[metric] == min(values) and values.count(run[metric]) == 1
            if (not close(float(cell['data-sort']), run[metric])
                    or len(cell.select('.comparison-winner')) != int(won)):
                raise ValueError('Latency diagnostic value or unique winner differs')
    context = section.select_one('[data-timing-context]')
    if formal_round is not None:
        if context is None or context.get('data-formal-round') != str(formal_round) or not context.get_text(strip=True):
            raise ValueError('Latency diagnostic must distinguish the separate formal timing')
    elif context is not None:
        raise ValueError('Latency diagnostic claims an unbound formal timing')
    cases = evidence['tail_repeat']['cases']
    case_rows = section.select('.wmb-rerun-cases tbody tr')
    if len(case_rows) != len(cases):
        raise ValueError('Latency diagnostic repeat population differs')
    for rendered, case in zip(case_rows, cases):
        cells = rendered.find_all('td', recursive=False)
        if len(cells) != 6 or not cells[1].select_one(f'a[href$="datasets/{case["case_id"]}.html"]'):
            raise ValueError('Latency diagnostic page evidence differs')
        for cell, metric in zip(cells[2:], ('older_median_ms', 'newer_median_ms', 'older_max_ms', 'newer_max_ms')):
            if not close(float(cell['data-sort']), case[metric]):
                raise ValueError('Latency diagnostic page timing differs')
    outliers = [case for case in cases if abs(case['older_median_ms'] - case['newer_median_ms']) > 20]
    direction = section.select_one('[data-tail-outliers]')
    expected = {'data-tail-outliers': len(outliers),
                'data-tail-newer-faster': sum(case['newer_median_ms'] < case['older_median_ms'] for case in outliers),
                'data-tail-older-faster': sum(case['older_median_ms'] < case['newer_median_ms'] for case in outliers)}
    if direction is None or any(direction.get(key) != str(value) for key, value in expected.items()) or not direction.get_text(strip=True):
        raise ValueError('Latency diagnostic tail conclusion differs')
    if len(section.select('.wmb-rerun-resource tbody tr')) != len(evidence['resource']) or not section.select_one('a[href$="latency-diagnostic.json"]'):
        raise ValueError('Latency diagnostic resource population or receipt is missing')
    failed_case = (evidence.get('failure_retest') or {}).get('case_id')
    if failed_case and not section.select_one(f'a[href$="datasets/{failed_case}.html"]'):
        raise ValueError('Latency diagnostic failure specimen is missing')


def check(root: Path, *, authority_root: Path | None = None) -> None:
    repo = authority_directory(authority_root, "evaluation/webmainbench-full/current-report.json")
    profile_path = repo / 'evaluation/webmainbench-full/current-report.json'
    target = json.loads((repo / 'evaluation/webmainbench-full/publication-target.json').read_text(encoding='utf-8'))
    registry_path = repo / 'evaluation/webmainbench-full/moli-variants.json'
    registry = json.loads(registry_path.read_text(encoding='utf-8'))
    variants = registry['variants']
    variant_ids = [item['provider'] for item in variants]
    expected_variant_hashes = {
        item['provider']: hashlib.sha256(
            (repo / 'evaluation/webmainbench-full' / item['profile']).read_bytes()
        ).hexdigest() for item in variants
    }
    if (target.get('profile_sha256') != hashlib.sha256(profile_path.read_bytes()).hexdigest()
            or target.get('variant_profile_sha256') != expected_variant_hashes
            or not str(target.get('expected_remote_version', '')).startswith('ver_')
            or not str(target.get('share_url', '')).startswith(str(target.get('host', '')).rstrip('/') + '/v/')
            or not target.get('site_slug')):
        raise ValueError('WebMainBench publication target is not bound to the certified profile')
    from .webmainbench_evidence import read_method_binding
    _, method_binding = read_method_binding(root)
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    expected_providers = profile["providers"] + variant_ids
    provider_count = len(expected_providers)
    case_count = profile["case_count"]
    teds_path = root / 'teds-correction.json'
    teds_receipt = json.loads(teds_path.read_text(encoding='utf-8')) if teds_path.is_file() else None
    scoring_receipt_path = root / 'scoring-correction.json'
    if not scoring_receipt_path.is_file():
        raise ValueError('scoring-correction.json missing from report evidence')
    scoring_binding = json.loads(
        (repo / 'evaluation/webmainbench-full/scoring-correction-binding.json').read_text(encoding='utf-8')
    )
    scoring_receipt = json.loads(scoring_receipt_path.read_text(encoding='utf-8'))
    bound_variants = {item['provider']: item for item in method_binding.get('variants', [])}
    if (method_binding.get('kind') not in {'webmainbench_seven_method_full_scope_binding', 'webmainbench_method_cohort_binding'}
            or method_binding.get('logical_results') != profile['case_count'] * (len(profile['providers']) + len(variants))
            or method_binding.get('registry_sha256') != hashlib.sha256(registry_path.read_bytes()).hexdigest()
            or method_binding.get('results_sha256') != (
                teds_receipt.get('input_results_sha256') if teds_receipt
                else scoring_receipt.get('input_results_sha256'))
            or any(bound_variants.get(item['provider'], {}).get('profile_sha256') != expected_variant_hashes[item['provider']]
                   for item in variants)):
        raise ValueError('Seven-method report binding missing or changed')
    if (scoring_binding.get('kind') != 'webmainbench_shared_population_scoring_correction_binding'
            or scoring_binding.get('receipt_sha256') != hashlib.sha256(scoring_receipt_path.read_bytes()).hexdigest()
            or scoring_binding.get('scorer_commit') != scoring_receipt.get('scorer_commit')
            or scoring_binding.get('regression_registry_sha256') != scoring_receipt.get('regression_registry_sha256')
            or scoring_binding.get('results_sha256') != scoring_receipt.get('output_results_sha256')
            or scoring_receipt.get('provider_calls') != 0):
        raise ValueError('Shared-population scoring correction differs from repository binding')
    home_html = (root / 'index.html').read_text(encoding='utf-8')
    methodology_html = (root / 'methodology/index.html').read_text(encoding='utf-8')
    if ('https://github.com/lexmount/WebMainBench/tree/lex-main' not in home_html
            or scoring_receipt['scorer_commit'] not in methodology_html
            or 'lex-main 修正评分器提交' not in methodology_html):
        raise ValueError('Public report does not identify the bound lex-main scorer')
    releases = json.loads((repo / 'research/report-changes.json').read_text(encoding='utf-8'))['releases']
    expected_releases = [item for item in releases if 'webmainbench_frozen_html_to_markdown' in item.get('comparison_kinds', [])]
    history = BeautifulSoup((root / 'history/index.html').read_text(encoding='utf-8'), 'html.parser')
    history_rows = history.select('tr[data-release-id]')
    if ([row['data-release-id'] for row in history_rows] != [item['id'] for item in expected_releases]
            or any(item['title'] not in row.get_text(' ', strip=True) or
                   item['summary'] not in row.get_text(' ', strip=True)
                   for row, item in zip(history_rows, expected_releases))):
        raise ValueError('WebMainBench release history differs from structured records')
    binding_path = repo / "evaluation/webmainbench-full/content-binding.json"
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    for path, expected in ((root / "report-content.json", binding["sha256"]),):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Bound report asset changed: " + path.name)
    failure_binding = json.loads((repo / "evaluation/webmainbench-full/failure-analysis-binding.json").read_text(encoding="utf-8"))
    failure_bytes = (root / "failure-analysis.json").read_bytes()
    if hashlib.sha256(failure_bytes).hexdigest() != failure_binding["sha256"]:
        raise ValueError("Bound failure analysis changed")
    failure_analysis = json.loads(failure_bytes)
    manifest = json.loads((root / "report-manifest.json").read_text(encoding="utf-8"))
    if manifest != {"schema_version": 1, "report": "webmainbench-545-full",
                    "languages": ["zh"], "case_count": case_count, "provider_count": provider_count,
                    "result_count": case_count * provider_count}:
        raise ValueError("Incomplete WebMainBench report manifest")
    dataset = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
    records = load_jsonl(root / "results.jsonl")
    correction_binding = repo / 'evaluation/webmainbench-full/case-correction-binding.json'
    if correction_binding.exists():
        receipt = json.loads((root / 'case-correction.json').read_text())
        if (receipt['preflight'] != receipt['postflight']
                or receipt['current_results_sha256'] != hashlib.sha256((root / 'results.jsonl').read_bytes()).hexdigest()
                or receipt['score_summaries_sha256'] != hashlib.sha256((root / 'score-summaries.json').read_bytes()).hexdigest()):
            raise ValueError('Published case correction differs from its certified results')
        selected = [r for r in records if r['case_id'] == receipt['preflight']['case_id']
                    and r['provider'] in receipt['preflight']['providers']]
        if (len(selected) != len(receipt['preflight']['providers'])
                or any(not r.get('supersedes_attempt_id') for r in selected)):
            raise ValueError('Case correction missing a declared method')
        original_certificate = {key: receipt[key] for key in ('kind', 'preflight', 'postflight', 'results_sha256', 'physical_sha256')}
        certificate_bytes = (json.dumps(original_certificate, ensure_ascii=False, indent=2) + '\n').encode()
        if hashlib.sha256(certificate_bytes).hexdigest() != json.loads(correction_binding.read_text())['sha256']:
            raise ValueError('Case correction certificate differs from repository binding')
        raw_parent = (teds_receipt['input_results_sha256'] if teds_receipt
                      else scoring_receipt['input_results_sha256'])
        if receipt['preflight']['parent_results_sha256'] != raw_parent:
            raise ValueError('Case correction predecessor differs from the certified raw matrix')
        by_provider = {r['provider']: r for r in selected}
        replacement_bytes = ''.join(json.dumps(by_provider[p], ensure_ascii=False) + '\n'
                                    for p in receipt['preflight']['providers']).encode()
        if hashlib.sha256(replacement_bytes).hexdigest() != receipt['results_sha256']:
            raise ValueError('Corrected rows differ from certified calls')
        case_scoring = json.loads((root / 'case-correction-scoring.json').read_text())
        if (case_scoring.get('kind') != 'webmainbench_case_correction_scoring_revision'
                or case_scoring.get('provider_calls') != 0
                or case_scoring.get('scorer_commit') != scoring_receipt.get('scorer_commit')
                or case_scoring.get('output_results_sha256') != receipt['results_sha256']):
            raise ValueError('Corrected case does not use the report scoring revision')
    providers = dataset["coverage_plan"]["static_html"]
    case_ids = {row["id"] for row in dataset["cases"]}
    if len(case_ids) != case_count or len(records) != case_count * provider_count or providers != expected_providers:
        raise ValueError("Full matrix missing")
    expected = {(provider, case_id) for provider in providers for case_id in case_ids}
    if {(row["provider"], row["case_id"]) for row in records} != expected:
        raise ValueError("Report matrix differs from evidence")
    reviewed = {(row["provider"], row["case_id"]) for row in failure_analysis["reviewed_failures"]}
    if (len(reviewed) != len(failure_analysis["reviewed_failures"])
            or any(not all(isinstance(row.get(key), str) and row[key].strip()
                           for key in ("category_label", "cause", "treatment", "status"))
                   for row in failure_analysis["reviewed_failures"])):
        raise ValueError("Published failure analysis contains duplicate or empty reviews")
    home = check_presentation((root / "index.html").read_text(encoding="utf-8"))
    guide = check_presentation((root / "guide/index.html").read_text(encoding="utf-8"))
    profile_rows = guide.select('table#method-error-profiles tbody > tr')
    fix_slides = guide.select('[data-moli-fix-slide]')
    content = json.loads((root / "report-content.json").read_text(encoding="utf-8"))
    if (len(profile_rows) != provider_count or len(fix_slides) != len(content["moli_fix_profiles"])
            or any(len(slide.select('.slide-facts > div')) != 4 for slide in fix_slides)):
        raise ValueError('Guide lacks the declared method error profiles or bound repair slides')
    illustrations = repo / 'evaluation/webmainbench-full/illustrations.json'
    overview_claims = None
    if illustrations.exists():
        for asset in json.loads(illustrations.read_text())['assets']:
            filename = 'assets/' + asset['name']
            if (hashlib.sha256(contained_path(root, filename).read_bytes()).hexdigest() != asset['sha256']
                    or not guide.select_one('img[src="' + filename + '"]')):
                raise ValueError('Bound error analysis illustration missing or changed')
            if asset.get('role') == 'overview':
                overview_claims = asset.get('claims')
    summaries = json.loads((root / 'score-summaries.json').read_text(encoding='utf-8'))
    expected_denominators = {
        'text_edit': 545, 'code_edit': 110, 'formula_edit': 257,
        'table_edit': 179, 'table_TEDS': 179,
    }
    if any(summary['official_aggregate']['denominators'] != expected_denominators
           for summary in summaries.values()):
        raise ValueError('Report methods do not share the bound reference-defined populations')
    if (not overview_claims or overview_claims.get('scores_percent') != {
            provider: f"{summaries[provider]['official_aggregate']['scores']['overall'] * 100:.2f}"
            for provider in providers}):
        raise ValueError('Overview illustration score claims differ from measured results')
    if correction_binding.exists() and not guide.select_one('#fixture-correction'):
        raise ValueError('Corrected case explanation missing')
    audit = guide.select_one('#content-audit')
    # The bound pre-fix audit contains 604 concrete observations across all 545 pages.
    audit_observation_count = 604
    if (not audit or '545/545' not in audit.get_text(' ', strip=True)
            or str(audit_observation_count) not in audit.get_text(' ', strip=True)
            or '修复前候选输出' not in audit.get_text(' ', strip=True)):
        raise ValueError('Full content-audit scope, findings or limitation missing')
    for page in (home, guide):
        if len(page.select('td[data-metric-direction="max"]')) != len(providers) * 6:
            raise ValueError("Core metrics or winner controls missing")
        if "545" not in page.get_text(" ", strip=True):
            raise ValueError("Complete population missing")
    quality_limits = {
        'issue-delivery': 545, 'issue-text': 545, 'issue-code': 110,
        'issue-formula': 257, 'issue-table': 179, 'issue-teds': 179,
    }
    for document in (home, guide):
        quality_rows = document.select('table#quality-issue-matrix tr[data-quality-provider]')
        if len(quality_rows) != len(providers):
            raise ValueError('Quality issue matrix missing methods')
        for row in quality_rows:
            cells = {cell.get('data-metric'): int(cell.get_text(' ', strip=True))
                     for cell in row.select('td[data-metric^="issue-"]')}
            if cells.keys() != quality_limits.keys() or any(
                    value < 0 or value > quality_limits[metric]
                    for metric, value in cells.items()):
                raise ValueError('Quality issue matrix exceeds its fixed population')
    if not guide.select_one('#error-landscape'):
        raise ValueError('Interpretation error landscape missing')
    latency_cells = home.select(
        'td[data-metric^="all-call-latency-"][data-metric-direction="min"], '
        'td[data-metric^="success-latency-"][data-metric-direction="min"]'
    )
    if len(latency_cells) != provider_count * 5:
        raise ValueError("All-call or conditional latency comparison missing")
    first_case = dataset["cases"][0]["id"]
    example_targets = {"viewer/index.html?path=sources/" + first_case + ".html.txt",
                       "viewer/index.html?path=references/" + first_case + ".md",
                       "cases/index.html?id=" + first_case}
    if not example_targets.issubset({link.get("href") for link in guide.select("a[href]")}):
        raise ValueError("Interpretation example lacks source, reference or same-case result")
    analysis = BeautifulSoup((root / "analysis/index.html").read_text(encoding="utf-8"), "html.parser")
    check_diagnosis_inventory(analysis)
    check_table_interaction(root, provider_count=provider_count, case_count=case_count)
    if (len(analysis.select("tr[data-result]")) != case_count * provider_count or not analysis.select_one("#kind")
            or not analysis.select_one("#cause") or not analysis.select_one("#metric")):
        raise ValueError("Result inventory incomplete")
    if not any(row.get("data-kind") != "未触发诊断" for row in analysis.select("tr[data-result]")):
        raise ValueError("Default diagnostic population is empty")
    if not (root / "en/index.html").is_file() or "返回中文报告" not in (root / "en/index.html").read_text(encoding="utf-8"):
        raise ValueError("English not-generated route missing")
    case_template = BeautifulSoup((root / "cases/index.html").read_text(encoding="utf-8"), "html.parser")
    if (not case_template.select_one("#case-results") or not case_template.select_one("#case-source")
            or not case_template.select_one("#case-reference") or not case_template.select_one("#case-comparison")):
        raise ValueError("Same-case comparison template is incomplete")
    result_template = check_presentation((root / "results/index.html").read_text(encoding="utf-8"))
    if not result_template.select_one('[data-result-summary]') or not result_template.select_one('#result-logs pre[data-kind="error"]'):
        raise ValueError("Shared result template lacks summary or exact error log")
    index = json.loads((root / "result-shard-index.json").read_text(encoding="utf-8"))
    artifact_index = json.loads((root / "artifact-shard-index.json").read_text(encoding="utf-8"))
    shard_paths = set(index.values()) | set(artifact_index.values())
    shards = {path: json.loads(contained_path(root, path).read_text(encoding="utf-8")) for path in shard_paths}
    if len(index) != len(records) or len(shards) > 64:
        raise ValueError("Complete result shard index missing or oversized")
    failures = set()
    diagnostic_counts = defaultdict(Counter)
    for row in records:
        shard = shards[index[row["provider"] + "|" + row["case_id"]]]
        diagnosis = shard["results"][row["provider"] + "|" + row["case_id"]]["diagnosis"]
        diagnostic_counts[row["provider"]][diagnosis] += 1
        path = row["artifact_path"]
        output = shard["blobs"].get(shard["artifacts"].get(path), "")
        if not row["ok"] or not row["output_chars"] or ERROR_PAGE.search(output):
            failures.add((row["provider"], row["case_id"]))
    if {"machine_issues": len(failures),
        "request_failures": sum(counts['请求失败'] for counts in diagnostic_counts.values()),
        "empty_outputs": sum(counts['空输出'] for counts in diagnostic_counts.values()),
        "error_pages": sum(counts['错误页'] for counts in diagnostic_counts.values())} != {
            key: overview_claims.get(key) for key in
            ('machine_issues', 'request_failures', 'empty_outputs', 'error_pages')}:
        raise ValueError('Overview illustration issue claims differ from measured results')
    if reviewed != {item for item in failures if item[0] not in variant_ids}:
        raise ValueError("Published baseline failure analysis scope is incomplete")
    if not set(variant_ids).issubset({row['provider'] for row in records}):
        raise ValueError('A Moli repair variant is absent from the result inventory')
    cells = guide.select("td[data-diagnostic-provider][data-kind]")
    expected_cells = {(provider, kind) for provider in providers
                      for kind in ("请求失败", "空输出", "错误页", "低全文相似度")}
    if ({(cell["data-diagnostic-provider"], cell["data-kind"]) for cell in cells} != expected_cells
            or len(cells) != len(expected_cells) or any(
        int(cell.get_text(strip=True)) != diagnostic_counts[cell["data-diagnostic-provider"]][cell["data-kind"]]
        for cell in cells
    )):
        raise ValueError("Interpretation diagnosis matrix differs from measured results")
    cause_counts = Counter((entry["category_label"], entry["provider"])
                           for entry in failure_analysis["reviewed_failures"])
    cause_cells = guide.select("td[data-cause-provider][data-cause-kind]")
    reviewed_cause_kinds = {kind for kind, _ in cause_counts}
    expected_cause_cells = {(kind, provider) for kind in reviewed_cause_kinds for provider in providers}
    if ({(cell["data-cause-kind"], cell["data-cause-provider"]) for cell in cause_cells} != expected_cause_cells
            or len(cause_cells) != len(expected_cause_cells)
            or any(cell.get_text(strip=True) !=
                   (str(cause_counts[cell["data-cause-kind"], cell["data-cause-provider"]])
                    if cause_counts[cell["data-cause-kind"], cell["data-cause-provider"]] else "—")
                   for cell in cause_cells)):
        raise ValueError("Per-method cause matrix differs from reviewed evidence")
    reviewed_by_key = {(row["provider"], row["case_id"]): row for row in failure_analysis["reviewed_failures"]}
    for row in records:
        check_result_payload(row, index, artifact_index, shards)
        bundled = shards[index[row["provider"] + "|" + row["case_id"]]]["results"][row["provider"] + "|" + row["case_id"]]
        if bundled.get("review") != reviewed_by_key.get((row["provider"], row["case_id"])):
            raise ValueError("Result detail differs from bound failure analysis: " + row["attempt_id"])
    for case in dataset["cases"]:
        case_id = case["id"]
        for path, expected_hash in (("sources/" + case_id + ".html.txt", case["input_html_sha256"]),
                                    ("references/" + case_id + ".md", case["reference_sha256"])):
            shard_path = artifact_index.get(path)
            if shard_path not in shards:
                raise ValueError("Frozen task evidence shard missing: " + path)
            blob_hash = shards[shard_path]["artifacts"].get(path)
            content = shards[shard_path]["blobs"].get(blob_hash)
            if not isinstance(content, str) or hashlib.sha256(content.encode()).hexdigest() != expected_hash:
                raise ValueError("Frozen task evidence changed: " + path)
    physical = load_jsonl(root / "physical-attempts.jsonl", skip_blank=True)
    for attempt in physical:
        path = attempt["physical_artifact_path"]
        shard_path = artifact_index.get(path)
        if shard_path not in shards:
            raise ValueError("Physical output shard missing: " + path)
        digest = shards[shard_path]["artifacts"].get(path)
        content = shards[shard_path]["blobs"].get(digest)
        if not isinstance(content, str) or hashlib.sha256(content.encode()).hexdigest() != attempt["output_sha256"]:
            raise ValueError("Physical output absent or changed: " + path)
        if attempt not in shards[shard_path]["physical"].get(attempt["track_id"], []):
            raise ValueError("Physical call missing from result detail: " + attempt["attempt_id"])
    public_files = [path for path in root.rglob("*") if path.is_file()]
    if len(public_files) > 2000 or sum(path.stat().st_size for path in public_files) > 314572800:
        raise ValueError("Report package exceeds Artifact Site deployment limits")
    viewer = (root / "viewer/index.html").read_text(encoding="utf-8")
    if "id=\"evidence-content\"" not in viewer or not (root / "assets/report.js").is_file():
        raise ValueError("On-demand evidence viewer missing")


SAMPLE_PROVIDERS = {"kitesurf", "lightpanda", "obscura", "moli", "moli-1.1.7", "chromium"}
SAMPLE_METRICS = ("text_edit", "code_edit", "table_edit", "table_TEDS", "formula_edit")
EXPECTED_STYLE_SHA256 = hashlib.sha256((STYLE + EXTRA).encode("utf-8")).hexdigest()
EXPECTED_SCRIPT_SHA256 = hashlib.sha256(JS.encode("utf-8")).hexdigest()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return load_jsonl(path, skip_blank=True)


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


def check_sample_report(root: Path, dataset: dict | None = None,
                        records: list[dict] | None = None, *,
                        authority_root: Path | None = None, profile_path: Path | None = None) -> None:
    repo = (profile_path.parent.parent if profile_path is not None else
            authority_directory(authority_root, "evaluation/current-report.json"))
    profile_path = profile_path or repo / "evaluation/current-report.json"
    check_language_contract(root)
    language_pairs = (('', 'zh'), ('en/', 'en')) if 'en' in generated_languages(root) else (('', 'zh'),)
    prefixes = tuple(prefix for prefix, _ in language_pairs)
    dataset = dataset or json.loads((root / "dataset.json").read_text(encoding="utf-8"))
    records = records or read_jsonl(root / "results.jsonl")
    latency_correction = validate_latency_correction(root)
    current_profile = json.loads(profile_path.read_text())
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
    source_history = webmainbench_history(json.loads((repo / 'research/report-changes.json').read_text(encoding='utf-8')))
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
    if len(cases) != 200 or len(records) != 200 * len(SAMPLE_PROVIDERS):
        raise ValueError("WebMainBench report matrix size mismatch")
    expected = {(provider, case_id) for provider in SAMPLE_PROVIDERS for case_id in cases}
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
    if (review.get("status") != "passed" or set(review.get("providers", [])) != SAMPLE_PROVIDERS
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
    if set(summaries) != SAMPLE_PROVIDERS:
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
        totals = {metric: 0.0 for metric in SAMPLE_METRICS}; denominators = {metric: 0 for metric in SAMPLE_METRICS}
        for row in rows:
            for metric in SAMPLE_METRICS:
                value = row.get("metrics", {}).get(metric, {})
                if value.get("success"):
                    totals[metric] += value["score"]; denominators[metric] += 1
        scores = {metric: totals[metric] / denominators[metric] if denominators[metric] else 0.0 for metric in SAMPLE_METRICS}
        scores["overall"] = sum(scores.values()) / len(SAMPLE_METRICS)
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
    if {row.get("provider") for row in executions} != SAMPLE_PROVIDERS:
        raise ValueError("WebMainBench execution manifest provider set mismatch")

    zh = {path.relative_to(root) for path in root.rglob("*.html") if path.relative_to(root).parts[0] != "en"}
    localized_report_routes(root, {path.as_posix() for path in zh})
    if len(zh) != 16 + len(cases) + len(records):
        raise ValueError("WebMainBench route inventory mismatch")
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
    if len(analysis.select("#wmb-compare-rows > tr")) != 200 or len(analysis.select("[data-wmb-compare-method]")) != len(SAMPLE_PROVIDERS):
        raise ValueError("WebMainBench same-page group comparison is incomplete")
    if {option.get("value") for option in analysis.select('[data-wmb-filter="failure"] option') if option.get("value")} != output_errors - {None}:
        raise ValueError("WebMainBench request failure classes are incomplete")
    expected_slices = sum(len(summaries[provider].get("dimensions", {}).get(axis, {}))
                          for provider in SAMPLE_PROVIDERS for axis in ("language", "level", "table", "code", "equation"))
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
        if (len(guide.select(".wmb-guide-results tbody > tr")) != len(SAMPLE_PROVIDERS)
                or len(guide.select(".wmb-guide-toc a")) != len(expected_guide_sections)
                or len(guide.select("#overall .wmb-score-part")) != 5
                or guide.select_one("#overall .wmb-score-result") is None
                or guide.select_one("#overall .wmb-denominator-matrix") is None
                or len(guide.select("#content .wmb-evidence-pair > article")) != 2
                or len(guide.select("#content .wmb-case-stats > span")) != len(SAMPLE_PROVIDERS)
                or len(guide.select("#tables .wmb-evidence-pair > article")) != 2
                or len(guide.select("#tables .wmb-case-stats > span")) != len(SAMPLE_PROVIDERS)
                or len(guide.select("#version-differences details.wmb-version-case")) != 5
                or len(guide.select("#version-differences .wmb-version-case .actions a")) != 15
                or len(guide.select("#version-differences .wmb-evidence-pair pre")) != 10
                or len(guide.select("#delivery .wmb-guide-latency tbody > tr")) != len(SAMPLE_PROVIDERS)):
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
                check_latency_diagnostic(section, latency_diagnostic,
                                         formal_round=latency_correction['selected_round'] if latency_correction else None)
        home_headers = {tag.get_text(" ", strip=True) for tag in localized_home.select("#wmb-core-results th")}
        if teds_revision is not None:
            correction_label = '修正后复算' if not prefix else 'recomputed after correction'
            if correction_label not in localized_home.get_text(' ', strip=True):
                raise ValueError(prefix + 'Corrected scores must not be labeled as original official scores')
        if (len(localized_home.select("#wmb-core-results tbody > tr")) != len(SAMPLE_PROVIDERS)
                or not {mean_label, "P50 latency ↕" if prefix else "P50 耗时 ↕", "P95 latency ↕" if prefix else "P95 耗时 ↕"}.issubset(home_headers)
                or localized_home.select_one('#wmb-core-results').get('data-col') != '2'
                or localized_home.select_one('#wmb-core-results').get('data-dir') != 'desc'
                or {"非空内容交付率 ↕", "Non-empty delivery rate ↕"} & home_headers):
            raise ValueError(prefix + "WebMainBench home must have one core comparison with mean, P50 and P95")
        table_population=localized_home.select('.wmb-table-population-table tbody tr')
        if len(table_population)!=len(SAMPLE_PROVIDERS):raise ValueError('Table-scoring population breakdown missing')
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
                            and not ERROR_PAGE.search(text)] for name in SAMPLE_PROVIDERS}
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
        if matched is None or str(len(shared_cases)) not in matched.get_text() or len(matched.select('tbody tr')) != len(SAMPLE_PROVIDERS):
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
    for provider in SAMPLE_PROVIDERS:
        method_page = BeautifulSoup((root / "providers" / f'{provider}.html').read_text(encoding="utf-8"),"html.parser")
        if len(method_page.select('table[data-wmb-table] tbody tr')) != 200:
            raise ValueError(provider + ": filtered per-method result inventory is incomplete")
    for relative in zh:
        editions = [BeautifulSoup((root / prefix / relative).read_text(encoding="utf-8"), "html.parser")
                    for prefix in prefixes]
        if len(editions) == 2 and Counter(tag.name for tag in editions[0].find_all()) != Counter(tag.name for tag in editions[1].find_all()):
            raise ValueError("WebMainBench bilingual semantic-shape mismatch: " + str(relative))
        for soup in editions:
            for table in soup.select("table"):
                width = len(table.select("thead th"))
                if any(len(row.find_all("td", recursive=False)) != width for row in table.select("tbody > tr")):
                    raise ValueError("WebMainBench table width mismatch: " + str(relative))
    if digest(root / "assets" / "style.css") != EXPECTED_STYLE_SHA256:
        raise ValueError("Report CSS differs from the reviewed reference-shell extension")
    if digest(root / "assets" / "report.js") != EXPECTED_SCRIPT_SHA256:
        raise ValueError("Report JavaScript differs from the reviewed inspection controls")
    validate_sample_content(root, authority_root=authority_root or repo, binding_path=profile_path.parent / "webmainbench-content-binding.json")



def main():
    parser = argparse.ArgumentParser(description="Check a complete WebMainBench report")
    parser.add_argument("report", type=Path)
    parser.add_argument("--authority-root", type=Path)
    args = parser.parse_args()
    check(args.report, authority_root=args.authority_root)
    print("Complete WebMainBench report release gate passed")


def sample_main():
    parser = argparse.ArgumentParser(description="Check a frozen WebMainBench sample report")
    parser.add_argument("report", type=Path)
    parser.add_argument("--authority-root", type=Path)
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args()
    check_sample_report(args.report, authority_root=args.authority_root, profile_path=args.profile)
    print("WebMainBench sample report release gate passed")
