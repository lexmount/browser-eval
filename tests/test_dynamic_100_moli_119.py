import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.scoring import SCORING_SOURCE_SHA256
from harness.web_fetch import local_audit
from harness.web_fetch.report import local_audit_banner, provider_configuration
from harness.web_fetch.stable_tasks import validate_acceptance_contract
from harness.web_fetch.scoring_regressions import load_scoring_regressions


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "evaluation/dynamic-content-100-moli-webfetch/dataset.json"
RECEIPT_PATH = ROOT / "research/dynamic-100-moli-119-local-audit.json"
COMPATIBILITY_PATH = ROOT / "research/dynamic-100-moli-119-risk-scorer-compatibility.json"
REVISION_PATH = ROOT / "research/moli-111-three-cohort-evaluation-revision.json"


def _dataset():
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def test_dynamic_100_dataset_matches_the_frozen_local_audit():
    dataset = _dataset()
    cases = dataset["cases"]
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    compatibility = json.loads(COMPATIBILITY_PATH.read_text(encoding="utf-8"))

    assert len(cases) == receipt["scope"]["cases"] == 100
    assert len({case["id"] for case in cases}) == 100
    assert len({case["url"] for case in cases}) == 100
    revision = json.loads(REVISION_PATH.read_text(encoding="utf-8"))
    assert revision["provider_calls_for_revision"] == 0
    assert revision["dynamic_dataset"]["historical_sha256"] == receipt["bindings"]["dataset_sha256"]
    assert hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest() == revision["dynamic_dataset"]["revised_sha256"]
    historical = DATASET_PATH.read_text(encoding="utf-8")
    controls = load_scoring_regressions()
    for change in revision["dynamic_dataset"]["changes"]:
        case = next(case for case in cases if case["id"] == change["case_id"])
        assert case[change["field"]] == change["value"]
        assert len(change["source_html_sha256"]) == 64
        value = json.dumps(change["value"])
        marker = f'      "url": "{case["url"]}",\n      "{change["field"]}": {value},\n'
        assert historical.count(marker) == 1
        historical = historical.replace(marker, f'      "url": "{case["url"]}",\n', 1)
    assert len(revision["dynamic_dataset"]["changes"]) == 9
    assert hashlib.sha256(historical.encode()).hexdigest() == receipt["bindings"]["dataset_sha256"]
    scorer_revision = revision["shared_scorer"]
    assert scorer_revision["main_baseline_sha256"] == compatibility["bindings"]["scoring_source_sha256"]
    assert scorer_revision["revised_sha256"] == SCORING_SOURCE_SHA256
    assert scorer_revision["scoring_regressions_sha256"] == hashlib.sha256(
        (ROOT / "evaluation/scoring-regressions.json").read_bytes()).hexdigest()
    assert set(scorer_revision["regression_ids"]) <= set(controls)
    modes = {}
    for identifier in scorer_revision["regression_ids"]:
        row = controls[identifier]
        modes.setdefault(row["root_cause"], set()).add(row["failure_mode"])
    assert all(value == {"false-positive", "false-negative"} for value in modes.values())
    assert compatibility["provider_calls"] == 0
    assert compatibility["scope"] == {
        "cases": 100,
        "input_passes": 44,
        "output_passes": 45,
        "task_decision_changes": 1,
    }
    bindings = compatibility["bindings"]
    assert bindings["dataset_sha256"] == receipt["bindings"]["dataset_sha256"]
    assert bindings["input_results_sha256"] == receipt["bindings"]["results_sha256"]
    assert bindings["decision_digest_format"] == (
        "sorted_attempt_id_boolean_pairs_compact_json_v1"
    )
    assert bindings["input_task_decisions_sha256"] != bindings["output_task_decisions_sha256"]
    assert compatibility["task_decision_changes"] == [
        {
            "attempt_id": "moli-webfetch__replacement-pt-kabum-smartphones__r001",
            "before": False,
            "after": True,
            "reason": (
                "The saved Markdown uses &amp; for a browser-visible ampersand in the "
                "required text 'Celular & Smartphone'; decoding one character-reference "
                "layer corrects the false negative."
            ),
        }
    ]
    assert bindings["scoring_source_sha256"] == scorer_revision["main_baseline_sha256"]
    assert bindings["scoring_regressions_sha256"] != scorer_revision["scoring_regressions_sha256"]
    assert bindings["composite_tests_sha256"] == hashlib.sha256(
        (ROOT / "tests/test_composite_tasks.py").read_bytes()
    ).hexdigest()
    assert bindings["stable_tests_sha256"] == hashlib.sha256(
        (ROOT / "tests/test_stable_tasks.py").read_bytes()
    ).hexdigest()
    assert "Moli 1.1.9 local audit complete" in dataset["approval_status"]
    assert dataset["comparison"]["local_audit_receipt"] == RECEIPT_PATH.relative_to(ROOT).as_posix()


def test_historical_scoring_controls_allow_only_new_tail_entries(tmp_path, monkeypatch):
    dataset = _dataset()
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    registry = json.loads((ROOT / "evaluation/scoring-regressions.json").read_text())
    original_file = local_audit._repository_file
    replacement = tmp_path / "scoring-regressions.json"

    def repository_file(relative):
        return replacement if relative == "evaluation/scoring-regressions.json" else original_file(relative)

    monkeypatch.setattr(local_audit, "_repository_file", repository_file)
    changed = json.loads(json.dumps(registry))
    changed["cases"][0]["artifact"] += " altered"
    replacement.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="Scoring control contents changed"):
        local_audit._require_repository_bindings(dataset, receipt)

    changed = json.loads(json.dumps(registry))
    changed["cases"].insert(0, dict(changed["cases"][-1], id="interleaved-control"))
    replacement.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="Scoring control partition"):
        local_audit._require_repository_bindings(dataset, receipt)


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
