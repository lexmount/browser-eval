import copy
import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.dataset_assembly import assemble_profile_dataset
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
        "set": {"url": "https://example.test/current"},
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
    assert dataset["comparison"]["case_corrections"]["rerun_all_methods"] == ["plain"]


def test_registered_430_assembly_uses_current_sources_and_four_contracts(tmp_path):
    from harness.web_fetch.config import load_dataset

    definition = json.loads(ASSEMBLY.read_text(encoding="utf-8"))
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
    contracts = {
        case["id"]: case["retrieval_hints"]["contract"]
        for case in dataset["cases"]
        if case.get("retrieval_hints", {}).get("contract")
    }
    assert contracts == {
        "dynamic-015-015-testpages-iframe-list": "iframe-list-one-hundred-items",
        "dynamic-019-selenium-dev-selenium": "iframe-one-form",
        "dynamic-iframe": "iframe-fourteen-family-headings",
        "dynamic-delayed": "delayed-ten-records",
    }
    output = tmp_path / "assembled-dataset.json"
    output.write_bytes(raw)
    _metadata, cases = load_dataset(output)
    assert len(cases) == 430
