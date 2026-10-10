"""Reference corrections retain complete evidence and publish only the declared scope."""

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.web_fetch.reference_revision import revise
from harness.web_fetch.scoring import score_attempt

AUTHORITY = {
    "root_cause_category": "reference-evidence",
    "root_cause": "Independent source extraction excluded a required paragraph from the reference.",
    "regression_ids": ["reference-scope-retains-main-content"],
}


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def reference_fixture(tmp_path):
    evidence, canonical = tmp_path / "evidence", tmp_path / "canonical"
    evidence.mkdir()
    canonical.mkdir()
    cases, records = [], []
    for identifier in ("one", "two"):
        case = {
            "id": identifier,
            "name": identifier,
            "url": f"https://example.invalid/{identifier}",
            "category": "web",
            "track": "web",
            "task_kind": "body",
            "expected_any": ["answer"],
            "ground_truth_main_content": "old reference answer",
        }
        raw = b"independent answer\n" * 20
        relative = f"artifacts/{identifier}.txt"
        (evidence / relative).parent.mkdir(exist_ok=True)
        (evidence / relative).write_bytes(raw)
        records.append(
            {
                "attempt_id": f"custom__{identifier}__r001",
                "provider": "custom",
                "provider_type": "synthetic",
                "case_id": identifier,
                "case_name": identifier,
                "category": "web",
                "artifact_path": relative,
                "ok": True,
                "run_id": f"frozen-{identifier}",
                "elapsed_ms": 10,
                "metrics": score_attempt(case, raw, ok=True),
            }
        )
        cases.append(case)
        for root, content in ((evidence, b"original source"), (canonical, b"corrected source")):
            source = root / "sources" / identifier
            source.mkdir(parents=True)
            (source / "source.html.txt").write_bytes(content)
    (evidence / "dataset.json").write_text(json.dumps({"cases": cases}))
    revised = deepcopy(cases)
    for case in revised:
        case["ground_truth_main_content"] = "corrected independent answer reference"
    (canonical / "dataset.json").write_text(json.dumps({"cases": revised}))
    (canonical / "sources" / "unrelated.txt").write_text("not selected")
    (evidence / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    (evidence / "summary.json").write_text("{}")
    (evidence / "manifest.json").write_text(
        json.dumps({"attempt_ids": [r["attempt_id"] for r in records]})
    )
    return evidence, canonical / "dataset.json"


@pytest.mark.parametrize("filename", ["reference-revisions.json", "scoring-revisions.json"])
@pytest.mark.parametrize("content", [b"{invalid", b"{}", b"[{}]"])
def test_invalid_history_keeps_entire_bundle_unchanged(tmp_path, filename, content):
    evidence, canonical = reference_fixture(tmp_path)
    (evidence / filename).write_bytes(content)
    before = snapshot(evidence)
    with pytest.raises(ValueError):
        revise(evidence, canonical, **AUTHORITY)
    assert snapshot(evidence) == before


def test_reference_copy_failure_keeps_entire_bundle_unchanged(tmp_path):
    import shutil

    evidence, canonical = reference_fixture(tmp_path)
    before = snapshot(evidence)
    copytree = shutil.copytree

    def fail_source(src, dst, *args, **kwargs):
        if Path(src).is_relative_to(canonical.parent / "sources"):
            raise OSError("reference copy failed")
        return copytree(src, dst, *args, **kwargs)

    with patch("shutil.copytree", fail_source), pytest.raises(OSError):
        revise(evidence, canonical, **AUTHORITY)
    assert snapshot(evidence) == before


def test_receipt_failure_keeps_entire_bundle_unchanged(tmp_path):
    evidence, canonical = reference_fixture(tmp_path)
    before = snapshot(evidence)
    write = Path.write_text

    def fail_receipt(path, *args, **kwargs):
        if path.name == "reference-revisions.json":
            raise OSError("receipt write failed")
        return write(path, *args, **kwargs)

    with patch.object(Path, "write_text", fail_receipt), pytest.raises(OSError):
        revise(evidence, canonical, **AUTHORITY)
    assert snapshot(evidence) == before


def test_selected_reference_does_not_replace_other_task_sources(tmp_path):
    evidence, canonical = reference_fixture(tmp_path)
    other = (evidence / "sources/two/source.html.txt").read_bytes()
    receipt = revise(evidence, canonical, case_ids={"one"}, **AUTHORITY)
    assert {row["case_id"] for row in receipt["changed_cases"]} == {"one"}
    assert (evidence / "sources/one/source.html.txt").read_bytes() == b"corrected source"
    assert (evidence / "sources/two/source.html.txt").read_bytes() == other
    assert not (evidence / "sources/unrelated.txt").exists()


def test_success_retains_complete_original_and_can_extend_history(tmp_path):
    evidence, canonical = reference_fixture(tmp_path)
    original = snapshot(evidence)
    first = revise(evidence, canonical, case_ids={"one"}, **AUTHORITY)
    (backup,) = tmp_path.glob(".evidence-reference-history-*/original")
    assert snapshot(backup) == original
    second = revise(evidence, canonical, case_ids={"two"}, **AUTHORITY)
    assert json.loads((evidence / "reference-revisions.json").read_bytes()) == [first, second]
    assert second["input_dataset_sha256"] == first["output_dataset_sha256"]
    assert second["input_results_sha256"] == first["output_results_sha256"]
    assert len(list(tmp_path.glob(".evidence-reference-history-*/original"))) == 2
    for name, content in original.items():
        if name.startswith("artifacts/") or name == "manifest.json":
            assert (evidence / name).read_bytes() == content


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_promotion_failure_preserves_recoverable_original(tmp_path, rollback_fails):
    evidence, canonical = reference_fixture(tmp_path)
    before = snapshot(evidence)
    rename = Path.rename

    def fail_promotion(path, target):
        if path.name == "staged" or (rollback_fails and path.name == "original"):
            raise OSError("promotion failed")
        return rename(path, target)

    with patch.object(Path, "rename", fail_promotion):
        with pytest.raises(RuntimeError if rollback_fails else OSError):
            revise(evidence, canonical, **AUTHORITY)
    if rollback_fails:
        (original,) = tmp_path.glob(".evidence-reference-history-*/original")
        assert snapshot(original) == before
    else:
        assert snapshot(evidence) == before
        assert not list(tmp_path.glob(".evidence-reference-history-*"))


def test_concurrent_evidence_write_is_not_overwritten(tmp_path):
    import shutil

    evidence, canonical = reference_fixture(tmp_path)
    copytree = shutil.copytree

    def concurrent_write(src, dst, *args, **kwargs):
        result = copytree(src, dst, *args, **kwargs)
        if Path(src) == evidence:
            with (evidence / "results.jsonl").open("ab") as stream:
                stream.write(b"\n")
        return result

    before = snapshot(evidence)
    with (
        patch("shutil.copytree", concurrent_write),
        pytest.raises(ValueError, match="inputs changed"),
    ):
        revise(evidence, canonical, **AUTHORITY)
    before["results.jsonl"] += b"\n"
    assert snapshot(evidence) == before


def test_changed_reference_copy_is_rejected_before_promotion(tmp_path):
    import shutil

    evidence, canonical = reference_fixture(tmp_path)
    before = snapshot(evidence)
    copytree = shutil.copytree

    def corrupt_copy(src, dst, *args, **kwargs):
        result = copytree(src, dst, *args, **kwargs)
        if Path(src) == canonical.parent / "sources/one":
            (Path(dst) / "source.html.txt").write_text("changed during copy")
        return result

    with (
        patch("shutil.copytree", corrupt_copy),
        pytest.raises(ValueError, match="changed during copy"),
    ):
        revise(evidence, canonical, **AUTHORITY)
    assert snapshot(evidence) == before


def test_linked_corrected_reference_is_rejected_before_writes(tmp_path):
    evidence, canonical = reference_fixture(tmp_path)
    source = canonical.parent / "sources/one/source.html.txt"
    target = tmp_path / "linked-source.html"
    target.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(target)
    before = snapshot(evidence)
    with pytest.raises(ValueError, match="symlink"):
        revise(evidence, canonical, **AUTHORITY)
    assert snapshot(evidence) == before


def test_attribution_only_field_scope_keeps_sources_and_results_unchanged(tmp_path):
    evidence, canonical = reference_fixture(tmp_path)
    document = json.loads(canonical.read_bytes())
    document["cases"][0]["ground_truth_evidence"] = {"kind": "independent_annotation"}
    canonical.write_text(json.dumps(document))
    before = snapshot(evidence)
    receipt = revise(
        evidence, canonical, case_ids={"one"}, fields={"ground_truth_evidence"}, **AUTHORITY
    )
    assert receipt["task_decision_changes"] == []
    assert receipt["changed_cases"][0]["fields"] == ["ground_truth_evidence"]
    after = snapshot(evidence)
    assert after["results.jsonl"] == before["results.jsonl"]
    for name, content in before.items():
        if name.startswith("sources/"):
            assert after[name] == content


def test_empty_correction_does_not_create_an_unbound_history_receipt(tmp_path):
    evidence, canonical = reference_fixture(tmp_path)
    canonical.write_bytes((evidence / "dataset.json").read_bytes())
    before = snapshot(evidence)
    with pytest.raises(ValueError, match="no selected changes"):
        revise(evidence, canonical, **AUTHORITY)
    assert snapshot(evidence) == before
    assert not list(tmp_path.glob(".evidence-reference-history-*"))
