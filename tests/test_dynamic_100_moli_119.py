import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.scoring import SCORING_SOURCE_SHA256
from harness.web_fetch import local_audit
from harness.web_fetch.report import local_audit_banner, provider_configuration
from harness.web_fetch.stable_tasks import validate_acceptance_contract
from harness.web_fetch.scoring_regressions import valid_correction_provenance


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "evaluation/dynamic-content-100-moli-webfetch/dataset.json"
RECEIPT_PATH = ROOT / "research/dynamic-100-moli-119-local-audit.json"
CURRENT_SCORING_REVISION_PATH = ROOT / "research/moli-111-visible-html-scoring-revision.json"


def _dataset():
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def test_dynamic_100_dataset_matches_the_frozen_local_audit():
    dataset = _dataset()
    cases = dataset["cases"]
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))

    assert len(cases) == receipt["scope"]["cases"] == 100
    assert len({case["id"] for case in cases}) == 100
    assert len({case["url"] for case in cases}) == 100
    assert hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest() == receipt["bindings"]["dataset_sha256"]
    revision = json.loads(CURRENT_SCORING_REVISION_PATH.read_text(encoding="utf-8"))
    assert receipt["bindings"]["scoring_source_sha256"] == revision["historical_scoring_source_sha256"]
    assert SCORING_SOURCE_SHA256 == revision["revised_scoring_source_sha256"]
    regression_bytes = (ROOT / "evaluation/scoring-regressions.json").read_bytes()
    assert hashlib.sha256(regression_bytes).hexdigest() == revision["scoring_regressions_sha256"]
    assert revision["dataset_sha256"]["dynamic"] == hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()
    assert valid_correction_provenance(revision)
    assert "Moli 1.1.9 local audit complete" in dataset["approval_status"]
    assert dataset["comparison"]["local_audit_receipt"] == RECEIPT_PATH.relative_to(ROOT).as_posix()


def test_dynamic_100_contracts_are_valid_and_do_not_configure_provider_waits():
    for case in _dataset()["cases"]:
        validate_acceptance_contract(case["acceptance_contract"])
        serialized = json.dumps(case, ensure_ascii=False).lower()
        assert "wait_for_selector" not in serialized
        assert "wait_after_load_ms" not in serialized
        assert "extraction_script" not in serialized
        assert "retrieval_hints" not in serialized


def test_native_moli_report_describes_the_frozen_uniform_call():
    record = {
        "provider_type": "moli_webfetch",
        "provider_metadata": {
            "dump": "markdown",
            "route": "dynamic_native_markdown",
            "wait_until": "done",
            "wait": {"strategy": "native_default_completion"},
        },
    }
    text = provider_configuration("moli-webfetch", [record] * 100, {})
    assert "默认 done" in text
    assert "除目标 URL 外没有逐例等待元素" in text


def test_local_audit_banner_has_no_pull_request_number():
    report_source = (ROOT / "harness/web_fetch/report.py").read_text(encoding="utf-8")
    translations = (ROOT / "harness/web_fetch/report_i18n.py").read_text(encoding="utf-8")
    assert "PR #30" not in report_source
    assert "PR #30" not in translations


def test_local_audit_banner_depends_on_dataset_publication_state():
    assert local_audit_banner({}) == ""
    assert local_audit_banner({"comparison": {"publication_state": "release"}}) == ""
    assert "本地审核版" in local_audit_banner(_dataset())


def test_dynamic_100_repository_evidence_and_scoring_revision_are_exact():
    dataset = _dataset()
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    local_audit._require_repository_bindings(dataset, receipt)


def test_historical_audit_accepts_only_appended_scoring_controls(tmp_path, monkeypatch):
    dataset = _dataset()
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    registry = json.loads((ROOT / "evaluation/scoring-regressions.json").read_text())
    original_file = local_audit._repository_file
    replacement = tmp_path / "scoring-regressions.json"

    def repository_file(relative):
        return replacement if relative == "evaluation/scoring-regressions.json" else original_file(relative)

    monkeypatch.setattr(local_audit, "_repository_file", repository_file)
    changed = json.loads(json.dumps(registry))
    changed["cases"][0]["artifact"] += " changed"
    replacement.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="Scoring control contents changed"):
        local_audit._require_repository_bindings(dataset, receipt)

    changed = json.loads(json.dumps(registry))
    inserted = dict(changed["cases"][-1], id="interleaved-unbound-control")
    changed["cases"].insert(0, inserted)
    replacement.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="Scoring control partition"):
        local_audit._require_repository_bindings(dataset, receipt)


def test_source_evidence_inventory_detects_omissions_extras_changes_and_symlinks(
        tmp_path, monkeypatch):
    root = tmp_path / "repo"
    source = root / "evaluation/sources/case-1/reference.txt"
    source.parent.mkdir(parents=True)
    source.write_text("frozen\n", encoding="utf-8")
    monkeypatch.setattr(local_audit, "ROOT", root)
    dataset = {"cases": [{"id": "case-1"}]}

    expected = local_audit._source_evidence_inventory(dataset)
    source.unlink()
    assert local_audit._source_evidence_inventory(dataset) != expected
    source.write_text("changed\n", encoding="utf-8")
    assert local_audit._source_evidence_inventory(dataset) != expected
    source.write_text("frozen\n", encoding="utf-8")
    (source.parent / "extra.txt").write_text("extra\n", encoding="utf-8")
    assert local_audit._source_evidence_inventory(dataset) != expected

    linked = root / "evaluation/sources/case-2"
    linked.symlink_to(source.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        local_audit._source_evidence_inventory({"cases": [{"id": "case-2"}]})
