"""Release gate for the separately certified complete WebMainBench report."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from bs4 import BeautifulSoup

from scripts.report_presentation_contract import check_presentation
from harness.web_fetch.safety import contained_path
from harness.web_fetch.report import ERROR_PAGE


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


def check_moli_followup(root: Path, binding: dict) -> None:
    receipt_path = root / binding['relative_path']
    if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != binding['sha256']:
        raise ValueError('Moli follow-up receipt changed')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if (receipt.get('kind') != 'webmainbench_moli_full_diagnostic_followup'
            or receipt.get('case_count') != 545
            or receipt.get('baseline_successes') != 542
            or receipt.get('candidate_successes') != 544
            or len(receipt.get('recovered', [])) != 2
            or len(receipt.get('remaining', [])) != 1):
        raise ValueError('Moli follow-up population or outcomes changed')
    home = BeautifulSoup((root / 'index.html').read_text(encoding='utf-8'), 'html.parser')
    page = BeautifulSoup((root / 'moli-update/index.html').read_text(encoding='utf-8'), 'html.parser')
    if (not home.select_one('a[href="moli-update/index.html"]')
            or '542/545' not in page.get_text(' ', strip=True)
            or '544/545' not in page.get_text(' ', strip=True)
            or len(page.select('tr[data-followup-metric]')) != 4):
        raise ValueError('Moli follow-up result is missing from report')
    for metric, values in receipt['metrics_original_scorer'].items():
        row = page.select_one('tr[data-followup-metric="' + metric + '"]')
        if (row is None or f"{values['baseline'] * 100:.2f}%" not in row.get_text(' ', strip=True)
                or f"{values['candidate'] * 100:.2f}%" not in row.get_text(' ', strip=True)
                or str(values['baseline_n']) not in row.get_text(' ', strip=True)
                or str(values['candidate_n']) not in row.get_text(' ', strip=True)):
            raise ValueError('Moli follow-up score differs from bound evidence: ' + metric)
    for entry in receipt['recovered']:
        path = root / 'moli-followup' / (entry['case_id'] + '.txt')
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry['output_sha256']:
            raise ValueError('Recovered Moli output changed: ' + entry['case_id'])
        if not page.select_one('a[href="moli-followup/' + entry['case_id'] + '.txt"]'):
            raise ValueError('Recovered Moli output link missing: ' + entry['case_id'])


def check(root: Path) -> None:
    followup_binding = json.loads((Path(__file__).resolve().parents[1] /
        'evaluation/webmainbench-full/moli-followup-binding.json').read_text(encoding='utf-8'))
    check_moli_followup(root, followup_binding)
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
    for path, expected in ((root / "report-content.json", binding["sha256"]),
                           (root / "assets/infographic.png", binding["infographic_sha256"]),
                           (root / "assets/infographic-mobile.png", binding["infographic_mobile_sha256"])):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Bound report asset changed: " + path.name)
    failure_binding = json.loads((Path(__file__).resolve().parents[1] / "evaluation/webmainbench-full/failure-analysis-binding.json").read_text(encoding="utf-8"))
    failure_bytes = (root / "failure-analysis.json").read_bytes()
    if hashlib.sha256(failure_bytes).hexdigest() != failure_binding["sha256"]:
        raise ValueError("Bound failure analysis changed")
    failure_analysis = json.loads(failure_bytes)
    manifest = json.loads((root / "report-manifest.json").read_text(encoding="utf-8"))
    if manifest != {"schema_version": 1, "report": "webmainbench-545-full",
                    "languages": ["zh"], "case_count": 545, "provider_count": 5,
                    "result_count": 2725}:
        raise ValueError("Incomplete WebMainBench report manifest")
    dataset = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
    records = [json.loads(line) for line in (root / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    providers = dataset["coverage_plan"]["static_html"]
    case_ids = {row["id"] for row in dataset["cases"]}
    if len(case_ids) != 545 or len(records) != 2725 or len(providers) != 5:
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
    for page in (home, guide):
        if len(page.select('td[data-metric-direction="max"]')) != 30:
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
    if not home.select_one('img[src="assets/infographic.png"]') or not (root / "assets/infographic.png").is_file():
        raise ValueError("Bound infographic missing")
    if not home.select_one('source[media="(max-width:650px)"][srcset="assets/infographic-mobile.png"]'):
        raise ValueError("Mobile infographic route missing")
    analysis = BeautifulSoup((root / "analysis/index.html").read_text(encoding="utf-8"), "html.parser")
    if (len(analysis.select("tr[data-result]")) != 2725 or not analysis.select_one("#kind")
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
    if len(index) != len(records) or len(shards) > 48:
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
    if reviewed != failures:
        raise ValueError("Published failure analysis scope is incomplete")
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
    expected_cause_cells = {(kind, provider) for kind, _ in cause_counts for provider in providers}
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
