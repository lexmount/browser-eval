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


@pytest.mark.parametrize("case_count,method_count,timeout,failures", [
    (1, 1, 7, True),
    (1, 1, 7, False),
    (5, 2, 18, True),
    (12, 3, 30, True),
    (7, 4, 45, True),
    (7, 4, 11, True),
])
def test_external_matrix_through_build_and_check_clis(tmp_path, monkeypatch,
                                                     case_count, method_count, timeout, failures):
    from harness.web_fetch.providers.fixture import FixtureProvider
    original_fetch = FixtureProvider.fetch

    def versioned_fixture(self, case, timeout, **kwargs):
        result = original_fetch(self, case, timeout, **kwargs)
        result.metadata.update(reported_version="9.9.9", browser_version="150.0.0.0",
                               output_contract="native_markdown_resource_urls")
        return result

    monkeypatch.setattr(FixtureProvider, "fetch", versioned_fixture)

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
    config = {name: {"type": "fixture", "fail_cases": ["task-0"] if failures else []}
              for name in methods}
    # Keep a public control on the first method and defaults on additional methods.
    # Private configuration fields must never enter the generated report.
    config[methods[0]].update(max_bytes=12345, api_key="synthetic-private-credential")
    config_path = evidence / "providers.json"
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
    for name in methods:
        for prefix in ['', 'en/']:
            page = (output / prefix / f'attempts/{name}__task-{min(1,case_count-1)}__r001.html').read_text()
            assert name+' 9.9.9' in page
            assert 'Moli 原生 Markdown' not in page
            assert 'Moli native Markdown' not in page
            assert 'native Markdown' in page
    saved = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert len(saved) == case_count * method_count
    for prefix, unit in (("", "秒"), ("en/", "seconds")):
        methodology = (output / prefix / "methodology/index.html").read_text()
        assert f"{timeout} {unit}" in methodology
        if timeout != 45:
            assert "45 秒" not in methodology
        assert "6.1 秒" not in methodology
    assert all(row["provider_metadata"]["reported_version"] == "9.9.9" for row in saved)
    if timeout == 30:
        for page in output.rglob("*.html"):
            content = page.read_text()
            for stale in ("45 秒", "45-second", "6.1 秒", "6.1-second",
                          "node-html-markdown 2.0.0", "40.5 秒"):
                assert stale not in content, (page.relative_to(output), stale)
    for prefix in ("", "en/"):
        home = (output / prefix / "index.html").read_text()
        assert "synthetic-0 9.9.9" in home
        assert "synthetic-0 150.0.0.0" not in home
    for prefix in ("", "en/"):
        assert len(list((output / prefix / "attempts").glob("*.html"))) == len(saved)
    assert (output / "local-evidence/input-dataset.json").read_bytes() == frozen.read_bytes()
    from harness.web_fetch.report import load_frozen_method_options
    options = load_frozen_method_options(output, saved)
    assert options["schema_version"] == 2
    assert options["providers"][methods[0]] == {"max_bytes": 12345}
    assert all(options["providers"][name] == {} for name in methods[1:])
    assert options["result_count"] == len(saved)
    assert options["provider_config_sha256"] == config_digest
    projected = (output / options["source_config"]["path"]).read_bytes()
    assert hashlib.sha256(projected).hexdigest() == options["source_config"]["sha256"]
    assert json.loads(projected)["original_provider_config_sha256"] == config_digest
    assert not (output / "providers.json").exists()
    for path in output.rglob("*"):
        if path.is_file():
            assert b"synthetic-private-credential" not in path.read_bytes()
    for prefix in ("", "en/"):
        assert "12345" in (output / prefix / "reproduce/index.html").read_text()
    if case_count == 5:
        from harness.web_fetch.report_build import build_report
        from harness.web_fetch.report_package import build as package_report
        from harness.web_fetch.publication_check import check as check_package
        binding_path = tmp_path / "presentation-binding.json"
        write(binding_path, {
            "comparison_kind": "uniform_native_local_measurement",
            "result_count": len(saved), "case_count": case_count, "providers": methods,
            "frozen_dataset_sha256": hashlib.sha256((output / "dataset.json").read_bytes()).hexdigest(),
            "results_sha256": hashlib.sha256((output / "results.jsonl").read_bytes()).hexdigest(),
            "measured_scoring_source_sha256": SCORING_SOURCE_SHA256,
            "evidence_bindings": [{"path": name,
                "sha256": hashlib.sha256((output / name).read_bytes()).hexdigest()}
                for name in ("local-run-contract.json", "execution.json",
                             "frozen-method-options.json", options["source_config"]["path"])],
        })
        rebuilt = tmp_path / "rebuilt"
        build_report(output, rebuilt, local_audit=True, presentation_binding=binding_path,
                     language="en", include_english=True)
        assert load_frozen_method_options(rebuilt, saved) == options
        assert (rebuilt / options["source_config"]["path"]).read_bytes() == projected
        archive = tmp_path / "frozen-report.zip"
        package_report(rebuilt, archive, presentation_binding=binding_path)
        check_package(archive, rebuilt, presentation_binding=binding_path)
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
