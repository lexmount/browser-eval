import hashlib
import json
import re
from pathlib import Path

import pytest

from harness.web_fetch.composite_tasks import validate_composite_acceptance
from harness.web_fetch.metrics import evaluate_content, normalize_for_match
from harness.web_fetch.report_build import require_risk_control_certification
from harness.web_fetch.risk_control import measured_risk_control_dataset
from harness.web_fetch.stable_tasks import validate_acceptance_contract
from scripts.build_risk_control_dataset import evidence_roots
from scripts.run_risk_control_moli import main as run_risk_control
from scripts.run_risk_control_moli import require_complete_diagnostic_logs

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation/risk-control/dataset.json"
COHORT = ROOT / "evaluation/risk-control/qualification/cohort/cohort-manifest.json"
COHORT_RECEIPT = ROOT / "evaluation/risk-control/qualification/cohort/cohort-receipt.json"
RUN_PROFILE = ROOT / "evaluation/risk-control/run-profile.json"


@pytest.fixture(scope="module")
def dataset():
    return json.loads(DATASET.read_text())


def test_dataset_covers_the_exact_audited_cohort(dataset):
    cohort = json.loads(COHORT.read_text())
    assert dataset["case_count"] == len(dataset["cases"]) == 100
    assert [case["id"] for case in dataset["cases"]] == [case["id"] for case in cohort["cases"]]
    assert dataset["site_count"] == 74
    assert dataset["stable_literal_count"] == 1599
    assert dataset["body_case_count"] == 79
    assert dataset["quantity_case_count"] == 21
    assert dataset["independent_quantity_group_count"] == 27
    assert dataset["list_or_field_case_count"] == 23
    assert dataset["composite_group_count"] == 29


def test_external_evidence_roots_honor_data_home(monkeypatch, tmp_path):
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path))
    assert all(path.is_relative_to(tmp_path) for path in evidence_roots().values())


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
        path = ROOT / binding["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["sha256"]


def test_direct_formal_execution_is_rejected_before_provider_validation(tmp_path):
    with pytest.raises(SystemExit):
        run_risk_control(["--execute", "--output", str(tmp_path / "run")])


def certified_report_fixture(tmp_path):
    profile_bytes = RUN_PROFILE.read_bytes()
    profile = json.loads(profile_bytes)
    profile_sha = hashlib.sha256(profile_bytes).hexdigest()
    frozen_dataset = json.loads(DATASET.read_text())
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
    assert "collections of collections" not in json.dumps(ibiblio, ensure_ascii=False)
    news = ibiblio["composite_acceptance"]["groups"]
    assert len(news) == 1
    assert news[0]["id"] == "homepage-news"
    assert news[0]["minimum_records"] == 3
    assert news[0]["hosts"] == ["www.ibiblio.org"]
    assert [field["id"] for field in news[0]["required_fields"]] == ["published", "summary"]

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


def test_ibiblio_news_requires_three_unique_real_article_cards(dataset):
    case = next(case for case in dataset["cases"] if case["id"] == "risk-diverse-192")
    contract = case["composite_acceptance"]
    cards = [
        ("Importance of Summer", "/archive/2026/06/importance-of-summer/", "19 June 2026"),
        (
            "Poetry Available through ibiblio",
            "/archive/2024/03/poetry-available-through-ibiblio/",
            "4 March 2024",
        ),
        (
            "SoundExchange and Digital Audio Streaming",
            "/archive/2024/01/soundexchange-and-digital-audio-streaming/",
            "24 January 2024",
        ),
    ]

    def markdown(selected):
        return "\n\n".join(
            f"## [{title}]({path})\n"
            "This is a sufficiently long same-record article summary explaining "
            f"the news item in useful detail.\n[{date}]({path})"
            for title, path, date in selected
        )

    from harness.web_fetch.composite_tasks import evaluate_composite_acceptance

    positive = evaluate_composite_acceptance(
        markdown(cards), contract, base_url="https://ibiblio.org/"
    )
    assert positive["composite_acceptance_pass"]
    assert positive["composite_group_results"][0]["count"] == 3

    missing = evaluate_composite_acceptance(
        markdown(cards[:2]), contract, base_url="https://ibiblio.org/"
    )
    assert not missing["composite_acceptance_pass"]
    assert missing["composite_group_results"][0]["count"] == 2

    duplicate = evaluate_composite_acceptance(
        markdown([cards[0], cards[0], cards[1]]),
        contract,
        base_url="https://ibiblio.org/",
    )
    assert not duplicate["composite_acceptance_pass"]
    assert duplicate["composite_group_results"][0]["count"] == 2

    wrong_path = list(cards)
    wrong_path[2] = (wrong_path[2][0], "/about/", wrong_path[2][2])
    invalid = evaluate_composite_acceptance(
        markdown(wrong_path), contract, base_url="https://ibiblio.org/"
    )
    assert not invalid["composite_acceptance_pass"]
    assert invalid["composite_group_results"][0]["count"] == 2


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
            ["Products", "Services & Support", "Resources", "Articles", "Tables", "Calculators"],
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
    omitted = set(required_chrome)
    partial = "\n".join(value for value in anchors if value not in omitted)
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
