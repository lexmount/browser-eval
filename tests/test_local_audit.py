from harness.web_fetch import adjudication_evidence, local_execution_contract
import hashlib
import json

import pytest

from harness.web_fetch import local_audit
from harness.record_io import canonical_json_sha256


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_review_refresh_derives_changed_details_and_cases_from_bound_inputs(tmp_path):
    from copy import deepcopy
    from harness.web_fetch.report_build import _review_refresh_changes
    dataset = {'cases': [{'id': 'a', 'name': 'Old task'}, {'id': 'b', 'name': 'Unchanged'}]}
    rows = [{'attempt_id': 'a1', 'case_id': 'a', 'metrics': {'value': 1}},
            {'attempt_id': 'b1', 'case_id': 'b', 'metrics': {'value': 1}},
            {'attempt_id': 'b2', 'case_id': 'b', 'metrics': {'value': 1}}]
    _write(tmp_path / 'before-dataset.json', dataset)
    (tmp_path / 'before-results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    baseline = {key: {'path': path, 'sha256': _digest(tmp_path / path)}
                for key, path in [('dataset', 'before-dataset.json'), ('results', 'before-results.jsonl')]}
    binding = {'review_refresh_baseline': baseline}
    current_dataset, current_rows = deepcopy(dataset), deepcopy(rows)
    current_dataset['cases'][0]['name'] = 'Revised frozen task'
    current_rows[1]['metrics']['value'] = 2
    current_rows[2]['scoring'] = {'scored_at': 'new timestamp'}
    assert _review_refresh_changes(tmp_path, binding, current_dataset, current_rows) == ({'a1', 'b1'}, {'a'})
    assert _review_refresh_changes(tmp_path, {}, current_dataset, current_rows) == (set(), set())
    with pytest.raises(ValueError, match='inventory'):
        _review_refresh_changes(tmp_path, binding, current_dataset, current_rows[:-1])
    (tmp_path / 'before-results.jsonl').write_text('changed bytes')
    with pytest.raises(ValueError):
        _review_refresh_changes(tmp_path, binding, current_dataset, current_rows)


def test_frozen_scoring_controls_allow_additions_but_reject_edits(tmp_path, monkeypatch):
    original_root = local_audit.ROOT
    receipt = json.loads((original_root / 'research/dynamic-100-moli-119-local-audit.json').read_text())
    root = tmp_path / 'repo'
    bindings = receipt['repository_evidence']
    scoring_path = root / bindings['scoring_revision_path']
    scoring_path.parent.mkdir(parents=True)
    scoring_path.write_bytes((original_root / bindings['scoring_revision_path']).read_bytes())
    scoring = json.loads(scoring_path.read_text())
    # This boundary test owns a frozen synthetic registry. Historical measured
    # registries are retained with their scorer, not selected by today's runtime.
    controls = {'cases': [
        {'id': 'base', 'artifact': 'fixed'},
        {'id': 'positive', 'artifact': 'complete', 'root_cause': 'fixture', 'failure_mode': 'false-negative'},
        {'id': 'negative', 'artifact': 'missing', 'root_cause': 'fixture', 'failure_mode': 'false-positive'}]}
    scoring['preexisting_control_ids'] = ['base']
    scoring['revision_controls'] = [{k: row[k] for k in ('id', 'root_cause', 'failure_mode')}
                                    for row in controls['cases'][1:]]
    scoring['bindings']['preexisting_controls_sha256'] = canonical_json_sha256(controls['cases'][:1])
    scoring['bindings']['revision_controls_sha256'] = canonical_json_sha256(controls['cases'][1:])
    scoring['root_causes'] = [{'id': 'fixture', 'false_positive_ids': ['negative'], 'false_negative_ids': ['positive']}]
    scoring['scope'] = {'preexisting_controls': 1, 'revision_controls': 2, 'root_causes': 1, 'paired_error_directions': True}
    _write(scoring_path, scoring)
    bindings['scoring_revision_sha256'] = _digest(scoring_path)
    dataset = {'id': scoring['dataset_id'], 'cases': [{'id': 'fixture'}]}
    reference = root / 'evaluation/sources/fixture/reference.txt'
    reference.parent.mkdir(parents=True)
    reference.write_text('Independent synthetic reference')
    inventory = [{
        'case_id': 'fixture', 'path': 'evaluation/sources/fixture/reference.txt',
        'sha256': _digest(reference), 'bytes': reference.stat().st_size,
        'purpose': 'frozen scoring reference',
    }]
    source_manifest = root / bindings['source_manifest_path']
    _write(source_manifest, {
        'schema_version': 1, 'kind': 'dataset_source_evidence_inventory',
        'dataset_id': dataset['id'], 'dataset_sha256': receipt['bindings']['dataset_sha256'],
        'files': inventory,
        'scope': {'dataset_cases': 1, 'cases_with_source_evidence': 1, 'files': 1},
    })
    bindings['source_manifest_sha256'] = _digest(source_manifest)
    relative = 'evaluation/scoring-regressions.json'
    controls['cases'].append({'id': 'new-independent-control'})
    path = root / relative
    _write(path, controls)
    monkeypatch.setattr(local_audit, 'ROOT', root)
    local_audit._require_repository_bindings(dataset, receipt)
    controls['cases'][0]['artifact'] = 'changed frozen evidence'
    _write(path, controls)
    with pytest.raises(ValueError, match='control contents changed'):
        local_audit._require_repository_bindings(dataset, receipt)


