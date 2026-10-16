from lexbench_webfetch.inputs import definition_path
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from lexbench_webfetch.composite_tasks import validate_composite_acceptance
from lexbench_webfetch.metrics import evaluate_content, normalize_for_match, task_passed
from harness.web_fetch.report_build import require_risk_control_certification
from harness.web_fetch.risk_control import main as run_risk_control
from harness.web_fetch.risk_control import (
    measured_risk_control_dataset,
    require_complete_diagnostic_logs,
)
from lexbench_webfetch.risk_control_dataset import BODY_CONTRACTS, EXPECTED_COHORT_HASH, EXPECTED_RULE_HASHES, LIST_CONTRACTS, MANIFEST, RULE_FILES, evidence_roots, parse_sections
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import evaluate_stable_task, validate_acceptance_contract
from tests.conftest import read_frozen_dataset_bytes
from harness.web_fetch.reference_inputs import benchmark_input_path

ROOT = Path(__file__).resolve().parents[1]


def test_certificate_validation_import_does_not_require_execution_stack(tmp_path):
    code = '''
import builtins
original = builtins.__import__
def restricted(name, *args, **kwargs):
    if name in {"harness.web_fetch.providers", "harness.web_fetch.runner", "harness.web_fetch.report_build"}:
        raise RuntimeError("Execution stack is unavailable")
    return original(name, *args, **kwargs)
builtins.__import__ = restricted
from harness.web_fetch.risk_control import validate_risk_control_certification
assert callable(validate_risk_control_certification)
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_risk_control_history_contains_no_executable_input_versions():
    history = definition_path("evaluation/risk-control/history")
    files = sorted(path.relative_to(history).as_posix() for path in history.rglob("*") if path.is_file())
    assert files == ["v5/dataset-catalog.json"]
    assert not list(history.rglob("source-inputs.json"))
    assert not list(history.rglob("run-profile.json"))


DATASET = ROOT / "evaluation/risk-control/current/dataset.json"
COHORT = definition_path('evaluation/risk-control/qualification/cohort/cohort-manifest.json')
COHORT_RECEIPT = definition_path('evaluation/risk-control/qualification/cohort/cohort-receipt.json')
RUN_PROFILE = ROOT / "evaluation/risk-control/run-profile.json"
LANGUAGE_REVISION = ROOT / "research/risk-control-language-rule-revision.json"
ZILLOW_REVISION = ROOT / "research/risk-control-zillow-sample-revision.json"
XTOOLS_REVISION = ROOT / "research/risk-control-xtools-field-scope-revision.json"
CONTENT_OWNER_REVISION = ROOT / "research/risk-control-explicit-content-owner-revision.json"


@pytest.fixture(scope="module")
def dataset():
    return json.loads(read_frozen_dataset_bytes(DATASET).decode("utf-8"))


def test_dataset_covers_the_exact_audited_cohort(dataset):
    cohort = json.loads(COHORT.read_text())
    assert dataset["case_count"] == len(dataset["cases"]) == 100
    assert [case["id"] for case in dataset["cases"]] == [case["id"] for case in cohort["cases"]]
    assert dataset["site_count"] == 74
    assert dataset["stable_literal_count"] == 1586
    assert "page_context_literal_count" not in dataset
    assert dataset["observed_literal_count"] == 1586
    assert dataset["body_case_count"] == 79
    assert dataset["quantity_case_count"] == 20
    assert dataset["independent_quantity_group_count"] == 26
    assert dataset["list_or_field_case_count"] == 22
    assert dataset["composite_group_count"] == 29


def test_training_page_accepts_the_visible_english_ui_without_dropping_chinese_content(dataset):
    case = next(case for case in dataset["cases"] if case["id"] == "risk-diverse-153")
    # The live page retains its Chinese training title but renders its UI in
    # English. Both surfaces are part of the requested page, not a translated
    # substitute or a relaxed content requirement.
    visible = (
        "教材《C++版信息学奥赛一本通编程启蒙》题单\n"
        "Login to join training plan\n"
        "Problem Tried AC Difficulty\n"
        "Home Problem Set Training Contest Record Ranking Login Sign Up Language\n"
        "P1 [例2.1]Hello World 454 122 7\n"
    )
    result = evaluate_stable_task(visible, case["acceptance_contract"], case["url"])
    assert result["stable_task_pass"] is True
    assert result["stable_anchor_hits"] == result["stable_anchor_total"] == 15
    without_training_title = evaluate_stable_task(
        visible.replace("教材《C++版信息学奥赛一本通编程启蒙》题单", ""),
        case["acceptance_contract"], case["url"],
    )
    assert without_training_title["stable_task_pass"] is False


def test_training_page_language_alternatives_rebuild_from_rule_review(dataset):
    section = next(section for section in parse_sections() if section["id"] == "risk-diverse-153")
    case = next(case for case in dataset["cases"] if case["id"] == section["id"])
    assert section["stable"] == [
        anchor["alternatives"] for anchor in case["acceptance_contract"]["stable_text_anchors"]
    ]
    assert len(section["stable"]) == 15


def test_language_rule_revision_is_the_predecessor_of_the_current_sample_revision():
    revision = json.loads(LANGUAGE_REVISION.read_text())
    successor = json.loads(ZILLOW_REVISION.read_text())
    assert (
        successor["correction"]["predecessor_dataset_sha256"]
        == revision["correction"]["new_dataset_sha256"]
    )
    assert (
        successor["correction"]["predecessor_run_profile_sha256"]
        == revision["correction"]["new_run_profile_sha256"]
    )
    assert revision["independent_browser_observation"]["anchor_total"] == 6
    assert revision["correction"]["provider_calls"] == 0


def test_zillow_sample_revision_binds_current_frozen_inputs():
    revision = json.loads(ZILLOW_REVISION.read_text())
    successor = json.loads((ROOT/'research/risk-control-training-list-revision.json').read_text())
    profile = json.loads(RUN_PROFILE.read_text())
    for key in ('dataset', 'run_profile'):
        assert revision['correction']['new_'+key+'_sha256'] == successor['correction']['predecessor_'+key+'_sha256']
    full_page = json.loads((ROOT/'research/risk-control-full-page-rule-revision.json').read_text())
    assert successor['correction']['new_dataset_sha256'] == full_page['correction']['predecessor_dataset_sha256']
    assert successor['correction']['new_run_profile_sha256'] == full_page['correction']['predecessor_run_profile_sha256']
    locale=json.loads((ROOT/'research/risk-control-navigation-language-revision.json').read_text())
    for key in ('dataset','run_profile'):
        assert full_page['correction']['new_'+key+'_sha256'] == locale['correction']['predecessor_'+key+'_sha256']
    responsive=json.loads((ROOT/'research/risk-control-responsive-navigation-revision.json').read_text())
    xtools=json.loads(XTOOLS_REVISION.read_text())
    content_owner=json.loads(CONTENT_OWNER_REVISION.read_text())
    assert responsive['predecessor_revision'] == 'research/risk-control-navigation-language-revision.json'
    assert locale['correction']['new_dataset_sha256'] == responsive['correction']['predecessor_dataset_sha256']
    assert locale['correction']['new_run_profile_sha256'] == responsive['correction']['predecessor_run_profile_sha256']
    assert xtools['predecessor_revision'] == 'research/risk-control-responsive-navigation-revision.json'
    assert responsive['correction']['new_dataset_sha256'] == xtools['correction']['predecessor_dataset_sha256']
    assert responsive['correction']['new_run_profile_sha256'] == xtools['correction']['predecessor_run_profile_sha256']
    assert xtools['correction']['predecessor_dataset_sha256'] == responsive['correction']['new_dataset_sha256']
    assert xtools['correction']['predecessor_run_profile_sha256'] == responsive['correction']['new_run_profile_sha256']
    assert content_owner['predecessor_revision'] == str(
        XTOOLS_REVISION.relative_to(ROOT)
    )
    assert content_owner['correction']['predecessor_dataset_sha256'] == xtools['correction']['new_dataset_sha256']
    assert content_owner['correction']['predecessor_run_profile_sha256'] == xtools['correction']['new_run_profile_sha256']
    assert content_owner['correction']['new_dataset_sha256'] == hashlib.sha256(
        read_frozen_dataset_bytes(DATASET)
    ).hexdigest()
    assert content_owner['correction']['new_run_profile_sha256'] == hashlib.sha256(
        RUN_PROFILE.read_bytes()
    ).hexdigest()
    assert profile["dataset"]["sha256"] == content_owner["correction"]["new_dataset_sha256"]
    assert profile["dataset"]["path"] == "evaluation/risk-control/current/dataset.json"
    assert revision["correction"]["provider_calls"] == 1
    assert revision["correction"]["rerun_case_ids"] == ["risk-diverse-134"]


def test_every_secondary_risk_anchor_is_bound_to_independent_revision(dataset):
    revision = json.loads(LANGUAGE_REVISION.read_text())
    case_id = revision["case_id"]
    locale=json.loads((ROOT/'research/risk-control-navigation-language-revision.json').read_text())
    declared = {**revision["correction"]["equivalent_ui_alternatives_added"],
                **locale["correction"]["equivalent_ui_alternatives_added"]}
    observed = {}
    for case in dataset["cases"]:
        for anchor in case["acceptance_contract"]["stable_text_anchors"]:
            for alternative in anchor["alternatives"][1:]:
                observed[(case["id"], anchor["id"])] = alternative
    assert observed == {(case_id, anchor_id): text for anchor_id, text in declared.items()}
    assert len(revision["independent_browser_observation"]["visible_text_sha256"]) == 64


def test_external_evidence_roots_honor_data_home(monkeypatch, tmp_path):
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path))
    assert all(path.is_relative_to(tmp_path) for path in evidence_roots().values())


def test_builder_hash_guards_match_the_current_audited_sources():
    assert hashlib.sha256(COHORT.read_bytes()).hexdigest() == EXPECTED_COHORT_HASH
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in RULE_FILES
    } == EXPECTED_RULE_HASHES


def test_compiler_reads_explicit_definitions_without_checkout_defaults(tmp_path, monkeypatch):
    from lexbench_webfetch import risk_control_dataset as compiler

    rule = tmp_path / "rules.md"
    rule.write_text(RULE_FILES[0].read_text())
    expected = compiler.parse_sections((RULE_FILES[0],))
    monkeypatch.setattr(compiler, "RULE_FILES", (tmp_path / "absent.md",))
    assert compiler.parse_sections((rule,)) == expected
    manifest = tmp_path / "input-manifest.json"
    manifest.write_text('{"evidence_roots":{"reference":"independent-inputs"}}')
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path / "data"))
    assert compiler.evidence_roots(manifest) == {
        "reference": tmp_path / "data/independent-inputs"
    }
    cohort = tmp_path / "cohort.json"
    cohort.write_bytes(COHORT.read_bytes())
    assert compiler.load_cohort(cohort) == compiler.load_cohort(COHORT)


def test_dataset_and_qualification_manifest_bind_current_source_bytes(dataset):
    expected_dataset_bindings = {
        "cohort_manifest_sha256": hashlib.sha256(COHORT.read_bytes()).hexdigest(),
        "first_30_rules_sha256": hashlib.sha256(RULE_FILES[0].read_bytes()).hexdigest(),
        "remaining_70_rules_sha256": hashlib.sha256(RULE_FILES[1].read_bytes()).hexdigest(),
        "list_contracts_sha256": hashlib.sha256(LIST_CONTRACTS.read_bytes()).hexdigest(),
        "body_contracts_sha256": hashlib.sha256(BODY_CONTRACTS.read_bytes()).hexdigest(),
    }
    assert dataset["source_bindings"] == expected_dataset_bindings

    qualification = json.loads(MANIFEST.read_text())
    qualification_root = MANIFEST.parent.parent
    for binding in qualification["files"]:
        path = qualification_root / binding["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["sha256"]


def test_zillow_case_binds_location_and_only_requires_visible_card_fields(dataset):
    case = next(case for case in dataset["cases"] if case["id"] == "risk-diverse-134")
    assert case["url"] == "https://www.zillow.com/seattle-wa/"
    anchors = [
        item["alternatives"][0]
        for item in case["acceptance_contract"]["stable_text_anchors"]
    ]
    assert "Seattle WA Real Estate & Homes For Sale" in anchors
    fields = case["composite_acceptance"]["groups"][0]["required_fields"]
    assert [field["id"] for field in fields] == [
        "price", "bedrooms", "bathrooms", "area"
    ]

    cards = []
    for index in range(8):
        zpid = 10000000 + index
        cards.append(
            f"[10{index} Example Avenue, Seattle, WA 9810{index}]"
            f"(https://www.zillow.com/homedetails/10{index}-Example-Ave-Seattle-WA-9810{index}/"
            f"{zpid}_zpid/)\n$900,000\n3 bds\n2 ba\n1,800 sqft"
        )
    positive = "\n".join(anchors) + "\n\n" + "\n\n".join(cards)
    accepted = evaluate_content(
        positive,
        [],
        min_content_chars=1,
        acceptance_contract=case["acceptance_contract"],
        composite_acceptance=case["composite_acceptance"],
        base_url=case["url"],
    )
    assert accepted["content_pass"] is True

    wrong_location = positive.replace(
        "Seattle WA Real Estate & Homes For Sale",
        "Liberal MO Real Estate & Homes For Sale",
    )
    assert evaluate_content(
        wrong_location,
        [],
        min_content_chars=1,
        acceptance_contract=case["acceptance_contract"],
        composite_acceptance=case["composite_acceptance"],
        base_url=case["url"],
    )["content_pass"] is False

    missing_visible_area = positive.replace("1,800 sqft", "", 1)
    assert evaluate_content(
        missing_visible_area,
        [],
        min_content_chars=1,
        acceptance_contract=case["acceptance_contract"],
        composite_acceptance=case["composite_acceptance"],
        base_url=case["url"],
    )["content_pass"] is False


@pytest.mark.parametrize('fields,expected', [
    ('3 beds · 2 baths · 1,200 sqft', True),
    ('3 beds · 2.5 baths · 1,200 sqft', True),
    ('3 beds 2 baths 1,200 sqft', True),
    ('5 bds4 ba3,380 sqftActive', True),
    ('3 beds\n2 baths\n1,200 sqft', True),
    ('**3** beds · __2__ baths · **1,200** sqft', True),
    ('1200 sqft beds\n1200 sqft baths\n1200 sqft', False),
    ('3 beds · 1,200 sqft', False),
])
def test_zillow_full_score_requires_distinct_numeric_card_fields(dataset, fields, expected):
    from lexbench_webfetch.scoring import score_attempt
    case=next(c for c in dataset['cases'] if c['id']=='risk-diverse-134')
    anchors='\n'.join(a['alternatives'][0] for a in case['acceptance_contract']['stable_text_anchors'])
    cards=[f'[10{i} Example Avenue](https://www.zillow.com/homedetails/Example/{10000000+i}_zpid/)\n$900,000\n{fields}' for i in range(8)]
    result=score_attempt(case,(anchors+'\n\n'+'\n\n'.join(cards)).encode(),ok=True,final_url=case['url'])
    assert result['content_pass'] is expected
    assert result['composite_group_results'][0]['count']==(8 if expected else 0)


def test_compact_cohort_receipt_binds_final_manifest_and_limits_the_claim():
    import hashlib

    receipt = json.loads(COHORT_RECEIPT.read_text())
    assert receipt["cohort_manifest_sha256"] == hashlib.sha256(COHORT.read_bytes()).hexdigest()
    assert receipt["scope"] == {
        "url_cases": 100,
        "independent_sites": 74,
        "security_or_access_control_cases": 96,
        "login_only_cases": 4,
    }
    assert receipt["evidence_policy"]["raw_qualification_evidence_in_git"] is False
    assert any("does not classify an entire site" in value for value in receipt["limits"])


def test_cohort_keeps_only_scoring_inputs_and_compact_qualification_metadata():
    cohort = json.loads(COHORT.read_text())
    required = {
        "id",
        "url",
        "site_key",
        "target_scope",
        "target_language",
        "qualification_class",
        "reference_proof",
    }
    for case in cohort["cases"]:
        assert required <= set(case) <= required | {"observed_final_url"}
        assert set(case["reference_proof"]) == {"root", "path", "sha256"}
    assert cohort["scope"]["case_count"] == len(cohort["cases"]) == 100
    assert (
        cohort["scope"]["site_count"] == len({case["site_key"] for case in cohort["cases"]}) == 74
    )
    assert (
        cohort["scope"]["security_or_access_control_cases"]
        == sum(
            case["qualification_class"] == "security_or_access_control" for case in cohort["cases"]
        )
        == 96
    )
    assert (
        cohort["scope"]["login_only_cases"]
        == sum(case["qualification_class"] == "login_only" for case in cohort["cases"])
        == 4
    )
    serialized = json.dumps(cohort, ensure_ascii=False)
    for forbidden in ["browser.har", "browser.png", "browser-nodes.json", '"restriction"']:
        assert forbidden not in serialized


def test_run_profile_binds_every_rule_and_qualification_authority():
    import hashlib

    profile = json.loads(RUN_PROFILE.read_text())
    expected = {
        "qualification_manifest",
        "cohort_receipt",
        "cohort_manifest",
        "first_30_rules",
        "remaining_70_rules",
        "list_contracts",
        "body_contracts",
        "rule_independent_audit",
        "rule_completeness_audit",
    }
    assert set(profile["source_bindings"]) == expected
    for binding in profile["source_bindings"].values():
        path = benchmark_input_path(ROOT / binding["path"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["sha256"]


def test_direct_formal_execution_is_rejected_before_provider_validation(tmp_path):
    with pytest.raises(SystemExit):
        run_risk_control(["--execute", "--output", str(tmp_path / "run")])


def test_frozen_execution_inputs_do_not_require_historical_source_files(tmp_path):
    from harness.web_fetch.risk_control import frozen_risk_control_input_snapshot
    profile = json.loads(RUN_PROFILE.read_text())
    profile_path = tmp_path / "evaluation/risk-control/run-profile.json"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_bytes(RUN_PROFILE.read_bytes())
    bindings = [profile["dataset"], *profile["source_bindings"].values(),
                {"path": profile["provider"]["config_path"]}]
    for binding in bindings:
        target = tmp_path / binding["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(read_frozen_dataset_bytes(ROOT / binding["path"]) if binding == profile['dataset'] else benchmark_input_path(ROOT / binding["path"]).read_bytes())
    # There is no qualification data home or browser.txt tree in this fixture.
    snapshot = frozen_risk_control_input_snapshot(profile_path)
    assert snapshot["case_count"] == 100
    (tmp_path / profile["dataset"]["path"]).write_text('{"cases": []}')
    with pytest.raises(ValueError, match="Changed frozen input"):
        frozen_risk_control_input_snapshot(profile_path)


def certified_report_fixture(tmp_path):
    profile_bytes = RUN_PROFILE.read_bytes()
    profile = json.loads(profile_bytes)
    profile_sha = hashlib.sha256(profile_bytes).hexdigest()
    frozen_dataset = json.loads(read_frozen_dataset_bytes(DATASET).decode("utf-8"))
    dataset = measured_risk_control_dataset(profile, frozen_dataset, profile_sha)
    provider = profile["provider"]["name"]
    identity = {
        "reported_version": profile["provider"]["reported_version"],
        "binary_sha256": profile["provider"]["binary_sha256"],
    }
    records = []
    for case in frozen_dataset["cases"]:
        records.append(
            {
                "attempt_id": f"{provider}__{case['id']}__r001",
                "provider": provider,
                "case_id": case["id"],
                "run": 1,
                "provider_metadata": {
                    **identity,
                    "diagnostic_log_path": (
                        f"private_artifacts/{provider}/{case['id']}/run-001.stderr.log"
                    ),
                    "diagnostic_log_sha256": "a" * 64,
                    "diagnostic_log_bytes": 12,
                    "diagnostic_log_truncated": False,
                },
            }
        )
    attempt_ids = [record["attempt_id"] for record in records]
    manifest = {
        "started_at": "2026-09-29T00:00:00+00:00",
        "attempt_count": 100,
        "attempt_ids": attempt_ids,
        "unexecuted": [],
        "dataset_sha256": profile["dataset"]["sha256"],
        "provider_config_sha256": profile["provider"]["config_sha256"],
        "providers": [
            {
                "name": provider,
                "type": "moli_webfetch",
                "min_interval_seconds": 0,
                "implementation_identity": identity,
            }
        ],
        "parameters": {
            "runs": profile["execution"]["runs_per_case"],
            "timeout": profile["execution"]["timeout_seconds"],
            "delay": profile["execution"]["delay_seconds"],
            "provider_concurrency": profile["execution"]["concurrency"],
            "max_failure_retries": profile["execution"]["max_failure_retries"],
        },
    }
    execution = {
        "schema_version": 1,
        "aggregate": {
            "providers": [provider],
            "case_ids": [case["id"] for case in frozen_dataset["cases"]],
            "started_at": manifest["started_at"],
            "completed_at": "2026-09-29T00:10:00+00:00",
            "batch_count": 1,
        },
        "batches": [manifest],
    }
    proof_files = [
        {
            "case_id": case["id"],
            **case["source_benchmark"]["risk_control"]["reference_proof"],
            "bytes": 1,
        }
        for case in frozen_dataset["cases"]
    ]
    proof_inventory = json.dumps(
        proof_files, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    proof_snapshot = {
        "case_count": 100,
        "inventory_sha256": hashlib.sha256(proof_inventory.encode()).hexdigest(),
        "files": proof_files,
    }
    proof_verification = {
        "schema_version": 1,
        "kind": "risk_control_reference_proof_verification",
        "before": proof_snapshot,
        "after": proof_snapshot,
        "unchanged": True,
    }
    (tmp_path / "reference-proof-verification.json").write_text(json.dumps(proof_verification))
    proof_sha = hashlib.sha256(
        (tmp_path / "reference-proof-verification.json").read_bytes()
    ).hexdigest()
    manifest["reference_proof_verification_sha256"] = proof_sha
    execution["batches"] = [manifest]
    (tmp_path / "dataset.json").write_text(json.dumps(dataset))
    (tmp_path / "results.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records)
    )
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "execution.json").write_text(json.dumps(execution))
    receipt = {
        "schema_version": 1,
        "kind": "certified_risk_control_100_moli_v1",
        "profile_sha256": profile_sha,
        "dataset_sha256": hashlib.sha256((tmp_path / "dataset.json").read_bytes()).hexdigest(),
        "results_sha256": hashlib.sha256((tmp_path / "results.jsonl").read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256((tmp_path / "manifest.json").read_bytes()).hexdigest(),
        "execution_sha256": hashlib.sha256((tmp_path / "execution.json").read_bytes()).hexdigest(),
        "reference_proof_verification_sha256": proof_sha,
        "provider_identity_before": identity,
        "provider_identity_after": identity,
        "logical_results": 100,
        "physical_calls": 100,
        "complete_diagnostic_logs": True,
    }
    (tmp_path / "certification.json").write_text(json.dumps(receipt))
    return dataset, records, receipt, manifest, execution


def rewrite_certified_report_fixture(tmp_path, records, receipt, manifest, execution):
    manifest["attempt_count"] = len(records)
    manifest["attempt_ids"] = [record["attempt_id"] for record in records]
    execution["aggregate"]["case_ids"] = [record["case_id"] for record in records]
    execution["batches"] = [manifest]
    (tmp_path / "results.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records)
    )
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "execution.json").write_text(json.dumps(execution))
    receipt.update(
        {
            "results_sha256": hashlib.sha256((tmp_path / "results.jsonl").read_bytes()).hexdigest(),
            "manifest_sha256": hashlib.sha256(
                (tmp_path / "manifest.json").read_bytes()
            ).hexdigest(),
            "execution_sha256": hashlib.sha256(
                (tmp_path / "execution.json").read_bytes()
            ).hexdigest(),
            "logical_results": len(records),
            "physical_calls": len(records),
        }
    )
    (tmp_path / "certification.json").write_text(json.dumps(receipt))


def test_report_requires_complete_profile_bound_risk_control_certification(tmp_path):
    dataset, records, receipt, _, _ = certified_report_fixture(tmp_path)
    require_risk_control_certification(tmp_path, dataset, records)
    receipt["results_sha256"] = "0" * 64
    (tmp_path / "certification.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="mismatched"):
        require_risk_control_certification(tmp_path, dataset, records)



def test_report_rejects_hash_bound_but_incomplete_reference_proof_receipt(tmp_path):
    dataset, records, receipt, manifest, execution = certified_report_fixture(tmp_path)
    proof_path = tmp_path / "reference-proof-verification.json"
    proof = json.loads(proof_path.read_text())
    proof["unchanged"] = False
    proof_path.write_text(json.dumps(proof))
    proof_sha = hashlib.sha256(proof_path.read_bytes()).hexdigest()
    manifest["reference_proof_verification_sha256"] = proof_sha
    receipt["reference_proof_verification_sha256"] = proof_sha
    rewrite_certified_report_fixture(tmp_path, records, receipt, manifest, execution)

    with pytest.raises(ValueError, match="reference proof verification"):
        require_risk_control_certification(tmp_path, dataset, records)


@pytest.mark.parametrize("defect", ["missing", "duplicate", "provider", "identity"])
def test_report_rejects_self_consistent_but_incomplete_or_wrong_matrix(tmp_path, defect):
    dataset, records, receipt, manifest, execution = certified_report_fixture(tmp_path)
    if defect == "missing":
        records.pop()
    elif defect == "duplicate":
        records[-1] = {**records[0]}
    elif defect == "provider":
        records[-1] = {**records[-1], "provider": "wrong-provider"}
    else:
        wrong = {"reported_version": "moli 9.9.9", "binary_sha256": "b" * 64}
        receipt["provider_identity_before"] = wrong
        receipt["provider_identity_after"] = wrong
        manifest["providers"][0]["implementation_identity"] = wrong
        for record in records:
            record["provider_metadata"].update(wrong)
    rewrite_certified_report_fixture(tmp_path, records, receipt, manifest, execution)
    with pytest.raises(ValueError):
        require_risk_control_certification(tmp_path, dataset, records)


def test_every_contract_validates_and_body_has_positive_and_adjacent_negative(dataset):
    body_cases = 0
    for case in dataset["cases"]:
        validate_acceptance_contract(case["acceptance_contract"])
        if composite := case.get("composite_acceptance"):
            validate_composite_acceptance(composite)
        anchors = [
            row["alternatives"][0] for row in case["acceptance_contract"]["stable_text_anchors"]
        ]
        body = case.get("body_requirements")
        if not body:
            continue
        body_cases += 1
        regions = [row["text"] for row in body["regions"]]
        positive = "\n".join([*anchors, *regions])
        result = evaluate_content(
            positive,
            [],
            min_content_chars=1,
            acceptance_contract=case["acceptance_contract"],
            body_requirements=body,
            base_url=case["url"],
        )
        assert result["content_pass"], case["id"]
        # If a body boundary deliberately repeats a required stable label,
        # omitting that body region must omit the duplicated label too.  Keeping
        # the label would silently reinsert the supposedly missing region.
        omitted = normalize_for_match(regions[-1])
        negative_anchors = [
            anchor for anchor in anchors if normalize_for_match(anchor) not in omitted
        ]
        negative = "\n".join([*negative_anchors, *regions[:-1]])
        failed = evaluate_content(
            negative,
            [],
            min_content_chars=1,
            acceptance_contract=case["acceptance_contract"],
            body_requirements=body,
            base_url=case["url"],
        )
        assert not failed["body_coverage_pass"], case["id"]
        assert not failed["content_pass"], case["id"]
    assert body_cases == 79


def test_corrected_rule_evidence_conflicts_are_encoded_exactly(dataset):
    cases = {case["id"]: case for case in dataset["cases"]}

    spcodex = cases["risk-diverse-182"]
    spcodex_anchors = {
        row["alternatives"][0] for row in spcodex["acceptance_contract"]["stable_text_anchors"]
    }
    assert "Releases" not in spcodex_anchors
    assert "Live shows" not in spcodex_anchors
    assert len(spcodex["body_requirements"]["regions"]) == 2
    assert "More fan sites" in spcodex["body_requirements"]["regions"][1]["text"]
    assert "Nothing Ever Changes" not in spcodex["body_requirements"]["regions"][1]["text"]

    ibiblio = cases["risk-diverse-192"]
    assert "body_requirements" not in ibiblio
    assert "composite_acceptance" not in ibiblio
    assert ibiblio["acceptance_contract"]["stable_text_role"] == "diagnostic"
    patterns = ibiblio["acceptance_contract"]["page_patterns"]
    assert len(patterns) == 1
    assert "collections? of collections" in patterns[0]

    anton_paar = cases["risk-diverse-225"]
    assert len(anton_paar["body_requirements"]["regions"]) == 1
    anton_body = anton_paar["body_requirements"]["regions"][0]["text"]
    assert "living, growing scientific platform" in anton_body
    assert "Articles" not in anton_body

    ticketmaster = cases["risk-diverse-074"]["composite_acceptance"]["groups"][0]
    assert re.fullmatch(ticketmaster["entry_path"], "/event/Z7r9jZ1A7O9_A")

    global_european = cases["risk-diverse-248"]["composite_acceptance"]["groups"]
    picks = next(group for group in global_european if group["id"] == "agp-picks")
    assert "required_fields" not in picks
    perspectify = next(group for group in global_european if group["id"] == "perspectify")
    assert perspectify["record_line_pattern"] == r"^##\s+"
    assert [field["id"] for field in perspectify["required_fields"]] == [
        "time",
        "label",
        "source",
    ]

    zhihu = cases["risk-diverse-002"]
    zhihu_anchors = [
        row["alternatives"][0] for row in zhihu["acceptance_contract"]["stable_text_anchors"]
    ]
    assert len(zhihu_anchors) == 15
    assert len(zhihu["body_requirements"]["regions"]) == 5
    assert (
        "成为推动本世纪人类社会发展的主要技术" in zhihu["body_requirements"]["regions"][-1]["text"]
    )
    assert "copilot工具" not in json.dumps(zhihu["body_requirements"], ensure_ascii=False)
    zhihu_partial = "\n".join(
        [
            *zhihu_anchors,
            *(row["text"] for row in zhihu["body_requirements"]["regions"][:3]),
        ]
    )
    zhihu_partial_result = evaluate_content(
        zhihu_partial,
        [],
        min_content_chars=1,
        acceptance_contract=zhihu["acceptance_contract"],
        body_requirements=zhihu["body_requirements"],
        base_url=zhihu["url"],
    )
    assert not zhihu_partial_result["body_coverage_pass"]
    assert not zhihu_partial_result["content_pass"]

    archwiki = cases["risk-diverse-038"]
    archwiki_anchors = [
        row["alternatives"][0] for row in archwiki["acceptance_contract"]["stable_text_anchors"]
    ]
    for required in [
        "Home",
        "Packages",
        "Forums",
        "Wiki",
        "GitLab",
        "Security",
        "AUR",
        "Download",
        "Appearance",
        "Read",
        "View source",
        "View history",
    ]:
        assert required in archwiki_anchors
    assert (
        "Welcome to the ArchWiki: your source for Arch Linux documentation on the web."
        not in archwiki_anchors
    )
    assert "Welcome to the" in archwiki_anchors
    assert "your source for Arch Linux documentation on the web." in archwiki_anchors
    arch_body = "\n".join(row["text"] for row in archwiki["body_requirements"]["regions"])
    body_only = evaluate_content(
        arch_body,
        [],
        min_content_chars=1,
        acceptance_contract=archwiki["acceptance_contract"],
        body_requirements=archwiki["body_requirements"],
        base_url=archwiki["url"],
    )
    assert body_only["body_coverage_pass"]
    assert not body_only["stable_task_pass"]
    assert not body_only["content_pass"]
    chrome_only = evaluate_content(
        "\n".join(archwiki_anchors),
        [],
        min_content_chars=1,
        acceptance_contract=archwiki["acceptance_contract"],
        body_requirements=archwiki["body_requirements"],
        base_url=archwiki["url"],
    )
    assert chrome_only["stable_task_pass"]
    assert not chrome_only["body_coverage_pass"]
    assert not chrome_only["content_pass"]

    enlightenment = [
        row["alternatives"][0]
        for row in cases["risk-diverse-032"]["acceptance_contract"]["stable_text_anchors"]
    ]
    assert "enlightenment / enlightenment" not in enlightenment
    assert "enlightenment" in enlightenment
    assert "Please report bugs/issues at git.enlightenment.org" not in enlightenment
    assert "Please report bugs/issues at" in enlightenment
    assert "git.enlightenment.org" in enlightenment

    devuan = [
        row["alternatives"][0]
        for row in cases["risk-diverse-042"]["acceptance_contract"]["stable_text_anchors"]
    ]
    assert "devuan / installer-iso" not in devuan
    assert "The pool Directory" not in devuan
    assert "devuan" in devuan and "installer-iso" in devuan

    arch_packages = [
        row["alternatives"][0]
        for row in cases["risk-diverse-049"]["acceptance_contract"]["stable_text_anchors"]
    ]
    assert (
        "Arch / Repo / Name / Version / Bugs / Build Date / Last Updated / Delete"
        not in arch_packages
    )
    assert all(
        value in arch_packages
        for value in [
            "Arch",
            "Repo",
            "Name",
            "Version",
            "Bugs",
            "Build Date",
            "Last Updated",
            "Delete",
        ]
    )

    slackware = [
        row["alternatives"][0]
        for row in cases["risk-diverse-050"]["acceptance_contract"]["stable_text_anchors"]
    ]
    assert "index : current" not in slackware
    assert "index" in slackware and "current" in slackware

    superlove = [
        row["alternatives"][0]
        for row in cases["risk-diverse-059"]["acceptance_contract"]["stable_text_anchors"]
    ]
    assert "superlove is a project by girl on the moon." not in superlove
    assert "superlove is a project by" in superlove and "girl on the moon" in superlove

    azur_lane = [
        row["alternatives"][0]
        for row in cases["risk-diverse-101"]["acceptance_contract"]["stable_text_anchors"]
    ]
    assert "Commander, welcome to the Azur Lane English Wiki!" not in azur_lane
    assert "Commander, welcome to the" in azur_lane
    assert "Azur Lane English Wiki" in azur_lane

    replacement = cases["risk-diverse-271"]
    assert "composite_acceptance" not in replacement
    replacement_anchors = [
        row["alternatives"][0] for row in replacement["acceptance_contract"]["stable_text_anchors"]
    ]
    assert replacement_anchors == [
        "Tom Ryder—Code",
        "Repositories",
        "Why not GitHub?",
        "Contributing",
        "Forking/mirroring",
        "index",
        "about",
    ]
    assert len(replacement["body_requirements"]["regions"]) == 4
    assert "generated by" not in json.dumps(replacement["body_requirements"], ensure_ascii=False)


def test_stable_text_groups_are_unique_after_display_normalization(dataset):
    for case in dataset["cases"]:
        anchors = [
            normalize_for_match(row["alternatives"][0])
            for row in case["acceptance_contract"]["stable_text_anchors"]
        ]
        assert len(anchors) == len(set(anchors)), case["id"]


def test_ibiblio_scope_requires_archive_identity_and_collection_introduction(dataset):
    case = next(case for case in dataset["cases"] if case["id"] == "risk-diverse-192")
    complete = (
        "ibiblio.org is a public digital library and collection of collections. "
        "It preserves freely available information about software, music, literature, art, "
        "history, science, politics, and cultural studies in collaboration with the "
        "University of North Carolina at Chapel Hill."
    )
    news_and_footer = "ibiblio News One Two Three Logos Hosted by UNC Chapel Hill"
    wrong_identity = complete.replace("ibiblio.org", "Another Archive")
    identity_only = "ibiblio.org is hosted with UNC Chapel Hill. Browse Logos and news."
    assert task_passed(True, score_attempt(case, complete.encode(), ok=True))
    assert not task_passed(True, score_attempt(case, news_and_footer.encode(), ok=True))
    assert not task_passed(True, score_attempt(case, wrong_identity.encode(), ok=True))
    assert not task_passed(True, score_attempt(case, identity_only.encode(), ok=True))


def test_formal_run_requires_one_complete_hash_bound_diagnostic_log_per_case():
    complete = {
        "case_id": "complete",
        "provider_metadata": {
            "diagnostic_log_path": "private_artifacts/moli/complete/run-001.stderr.log",
            "diagnostic_log_sha256": "a" * 64,
            "diagnostic_log_bytes": 12,
            "diagnostic_log_truncated": False,
        },
    }
    require_complete_diagnostic_logs([complete])
    for row in [
        {"case_id": "missing", "provider_metadata": {}},
        {
            "case_id": "truncated",
            "provider_metadata": {
                **complete["provider_metadata"],
                "diagnostic_log_truncated": True,
            },
        },
    ]:
        with pytest.raises(ValueError, match=row["case_id"]):
            require_complete_diagnostic_logs([row])


def test_zhihu_full_answer_rejects_first_three_regions_only(dataset):
    case = next(case for case in dataset["cases"] if case["id"] == "risk-diverse-002")
    anchors = [row["alternatives"][0] for row in case["acceptance_contract"]["stable_text_anchors"]]
    regions = [row["text"] for row in case["body_requirements"]["regions"]]
    partial = "\n".join([*anchors, *regions[:3]])
    result = evaluate_content(
        partial,
        [],
        min_content_chars=1,
        acceptance_contract=case["acceptance_contract"],
        body_requirements=case["body_requirements"],
        base_url=case["url"],
    )
    assert not result["body_coverage_pass"]
    assert not result["content_pass"]


def test_archwiki_requires_both_page_chrome_and_full_body(dataset):
    case = next(case for case in dataset["cases"] if case["id"] == "risk-diverse-038")
    anchors = [row["alternatives"][0] for row in case["acceptance_contract"]["stable_text_anchors"]]
    body = "\n".join(row["text"] for row in case["body_requirements"]["regions"])
    body_only = evaluate_content(
        body,
        [],
        min_content_chars=1,
        acceptance_contract=case["acceptance_contract"],
        body_requirements=case["body_requirements"],
        base_url=case["url"],
    )
    assert body_only["body_coverage_pass"]
    assert not body_only["stable_task_pass"]
    assert not body_only["content_pass"]
    chrome_only = evaluate_content(
        "\n".join(anchors),
        [],
        min_content_chars=1,
        acceptance_contract=case["acceptance_contract"],
        body_requirements=case["body_requirements"],
        base_url=case["url"],
    )
    assert chrome_only["stable_task_pass"]
    assert not chrome_only["body_coverage_pass"]
    assert not chrome_only["content_pass"]


@pytest.mark.parametrize(
    ("case_id", "required_chrome"),
    [
        ("risk-011", ["视频", "财经", "科技", "热点", "国际"]),
        ("risk-101", ["首页", "番剧", "直播", "游戏中心", "会员购", "漫画", "赛事"]),
        (
            "risk-diverse-031",
            ["Forums", "Wiki", "Test Exchange", "Read", "View source", "View history"],
        ),
        (
            "risk-diverse-041",
            ["Help/Guide", "Wiki", "Timeline", "Roadmap", "Browse Source", "View Tickets"],
        ),
        (
            "risk-diverse-225",
            ["Products", "Services & Support"],
        ),
    ],
)
def test_page_content_without_declared_initial_chrome_fails(
    dataset,
    case_id,
    required_chrome,
):
    case = next(case for case in dataset["cases"] if case["id"] == case_id)
    anchors = [row["alternatives"][0] for row in case["acceptance_contract"]["stable_text_anchors"]]
    assert all(value in anchors for value in required_chrome)
    partial = "\n".join(value for value in anchors if value not in required_chrome)
    if body := case.get("body_requirements"):
        partial += "\n" + "\n".join(row["text"] for row in body["regions"])
    result = evaluate_content(
        partial,
        [],
        min_content_chars=1,
        acceptance_contract=case["acceptance_contract"],
        body_requirements=case.get("body_requirements"),
        base_url=case["url"],
    )
    assert not result["stable_task_pass"]
    assert not result["content_pass"]



def test_certified_report_preserves_certificate_bound_files(tmp_path, capsys):
    from harness.web_fetch.config import load_dataset
    from harness.web_fetch.providers.fixture import FixtureProvider
    from harness.web_fetch.report_build import build_report
    from harness.web_fetch.runner import run_evaluation

    profile = json.loads(RUN_PROFILE.read_text())
    provider = profile["provider"]["name"]
    read_frozen_dataset_bytes(DATASET)
    frozen, cases = load_dataset(DATASET)
    measured = measured_risk_control_dataset(
        profile, frozen, hashlib.sha256(RUN_PROFILE.read_bytes()).hexdigest())
    provider_config = tmp_path / "fixture-provider.json"
    provider_config.write_text(json.dumps({provider: {"type": "fixture"}}))
    records, _ = run_evaluation(
        output_dir=tmp_path, dataset_meta=measured, dataset_path=DATASET,
        cases=cases, providers=[FixtureProvider(provider, {"type": "fixture"})],
        provider_config_path=provider_config, runs=1, timeout=5, delay=0,
        provider_concurrency=1, max_failure_retries=0)
    capsys.readouterr()
    _, certified, receipt, _, _ = certified_report_fixture(tmp_path)
    diagnostic = b"DEBUG fixture diagnostic\n"
    for row, certified_row in zip(records, certified, strict=True):
        row["provider_metadata"].update(certified_row["provider_metadata"])
        metadata = row["provider_metadata"]
        metadata["diagnostic_log_sha256"] = hashlib.sha256(diagnostic).hexdigest()
        metadata["diagnostic_log_bytes"] = len(diagnostic)
        path = tmp_path / metadata["diagnostic_log_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(diagnostic)
    results = tmp_path / "results.jsonl"
    results.write_text("".join(json.dumps(row) + "\n" for row in records))
    receipt["results_sha256"] = hashlib.sha256(results.read_bytes()).hexdigest()
    (tmp_path / "certification.json").write_text(json.dumps(receipt))
    report = tmp_path / "report"
    assert build_report(tmp_path, report).is_file()
    for name in ("dataset.json", "manifest.json", "execution.json",
                 "reference-proof-verification.json", "certification.json"):
        assert (report / name).read_bytes() == (tmp_path / name).read_bytes()
    require_risk_control_certification(report, measured, records)


def test_new_risk_certification_uses_frozen_inputs_and_preserves_old_receipts(tmp_path):
    from harness.web_fetch.risk_control import frozen_risk_control_input_snapshot
    dataset, records, receipt, manifest, execution = certified_report_fixture(tmp_path)
    snapshot = frozen_risk_control_input_snapshot(RUN_PROFILE)
    verification = {"schema_version": 1, "kind": "risk_control_frozen_input_verification",
                    "before": snapshot, "after": snapshot, "unchanged": True}
    path = tmp_path / "frozen-input-verification.json"
    path.write_text(json.dumps(verification))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.pop("reference_proof_verification_sha256")
    manifest["frozen_input_verification_sha256"] = digest
    receipt.pop("reference_proof_verification_sha256")
    receipt.update(kind="certified_risk_control_100_moli_v2",
                   frozen_input_verification_sha256=digest)
    (tmp_path / "reference-proof-verification.json").unlink()
    rewrite_certified_report_fixture(tmp_path, records, receipt, manifest, execution)
    require_risk_control_certification(tmp_path, dataset, records)
    verification["after"] = {**snapshot, "case_count": 99}
    path.write_text(json.dumps(verification))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["frozen_input_verification_sha256"] = digest
    receipt["frozen_input_verification_sha256"] = digest
    rewrite_certified_report_fixture(tmp_path, records, receipt, manifest, execution)
    with pytest.raises(ValueError, match="frozen input verification"):
        require_risk_control_certification(tmp_path, dataset, records)
