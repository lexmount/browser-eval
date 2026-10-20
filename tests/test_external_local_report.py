"""A new local report uses external inputs without repository instance files."""
import hashlib
import json
import runpy
import sys
from pathlib import Path

import pytest

from harness.web_fetch.config import load_dataset
from harness.web_fetch.providers import build_provider
from harness.web_fetch.runner import run_evaluation
from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("case_count,method_count,timeout", [(5, 2, 18), (12, 3, 30)])
def test_external_matrix_through_build_and_check_clis(tmp_path, monkeypatch,
                                                     case_count, method_count, timeout):
    root = Path(__file__).resolve().parents[1]
    evidence = tmp_path / "evidence"
    methods = [f"synthetic-{n}" for n in range(method_count)]
    cases = [{
        "id": f"task-{n}", "name": f"Synthetic task {n}",
        "url": f"https://example.invalid/task/{n}",
        "category": "dynamic", "track": "dynamic", "task_kind": "body",
        "expected_any": ["Independent fixture output"],
        "source_verification": {"state": "unavailable"},
        "acceptance_contract": {"version": 1, "schema": "body_text",
            "minimum_records": 0, "stable_text_anchors": [
                {"id": "identity", "alternatives": ["Independent fixture output"]}]},
    } for n in range(case_count)]
    dataset = {"tool": "web_fetch", "id": "external-synthetic", "name": "Synthetic example",
        "cases": cases, "coverage_plan": {"dynamic": methods},
        "comparison": {"kind": "uniform_native_local_measurement",
            "publication_state": "local_audit_only_not_for_release",
            "source_reverified": False}}
    frozen = evidence / "local-evidence/input-dataset.json"
    dataset_digest = write(frozen, dataset)
    config = {name: {"type": "fixture", "fail_cases": ["task-0"]} for name in methods}
    config_path = tmp_path / "providers.json"
    config_digest = write(config_path, {"providers": config})
    _, loaded = load_dataset(frozen)
    records, batches = [], []
    for name in methods:
        rows, _ = run_evaluation(
            output_dir=evidence, dataset_meta=dataset, dataset_path=frozen,
            cases=loaded, providers=[build_provider(name, config[name])],
            provider_config_path=config_path, runs=1, timeout=timeout,
            delay=0, max_failure_retries=0)
        records.extend(rows)
        batches.append(json.loads((evidence / "manifest.json").read_text()))
    (evidence / "results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in records))
    write(evidence / "execution.json", {"batches": batches})
    contract_digest = write(evidence / "local-run-contract.json", {
        "schema_version": 1, "kind": "uniform_native_local_measurement",
        "dataset_storage": "evidence",
        "dataset_sources": [{"path": "local-evidence/input-dataset.json",
                             "sha256": dataset_digest}],
        "scoring_source_sha256": SCORING_SOURCE_SHA256,
        "provider_config_sha256": config_digest,
        "runtime_source_sha256": batches[0]["runtime_source_sha256"],
        "providers": methods, "runs": 1, "timeout_seconds": timeout,
        "max_failure_retries": 0})
    dataset["comparison"]["local_run_contract_sha256"] = contract_digest
    write(evidence / "dataset.json", dataset)
    output = tmp_path / "report"
    for script, args in [
        ("build_report.py", ["--local-audit", "--evidence", str(evidence),
                             "--output", str(output), "--language", "en"]),
        ("check_report.py", [str(output)]),
    ]:
        monkeypatch.setattr(sys, "argv", [script, *args])
        try:
            runpy.run_path(str(root / "scripts" / script), run_name="__main__")
        except SystemExit as result:
            assert result.code == 0
    manifest = json.loads((output / "report-manifest.json").read_text())
    assert manifest["generated_languages"] == ["zh", "en"]
    saved = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert len(saved) == case_count * method_count
    for prefix in ("", "en/"):
        assert len(list((output / prefix / "attempts").glob("*.html"))) == len(saved)
    assert (output / "local-evidence/input-dataset.json").read_bytes() == frozen.read_bytes()
    # Language entry normalization must not hide changes to ordinary links.
    from bs4 import BeautifulSoup
    from harness.web_fetch.report_check import check
    english_home = output / "en/index.html"
    soup = BeautifulSoup(english_home.read_text(), "html.parser")
    anchor = next(a for a in soup.select("a[href]") if not a.get("hreflang")
                  and ".html" in a["href"] and "?" not in a["href"])
    anchor["href"] += "?unexpected=1"
    english_home.write_text(str(soup))
    with pytest.raises(ValueError, match="evidence or navigation links differ"):
        check(output, authority_root=root)
