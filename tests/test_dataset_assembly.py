import copy
import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.dataset_assembly import assemble_profile_dataset
from harness.web_fetch.reference_lifecycle import homepage_prompt_anchor_correction
from tests.conftest import read_frozen_dataset_bytes

ROOT = Path(__file__).resolve().parents[1]
ASSEMBLY = ROOT / "evaluation/browser-native-430/dataset-assembly.json"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def _canonical_sha256(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_homepage_anchor_reference_correction_preserves_reader_constraints():
    suffix = "不点击子页面、翻页、登录或执行其他 Browser Use 操作。"
    old = {
        "source_benchmark": {"mode": "ranked_domain_homepage"},
        "task_prompt": "保留 Current；Retired。" + suffix,
        "reference_answer": "保留 Current；Retired。" + suffix,
        "acceptance_contract": {
            "version": 1,
            "schema": "body_text",
            "minimum_records": 0,
            "stable_text_anchors": [
                {"id": "current", "alternatives": ["Current"]},
                {"id": "retired", "alternatives": ["Retired"]},
            ],
        },
    }
    current = copy.deepcopy(old)
    current["task_prompt"] = current["reference_answer"] = "保留 Current。" + suffix
    current["acceptance_contract"]["stable_text_anchors"] = [
        {"id": "current", "alternatives": ["Current"]},
    ]
    assert homepage_prompt_anchor_correction(old, current)

    current["task_prompt"] = current["reference_answer"] = (
        "保留 Current。不点击子页面、翻页后再登录。"
    )
    assert not homepage_prompt_anchor_correction(old, current)


def _fixture(tmp_path: Path) -> tuple[Path, dict]:
    profile = {
        "dataset_id": "combined",
        "comparison_kind": "headless_markdown",
        "case_count": 2,
        "case_ids_sha256": hashlib.sha256(b'["plain","frame"]').hexdigest(),
        "providers": ["lightpanda", "chromium"],
        "timeout_seconds": 45,
        "runs_per_case": 1,
        "concurrency": 1,
        "max_failure_retries": 0,
        "retry_selection": "transient_transport_or_http_failure_only; final_physical_attempt_is_authoritative",
        "require_independent_result_review": False,
        "reference_binding_mode": "frozen_inputs",
        "report_output": "reports/combined",
        "case_inventory": {"web": ["plain"], "dynamic": ["frame"]},
    }
    _write(tmp_path / "profile.json", profile)
    sources = {
        "plain.json": {"cases": [{"id": "plain", "url": "https://example.test", "track": "web"}]},
        "dynamic.json": {
            "cases": [
                {
                    "id": "frame",
                    "url": "https://example.test/frame",
                    "track": "dynamic",
                    "retrieval_hints": {"version": 3, "mode": "native"},
                }
            ]
        },
    }
    declarations = []
    for name, source in sources.items():
        _write(tmp_path / name, source)
        declarations.append(
            {
                "path": name,
                "sha256": hashlib.sha256((tmp_path / name).read_bytes()).hexdigest(),
                "tracks": ["web" if name == "plain.json" else "dynamic"],
            }
        )
    assembly = {
        "schema_version": 1,
        "kind": "profile_dataset_assembly",
        "profile": "profile.json",
        "dataset": {
            "id": "combined",
            "name": "Combined",
            "tool": "web_fetch",
            "dynamic_scoring": "stable",
        },
        "sources": declarations,
    }
    _write(tmp_path / "assembly.json", assembly)
    return tmp_path / "assembly.json", assembly


def test_profile_dataset_assembly_preserves_native_contract(tmp_path):
    path, _assembly = _fixture(tmp_path)
    dataset, raw = assemble_profile_dataset(path, repository=tmp_path, require_output_hash=False)
    assert len(dataset["cases"]) == 2
    assert dataset["cases"][1]["retrieval_hints"] == {"version": 3, "mode": "native"}
    assert dataset["coverage_plan"] == {
        "web": ["lightpanda", "chromium"],
        "dynamic": ["lightpanda", "chromium"],
    }
    assert json.loads(raw) == dataset


def test_profile_dataset_assembly_rejects_stale_source_and_wrong_owner(tmp_path):
    path, assembly = _fixture(tmp_path)
    changed = copy.deepcopy(assembly)
    changed["sources"][1]["sha256"] = "0" * 64
    _write(path, changed)
    with pytest.raises(ValueError, match="source hash changed"):
        assemble_profile_dataset(path, repository=tmp_path, require_output_hash=False)
    changed = copy.deepcopy(assembly)
    changed["sources"][1]["tracks"] = ["web"]
    _write(path, changed)
    with pytest.raises(ValueError, match="track ownership"):
        assemble_profile_dataset(path, repository=tmp_path, require_output_hash=False)


def test_profile_dataset_assembly_applies_hash_locked_case_corrections(tmp_path):
    path, assembly = _fixture(tmp_path)
    source_case = json.loads((tmp_path / "plain.json").read_text())["cases"][0]
    definition_sha256 = hashlib.sha256(
        json.dumps(source_case, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    corrections = {
        "schema_version": 2,
        "kind": "profile_case_corrections",
        "cases": [
            {
                "case_id": "plain",
                "execution_effect": "rescore_only",
                "expected_definition_sha256": definition_sha256,
                "set": {
                    "ground_truth_main_content": "Current copy",
                    "source_verification": {
                        "state": "valid",
                        "reference_sha256": hashlib.sha256(b"Current copy").hexdigest(),
                    },
                    "required_facts": [{"id": "current", "alternatives": ["Current copy"]}],
                },
                "evidence": {
                    "source": "https://example.test",
                    "observed_at": "2026-10-13",
                    "observed_text": "Current copy",
                    "regression_ids": [
                        "example-current-body-passes",
                        "example-retired-body-does-not-pass",
                    ],
                },
            }
        ],
    }
    _write(tmp_path / "corrections.json", corrections)
    assembly["schema_version"] = 2
    assembly["case_corrections"] = {
        "path": "corrections.json",
        "sha256": hashlib.sha256((tmp_path / "corrections.json").read_bytes()).hexdigest(),
    }
    _write(path, assembly)
    dataset, _raw = assemble_profile_dataset(path, repository=tmp_path, require_output_hash=False)
    assert dataset["cases"][0]["ground_truth_main_content"] == "Current copy"
    assert dataset["cases"][0]["source_verification"]["state"] == "valid"
    assert dataset["cases"][0]["required_facts"][0]["alternatives"] == ["Current copy"]
    assert dataset["comparison"]["case_corrections"]["rerun_all_methods"] == []

    corrections["cases"][0]["expected_definition_sha256"] = "0" * 64
    _write(tmp_path / "corrections.json", corrections)
    assembly["case_corrections"]["sha256"] = hashlib.sha256(
        (tmp_path / "corrections.json").read_bytes()
    ).hexdigest()
    _write(path, assembly)
    with pytest.raises(ValueError, match="correction input changed"):
        assemble_profile_dataset(path, repository=tmp_path, require_output_hash=False)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row["set"].update({"provider": "favored-method"}),
        lambda row: row["evidence"].pop("observed_at"),
        lambda row: row["evidence"].update({"regression_ids": ["unknown-control"]}),
    ],
)
def test_profile_dataset_assembly_rejects_unbounded_case_corrections(tmp_path, mutate):
    path, assembly = _fixture(tmp_path)
    source_case = json.loads((tmp_path / "plain.json").read_text())["cases"][0]
    definition_sha256 = hashlib.sha256(
        json.dumps(
            source_case,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    row = {
        "case_id": "plain",
        "execution_effect": "rescore_only",
        "expected_definition_sha256": definition_sha256,
        "set": {"required_facts": [{"id": "current", "alternatives": ["Current copy"]}]},
        "evidence": {
            "source": "https://example.test",
            "observed_at": "2026-10-13",
            "observed_text": "Current copy",
            "regression_ids": [
                "example-current-body-passes",
                "example-retired-body-does-not-pass",
            ],
        },
    }
    mutate(row)
    corrections = {
        "schema_version": 2,
        "kind": "profile_case_corrections",
        "cases": [row],
    }
    _write(tmp_path / "corrections.json", corrections)
    assembly["schema_version"] = 2
    assembly["case_corrections"] = {
        "path": "corrections.json",
        "sha256": hashlib.sha256((tmp_path / "corrections.json").read_bytes()).hexdigest(),
    }
    _write(path, assembly)
    with pytest.raises(ValueError):
        assemble_profile_dataset(path, repository=tmp_path, require_output_hash=False)


def test_input_changing_case_correction_requires_all_method_rerun(tmp_path):
    path, assembly = _fixture(tmp_path)
    source_case = json.loads((tmp_path / "plain.json").read_text())["cases"][0]
    definition_sha256 = hashlib.sha256(
        json.dumps(source_case, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    row = {
        "case_id": "plain",
        "execution_effect": "rescore_only",
        "expected_definition_sha256": definition_sha256,
        "set": {
            "category": "current_homepage",
            "name": "Current example",
            "tags": ["current"],
            "url": "https://example.test/current",
        },
        "evidence": {
            "source": "https://example.test/current",
            "observed_at": "2026-10-13",
            "observed_text": "Current copy",
            "regression_ids": [
                "example-current-body-passes",
                "example-retired-body-does-not-pass",
            ],
        },
    }
    corrections = {
        "schema_version": 2,
        "kind": "profile_case_corrections",
        "cases": [row],
    }
    _write(tmp_path / "corrections.json", corrections)
    assembly["schema_version"] = 2
    assembly["case_corrections"] = {
        "path": "corrections.json",
        "sha256": hashlib.sha256((tmp_path / "corrections.json").read_bytes()).hexdigest(),
    }
    _write(path, assembly)
    with pytest.raises(ValueError, match="bounded fields or evidence"):
        assemble_profile_dataset(path, repository=tmp_path, require_output_hash=False)

    row["execution_effect"] = "rerun_all_methods"
    _write(tmp_path / "corrections.json", corrections)
    assembly["case_corrections"]["sha256"] = hashlib.sha256(
        (tmp_path / "corrections.json").read_bytes()
    ).hexdigest()
    _write(path, assembly)
    dataset, _raw = assemble_profile_dataset(path, repository=tmp_path, require_output_hash=False)
    assert dataset["cases"][0]["url"] == "https://example.test/current"
    assert dataset["cases"][0]["name"] == "Current example"
    assert dataset["cases"][0]["category"] == "current_homepage"
    assert dataset["cases"][0]["tags"] == ["current"]
    assert dataset["comparison"]["case_corrections"]["rerun_all_methods"] == ["plain"]


def test_registered_430_assembly_uses_current_sources_and_four_contracts(tmp_path):
    from harness.web_fetch.config import load_dataset

    definition = json.loads(ASSEMBLY.read_text(encoding="utf-8"))
    assert definition["schema_version"] == 3
    assert definition["native_retrieval_policy"] == "uniform_dynamic_native_v1"
    for source in definition["sources"]:
        raw = read_frozen_dataset_bytes(ROOT / source["path"])
        assert hashlib.sha256(raw).hexdigest() == source["sha256"]
    dataset, raw = assemble_profile_dataset(ASSEMBLY)
    assert hashlib.sha256(raw).hexdigest() == definition["native_dataset_sha256"]
    corrections = json.loads(
        (ROOT / definition["case_corrections"]["path"]).read_text(encoding="utf-8")
    )
    correction = next(
        row for row in corrections["cases"] if row["case_id"] == "dynamic-062-zh-sspai-home"
    )
    corrected_case = next(
        case for case in dataset["cases"] if case["id"] == "dynamic-062-zh-sspai-home"
    )
    dynamic_source = next(
        source for source in definition["sources"] if "dynamic" in source["tracks"]
    )
    source_dataset = json.loads(read_frozen_dataset_bytes(ROOT / dynamic_source["path"]))
    source_case = next(
        case for case in source_dataset["cases"] if case["id"] == "dynamic-062-zh-sspai-home"
    )
    assert correction["expected_definition_sha256"] == _canonical_sha256(source_case)
    assert correction["evidence"]["prior_acceptance_contract_sha256"] == _canonical_sha256(
        source_case["acceptance_contract"]
    )
    assert correction["evidence"]["root_cause"] == "dynamic_record_badge_not_page_anchor"
    assert correction["evidence"]["affected_scope"].endswith("offline rescore only")
    assert correction["evidence"]["revised_acceptance_contract_sha256"] == _canonical_sha256(
        corrected_case["acceptance_contract"]
    )
    assert all(
        anchor["id"] != "source-stable-310fe77a5860"
        for anchor in corrected_case["acceptance_contract"]["stable_text_anchors"]
    )
    corrected_by_id = {case["id"]: case for case in dataset["cases"]}
    assert corrected_by_id["dynamic-050-en-real-usgs-earthquakes"][
        "acceptance_contract"
    ]["minimum_records"] == 3
    assert all(
        "Log in" not in anchor["alternatives"]
        for anchor in corrected_by_id["global-homepage-045-nytimes-com"][
            "acceptance_contract"
        ]["stable_text_anchors"]
    )
    assert all(
        "翻译" not in anchor["alternatives"]
        for anchor in corrected_by_id["risk-diverse-011"]["acceptance_contract"][
            "stable_text_anchors"
        ]
    )
    risk_correction = next(
        row for row in corrections["cases"] if row["case_id"] == "risk-102"
    )
    corrected_risk = next(case for case in dataset["cases"] if case["id"] == "risk-102")
    risk_source = next(
        source for source in definition["sources"] if "risk_control" in source["tracks"]
    )
    source_risk_dataset = json.loads(
        read_frozen_dataset_bytes(ROOT / risk_source["path"])
    )
    source_risk = next(
        case for case in source_risk_dataset["cases"] if case["id"] == "risk-102"
    )
    assert risk_correction["expected_definition_sha256"] == _canonical_sha256(source_risk)
    assert risk_correction["evidence"]["prior_composite_acceptance_sha256"] == (
        _canonical_sha256(source_risk["composite_acceptance"])
    )
    assert risk_correction["evidence"]["revised_composite_acceptance_sha256"] == (
        _canonical_sha256(corrected_risk["composite_acceptance"])
    )
    assert corrected_risk["composite_acceptance"]["groups"][0]["start_pattern"]
    assert corrected_risk["composite_acceptance"]["groups"][0]["end_pattern"]
    dynamic_hints = {
        case["id"]: case["retrieval_hints"]
        for case in dataset["cases"]
        if case.get("track") == "dynamic"
    }
    assert len(dynamic_hints) == 100
    assert set(map(json.dumps, dynamic_hints.values())) == {
        json.dumps({"version": 3, "mode": "native"})
    }
    output = tmp_path / "assembled-dataset.json"
    output.write_bytes(raw)
    _metadata, cases = load_dataset(output)
    assert len(cases) == 430


def test_registered_430_assembly_keeps_only_frozen_first_load_alternatives():
    dataset, _raw = assemble_profile_dataset(ASSEMBLY)
    cases = {case["id"]: case for case in dataset["cases"]}

    def alternatives(case_id: str, anchor_id: str) -> list[str]:
        anchors = cases[case_id]["acceptance_contract"]["stable_text_anchors"]
        return next(anchor["alternatives"] for anchor in anchors if anchor["id"] == anchor_id)

    assert alternatives("china-homepage-089-yuque-com", "stable-anchor-23") == ["Download"]
    assert alternatives(
        "global-homepage-008-whatsapp-com", "current-stable-c737a59e7210"
    ) == ["私密地收发消息"]
    assert alternatives(
        "global-homepage-008-whatsapp-com", "current-stable-8a47066bf393"
    ) == ["下载"]
    assert alternatives("china-homepage-012-feishu-cn", "stable-anchor-25") == [
        "查看案例"
    ]

    # English table labels were independently observed on the untouched first load;
    # only navigation labels introduced by clicking Language -> English are excluded.
    assert alternatives("risk-diverse-153", "stable-03") == ["题目", "Problem"]
    assert alternatives("risk-diverse-153", "stable-07") == ["首页"]
    assert alternatives("risk-diverse-153", "stable-14") == ["注册"]

    noaa_facts = {fact["id"]: fact for fact in cases["live-037-breadcrumb"]["required_facts"]}
    assert noaa_facts["climate-destination-drought"]["alternatives"] == ["Drought.gov"]
    assert noaa_facts["climate-destination-heat"]["alternatives"] == ["Heat.gov"]
    assert "climate-destinations" not in noaa_facts

    cowagent_purpose = next(
        fact for fact in cases["cowagent-doc"]["required_facts"] if fact["id"] == "purpose"
    )
    assert cowagent_purpose["alternatives"] == [
        "获取网页或文档内容",
        "获取 HTTP/HTTPS URL 的内容",
        "获取网页的文本内容",
    ]


def test_registered_m365_reference_uses_one_current_body_version():
    from harness.web_fetch.metrics import task_passed
    from harness.web_fetch.scoring import SCORING_SOURCE_SHA256, score_attempt

    dataset, _raw = assemble_profile_dataset(ASSEMBLY)
    case = next(
        row for row in dataset["cases"]
        if row["id"] == "global-homepage-023-cloud-microsoft"
    )
    anchors = {
        row["id"]: row["alternatives"]
        for row in case["acceptance_contract"]["stable_text_anchors"]
    }
    current_introduction = (
        "AI in Microsoft 365 helps you create, summarize, and find information "
        "across your work."
    )
    retired_introduction = (
        "Sign in to create, collaborate, and move your work forward with Copilot "
        "in Microsoft 365."
    )
    unchanged = [
        "Work smarter across Microsoft 365",
        "Popular prompts to try",
        "Jump into your favorite app",
        "Bring Copilot and your work together in a focused desktop experience.",
    ]

    assert case["url"] == "https://m365.cloud.microsoft/en-us/"
    assert anchors["stable-anchor-2"] == [current_introduction]
    definition = json.loads(ASSEMBLY.read_text())
    corrections = json.loads(
        (ROOT / definition["case_corrections"]["path"]).read_text()
    )
    correction = next(
        row for row in corrections["cases"]
        if row["case_id"] == "global-homepage-023-cloud-microsoft"
    )
    evidence = correction["evidence"]
    assert evidence["root_cause"] == (
        "retired_homepage_copy_was_kept_as_a_parallel_reference"
    )
    assert evidence["reference_version_policy"]["current"] == (
        "single_v3_observed_body"
    )
    assert evidence["revised_acceptance_contract_sha256"] == _canonical_sha256(
        case["acceptance_contract"]
    )
    assert evidence["scoring_source_sha256"] == SCORING_SOURCE_SHA256

    def verdict(introduction: str) -> bool:
        body = "\n".join([unchanged[0], introduction, *unchanged[1:]])
        metrics = score_attempt(case, body.encode(), ok=True)
        return task_passed(True, metrics, case["task_kind"])

    assert verdict(current_introduction)
    assert not verdict(retired_introduction)


def test_registered_430_profile_uses_official_release_and_frozen_minimal_reruns():
    profile = json.loads((ROOT / "evaluation/browser-native-430/current-report.json").read_text())
    providers = json.loads((ROOT / "configs/providers.headless.json").read_text())["providers"]
    assert profile["providers"] == [
        "moli-1.1.15", "moli-fixed", "lightpanda", "obscura", "chromium", "kitesurf"
    ]
    assert profile["method_sources"]["moli-1.1.15"] == {
        "version": "1.1.15",
        "binary_sha256": "7a53cdd071ae60a2f8ff6d08107f67f6b0f1d6247a9acd970d2bcec77ff15b50",
        "source": "https://github.com/lexmount/moli/releases/tag/v1.1.15",
    }
    assert profile["method_sources"]["moli-fixed"]["source_commit"] == (
        "cb81f05a505c10f1fba2a7562e9c0bad71d11399"
    )
    assert profile["method_sources"]["moli-fixed"]["binary_sha256"] == (
        "05f8d6eee72b6e85215ef8be6a92b9951ae6ff299776a334c06e111d797e75a2"
    )
    assert profile["method_sources"]["moli-fixed"]["identity_state"] == "latest_failure_scope_measured"
    scope = profile["method_sources"]["moli-fixed"]["measurement_scope"]
    assert scope["latest_binary_case_count"] == 162
    assert scope["inherited_case_count"] == 268
    assert scope["latest_binary_case_count"] + scope["inherited_case_count"] == profile["case_count"]
    assert scope["inherited_binary_sha256"] != profile["method_sources"]["moli-fixed"]["binary_sha256"]
    for method in ("moli-1.1.15", "moli-fixed", "lightpanda", "obscura", "chromium"):
        assert providers[method]["dns_mode"] == "public_doh"
    for method in ("moli-1.1.15", "moli-fixed", "lightpanda", "obscura"):
        assert providers[method]["native_dns_library"] == "${MOLI_NATIVE_DNS_LIBRARY}"
    binding = profile["rerun_scope"]
    path = ROOT / binding["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["sha256"]
    scope = json.loads(path.read_text())
    assert scope["provider_calls_at_freeze"] == 0
    assert scope["logical_new_calls"] == 651
    assert scope["execution_groups"] == {
        "new_identity_or_route_calls": 526,
        "inherited_transient_followups": 125,
    }
    assert scope["methods"]["moli-fixed"]["case_count"] == 430
    assert scope["methods"]["moli-1.1.15"]["case_count"] == 20
    assert scope["source_results"][0]["selected_provider"] == "moli"
    assert scope["source_results"][0]["method_scope"] == ["moli-1.1.15"]
