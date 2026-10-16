import hashlib
import json

import pytest

from harness.web_fetch import local_audit


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_frozen_scoring_controls_allow_additions_but_reject_edits(tmp_path, monkeypatch):
    original_root = local_audit.ROOT
    receipt = json.loads((original_root / 'research/dynamic-100-moli-119-local-audit.json').read_text())
    root = tmp_path / 'repo'
    bindings = receipt['repository_evidence']
    scoring_path = root / bindings['scoring_revision_path']
    scoring_path.parent.mkdir(parents=True)
    scoring_path.write_bytes((original_root / bindings['scoring_revision_path']).read_bytes())
    scoring = json.loads(scoring_path.read_text())
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
    controls = json.loads((original_root / relative).read_text())
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


def test_new_local_matrix_is_complete_and_keeps_logs_without_claiming_review(tmp_path, monkeypatch):
    from pathlib import Path
    from harness.web_fetch.config import load_dataset
    from harness.web_fetch.providers import build_provider
    from harness.web_fetch.runner import run_evaluation
    from harness.web_fetch.scoring import SCORING_SOURCE_SHA256
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
        provider_config_path=config, runs=1, timeout=45, delay=0, max_failure_retries=0)
    batch = json.loads((evidence / "manifest.json").read_text())
    _write(evidence / "execution.json", {"batches": [batch]})
    _write(evidence / "local-run-contract.json", {
        "schema_version": 1, "kind": "uniform_native_local_measurement",
        "dataset_path": "evaluation/dataset.json", "dataset_sha256": _digest(canonical),
        "scoring_source_sha256": SCORING_SOURCE_SHA256, "providers": ["fixture"],
        "runs": 1, "timeout_seconds": 45, "max_failure_retries": 0})
    contract_path = evidence / "local-run-contract.json"
    contract = json.loads(contract_path.read_text())
    contract.update(provider_config_sha256=batch["provider_config_sha256"],
                    runtime_source_sha256=batch["runtime_source_sha256"])
    _write(contract_path, contract)
    dataset["comparison"]["local_run_contract_sha256"] = _digest(contract_path)
    _write(evidence / "dataset.json", dataset)
    monkeypatch.setattr(local_audit, "ROOT", tmp_path / "repo")
    assert local_audit.require_local_audit(evidence, dataset, records) is None
    output = tmp_path / "report"
    build_report(evidence, output, local_audit=True)
    home = (output / "index.html").read_text()
    assert "本地实测版" in home
    assert "1 个案例 · 1 次调用，机器判分。" in home
    assert "本轮未重新核验来源。" in home
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
    assert "产品内部超时保留默认值" in page
    assert "Actions" not in page
    assert "Actions" in provider_page_body("firecrawl", method_records, method_cases, [], [])

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
    _write(output / "report-manifest.json", manifest)
    binding = {"comparison_kind": "uniform_native_local_measurement",
        "result_count": 1, "case_count": 1, "providers": ["fixture"],
        "frozen_dataset_sha256": _digest(output / "dataset.json"),
        "results_sha256": manifest["results_sha256"],
        "measured_scoring_source_sha256": historical_scorer,
        "measured_scoring_git_revision": "historical-revision"}
    _write(binding_path, binding)
    (output / "changes/index.html").write_text("<html><body>Old release history</body></html>")
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
    refresh_local_presentation(output, binding_path)
    allowed = {"index.html", "methodology/index.html", "adoption/index.html",
               "reproduce/index.html", "changes/index.html", "providers/fixture.html",
               "adoption/interfaces/fixture.json", "report-manifest.json"}
    for relative, digest in before.items():
        if relative not in allowed:
            assert _digest(output / relative) == digest
    assert _digest(output / "results.jsonl") == binding["results_sha256"]
    refreshed = json.loads((output / "report-manifest.json").read_text())
    assert refreshed["certified"] is False
    assert refreshed["presentation_refresh"]["scores_changed"] is False
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
    local_audit._require_local_batch_segments(batch)
    duplicate = copy.deepcopy(batch)
    duplicate["segments"][1]["attempt_ids"] = ["one"]
    with pytest.raises(ValueError, match="repeat or omit"):
        local_audit._require_local_batch_segments(duplicate)
    changed = copy.deepcopy(batch)
    changed["segments"][1]["parameters"] = {"timeout": 90}
    with pytest.raises(ValueError, match="parameters differ"):
        local_audit._require_local_batch_segments(changed)


