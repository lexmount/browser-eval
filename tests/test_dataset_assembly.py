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
        "dynamic.json": {"cases": [{
            "id": "frame", "url": "https://example.test/frame", "track": "dynamic",
            "retrieval_hints": {"version": 3, "mode": "native"},
        }]},
    }
    declarations = []
    for name, source in sources.items():
        _write(tmp_path / name, source)
        declarations.append({
            "path": name,
            "sha256": hashlib.sha256((tmp_path / name).read_bytes()).hexdigest(),
            "tracks": ["web" if name == "plain.json" else "dynamic"],
        })
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
    dataset, raw = assemble_profile_dataset(
        path, repository=tmp_path, require_output_hash=False
    )
    assert len(dataset["cases"]) == 2
    assert dataset["cases"][1]["retrieval_hints"] == {
        "version": 3, "mode": "native"
    }
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


def test_registered_430_assembly_uses_current_sources_and_four_contracts():
    definition = json.loads(ASSEMBLY.read_text(encoding="utf-8"))
    for source in definition["sources"]:
        raw = read_frozen_dataset_bytes(ROOT / source["path"])
        assert hashlib.sha256(raw).hexdigest() == source["sha256"]
    dataset, raw = assemble_profile_dataset(ASSEMBLY)
    assert hashlib.sha256(raw).hexdigest() == definition["native_dataset_sha256"]
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
