"""Current-source qualification preserves frozen body references and tolerances."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lexbench_webfetch.body_coverage import body_requirements

from harness.web_fetch import reference_freeze
from harness.web_fetch.reference_lifecycle import (
    ReferenceLifecycleError,
    _postcheck_case,
    preflight_reference_checks,
    reference_cases,
    sha256,
)

TEXT = "Birds navigate by observing the sun and stars while migrating over long distances. " * 12
NOW = datetime.now(UTC)


def inputs(tmp_path, current=TEXT):
    body = body_requirements(TEXT, structure_hint="whole_declared_component")
    body.update(minimum_coverage=0.8, minimum_floor=0.8, minimum_region_passes=len(body["regions"]))
    case = {
        "id": "body",
        "name": "Body",
        "url": "https://example.test/body",
        "track": "web",
        "task_kind": "body",
        "task_prompt": "Read the full article",
        "expected_any": [],
        "min_content_chars": 1,
        "ground_truth_main_content": TEXT,
        "body_requirements": body,
        "source_verification": {"reference_mode": "live_body"},
    }
    dataset = {"cases": [case], "coverage_plan": {"web": ["fixture"]}}
    captures = tmp_path / "captures"
    captures.mkdir()
    folder = captures / "body"
    folder.mkdir()
    (folder / "reference.txt").write_text(current)
    (folder / "source.html").write_text(current)
    (captures / "audit.json").write_text(
        json.dumps(
            [
                {
                    "case_id": "body",
                    "status": 200,
                    "state": "captured",
                    "checked_at": NOW.isoformat(),
                    "selector": "main",
                    "sha256": sha256(current),
                    "extracted_sha256": sha256(current),
                    "quality_issues": [],
                }
            ]
        )
    )
    source = tmp_path / "dataset.json"
    source.write_text(json.dumps(dataset))
    return dataset, captures, source


@pytest.mark.parametrize("changed", [False, True])
def test_freezer_keeps_original_body_and_reference_with_a_current_receipt(tmp_path, changed):
    current = TEXT.replace("stars", "moon") if changed else TEXT
    dataset, captures, source = inputs(tmp_path, current)
    old = deepcopy(dataset)
    output = tmp_path / "frozen"
    reference_freeze.main(
        ["--dataset", str(source), "--captures", str(captures), "--output", str(output)]
    )
    frozen = json.loads((output / "dataset.json").read_text())
    case = frozen["cases"][0]
    assert case["ground_truth_main_content"] == TEXT
    assert case["body_requirements"] == old["cases"][0]["body_requirements"]
    assert dataset == old
    assert set(reference_cases(frozen)) == {"body"}
    assert next((output / "sources/body").glob("source-qualification-*.txt")).read_text() == current
    checks = preflight_reference_checks(frozen["cases"], output / "dataset.json", now=NOW)
    assert set(checks["cases"]) == {"body"}
    assert not json.loads((output / "reference-refresh.json").read_text())["reference_changed"]


@pytest.mark.parametrize("current", ["Unrelated rewritten official article. " * 80, ""])
def test_changed_or_missing_source_is_rejected_before_scoring(tmp_path, current):
    dataset, captures, _ = inputs(tmp_path, current)
    with pytest.raises(ValueError, match="source|qualification"):
        reference_freeze.compute_reference_update(dataset, captures)


@pytest.mark.parametrize("damage", ["missing", "tampered", "newlines", "stale"])
def test_preflight_requires_intact_fresh_qualification(tmp_path, damage):
    dataset, captures, source = inputs(tmp_path, TEXT + "\n")
    output = tmp_path / "frozen"
    reference_freeze.main(
        ["--dataset", str(source), "--captures", str(captures), "--output", str(output)]
    )
    frozen = json.loads((output / "dataset.json").read_text())
    receipt = next((output / "sources/body").glob("source-qualification-*.txt"))
    if damage == "missing":
        receipt.unlink()
    if damage == "tampered":
        receipt.write_text("changed")
    if damage == "newlines":
        receipt.write_bytes(receipt.read_bytes().replace(b"\n", b"\r\n"))
    if damage == "stale":
        frozen["cases"][0]["source_verification"]["checked_at"] = "2020-01-01T00:00:00Z"
    with pytest.raises(ReferenceLifecycleError):
        preflight_reference_checks(frozen["cases"], output / "dataset.json", now=NOW)


@pytest.mark.parametrize(
    "post,expected",
    [
        (TEXT, "verified"),
        (TEXT.replace("stars", "moon"), "verified"),
        ("A different article. " * 80, "reference_changed"),
    ],
)
def test_postcheck_uses_frozen_body_tolerance_not_exact_current_text(tmp_path, post, expected):
    dataset, captures, source = inputs(tmp_path)
    output = tmp_path / "frozen"
    reference_freeze.main(
        ["--dataset", str(source), "--captures", str(captures), "--output", str(output)]
    )
    frozen = json.loads((output / "dataset.json").read_text())
    case = frozen["cases"][0]
    binding = preflight_reference_checks([case], output / "dataset.json", now=NOW)["cases"]["body"]
    records = [
        {
            "started_at": NOW.isoformat(),
            "elapsed_ms": 1000,
            "metrics": {"reference_sha256": sha256(TEXT)},
        }
    ]
    observation = {
        "independent": True,
        "scope_identity": binding["scope_identity"],
        "completion": {"satisfied": True, "elapsed_ms": 1, "timeout_ms": 1000},
        "checked_at": (NOW + timedelta(seconds=2)).isoformat(),
        "content_text": post,
    }
    result = _postcheck_case(binding, case, observation, records, NOW + timedelta(seconds=3))
    assert result["status"] == expected


def certified_run(tmp_path, monkeypatch, invalid_phase=None, current_text=None):
    from harness.web_fetch import benchmark_runner, certification, evidence
    from harness.web_fetch.reference_lifecycle import (
        require_verified_reference_checks,
        scope_identity,
    )

    current = (
        "A completely rewritten document about unrelated subjects. " * 50
        if invalid_phase == "pre"
        else (current_text or TEXT.replace("stars", "moon")) + "\n"
    )
    dataset, _, source = inputs(tmp_path, current)
    dataset["cases"][0]["category"] = "web"
    dataset["tool"] = "web_fetch"
    dataset["comparison"] = {}
    source.write_text(json.dumps(dataset))
    output = tmp_path / "run"
    work = tmp_path / "work"
    config = tmp_path / "providers.json"
    config.write_text(json.dumps({"providers": {"fixture": {"type": "fixture"}}}))
    stages = []

    def capture(destination, case=None):
        case = case or dataset["cases"][0]
        observed = (
            "A different current document. " * 50
            if invalid_phase == "post" and destination.name == "post-captures"
            else current
        )
        folder = destination / "body"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "reference.txt").write_text(observed)
        (folder / "source.html").write_text(observed)
        row = {
            "case_id": "body",
            "url": case["url"],
            "status": 200,
            "state": "captured",
            "missing_facts": [],
            "quality_issues": [],
            "selector": "main",
            "checked_at": datetime.now(UTC).isoformat(),
            "sha256": sha256(observed),
            "reference_sha256": sha256(observed),
            "extracted_sha256": sha256(observed),
            "scope_identity": scope_identity(case),
            "independent": True,
            "source": "independent HTTP GET (no provider output)",
            "completion": {"satisfied": True, "elapsed_ms": 1, "timeout_ms": 1000},
        }
        (destination / "audit.json").write_text(json.dumps([row]))
        (folder / "evidence.json").write_text(json.dumps(row))

    def execute(stage, arguments):
        name = stage.__module__.rsplit(".", 1)[-1] + "." + stage.__name__
        stages.append(name)
        if name == "source_capture.main":
            selected = json.loads(Path(arguments[0]).read_text())["cases"][0]
            capture(Path(arguments[1]), selected)
        elif name == "reference_freeze.main":
            stage(arguments)
        elif name == "benchmark_runner.main":
            monkeypatch.setattr(benchmark_runner, "load_runtime_env", lambda *a, **k: None)
            stage(arguments)
        elif name == "reference_freeze.postcheck_main":
            monkeypatch.setattr(
                reference_freeze, "capture", lambda case, destination: capture(destination, case)
            )
            stage(arguments)
        elif name == "evidence.finalize_main":
            evidence.finalize_aggregate(output, work / "post-captures/observations.json")
        else:
            pytest.fail("Unexpected stage " + name)

    monkeypatch.setattr(certification, "run", execute)
    args = [
        "--dataset",
        str(source),
        "--output",
        str(output),
        "--work-root",
        str(work),
        "--providers",
        "fixture",
        "--provider-config",
        str(config),
        "--case-ids",
        "body",
    ]
    if invalid_phase:
        with pytest.raises((SystemExit, ValueError)):
            certification.main(args)
        if invalid_phase == "pre":
            assert "benchmark_runner.main" not in stages
            assert not output.exists()
        else:
            assert "reference_freeze.postcheck_main" in stages
    else:
        certification.main(args)
        assert stages[0] == "source_capture.main"
        assert "reference_freeze.postcheck_main" in stages
        saved = json.loads((output / "dataset.json").read_text())
        assert saved["cases"][0]["ground_truth_main_content"] == TEXT
        manifest = json.loads((output / "execution.json").read_text())["batches"]
        records = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
        require_verified_reference_checks(manifest, cases=saved, records=records, source=output)
        receipt = next((output / "sources/body").glob("source-qualification-*.txt"))
        original = receipt.read_bytes()
        receipt.write_bytes(original.replace(b"\n", b"\r\n"))
        with pytest.raises(ReferenceLifecycleError, match="qualification hash mismatch"):
            require_verified_reference_checks(manifest, cases=saved, records=records, source=output)
        receipt.unlink()
        with pytest.raises(ReferenceLifecycleError, match="qualification is missing"):
            require_verified_reference_checks(manifest, cases=saved, records=records, source=output)

        receipt.write_bytes(original)
        return output


@pytest.mark.parametrize("invalid_phase", [None, "pre", "post"])
def test_formal_entry_captures_before_calls_and_rejects_invalid_source(
    tmp_path, monkeypatch, invalid_phase
):
    certified_run(tmp_path, monkeypatch, invalid_phase)


def test_combined_batches_retain_distinct_qualified_sources(tmp_path, monkeypatch):
    from harness.web_fetch.artifacts import materialize_reference_checks
    from harness.web_fetch.reference_lifecycle import require_verified_reference_checks
    from harness.web_fetch.refresh_assembly import _require_canonical_cases

    combined = tmp_path / "combined"
    combined.mkdir()
    runs = []
    for word in ("moon", "clouds"):
        root = tmp_path / word
        root.mkdir()
        runs.append(certified_run(root, monkeypatch, current_text=TEXT.replace("stars", word)))
    canonical = json.loads((runs[0] / "dataset.json").read_text())
    (combined / "dataset.json").write_text(json.dumps(canonical))
    canonical_cases = {case["id"]: case for case in canonical["cases"]}
    for run in runs:
        supplied = json.loads((run / "dataset.json").read_text())
        batches = json.loads((run / "execution.json").read_text())["batches"]
        rows = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
        _require_canonical_cases(
            supplied, canonical_cases, rows, "batch", equivalent_capture_bindings=True
        )
        materialize_reference_checks(combined, run, batches)
        require_verified_reference_checks(
            batches,
            cases=canonical,
            records=rows,
            source=combined,
            equivalent_capture_bindings=True,
        )
        changed = deepcopy(supplied)
        changed["cases"][0]["body_requirements"]["minimum_coverage"] = 0.5
        with pytest.raises(ValueError, match="differs from canonical"):
            _require_canonical_cases(
                changed, canonical_cases, rows, "batch", equivalent_capture_bindings=True
            )
    receipts = list((combined / "sources/body").glob("source-qualification-*.txt"))
    assert len(receipts) == 2
    assert {path.read_text() for path in receipts} == {
        TEXT.replace("stars", word) + "\n" for word in ("moon", "clouds")
    }
    receipt = next((runs[1] / "sources/body").glob("source-qualification-*.txt"))
    receipt.write_text("tampered")
    with pytest.raises(ValueError, match="differs from its certificate"):
        materialize_reference_checks(combined, runs[1], batches)
