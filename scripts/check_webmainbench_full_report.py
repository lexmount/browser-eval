"""Release gate for the separately certified complete WebMainBench report."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.report_presentation_contract import check_presentation
from harness.web_fetch.safety import contained_path
from harness.web_fetch.report import ERROR_PAGE, WEBMAINBENCH_FULL_CAUSE_OWNERS


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


def check_table_interaction(root: Path) -> None:
    check_interaction_script((root / 'assets/report.js').read_text(encoding='utf-8'))
    home = BeautifulSoup((root / 'index.html').read_text(encoding='utf-8'), 'html.parser')
    style_href = home.select_one('link[rel="stylesheet"]')
    script_src = home.select_one('script[src]')
    if (not style_href or not script_src
            or '?v=' not in style_href.get('href', '')
            or style_href['href'].split('?v=', 1)[1] != script_src.get('src', '').split('?v=', 1)[-1]):
        raise ValueError('Report assets lack a shared cache-busting version')
    core = home.select_one('table#core-results')
    if (not core or len(core.select('tbody > tr')) != 7
            or len(core.select('th[aria-sort="none"] > button[data-table-sort][onclick="sortReportTable(this)"]')) != 8
            or any(cell.get('data-sort') is None for cell in core.select('tbody > tr > td.number'))):
        raise ValueError('Core comparison lacks complete stable sorting semantics')
    if len(core.select('.comparison-winner')) != 6:
        raise ValueError('Core comparison highlights changed while adding sorting')
    dataset = BeautifulSoup((root / 'dataset/index.html').read_text(encoding='utf-8'), 'html.parser')
    dataset_table = dataset.select_one('table#dataset-inventory')
    if (not dataset.select_one('#dataset-q') or not dataset.select_one('#dataset-language')
            or not dataset.select_one('#dataset-level') or not dataset.select_one('#dataset-count')
            or not dataset_table or len(dataset_table.select('tbody > tr[data-dataset]')) != 545
            or len(dataset_table.select('button[data-table-sort][onclick="sortReportTable(this)"]')) != 4):
        raise ValueError('Dataset inventory lacks complete search, filters or sorting')
    analysis = BeautifulSoup((root / 'analysis/index.html').read_text(encoding='utf-8'), 'html.parser')
    inventory = analysis.select_one('table#result-inventory')
    if (not inventory or len(inventory.select('tbody > tr[data-result]')) != 3815
            or len(inventory.select('button[data-table-sort][onclick="sortReportTable(this)"]')) != 5
            or any(cell.get('data-sort') is None for cell in inventory.select('tbody > tr > td:nth-of-type(4), tbody > tr > td:nth-of-type(5)'))):
        raise ValueError('Result inventory lacks complete filters or stable sorting semantics')


def check(root: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
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
    seven_binding = json.loads((root / 'seven-method-binding.json').read_text(encoding='utf-8'))
    teds_receipt = json.loads((root / 'teds-correction.json').read_text(encoding='utf-8'))
    scoring_receipt_path = root / 'scoring-correction.json'
    if not scoring_receipt_path.is_file():
        raise ValueError('scoring-correction.json missing from report evidence')
    scoring_binding = json.loads(
        (repo / 'evaluation/webmainbench-full/scoring-correction-binding.json').read_text(encoding='utf-8')
    )
    scoring_receipt = json.loads(scoring_receipt_path.read_text(encoding='utf-8'))
    bound_variants = {item['provider']: item for item in seven_binding.get('variants', [])}
    if (seven_binding.get('kind') != 'webmainbench_seven_method_full_scope_binding'
            or seven_binding.get('logical_results') != 3815
            or seven_binding.get('registry_sha256') != hashlib.sha256(registry_path.read_bytes()).hexdigest()
            or seven_binding.get('results_sha256') != teds_receipt.get('input_results_sha256')
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
    if ('https://github.com/lexmount/WebMainBench/tree/lexmain' not in home_html
            or scoring_receipt['scorer_commit'] not in methodology_html
            or 'Lexmain 修正评分器提交' not in methodology_html):
        raise ValueError('Public report does not identify the bound Lexmain scorer')
    releases = json.loads((Path(__file__).resolve().parents[1] / 'research/report-changes.json').read_text(encoding='utf-8'))['releases']
    expected_releases = [item for item in releases if 'webmainbench_frozen_html_to_markdown' in item.get('comparison_kinds', [])]
    history = BeautifulSoup((root / 'history/index.html').read_text(encoding='utf-8'), 'html.parser')
    history_rows = history.select('tr[data-release-id]')
    if ([row['data-release-id'] for row in history_rows] != [item['id'] for item in expected_releases]
            or any(item['title'] not in row.get_text(' ', strip=True) or
                   item['summary'] not in row.get_text(' ', strip=True)
                   for row, item in zip(history_rows, expected_releases))):
        raise ValueError('WebMainBench release history differs from structured records')
    binding_path = Path(__file__).resolve().parents[1] / "evaluation/webmainbench-full/content-binding.json"
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    for path, expected in ((root / "report-content.json", binding["sha256"]),):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Bound report asset changed: " + path.name)
    failure_binding = json.loads((Path(__file__).resolve().parents[1] / "evaluation/webmainbench-full/failure-analysis-binding.json").read_text(encoding="utf-8"))
    failure_bytes = (root / "failure-analysis.json").read_bytes()
    if hashlib.sha256(failure_bytes).hexdigest() != failure_binding["sha256"]:
        raise ValueError("Bound failure analysis changed")
    failure_analysis = json.loads(failure_bytes)
    manifest = json.loads((root / "report-manifest.json").read_text(encoding="utf-8"))
    if manifest != {"schema_version": 1, "report": "webmainbench-545-full",
                    "languages": ["zh"], "case_count": 545, "provider_count": 7,
                    "result_count": 3815}:
        raise ValueError("Incomplete WebMainBench report manifest")
    dataset = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
    records = [json.loads(line) for line in (root / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    correction_binding = repo / 'evaluation/webmainbench-full/case-correction-binding.json'
    if correction_binding.exists():
        receipt = json.loads((root / 'case-correction.json').read_text())
        if (receipt['preflight'] != receipt['postflight']
                or receipt['current_results_sha256'] != hashlib.sha256((root / 'results.jsonl').read_bytes()).hexdigest()
                or receipt['score_summaries_sha256'] != hashlib.sha256((root / 'score-summaries.json').read_bytes()).hexdigest()):
            raise ValueError('Published case correction differs from its certified results')
        selected = [r for r in records if r['case_id'] == receipt['preflight']['case_id']]
        if len(selected) != 7 or any(not r.get('supersedes_attempt_id') for r in selected):
            raise ValueError('Case correction missing a method')
        original_certificate = {key: receipt[key] for key in ('kind', 'preflight', 'postflight', 'results_sha256', 'physical_sha256')}
        certificate_bytes = (json.dumps(original_certificate, ensure_ascii=False, indent=2) + '\n').encode()
        if hashlib.sha256(certificate_bytes).hexdigest() != json.loads(correction_binding.read_text())['sha256']:
            raise ValueError('Case correction certificate differs from repository binding')
        teds = json.loads((root / 'teds-correction.json').read_text())
        if receipt['preflight']['parent_results_sha256'] != teds['input_results_sha256']:
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
    if len(case_ids) != 545 or len(records) != 3815 or len(providers) != 7:
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
        'text_edit': 545, 'code_edit': 110, 'formula_edit': 113,
        'table_edit': 81, 'table_TEDS': 81,
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
    if len(home.select('td[data-metric-direction="min"]')) != 35:
        raise ValueError("All-call or conditional latency comparison missing")
    first_case = dataset["cases"][0]["id"]
    example_targets = {"viewer/index.html?path=sources/" + first_case + ".html.txt",
                       "viewer/index.html?path=references/" + first_case + ".md",
                       "cases/index.html?id=" + first_case}
    if not example_targets.issubset({link.get("href") for link in guide.select("a[href]")}):
        raise ValueError("Interpretation example lacks source, reference or same-case result")
    analysis = BeautifulSoup((root / "analysis/index.html").read_text(encoding="utf-8"), "html.parser")
    check_diagnosis_inventory(analysis)
    check_table_interaction(root)
    if (len(analysis.select("tr[data-result]")) != 3815 or not analysis.select_one("#kind")
            or not analysis.select_one("#cause")):
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
    expected_cause_cells = {(kind, provider) for kind in WEBMAINBENCH_FULL_CAUSE_OWNERS for provider in providers}
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
    physical = [json.loads(line) for line in (root / "physical-attempts.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    check(parser.parse_args().report)
    print("Complete WebMainBench report release gate passed")
