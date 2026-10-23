import copy
import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.dataset_assembly import assemble_profile_dataset
from harness.web_fetch.reference_lifecycle import homepage_prompt_anchor_correction


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


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


def test_profile_review_scope_survives_dataset_assembly(tmp_path):
    path, _assembly = _fixture(tmp_path)
    profile_path = tmp_path / "profile.json"
    profile = json.loads(profile_path.read_text())
    profile["require_independent_result_review"] = True
    for scope in ("all", "machine-failures"):
        profile["independent_result_review_scope"] = scope
        _write(profile_path, profile)
        dataset, raw = assemble_profile_dataset(
            path, repository=tmp_path, require_output_hash=False)
        assert dataset["comparison"]["independent_result_review_scope"] == scope
        assert json.loads(raw)["comparison"]["independent_result_review_scope"] == scope
    profile["independent_result_review_scope"] = "unsupported"
    _write(profile_path, profile)
    with pytest.raises(ValueError, match="Unsupported declared"):
        assemble_profile_dataset(path, repository=tmp_path, require_output_hash=False)


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


def test_reusable_assembly_requires_external_profile(tmp_path):
    path, assembly = _fixture(tmp_path)
    assembly.pop("profile")
    _write(path, assembly)
    with pytest.raises(ValueError, match="external report profile"):
        assemble_profile_dataset(path, repository=tmp_path, require_output_hash=False)
    dataset, _raw = assemble_profile_dataset(
        path,
        repository=tmp_path,
        require_output_hash=False,
        profile_path_override=Path("profile.json"),
    )
    assert dataset["comparison"]["reproduction_profile"] == "profile.json"


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


def test_explicit_external_profile_is_portable_and_preserves_input_hash_checks(tmp_path):
    repository = tmp_path / 'checkout'
    path, assembly = _fixture(repository)
    profile = tmp_path / 'instance/profile.json'
    _write(profile, json.loads((repository / 'profile.json').read_text()))
    dataset, raw = assemble_profile_dataset(path, repository=repository,
        profile_path_override=profile, require_output_hash=False)
    assert dataset['comparison']['reproduction_profile'] == 'profile.json'
    assert str(tmp_path).encode() not in raw
    source = repository / 'plain.json'
    source.write_text('{"cases": []}')
    with pytest.raises(ValueError, match='source hash changed'):
        assemble_profile_dataset(path, repository=repository,
            profile_path_override=profile, require_output_hash=False)