def test_local_call_chain_section_discloses_real_log_gaps_and_escapes_text():
    from harness.web_fetch.report import local_call_chain_section
    dataset = {"comparison": {"call_chain_audit": {
        "summary": "原判定保留；评分问题未更正",
        "methods": [{"provider": "moli-webfetch", "calls": 5, "request_ok": 0,
            "task_pass": 0, "logs": 5, "native_log_note": "2次原生日志缺失",
            "assessment": "<原生内部原因未知>"}],
        "findings": [{"text": "无新增调用"}]}}}
    html = local_call_chain_section(dataset)
    assert "2次原生日志缺失" in html and "无新增调用" in html
    assert "&lt;原生内部原因未知&gt;" in html
    assert "原判定保留；评分问题未更正" in html


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
    from harness.web_fetch.scoring import SCORING_SOURCE_SHA256
    contract = {'scoring_source_sha256': 'old', 'provider_config_sha256': 'config'}
    record = {'attempt_id': 'a', 'ok': True, 'latency_ms': 1200,
              'metrics': {'content_sha256': 'body', 'scoring_source_sha256': SCORING_SOURCE_SHA256},
              'scoring': {'mode': 'offline_rescore', 'input_results_sha256': 'original',
                          'rule_revision': False, 'reference_revision': False}}
    receipt = {'kind': 'offline_generic_parser_correction', 'provider_calls': 0,
               'original_scoring_source_sha256': 'old',
               'corrected_scoring_source_sha256': SCORING_SOURCE_SHA256,
               'original_call_config_sha256': 'config', 'original_results_sha256': 'original',
               'call_identities': {'a': local_audit.local_measurement_call_sha256(record)}}
    from harness.web_fetch.scoring_regressions import build_correction_provenance
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
    local_audit._require_local_method_bindings(contract, batches, "runtime_source_sha256")
    for mutation in ("wrong_hash", "duplicate", "missing", "extra_binding"):
        c, b = copy.deepcopy(contract), copy.deepcopy(batches)
        if mutation == "wrong_hash": b[1]["runtime_source_sha256"] = "c" * 64
        if mutation == "duplicate": b[1] = b[0]
        if mutation == "missing": b.pop()
        if mutation == "extra_binding": c["runtime_source_sha256_by_provider"]["other"] = "c" * 64
        with pytest.raises(ValueError):
            local_audit._require_local_method_bindings(c, b, "runtime_source_sha256")
    legacy = {"providers": ["first"], "runtime_source_sha256": "a" * 64}
    local_audit._require_local_method_bindings(legacy, batches[:1], "runtime_source_sha256")
    with pytest.raises(ValueError):
        local_audit._require_local_method_bindings({**legacy, "runtime_source_sha256": "b" * 64}, batches[:1], "runtime_source_sha256")


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
    local_audit._require_local_method_bindings(contract, batches, "provider_config_sha256")
    for mutation in ("wrong_hash", "duplicate", "missing", "extra_binding", "invalid_hash"):
        c, b = copy.deepcopy(contract), copy.deepcopy(batches)
        if mutation == "wrong_hash": b[1]["provider_config_sha256"] = "c" * 64
        if mutation == "duplicate": b[1] = b[0]
        if mutation == "missing": b.pop()
        if mutation == "extra_binding": c["provider_config_sha256_by_provider"]["other"] = "c" * 64
        if mutation == "invalid_hash": c["provider_config_sha256_by_provider"]["first"] = "invalid"
        with pytest.raises(ValueError):
            local_audit._require_local_method_bindings(c, b, "provider_config_sha256")
    legacy = {"providers": ["first"], "provider_config_sha256": "a" * 64}
    local_audit._require_local_method_bindings(legacy, batches[:1], "provider_config_sha256")
    with pytest.raises(ValueError):
        local_audit._require_local_method_bindings({**legacy, "provider_config_sha256": "b" * 64}, batches[:1], "provider_config_sha256")


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