def test_local_audit_requires_complete_inventory_and_review_lineage(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    source = tmp_path / "evidence"
    source.mkdir()
    receipt_relative = "research/local-audit.json"
    dataset = {
        "comparison": {
            "publication_state": "local_audit_only_not_for_release",
            "local_audit_receipt": receipt_relative,
        },
        "cases": [{"id": "case-1", "task_kind": "body"}],
    }
    record = {
        "attempt_id": "attempt-1", "case_id": "case-1", "ok": True,
        "metrics": {"content_pass": True},
    }
    _write(source / "dataset.json", dataset)
    (source / "results.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    supplement_dataset = "a" * 64
    supplement_results = "b" * 64
    review = {
        "schema_version": 1,
        "review_protocol": {"scope": "supplement-25-local-audit"},
        "bindings": {
            "dataset_sha256": _digest(source / "dataset.json"),
            "results_sha256": _digest(source / "results.jsonl"),
            "supplement_dataset_sha256": supplement_dataset,
            "supplement_results_sha256": supplement_results,
        },
        "attempts": [{
            "attempt_id": "attempt-1", "machine_verdict": "pass",
            "agent_verdict": "pass", "final_verdict": "pass",
        }],
        "total_attempts": 1, "expected_attempts": 1, "reviewed_attempts": 1,
        "accepted_unreviewed_passes": 0, "disagreements": 0, "unresolved": 0,
        "prior_75_audit_records_retained": True,
    }
    _write(source / "result-review.json", review)
    input_dataset = "c" * 64
    input_results = "d" * 64
    lineage = {
        "local-evidence/merge-receipt.json": {
            "sources": {
                "75": {"dataset_sha256": "e" * 64, "results_sha256": "f" * 64,
                       "cases": 0},
                "25": {"dataset_sha256": supplement_dataset,
                       "results_sha256": supplement_results, "cases": 1},
            },
            "output": {"dataset.json": input_dataset, "results.jsonl": input_results,
                       "cases": 1, "results": 1},
            "checks": {"complete": True},
        },
        "local-evidence/dynamic-25-full-result-audit.json": {
            "bindings": {"dataset_sha256": supplement_dataset,
                         "results_sha256": supplement_results},
            "cases": [{}], "provider_calls_during_review": 0,
            "verdict_changes_after_current_scorer": 0,
        },
        "local-evidence/failure-classification-correction.json": {
            "bindings": {"dataset_sha256": input_dataset,
                         "results_after_sha256": input_results},
            "invariants": {"verdicts_changed": False},
        },
        "local-evidence/cohorts/75/native-failure-audit.json": {
            "dataset_sha256": "e" * 64, "results_sha256": "f" * 64,
            "original_calls_added": 0,
        },
        "local-evidence/cohorts/75/qualification-review.json": {
            "summary": {"supports_dynamic_qualification": 0},
        },
        "local-evidence/local-audit-correction.json": {
            "output_dataset_sha256": _digest(source / "dataset.json"),
            "output_results_sha256": _digest(source / "results.jsonl"),
            "provider_calls": 0, "task_verdict_changes": [],
        },
        "local-evidence/scoring-plumbing-correction.json": {
            "input_dataset_sha256": _digest(source / "dataset.json"),
            "input_results_sha256": _digest(source / "results.jsonl"),
            "output_dataset_sha256": _digest(source / "dataset.json"),
            "output_results_sha256": _digest(source / "results.jsonl"),
            "scoring_source_sha256": "g" * 64,
            "provider_calls": 0, "task_verdict_changes": [],
        },
    }
    bound_receipts = {}
    for relative, document in lineage.items():
        path = source / relative
        _write(path, document)
        bound_receipts[relative] = _digest(path)
    manifest_path = source / local_audit.MANIFEST_NAME
    _write(manifest_path, local_audit.local_audit_manifest(source))
    receipt = {
        "status": "local_audit_complete",
        "bindings": {
            "input_dataset_sha256": input_dataset,
            "input_results_sha256": input_results,
            "dataset_sha256": _digest(source / "dataset.json"),
            "results_sha256": _digest(source / "results.jsonl"),
            "scoring_source_sha256": "g" * 64,
        },
        "scope": {"cases": 1, "results": 1},
        "evidence": {
            "manifest_path": local_audit.MANIFEST_NAME,
            "manifest_sha256": _digest(manifest_path),
            "result_review_sha256": _digest(source / "result-review.json"),
        },
        "review": {
            "supplement_cases_reviewed": 1,
            "supplement_dataset_sha256": supplement_dataset,
            "supplement_results_sha256": supplement_results,
            "post_correction_receipt": "local-evidence/scoring-plumbing-correction.json",
            "bound_receipts": bound_receipts,
        },
    }
    _write(root / receipt_relative, receipt)
    monkeypatch.setattr(local_audit, "ROOT", root)
    monkeypatch.setattr(local_audit, "_require_repository_bindings", lambda *_: None)

    validated = local_audit.require_local_audit(source, dataset, [record])
    assert validated["reviewed_attempts"] == 1

    prior = source / "local-evidence/cohorts/75/qualification-review.json"
    _write(prior, {"reviewed": False})
    with pytest.raises(ValueError, match="inventory or hashes changed"):
        local_audit.require_local_audit(source, dataset, [record])


@pytest.mark.parametrize('timeout', [None, 0, -1, True, '45', float('nan'), float('inf')])
def test_local_contract_rejects_invalid_deadline_before_reading_evidence(tmp_path, timeout):
    with pytest.raises(ValueError, match='positive finite caller timeout'):
        local_execution_contract.require_local_execution_contract(
            tmp_path, {}, [], {'timeout_seconds': timeout}, {})


@pytest.mark.parametrize('field,value', [('runs', 2), ('max_failure_retries', 1)])
def test_local_single_call_mode_rejects_rounds_and_retries(tmp_path, field, value):
    contract = {'schema_version': 1, 'kind': 'uniform_native_local_measurement',
                'timeout_seconds': 45, 'runs': 1, 'max_failure_retries': 0, field: value}
    with pytest.raises(ValueError, match='Invalid uniform native'):
        local_execution_contract.require_local_execution_contract(tmp_path, {}, [], contract, {})


@pytest.mark.parametrize("include_english,primary_language", [(False,"zh"), (True,"zh"), (True,"en")])
@pytest.mark.parametrize('timeout', [45, 12.5])
def test_new_local_matrix_is_complete_and_keeps_logs_without_claiming_review(tmp_path, monkeypatch, include_english, primary_language, timeout):
    from pathlib import Path
    from harness.web_fetch.config import load_dataset
    from harness.web_fetch.providers import build_provider
    from harness.web_fetch.runner import run_evaluation
    from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256
    from harness.web_fetch.report_build import build_report
    canonical = tmp_path / "repo/evaluation/dataset.json"
    # Exercise the production acceptance boundary without historical downloads.
    actual = {"id": "dynamic-control", "name": "Stable body fixture",
              "url": "https://example.invalid/", "category": "dynamic", "track": "dynamic",
              "task_kind": "body", "expected_any": ["Independent fixture output"],
              "source_verification": {"state": "unavailable"},
              "acceptance_contract": {"version": 1, "schema": "body_text",
                  "minimum_records": 0, "stable_text_anchors": [
                      {"id": "identity", "alternatives": ["Independent fixture output"]}]}}
    canonical_data = {"tool": "web_fetch", "cases": [actual]}
    _write(canonical, canonical_data)
    dataset = {**canonical_data, "id": "local-fixture", "name": "Local fixture",
               "coverage_plan": {"dynamic": ["fixture"]},
               "comparison": {"kind": "uniform_native_local_measurement",
                   "publication_state": "local_audit_only_not_for_release",
                   "source_reverified": False, "call_chain_audit": {"summary": "fixture"}}}
    inputs = tmp_path / "inputs/dataset.json"
    _write(inputs, dataset)
    config = tmp_path / "inputs/providers.json"
    _write(config, {"providers": {"fixture": {"type": "fixture"}}})
    _, cases = load_dataset(inputs)
    evidence = tmp_path / "evidence"
    records, _ = run_evaluation(output_dir=evidence, dataset_meta=dataset,
        dataset_path=inputs, cases=cases, providers=[build_provider("fixture", {"type": "fixture"})],
        provider_config_path=config, runs=1, timeout=timeout, delay=0, max_failure_retries=0)
    batch = json.loads((evidence / "manifest.json").read_text())
    _write(evidence / "execution.json", {"batches": [batch]})
    _write(evidence / "local-run-contract.json", {
        "schema_version": 1, "kind": "uniform_native_local_measurement",
        "dataset_path": "evaluation/dataset.json", "dataset_sha256": _digest(canonical),
        "scoring_source_sha256": SCORING_SOURCE_SHA256, "providers": ["fixture"],
        "runs": 1, "timeout_seconds": timeout, "max_failure_retries": 0})
    contract_path = evidence / "local-run-contract.json"
    contract = json.loads(contract_path.read_text())
    contract.update(provider_config_sha256=batch["provider_config_sha256"],
                    runtime_source_sha256=batch["runtime_source_sha256"])
    _write(contract_path, contract)
    dataset["comparison"]["local_run_contract_sha256"] = _digest(contract_path)
    _write(evidence / "dataset.json", dataset)
    monkeypatch.setattr(local_execution_contract, "ROOT", tmp_path / "repo")
    assert local_audit.require_local_audit(evidence, dataset, records) is None
    changed_contract = {**contract, 'timeout_seconds': timeout + 1}
    with pytest.raises(ValueError, match='parameters differ'):
        local_execution_contract.require_local_execution_contract(
            evidence, dataset, records, changed_contract, {'batches': [batch]})
    output = tmp_path / "report"
    build_report(evidence, output, local_audit=True, include_english=include_english)
    home = (output / "index.html").read_text()
    assert "本地未认证诊断快照" in home
    assert "查看调用链问题和日志留存范围" not in home
    assert 'href="guide/index.html"' in home
    assert "5案例小范围" not in home
    guide = (output / "guide/index.html").read_text()
    assert "比例只对应本报告列出的案例与配置" in guide
    assert "小范围调用链测试" not in guide
    assert "推算100题" not in guide
    detail = (output / "attempts" / (records[0]["attempt_id"] + ".html")).read_text()
    assert "运行日志与证据" in detail
    assert not (output / "result-review.json").exists()
    assert json.loads((output / "report-manifest.json").read_text())["certified"] is False
    english_output = tmp_path / "report-bilingual"
    build_report(evidence, english_output, local_audit=True, include_english=True)
    assert json.loads((english_output / "report-manifest.json").read_text())["languages"] == ["zh", "en"]
    assert (english_output / "en/index.html").is_file()
    from harness.web_fetch import report_interfaces
    from harness.web_fetch.artifacts import record_public_call_log_bindings
    spec = report_interfaces.SPECS["langchain-webbase"]
    monkeypatch.setattr(report_interfaces, "SPECS", {"fixture": spec})
    interfaces = report_interfaces.render_interfaces(output, records)
    assert "本轮保存的调用字段" in interfaces
    saved = json.loads((output / record_public_call_log_bindings(records[0])[-1]["path"]).read_text())
    rendered = json.loads((output / "adoption/interfaces/fixture.json").read_text())
    assert rendered["interface"]["request"] == saved["request"]
    assert rendered["interface"]["params"] == []
    assert spec["params"]  # Rendering must not mutate registered examples.
    from harness.web_fetch.report import provider_page_body
    method_records = [{**records[0], "provider_type": "firecrawl"}]
    method_cases = {actual["id"]: actual}
    page = provider_page_body("firecrawl", method_records, method_cases, [], [], native_default=True)
    assert "产品内部超时保留默认值" not in page
    assert "内部参数以保存的请求为准" in page
    assert 'href="../methodology/index.html"' in page
    assert 'href="../reproduce/index.html"' in page
    assert "Actions" not in page
    assert "Actions" in provider_page_body("firecrawl", method_records, method_cases, [], [])

    from harness.report_languages import select_report_language
    select_report_language(output, primary_language)
    # Explanatory edits of a historical snapshot must preserve every measured
    # byte, even after the repository scorer has moved on.
    from harness.web_fetch.report_build import refresh_local_presentation
    binding_path = tmp_path / "presentation-binding.json"
    frozen_rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    historical_scorer = "f" * 64
    for row in frozen_rows:
        row["metrics"]["scoring_source_sha256"] = historical_scorer
    (output / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in frozen_rows))
    manifest = json.loads((output / "report-manifest.json").read_text())
    manifest["results_sha256"] = _digest(output / "results.jsonl")
    manifest["template"] = {"renderer": "historical-template"}
    _write(output / "report-manifest.json", manifest)
    binding = {"comparison_kind": "uniform_native_local_measurement",
        "result_count": 1, "case_count": 1, "providers": ["fixture"],
        "frozen_dataset_sha256": _digest(output / "dataset.json"),
        "results_sha256": manifest["results_sha256"],
        "measured_scoring_source_sha256": historical_scorer,
        "measured_scoring_git_revision": "historical-revision",
        "evidence_bindings": [{"path": name, "sha256": _digest(output / name)}
                              for name in ("local-run-contract.json", "execution.json")]}
    _write(binding_path, binding)
    for missing in ("local-run-contract.json", "execution.json"):
        _write(binding_path, {**binding, "evidence_bindings": [
            item for item in binding["evidence_bindings"] if item["path"] != missing]})
        with pytest.raises(ValueError, match="input lacks a hash binding: " + missing):
            build_report(output, tmp_path / (missing + "-unbound"), local_audit=True,
                         presentation_binding=binding_path)
    _write(binding_path, binding)
    with pytest.raises(ValueError, match="outside its source tree"):
        build_report(output, output / "nested-report", local_audit=True,
                     presentation_binding=binding_path)
    public_logs = output / "logs"
    public_logs.mkdir(exist_ok=True)
    attempt_id = frozen_rows[0]["attempt_id"]
    debug = public_logs / (attempt_id + ".debug.txt")
    response = public_logs / (attempt_id + ".response.txt")
    debug.write_text("actual library event\n")
    response.write_text('{"observed": "actual API response"}\n')
    rebuilt = tmp_path / "frozen-bilingual-report"
    before_results = (output / "results.jsonl").read_bytes()
    with pytest.raises(ValueError, match="stale scoring"):
        build_report(output, rebuilt, local_audit=True, include_english=True)
    build_report(output, rebuilt, local_audit=True, include_english=True,
                 presentation_binding=binding_path)
    assert (rebuilt / "results.jsonl").read_bytes() == before_results
    assert (rebuilt / "dataset.json").read_bytes() == (output / "dataset.json").read_bytes()
    assert (rebuilt / "en/index.html").is_file()
    for prefix in ("", "en/"):
        detail = (rebuilt / prefix / "attempts" / (attempt_id + ".html")).read_text()
        assert attempt_id + ".debug.txt" in detail
        assert attempt_id + ".response.html" in detail
    from harness.web_fetch.report_i18n import untranslated_english_narrative, localize_html
    from pathlib import PurePosixPath
    english_detail = (rebuilt / "en/attempts" / (attempt_id + ".html")).read_text()
    assert "View complete API response (credentials and local paths hidden)" in english_detail
    for suffix in ("debug", "response"):
        view = (rebuilt / "en/logs" / (attempt_id + "." + suffix + ".html")).read_text()
        assert not untranslated_english_narrative(view, {"中文"})
    sample = ('<p>必需文字组 3（任一写法即可）</p><blockquote>first\n<span>或</span>\nsecond</blockquote><blockquote>引文 first\n或\nsecond</blockquote>'
              '<p>正文没有抓全；低于目标的位置为：位置 1 77.5%。下方会把完整标准答案与实际返回并排展示。</p>'
              '<pre>原始返回：位置 1\n或\n真实正文</pre>')
    localized = localize_html(sample, PurePosixPath("attempts/example.html"))
    assert "Required text group 3" in localized
    assert "first\n<span>or</span>\nsecond" in localized
    assert "引文 first\n或\nsecond" in localized
    from harness.web_fetch.report import required_text_answer_panel, stable_answer_panel
    alternatives = ["<source>\n或\noriginal", "second"]
    panels = [required_text_answer_panel({"required_facts": [{"alternatives": alternatives}]}),
              stable_answer_panel({"stable_text_anchors": [{"alternatives": alternatives}], "minimum_records": 1})]
    for panel in panels:
        assert '<span translate="no">&lt;source&gt;\n或\noriginal</span>\n<span>或</span>\n<span translate="no">second</span>' in panel
        english_panel = localize_html(panel, PurePosixPath("attempts/example.html"))
        assert '<span translate="no">&lt;source&gt;\n或\noriginal</span>\n<span>or</span>\n<span translate="no">second</span>' in english_panel
    assert "Region 1 77.5%" in localized
    assert '<pre>原始返回：位置 1\n或\n真实正文</pre>' in localized
    assert (rebuilt / "logs" / debug.name).read_bytes() == debug.read_bytes()
    assert (rebuilt / "logs" / response.name).read_bytes() == response.read_bytes()
    assert json.loads((rebuilt / "report-manifest.json").read_text())["presentation"]["frozen_scores"]
    rebuilt_manifest = json.loads((rebuilt / "report-manifest.json").read_text())
    from harness.reporting.templates import resolve_template
    assert rebuilt_manifest["template"] == resolve_template("webfetch")
    assert rebuilt_manifest["generated_languages"] == ["zh", "en"]
    assert rebuilt_manifest["presentation"]["authored_interpretation"] is False
    # Sharing a saved diagnostic must be explicit and retain strict defaults.
    from harness.web_fetch.report_package import build as package_report
    from harness.web_fetch.publication_check import check as check_package
    archive = tmp_path / "frozen-presentation.zip"
    with pytest.raises(ValueError, match="Diagnostic evidence"):
        package_report(rebuilt, archive)
    package_report(rebuilt, archive, presentation_binding=binding_path)
    check_package(archive, rebuilt, presentation_binding=binding_path)
    import zipfile
    cached = tmp_path/'cached-public-content'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(cached)
    package_report(rebuilt, archive, presentation_binding=binding_path, cached_content=cached)
    check_package(archive, rebuilt, presentation_binding=binding_path)
    with zipfile.ZipFile(archive) as bundle:
        assert json.loads(bundle.read('manifest.json'))['schema_version'] == 3
    verified_archive = archive.read_bytes()
    _write(binding_path, {**binding, "results_sha256": "0" * 64})
    with pytest.raises(ValueError, match="results.jsonl"):
        package_report(rebuilt, archive, presentation_binding=binding_path)
    assert archive.read_bytes() == verified_archive
    _write(binding_path, binding)
    from harness.web_fetch.report_check import check
    for invalid_template in (None, {"renderer": "unregistered-renderer"}):
        _write(rebuilt/"report-manifest.json", {**rebuilt_manifest, "template": invalid_template})
        with pytest.raises(ValueError, match="maintained template"):
            check(rebuilt, authority_root=Path(__file__).resolve().parents[1],
                  presentation_binding=binding_path)
    _write(rebuilt/"report-manifest.json", rebuilt_manifest)
    reproduce = (rebuilt / "reproduce/index.html").read_text()
    assert "--language zh --include-english" in reproduce
    assert "--language bilingual" not in reproduce
    assert "--presentation-binding" in reproduce
    assert all(row["metrics"]["scoring_source_sha256"] == historical_scorer
               for row in map(json.loads, (rebuilt / "results.jsonl").read_text().splitlines()))
    chinese = tmp_path / "frozen-chinese-report"
    build_report(rebuilt, chinese, local_audit=True, presentation_binding=binding_path)
    assert list((chinese / "en").rglob("*.html")) == [chinese / "en/index.html"]
    assert "not been generated" in (chinese / "en/index.html").read_text()
    _write(binding_path, {**binding, "results_sha256": "0" * 64})
    with pytest.raises(ValueError, match="input binding changed"):
        build_report(output, tmp_path / "invalid-rebuild", local_audit=True,
                     presentation_binding=binding_path)
    _write(binding_path, binding)
    binding.pop("measured_scoring_git_revision", None)
    _write(binding_path, binding)
    (output / "changes/index.html").write_text("<html><body>Old release history</body></html>")
    # Refresh must install the shared behavior/style required by new detail HTML.
    for relative in ("assets/report.js", "assets/style.css"):
        (output / relative).write_text("stale-presentation-asset")
    before = {path.relative_to(output).as_posix(): _digest(path)
              for path in output.rglob("*") if path.is_file()}
    from unittest.mock import patch
    original_rename = Path.rename
    def interrupted_swap(path, target):
        if Path(target) == output and path.name == 'selected':
            raise OSError('simulated installation interruption')
        return original_rename(path, target)
    with patch.object(Path, 'rename', interrupted_swap):
        with pytest.raises(OSError, match='installation interruption'):
            refresh_local_presentation(output, binding_path)
    assert {path.relative_to(output).as_posix(): _digest(path)
            for path in output.rglob('*') if path.is_file()} == before
    from harness.web_fetch.report_build import generate as current_renderer
    with patch('harness.web_fetch.report_build.generate', wraps=current_renderer) as rendered:
        refresh_local_presentation(output, binding_path)
    assert rendered.call_args.kwargs['authored_interpretation'] is False
    for relative in ("assets/report.js", "assets/style.css"):
        assert "stale-presentation-asset" not in (output / relative).read_text()
    allowed = {"index.html", "methodology/index.html", "adoption/index.html",
               "reproduce/index.html", "changes/index.html", "providers/fixture.html",
               "adoption/interfaces/fixture.json", "report-manifest.json",
               "assets/report.js", "assets/style.css"}
    if include_english:
        allowed.update('en/' + path for path in list(allowed) if path.endswith('.html'))
    for relative, digest in before.items():
        if relative not in allowed:
            assert _digest(output / relative) == digest
    assert _digest(output / "results.jsonl") == binding["results_sha256"]
    refreshed = json.loads((output / "report-manifest.json").read_text())
    assert refreshed["certified"] is False
    assert refreshed["presentation_refresh"]["scores_changed"] is False
    assert refreshed["presentation_refresh"]["scoring_git_revision"] is None
    assert "Old release history" not in (output / "changes/index.html").read_text()
    assert "2026-10-09-dynamic-1000-native-presentation" not in (output / "changes/index.html").read_text()
    assert "changes/index.html" in refreshed["presentation_refresh"]["changed_routes"]
    _write(binding_path, {**binding, "results_sha256": "0" * 64})
    with pytest.raises(ValueError, match="input binding changed"):
        refresh_local_presentation(output, binding_path)
    _write(binding_path, binding)
    artifact = output / frozen_rows[0]["artifact_path"]
    original_output = artifact.read_bytes()
    artifact.write_bytes(b"altered output")
    with pytest.raises(ValueError, match="Saved output changed"):
        refresh_local_presentation(output, binding_path)
    artifact.write_bytes(original_output)

    # A review-only update installs both the new detail and current coverage,
    # while keeping the authored guide, logs, references and measured inputs.
    from lexbench_webfetch.metrics import task_passed
    current_case = json.loads((output / "dataset.json").read_text())["cases"][0]
    current_record = frozen_rows[0]
    verdict = "pass" if task_passed(current_record["ok"], current_record["metrics"]) else "fail"
    text = artifact.read_text()
    opinion = {"attempt_id": attempt_id, "verdict": verdict,
               "reason": "Observed fixture answer: " + text[:80], "anomalies": []}
    initial_path = output / "local-evidence/fresh-initial.json"
    _write(initial_path, {"machine_judgments_withheld": True, "attempts": [opinion]})
    fresh = {"attempt_id": attempt_id, "agent_verdict": verdict,
             "agent_evidence": opinion["reason"], "reviewer_anomalies": [],
             "machine_verdict": verdict, "current_verdict": verdict,
             "final_verdict": verdict, "review_origin": "fresh",
             "content_sha256": current_record["metrics"]["content_sha256"],
             "review_evidence": {"case_sha256": canonical_json_sha256(current_case),
                                 "samples": [{"start": 0, "end": len(text), "text": text}]}}
    supplement_path = output / "local-evidence/fresh-review.json"
    _write(supplement_path, {"kind": "supplemental_saved_output_review",
        "results_sha256": binding["results_sha256"], "provider_calls_added": 0,
        "reviewed_attempts": 1, "reviewer": {"model": "synthetic reviewer"},
        "review_protocol": {"blind_initial_verdict": True},
        "blind_initial_outputs": [{"path": "local-evidence/fresh-initial.json",
                                    "sha256": _digest(initial_path)}], "attempts": [fresh]})
    review_path = output / "result-review.json"
    _write(review_path, {"kind": "local_independent_review", "review_rounds": [],
        "results_sha256": binding["results_sha256"], "reviewer": {},
        "review_protocol": {"scope": "all", "blind_initial_verdict": True},
        "reviewed_attempts": 1, "fresh_reviewed_attempts": 1,
        "expected_attempts": 1, "total_attempts": 1, "unresolved": 0,
        "unreviewed_attempt_ids": [], "accepted_unreviewed_passes": 0,
        "attempts": [fresh], "supplemental_reviews": [{
            "path": "local-evidence/fresh-review.json", "sha256": _digest(supplement_path)}]})
    binding["evidence_bindings"].append({"path": "result-review.json", "sha256": _digest(review_path)})
    _write(binding_path, binding)
    before_review = {path.relative_to(output).as_posix(): _digest(path)
                     for path in output.rglob("*") if path.is_file()}
    refresh_local_presentation(output, binding_path, review_only=True)
    permitted = allowed | {"review/index.html", "attempts/" + attempt_id + ".html",
                           "presentation-binding.json"}
    if include_english:
        permitted |= {"en/review/index.html", "en/attempts/" + attempt_id + ".html"}
    for relative, digest in before_review.items():
        if relative not in permitted:
            assert _digest(output / relative) == digest
    assert "1/1" in (output / "review/index.html").read_text()
    for prefix in (["", "en/"] if include_english else [""]):
        assert opinion["reason"] in (output / prefix / "attempts" / (attempt_id + ".html")).read_text()
    assert json.loads((output / "presentation-binding.json").read_text()) == binding
    assert (output / "results.jsonl").read_bytes() == before_results
    # A compact current authority has no old full-matrix baseline. Template
    # invalidation must still install all live derived pages, not copied ones.
    stale_routes = ['analysis/index.html', 'guide/index.html', 'datasets/index.html',
                    'datasets/' + current_case['id'] + '.html']
    for relative in stale_routes:
        (output/relative).write_text('<html><body>stale-current-state-route</body></html>')
    for relative in ['summary.json', 'reliability.json']:
        _write(output/relative, {'stale_current_state': True})
    with patch('harness.web_fetch.report_build._current_template_refresh', return_value=True):
        refresh_local_presentation(output, binding_path, review_only=True)
    for relative in stale_routes:
        assert 'stale-current-state-route' not in (output/relative).read_text()
        if include_english:
            assert 'stale-current-state-route' not in (output/'en'/relative).read_text()
    for relative in ['summary.json', 'reliability.json']:
        assert 'stale_current_state' not in json.loads((output/relative).read_text())
    assert (output/'results.jsonl').read_bytes() == before_results
    assert artifact.read_bytes() == original_output

    # A changed initial opinion must fail before any page is installed.
    _write(initial_path, {"machine_judgments_withheld": True, "attempts": []})
    before_invalid = {path.relative_to(output).as_posix(): _digest(path)
                      for path in output.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="Local revision evidence binding changed"):
        refresh_local_presentation(output, binding_path, review_only=True)
    assert before_invalid == {path.relative_to(output).as_posix(): _digest(path)
                              for path in output.rglob("*") if path.is_file()}

    # Self-consistent totals must still identify the actual physical calls.
    import copy
    changed_batch = copy.deepcopy(batch)
    changed_batch["attempt_ids"] = ["unrelated-call"]
    _write(evidence / "execution.json", {"batches": [changed_batch]})
    with pytest.raises(ValueError, match="execution calls"):
        local_audit.require_local_audit(evidence, dataset, records)
    _write(evidence / "execution.json", {"batches": [batch]})
    changed_records = copy.deepcopy(records)
    changed_records[0]["attempt_id"] = "other-call"
    changed_batch = copy.deepcopy(batch)
    changed_batch["attempt_ids"] = ["other-call"]
    _write(evidence / "execution.json", {"batches": [changed_batch]})
    with pytest.raises(ValueError, match="different result"):
        local_audit.require_local_audit(evidence, dataset, changed_records)
    _write(evidence / "execution.json", {"batches": [batch]})
    changed_records = copy.deepcopy(records)
    changed_records[0]["metrics"]["scoring_source_sha256"] = "stale"
    with pytest.raises(ValueError, match="scoring implementation"):
        local_audit.require_local_audit(evidence, dataset, changed_records)
    artifact = evidence / records[0]["artifact_path"]
    retained = artifact.with_suffix(".retained")
    artifact.rename(retained)
    artifact.symlink_to(retained.name)
    with pytest.raises(ValueError, match="unsafe"):
        local_audit.require_local_audit(evidence, dataset, records)
    artifact.unlink()
    retained.rename(artifact)
    with pytest.raises(ValueError, match="incomplete"):
        local_audit.require_local_audit(evidence, dataset, [])
    changed = json.loads(json.dumps(dataset))
    changed["cases"][0]["url"] = "https://other.test/"
    with pytest.raises(ValueError, match="changed the committed cases"):
        local_audit.require_local_audit(evidence, changed, records)


def test_resumed_local_segments_reject_repeated_calls_and_policy_changes():
    import copy
    policy = {"provider_config_sha256": "a", "runtime_source_sha256": "b",
              "parameters": {"timeout": 45}, "providers": [{"name": "test"}]}
    first = {**policy, "attempt_ids": ["one"], "attempt_count": 1}
    second = {**policy, "attempt_ids": ["two"], "attempt_count": 1}
    batch = {**policy, "attempt_ids": ["one", "two"], "attempt_count": 2,
             "segments": [first, second]}
    local_execution_contract._require_local_batch_segments(batch)
    duplicate = copy.deepcopy(batch)
    duplicate["segments"][1]["attempt_ids"] = ["one"]
    with pytest.raises(ValueError, match="repeat or omit"):
        local_execution_contract._require_local_batch_segments(duplicate)
    changed = copy.deepcopy(batch)
    changed["segments"][1]["parameters"] = {"timeout": 90}
    with pytest.raises(ValueError, match="parameters differ"):
        local_execution_contract._require_local_batch_segments(changed)


def test_local_call_chain_section_discloses_real_log_gaps_and_escapes_text():
    from harness.web_fetch.report import local_call_chain_section
    dataset = {"comparison": {"call_chain_audit": {
        "summary": "原判定保留；评分问题未更正",
        "methods": [{"provider": "moli-webfetch", "calls": 5, "request_ok": 0,
            "task_pass": 0, "logs": 5, "native_log_note": "2次原生日志缺失；<内部过程未知>",
            "assessment": "<原生内部原因未知>"}],
        "findings": [{"text": "无新增调用"}]}}}
    html = local_call_chain_section(dataset)
    assert "2次原生日志缺失" in html
    assert "&lt;内部过程未知&gt;" in html
    assert "调用数" not in html and "题目通过" not in html
    assert "调用方事件" in html and "产品与依赖库日志" in html


def test_report_real_diagnostics_do_not_claim_missing_debug(tmp_path):
    from harness.web_fetch.report import run_log_section
    log = tmp_path / "logs/real.txt"
    log.parent.mkdir()
    log.write_text('{"source":"caller","event":"request_sent"}\n')
    record = {"attempt_id": "a", "provider": "moli-webfetch", "case_id": "c",
              "run": 1, "started_at": "2026-10-08T00:00:00+00:00", "ok": False, "elapsed_ms": 45000,
              "provider_metadata": {"public_debug_log_path": "logs/real.txt",
                 "public_debug_log_sha256": _digest(log),
                 "public_log_note": "原生日志缺失，仅保留调用方事件",
                 "diagnostic_log_bytes": len(log.read_bytes()),
                 "diagnostic_log_sha256": _digest(log)}}
    html = run_log_section(tmp_path, record, "")
    assert "原生日志缺失，仅保留调用方事件" in html
    assert "已保存文件未截断；原生日志范围见说明" in html
    assert "没有保存 debug 日志" not in html
    assert "实际诊断日志" in html
    assert (tmp_path / "logs/real.html").is_file()
    assert "request_sent" in (tmp_path / "logs/real.html").read_text()


def test_local_score_correction_preserves_call_facts_and_original_contract(tmp_path):
    import copy
    from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256
    contract = {'scoring_source_sha256': 'old', 'provider_config_sha256': 'config'}
    record = {'attempt_id': 'a', 'ok': True, 'latency_ms': 1200,
              'metrics': {'content_sha256': 'body', 'scoring_source_sha256': SCORING_SOURCE_SHA256},
              'scoring': {'mode': 'offline_rescore', 'input_results_sha256': 'original',
                          'rule_revision': False, 'reference_revision': False}}
    receipt = {'kind': 'offline_generic_parser_correction', 'provider_calls': 0,
               'original_scoring_source_sha256': 'old',
               'corrected_scoring_source_sha256': SCORING_SOURCE_SHA256,
               'original_call_config_sha256': 'config', 'original_results_sha256': 'original',
               'call_identities': {'a': local_execution_contract.local_measurement_call_sha256(record)}}
    from lexbench_webfetch.scoring_regressions import build_correction_provenance
    receipt["provenance"] = build_correction_provenance(
        "structured-extraction", "Equivalent Markdown bullets must retain their named records.",
        ["markdown-plus-list-keeps-named-records", "markdown-plus-prose-is-not-a-record-list"])
    target = tmp_path / 'local-evidence/scoring-correction.json'
    _write(target, receipt)
    local_audit._require_local_scoring_revision(tmp_path, contract, [record])
    assert contract['scoring_source_sha256'] == 'old'
    _write(target, {key: value for key, value in receipt.items() if key != "provenance"})
    with pytest.raises(ValueError, match="root-cause"):
        local_audit._require_local_scoring_revision(tmp_path, contract, [record])
    _write(target, receipt)
    for key, value in [('ok', False), ('latency_ms', 900), ('attempt_id', 'other')]:
        changed = copy.deepcopy(record)
        changed[key] = value
        with pytest.raises(ValueError, match='binding'):
            local_audit._require_local_scoring_revision(tmp_path, contract, [changed])
    changed = copy.deepcopy(record)
    changed['scoring']['rule_revision'] = True
    with pytest.raises(ValueError, match='changed rules'):
        local_audit._require_local_scoring_revision(tmp_path, contract, [changed])
    changed = copy.deepcopy(record)
    changed['metrics']['content_sha256'] = 'different'
    with pytest.raises(ValueError, match='binding'):
        local_audit._require_local_scoring_revision(tmp_path, contract, [changed])
    _write(target, {**receipt, 'provider_calls': 1})
    with pytest.raises(ValueError, match='binding'):
        local_audit._require_local_scoring_revision(tmp_path, contract, [record])


def test_local_runtime_variants_bind_every_method_without_rewriting_manifests():
    import copy
    contract = {"providers": ["first", "second"], "runtime_source_sha256": "a" * 64,
                "runtime_source_sha256_by_provider": {"first": "a" * 64, "second": "b" * 64}}
    batches = [{"providers": [{"name": name}], "runtime_source_sha256": value}
               for name, value in contract["runtime_source_sha256_by_provider"].items()]
    local_execution_contract._require_local_method_bindings(contract, batches, "runtime_source_sha256")
    for mutation in ("wrong_hash", "duplicate", "missing", "extra_binding"):
        c, b = copy.deepcopy(contract), copy.deepcopy(batches)
        if mutation == "wrong_hash": b[1]["runtime_source_sha256"] = "c" * 64
        if mutation == "duplicate": b[1] = b[0]
        if mutation == "missing": b.pop()
        if mutation == "extra_binding": c["runtime_source_sha256_by_provider"]["other"] = "c" * 64
        with pytest.raises(ValueError):
            local_execution_contract._require_local_method_bindings(c, b, "runtime_source_sha256")
    legacy = {"providers": ["first"], "runtime_source_sha256": "a" * 64}
    local_execution_contract._require_local_method_bindings(legacy, batches[:1], "runtime_source_sha256")
    with pytest.raises(ValueError):
        local_execution_contract._require_local_method_bindings({**legacy, "runtime_source_sha256": "b" * 64}, batches[:1], "runtime_source_sha256")


def test_terminal_logs_are_separate_from_caller_events_and_not_excerpted():
    from harness.web_fetch.report import _separate_observed_logs
    terminal = "\n".join(f"DEBUG actual native line {i}" for i in range(30))
    raw = '{"source":"caller","event":"call_started"}\n--- native Moli stderr (verbatim, through collection cutoff) ---\n'+terminal+'\n'
    native, caller = _separate_observed_logs(raw)
    assert native == terminal
    assert 'call_started' not in native
    assert 'call_started' in caller
    assert 'actual native line 29' in native
    native, caller = _separate_observed_logs('{"source":"caller","event":"request_sent"}\n')
    assert native == '' and 'request_sent' in caller
    native, caller = _separate_observed_logs('{"source":"native_library","message":"actual library error","exception":"actual traceback"}\n')
    assert native == 'actual library error\nactual traceback' and caller == ''


def test_worker_stream_text_is_displayed_without_json_wrapper():
    from harness.web_fetch.report import _separate_observed_logs
    raw = '{"source":"native_process_stderr","event":"stream_line","text":"ERROR original process error"}\n{"source":"caller","event":"worker_request_sent"}\n'
    native, caller = _separate_observed_logs(raw)
    assert native == 'ERROR original process error'
    assert 'stream_line' not in native
    assert 'worker_request_sent' in caller


def test_empty_answer_does_not_display_stderr_as_returned_body():
    from harness.web_fetch.report import empty_output_reason, _error_locator
    trace = 'DEBUG original setup\n'*100+'Reason: fetch readiness timed out after 25000 ms\n'
    assert _error_locator(trace) == 'Reason: fetch readiness timed out after 25000 ms'
    text = empty_output_reason({'error_message':trace,'ok':False},{},'')
    assert 'timed out' in text and 'DEBUG original setup' not in text
    assert len(text) < 400


def test_local_configuration_variants_preserve_old_batches_and_reject_mismatches():
    import copy
    contract = {"providers": ["first", "second"], "provider_config_sha256": "a" * 64,
                "provider_config_sha256_by_provider": {"first": "a" * 64, "second": "b" * 64}}
    batches = [{"providers": [{"name": name}], "provider_config_sha256": value}
               for name, value in contract["provider_config_sha256_by_provider"].items()]
    local_execution_contract._require_local_method_bindings(contract, batches, "provider_config_sha256")
    for mutation in ("wrong_hash", "duplicate", "missing", "extra_binding", "invalid_hash"):
        c, b = copy.deepcopy(contract), copy.deepcopy(batches)
        if mutation == "wrong_hash": b[1]["provider_config_sha256"] = "c" * 64
        if mutation == "duplicate": b[1] = b[0]
        if mutation == "missing": b.pop()
        if mutation == "extra_binding": c["provider_config_sha256_by_provider"]["other"] = "c" * 64
        if mutation == "invalid_hash": c["provider_config_sha256_by_provider"]["first"] = "invalid"
        with pytest.raises(ValueError):
            local_execution_contract._require_local_method_bindings(c, b, "provider_config_sha256")
    legacy = {"providers": ["first"], "provider_config_sha256": "a" * 64}
    local_execution_contract._require_local_method_bindings(legacy, batches[:1], "provider_config_sha256")
    with pytest.raises(ValueError):
        local_execution_contract._require_local_method_bindings({**legacy, "provider_config_sha256": "b" * 64}, batches[:1], "provider_config_sha256")


def test_worker_protocol_response_is_not_a_runtime_log():
    from harness.web_fetch.report import _separate_observed_logs, runtime_log_text
    response = json.dumps({"source":"native_process_stdout", "text":json.dumps({"ok":True,"content":"body","raw_tool_response":{"value":"body"}})})+"\n"
    progress = json.dumps({"source":"native_process_stdout","text":"[FETCH] actual progress"})+"\n"
    worker = json.dumps({"source":"native_process_stderr","text":json.dumps({"source":"caller_worker","event":"native_tool_returned"})})+"\n"
    native,caller = _separate_observed_logs(response+progress+worker)
    assert native == "[FETCH] actual progress"
    assert "native_tool_returned" in caller
    assert "raw_tool_response" not in native+caller
    assert runtime_log_text(response+progress+worker)==progress+worker


@pytest.mark.parametrize('returned_url', ['https://example.test/actual', None])
def test_local_offline_projection_keeps_native_url_and_call_facts(tmp_path, returned_url):
    import copy
    original = {"attempt_id": "a", "ok": True, "elapsed_ms": 17,
                "final_url": "https://example.test/input", "metrics": {"content_sha256": "body"}}
    corrected = copy.deepcopy(original)
    corrected.update(final_url=returned_url, scoring={"mode": "offline_rescore"})
    corrected["metrics"]["score"] = 1
    native = tmp_path / "native.json"
    _write(native, {"data": {"url": corrected["final_url"]}})
    receipt = {"original_call_identities": {"a": local_execution_contract.local_measurement_call_sha256(original)},
               "call_identities": {"a": local_execution_contract.local_measurement_call_sha256(corrected)},
               "field_projections": {"a": {"final_url": {"path": "native.json", "sha256": _digest(native),
                   "json_pointer": ["data", "url"], "original_value": original["final_url"]}}}}
    path = tmp_path / "local-evidence/scoring-correction.json"
    _write(path, receipt)
    local_execution_contract._require_local_record_correction(tmp_path, original, corrected)
    changed = copy.deepcopy(corrected)
    changed["elapsed_ms"] = 1
    receipt["call_identities"]["a"] = local_execution_contract.local_measurement_call_sha256(changed)
    _write(path, receipt)
    with pytest.raises(ValueError, match="measured call facts"):
        local_execution_contract._require_local_record_correction(tmp_path, original, changed)
    _write(path, {**receipt, "call_identities": {"a": local_execution_contract.local_measurement_call_sha256(corrected)}})
    _write(native, {"data": {"url": "https://example.test/wrong"}})
    with pytest.raises(ValueError):
        local_execution_contract._require_local_record_correction(tmp_path, original, corrected)
    # Missing is not the same as an explicitly returned JSON null.
    for payload in ({'data': {}}, {}):
        _write(native, payload)
        receipt['call_identities']['a'] = local_execution_contract.local_measurement_call_sha256(corrected)
        receipt['field_projections']['a']['final_url']['sha256'] = _digest(native)
        _write(path, receipt)
        with pytest.raises(ValueError, match='field is missing'):
            local_execution_contract._require_local_record_correction(tmp_path, original, corrected)


def test_local_review_retains_deferred_failure_and_rejects_wrong_coverage(tmp_path):
    import copy
    (tmp_path / "results.jsonl").write_text("saved results")
    record = {"attempt_id": "a", "ok": False, "metrics": {"content_sha256": "body"}}
    review = {"kind": "local_independent_review", "review_protocol": {"scope": "all"},
              "reviewed_attempts": 1, "expected_attempts": 1,
              "results_sha256": _digest(tmp_path / "results.jsonl"), "unresolved": 1,
              "attempts": [{"attempt_id": "a", "content_sha256": "body", "agent_verdict": "pass",
                            "current_verdict": "fail", "final_verdict": None,
                            "status": "deferred_unadjudicated"}]}
    path = tmp_path / "review.json"
    def bind(value):
        _write(path, value)
        return {"comparison": {"independent_review": {"path": "review.json", "sha256": _digest(path)}}}
    assert adjudication_evidence._require_local_review(tmp_path, bind(review), [record])["unresolved"] == 1
    changed = copy.deepcopy(review)
    changed["attempts"][0]["final_verdict"] = "pass"
    with pytest.raises(ValueError, match="scored evidence"):
        adjudication_evidence._require_local_review(tmp_path, bind(changed), [record])
    changed = copy.deepcopy(review)
    changed["attempts"].append(changed["attempts"][0])
    with pytest.raises(ValueError, match="scope differs"):
        adjudication_evidence._require_local_review(tmp_path, bind(changed), [record])
    changed = {**review, "unresolved": 0}
    with pytest.raises(ValueError, match="unresolved count"):
        adjudication_evidence._require_local_review(tmp_path, bind(changed), [record])


def test_local_review_banner_distinguishes_reviewed_and_original_measurements():
    from harness.web_fetch.report import local_audit_banner
    dataset = {"comparison": {"kind": "uniform_native_local_measurement",
                               "publication_state": "local_audit_only_not_for_release"}}
    assert "未进行独立人工审核" in local_audit_banner(dataset)
    dataset["comparison"]["independent_review"] = {"path": "review.json"}
    text = local_audit_banner(dataset)
    assert "已有复核记录" in text
    assert "已完成全量独立复核" not in text
    assert "未进行独立人工审核" not in text


def test_retrospective_page_review_preserves_call_and_checks_other_requirements(tmp_path):
    import copy
    from lexbench_webfetch.scoring import score_attempt
    from lexbench_webfetch.metrics import task_passed
    body = 'Expected complete body. A second distinctive sentence describing this page.'
    case = {'id': 'synthetic', 'url': 'https://example.test/start', 'track': 'risk_control',
            'task_kind': 'body', 'page_identity_required': True, 'source_benchmark': {'risk_control': {'navigation_contract': {
                'requested_url': 'https://example.test/start',
                'observed_final_url': 'https://example.test/document'}}},
            'acceptance_contract': {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
                'stable_text_anchors': [{'id': 'body', 'alternatives': ['Expected complete body']}]}}
    (tmp_path / 'answer.txt').write_text(body)
    record = {'attempt_id': 'method__synthetic__r001', 'case_id': 'synthetic',
              'artifact_path': 'answer.txt', 'ok': True, 'final_url': None,
              'metrics': score_attempt(case, body.encode(), ok=True)}
    captures = [{'url': 'https://example.test/document', 'text': body}]
    annotation = {'attempt_id': record['attempt_id'],
                  'case_sha256': canonical_json_sha256(case),
                  'call_sha256': local_execution_contract.local_measurement_call_sha256(record),
                  'content_sha256': record['metrics']['content_sha256'],
                  'basis': 'retrospective_content_correspondence', 'reviewed_at': '2026-10-13',
                  'capture_index': 0, 'capture_sha256': canonical_json_sha256(captures[0]),
                  'corresponding_url': captures[0]['url'],
                  'matching_samples': ['Expected complete body.',
                      'A second distinctive sentence describing this page.']}
    before = copy.deepcopy(record)
    scored = adjudication_evidence.score_reviewed_page_identity(tmp_path, case, record, annotation, captures)
    assert record == before and record['final_url'] is None
    assert task_passed(True, scored)
    assert scored['page_identity_basis'] == 'retrospective_content_correspondence'
    # Two substantial matches establish correspondence; additional true short
    # labels do not invalidate them and cannot replace either long match.
    with_short = {**annotation, 'matching_samples': annotation['matching_samples'] + ['body']}
    assert task_passed(True, adjudication_evidence.score_reviewed_page_identity(tmp_path, case, record, with_short, captures))
    with pytest.raises(ValueError, match='substantive text'):
        adjudication_evidence.score_reviewed_page_identity(tmp_path, case, record,
            {**annotation, 'matching_samples': [annotation['matching_samples'][0], 'body']}, captures)
    for samples in ([' ' * 20, ' ' * 21], ['.' * 20, '!' * 21],
                    ['Expected complete body.', '**Expected complete body.**']):
        with pytest.raises(ValueError, match='substantive text'):
            adjudication_evidence.score_reviewed_page_identity(tmp_path, case, record,
                                                    {**annotation, 'matching_samples': samples}, captures)
    for field, value, message in (
        ('content_sha256', 'wrong', 'binding'),
        ('capture_sha256', 'wrong', 'capture'),
        ('matching_samples', ['Missing substantive text', 'Other missing substantive text'], 'matching'),
    ):
        bad = {**annotation, field: value}
        with pytest.raises(ValueError, match=message):
            adjudication_evidence.score_reviewed_page_identity(tmp_path, case, record, bad, captures)
    wrong = [{'url': 'https://example.test/other', 'text': body}]
    bad = {**annotation, 'corresponding_url': wrong[0]['url'],
           'capture_sha256': canonical_json_sha256(wrong[0])}
    with pytest.raises(ValueError, match='URL requirement'):
        adjudication_evidence.score_reviewed_page_identity(tmp_path, case, record, bad, wrong)
    # A reviewed page identity must never excuse missing required content.
    missing = copy.deepcopy(case)
    missing['acceptance_contract']['stable_text_anchors'].append(
        {'id': 'missing', 'alternatives': ['A required fact that was not returned']})
    bad = {**annotation, 'case_sha256': canonical_json_sha256(missing)}
    assert not task_passed(True, adjudication_evidence.score_reviewed_page_identity(
        tmp_path, missing, record, bad, captures))


def test_retrospective_page_review_requires_bound_receipt(tmp_path):
    with pytest.raises(ValueError, match='lack a receipt'):
        adjudication_evidence._require_page_identity_review(tmp_path, {}, [
            {'attempt_id': 'synthetic', 'metrics': {
                'page_identity_basis': 'retrospective_content_correspondence'}}])


@pytest.mark.parametrize('defect', [None, 'target_changed', 'sample_changed', 'call_changed', 'answer_changed', 'diagnostic_changed'])
@pytest.mark.parametrize('case_id', ['synthetic', 'synthetic__nested'])
def test_offline_rescore_reuses_only_unchanged_identity_inputs(tmp_path, defect, case_id):
    import copy
    from lexbench_webfetch.scoring import score_attempt
    body = 'Expected complete body. A second distinctive sentence describing this page.'
    old = {'id': case_id, 'url': 'https://example.test/start', 'track': 'risk_control',
        'task_kind': 'body', 'forbidden_any': ['CAPTCHA'],
        'page_identity_required': True, 'source_benchmark': {'risk_control': {'navigation_contract': {
            'requested_url': 'https://example.test/start', 'observed_final_url': 'https://example.test/document'}}},
        'acceptance_contract': {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'stable_text_anchors': [{'id': 'body', 'alternatives': ['Expected complete body']}]}}
    current = copy.deepcopy(old)
    (tmp_path/'answer.txt').write_text(body)
    record = {'attempt_id': f'method__{case_id}__r001', 'case_id': case_id,
        'artifact_path': 'answer.txt', 'ok': True, 'final_url': None,
        'metrics': score_attempt(current, body.encode(), ok=True)}
    captures = [{'url': 'https://example.test/document', 'text': body}]
    def save(name, obj):
        _write(tmp_path/name, obj)
        return {'path': name, 'sha256': _digest(tmp_path/name)}
    capture = save('captures.json', captures)
    annotation = {'attempt_id': record['attempt_id'], 'case_sha256': canonical_json_sha256(old),
        'call_sha256': local_execution_contract.local_measurement_call_sha256(record),
        'content_sha256': record['metrics']['content_sha256'], 'basis': 'retrospective_content_correspondence',
        'reviewed_at': '2026-10-13', 'capture_index': 0,
        'capture_sha256': canonical_json_sha256(captures[0]), 'corresponding_url': captures[0]['url'],
        'matching_samples': ['Expected complete body.', 'A second distinctive sentence describing this page.']}
    if defect == 'sample_changed': annotation['matching_samples'] = [' '*20, ' '*21]
    receipt = save('identity.json', {'kind': 'retrospective_page_identity_review',
        'reviewed_at': '2026-10-13', 'basis': 'retained human review', 'provider_calls_added': 0,
        'captures_path': capture['path'], 'captures_sha256': capture['sha256'], 'attempts': [annotation]})
    before = {'cases': [old], 'comparison': {'page_identity_review': receipt}}
    dataset = {'cases': [current], 'comparison': {}}
    prior_receipt = (tmp_path/'identity.json').read_bytes()
    if defect == 'target_changed': current['task_prompt'] = 'A new target requirement'
    if defect == 'diagnostic_changed': current['diagnostic_any'] = current.pop('forbidden_any')
    if defect == 'call_changed': record['elapsed_ms'] = 99
    if defect == 'answer_changed': record['metrics']['content_sha256'] = 'different-answer'
    from copy import deepcopy
    unchanged_dataset, unchanged_record = deepcopy(dataset), deepcopy(record)
    if defect:
        with pytest.raises(ValueError):
            adjudication_evidence.prepare_reviewed_page_identities(tmp_path, before, dataset, [record])
        assert dataset == unchanged_dataset
        assert record == unchanged_record
    else:
        updates = adjudication_evidence.prepare_reviewed_page_identities(tmp_path, before, dataset, [record])
        assert updates.binding == receipt
        assert dataset == unchanged_dataset
        assert record == unchanged_record
        dataset['comparison']['page_identity_review'] = updates.binding
        record['metrics'] = updates.metrics_by_attempt[record['attempt_id']]
    assert not (tmp_path/'local-evidence').exists()
    if not defect:
        assert local_audit.task_passed(record['ok'], record['metrics'])
        assert record['final_url'] is None
        adjudication_evidence._require_page_identity_review(tmp_path, dataset, [record])
        record['metrics']['scoring_source_sha256'] = 'historical-source'
        sources = {record['attempt_id']: 'historical-source'}
        adjudication_evidence._require_page_identity_review(tmp_path, dataset, [record], scoring_sources=sources)
        with pytest.raises(ValueError, match='shared scorer'):
            adjudication_evidence._require_page_identity_review(tmp_path, dataset, [record])
        with pytest.raises(ValueError, match='scoring source'):
            adjudication_evidence._require_page_identity_review(tmp_path, dataset, [record],
                scoring_sources={record['attempt_id']: 'other-source'})
        record['metrics']['content_chars'] += 1
        with pytest.raises(ValueError, match='shared scorer'):
            adjudication_evidence._require_page_identity_review(tmp_path, dataset, [record], scoring_sources=sources)
    assert (tmp_path/'identity.json').read_bytes() == prior_receipt


@pytest.mark.parametrize('implicit_identity', [False, True])
@pytest.mark.parametrize('defect', [None, 'target_changed', 'review_changed'])
def test_saved_legacy_identity_migration_is_read_only_and_hash_bound(tmp_path, implicit_identity, defect):
    """Published old receipts remain verifiable; no migration writer is needed."""
    import copy
    old = {'id': 'historical', 'forbidden_any': ['CAPTCHA'], 'track': 'risk_control',
           'source_benchmark': {'risk_control': {'navigation_contract': {
               'requested_url': 'https://example.test/start',
               'observed_final_url': 'https://example.test/end'}}}}
    if not implicit_identity:
        old['page_identity_required'] = True
    current = copy.deepcopy(old)
    current['diagnostic_any'] = current.pop('forbidden_any')
    current['page_identity_required'] = True
    annotation = {'case_sha256': canonical_json_sha256(old), 'attempt_id': 'retained'}
    previous = {'attempts': [annotation], 'captures_path': 'captures.json', 'captures_sha256': 'capture-hash'}
    def save(name, value):
        _write(tmp_path/name, value)
        return {'path': name, 'sha256': _digest(tmp_path/name)}
    receipt = {**previous, 'attempts': [{**annotation,
        'prior_case_sha256': annotation['case_sha256'],
        'case_sha256': canonical_json_sha256(current)}],
        'binding_revision': {'kind': 'diagnostic_field_migration', 'new_independent_review': False,
            'prior_receipt': save('prior.json', previous),
            'prior_cases': save('cases.json', {'historical': old})}}
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    if defect == 'target_changed': current['task_prompt'] = 'new requirement'
    if defect == 'review_changed': receipt['attempts'][0]['reviewed_at'] = 'new opinion'
    if defect:
        with pytest.raises(ValueError):
            adjudication_evidence._require_identity_binding_revision(tmp_path, {'cases': [current]}, receipt)
    else:
        adjudication_evidence._require_identity_binding_revision(tmp_path, {'cases': [current]}, receipt)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_diagnostic_viewers_share_unchanged_source_across_languages(tmp_path):
    from harness.web_fetch.report import diagnostic_view_page
    from harness.web_fetch.report_i18n import write_english_report
    from bs4 import BeautifulSoup
    source=tmp_path/'logs/actual.txt';source.parent.mkdir()
    original=('ERROR: exact target failure\n'+'payload ' * 200000+'unique-full-file-tail').encode()
    source.write_bytes(original)
    relative=diagnostic_view_page(tmp_path,'logs/actual.txt','已保存的诊断日志','仅包含实际观测的日志。')
    write_english_report(tmp_path,source_routes=[relative])
    for prefix in ['', 'en/']:
        raw=(tmp_path/prefix/relative).read_bytes()
        assert len(raw)<20000 and b'unique-full-file-tail'not in raw
        soup=BeautifulSoup(raw,'html.parser')
        assert soup.select_one('dialog pre[data-file]')['data-file']=='logs/actual.txt'
        assert soup.select_one('[data-call-log-open]')
        assert soup.select_one('[data-call-log-close]')
        assert 'exact target failure'in soup.get_text()
    assert source.read_bytes()==original


def test_long_product_and_caller_logs_use_one_existing_source(tmp_path):
    from harness.web_fetch.report import run_log_section
    from bs4 import BeautifulSoup
    source=tmp_path/'logs/runtime.txt';source.parent.mkdir()
    original=('{"source":"caller","event":"request_sent"}\n--- native product ---\nERROR: exact native failure\n'
              +'large-native-line ' * 200000+'unique-native-tail').encode()
    source.write_bytes(original)
    row={'attempt_id':'a','provider':'fixture','case_id':'c','run':1,'started_at':'2026-10-08',
         'ok':False,'elapsed_ms':100,'error_type':'NativeError','error_message':'exact native failure',
         'provider_metadata':{'public_debug_log_path':'logs/runtime.txt',
             'public_debug_log_sha256':_digest(source)}}
    rendered=run_log_section(tmp_path,row,'')
    assert len(rendered)<30000 and 'unique-native-tail'not in rendered
    soup=BeautifulSoup(rendered,'html.parser')
    assert soup.select_one('dialog pre[data-file]')['data-file']=='logs/runtime.txt'
    assert 'exact native failure'in soup.get_text()
    assert source.read_bytes()==original


def test_full_current_refresh_requires_explicit_authority_and_changed_template(tmp_path):
    from harness.web_fetch.report_build import _current_template_refresh
    from harness.web_fetch import report
    path=tmp_path/'authority.json'
    def binding(kind='current_local_snapshot_authority', schema=1):
        _write(path, {'kind': kind, 'schema_version': schema})
        return {'offline_revision': {'path': 'authority.json', 'sha256': _digest(path)}}
    changed={'presentation_refresh': {'renderer_sha256': 'old-template'}}
    assert _current_template_refresh(tmp_path,binding(),changed)
    same={'presentation_refresh': {'renderer_sha256': _digest(__import__('pathlib').Path(report.__file__))}}
    assert not _current_template_refresh(tmp_path,binding(),same)
    assert not _current_template_refresh(tmp_path,binding('local_offline_score_revision'),changed)
    assert not _current_template_refresh(tmp_path,binding(schema=2),changed)
    assert not _current_template_refresh(tmp_path,{},changed)
    bad=binding();path.write_text('changed')
    with pytest.raises(ValueError):
        _current_template_refresh(tmp_path,bad,changed)


@pytest.mark.parametrize("current", [False, True])
def test_frozen_manifest_uses_only_the_selected_task_replacement_authority(tmp_path, current):
    from harness.web_fetch.report_build import write_frozen_presentation_manifest
    scope = {'added_case_id': 'current-task', 'removed_case_id': 'retired-task'}
    authority = {'kind': 'current_local_snapshot_authority' if current else 'local_offline_score_revision',
                 'schema_version': 1, 'task_replacement_current_scope': scope}
    path = tmp_path/'authority.json'
    _write(path, authority)
    _write(tmp_path/'presentation-binding.json', {'offline_revision': {
        'path': path.name, 'sha256': _digest(path)}})
    _write(tmp_path/'report-languages.json', {'languages': ['zh'], 'primary_language': 'zh'})
    original = {'task_replacement': {'path': 'deleted-history/receipt.json', 'sha256': 'old'},
                'results_sha256': 'unchanged-results'}
    write_frozen_presentation_manifest(tmp_path, original)
    saved = json.loads((tmp_path/'report-manifest.json').read_text())
    assert saved['results_sha256'] == original['results_sha256']
    if current:
        assert 'task_replacement' not in saved
        assert saved['task_replacement_current_scope'] == scope
    else:
        assert saved['task_replacement'] == original['task_replacement']
        assert 'task_replacement_current_scope' not in saved
