from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from harness.trajectory_replay.public_webarena.manifest_repair import (
    build_supersession,
    supersede_rows,
    validate_correction_run_binding,
)
from tests.public_webarena_fixture import write_supersession_fixture


def test_complete_supersession_retains_raw_calls_and_rebuilds_a_portable_report(tmp_path):
    base, correction = write_supersession_fixture(tmp_path)
    before = {
        path: path.read_bytes()
        for source in (base, correction)
        for path in source.rglob("*")
        if path.is_file()
    }
    output = tmp_path / "derived"
    result = build_supersession(
        base, correction, output, {("case-a", "moli")}, reason="synthetic command repair"
    )
    assert result["attempts"] == 3 and result["replaced_scopes"] == 1
    assert result["functional"]["moli"]["physical_attempts"] == 2
    assert result["functional"]["moli"]["result_final_passes"] == 1
    assert all(path.read_bytes() == raw for path, raw in before.items())
    run = output / "run"
    manifest = json.loads((run / "manifest.json").read_bytes())
    index_path = run / "source-inputs.json"
    assert (
        manifest["artifact_provenance"]["source_inputs_sha256"]
        == hashlib.sha256(index_path.read_bytes()).hexdigest()
    )
    assert manifest["derived_from"]["reason"] == "synthetic command repair"
    index = json.loads(index_path.read_bytes())
    for binding in index["files"]:
        retained = (run / binding["retained_path"]).read_bytes()
        assert retained == before[Path(binding["path"])]
        assert hashlib.sha256(retained).hexdigest() == binding["sha256"]
    rows = [json.loads(line) for line in (run / "attempts.jsonl").read_text().splitlines()]
    assert [(row["engine"], row["attempt"], row["status"]) for row in rows] == [
        ("chromium", 1, "pass"),
        ("moli", 1, "fail"),
        ("moli", 2, "pass"),
    ]
    shutil.rmtree(base)
    shutil.rmtree(correction)
    from harness.trajectory_replay.public_webarena.qualification import _verify_attempt_projection

    for row in rows:
        assert _verify_attempt_projection(row)
        predecessor = run / row["source_attempt_path"]
        assert hashlib.sha256(predecessor.read_bytes()).hexdigest() == row["source_attempt_sha256"]
    from bs4 import BeautifulSoup

    results = output / "reports/webarena-trajectory-replay-latest-results.html"
    page = BeautifulSoup(results.read_text(), "html.parser")
    assert "Synthetic task" in page.get_text()
    details = {
        link["href"].split("#")[0] for link in page.select("a[href]") if "-task-" in link["href"]
    }
    assert len(details) == 2
    assert all((results.parent / target).is_file() for target in details)


@pytest.mark.parametrize("damaged_history", [False, True])
def test_subsequent_supersession_verifies_and_retains_the_complete_prior_chain(
    tmp_path, damaged_history
):
    base, correction = write_supersession_fixture(tmp_path / "first")
    first = tmp_path / "derived-first"
    build_supersession(base, correction, first, {("case-a", "moli")}, reason="first repair")
    shutil.rmtree(base)
    shutil.rmtree(correction)
    _, correction = write_supersession_fixture(tmp_path / "second")
    original_index = json.loads((first / "run/source-inputs.json").read_bytes())
    old_file = first / "run" / original_index["files"][0]["retained_path"]
    second = tmp_path / "derived-second"
    if damaged_history:
        old_file.write_bytes(b"changed history")
        with pytest.raises(ValueError, match="historical source hash"):
            build_supersession(
                first / "run", correction, second, {("case-a", "moli")}, reason="second repair"
            )
        assert not second.exists()
    else:
        build_supersession(
            first / "run", correction, second, {("case-a", "moli")}, reason="second repair"
        )
        index = json.loads((second / "run/source-inputs.json").read_bytes())
        assert any(
            (second / "run" / row["retained_path"]).read_bytes() == old_file.read_bytes()
            for row in index["files"]
        )


@pytest.mark.parametrize(
    "damage",
    [
        "raw-row",
        "typed-raw-row",
        "screenshot",
        "missing-har",
        "linked-har",
        "protocol",
        "empty-reason",
        "scope",
    ],
)
def test_unbound_or_incomplete_correction_cannot_leave_outputs(tmp_path, damage):
    base, correction = write_supersession_fixture(tmp_path)
    attempt = correction / "tasks/case-a/moli/attempt-1"
    reason, scope = "synthetic repair", {("case-a", "moli")}
    if damage in {"raw-row", "typed-raw-row"}:
        path = attempt / "attempt.json"
        row = json.loads(path.read_bytes())
        if damage == "raw-row":
            row["status"] = "pass"
        else:
            row["is_retry"] = 0
        path.write_text(json.dumps(row))
    elif damage == "screenshot":
        (attempt / "final.png").write_bytes(b"changed")
    elif damage == "missing-har":
        (attempt / "network.har").unlink()
    elif damage == "linked-har":
        path = attempt / "network.har"
        target = tmp_path / "external.har"
        path.rename(target)
        path.symlink_to(target)
    elif damage == "protocol":
        path = correction / "manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["protocol"]["sample_interval_ms"] = 300
        path.write_text(json.dumps(manifest))
    elif damage == "empty-reason":
        reason = " "
    else:
        scope = {("other", "moli")}
    output = tmp_path / "derived"
    with pytest.raises(ValueError):
        build_supersession(base, correction, output, scope, reason=reason)
    assert not output.exists()
    assert not list(tmp_path.glob(".supersession-*"))


@pytest.mark.parametrize("failure", ["render", "source-drift", "concurrent-output"])
def test_failed_finalization_preserves_sources_and_existing_outputs(tmp_path, monkeypatch, failure):
    from harness.trajectory_replay.public_webarena import manifest_repair as owner

    base, correction = write_supersession_fixture(tmp_path)
    output = tmp_path / "derived"
    render = owner.write_latest_report

    def changing(directory, **kwargs):
        value = render(directory, **kwargs)
        if failure == "render":
            raise ValueError("synthetic render failure")
        if failure == "source-drift":
            (base / "attempts.jsonl").write_bytes(b"concurrent source change")
        else:
            output.mkdir()
            (output / "retained").write_bytes(b"other writer")
        return value

    monkeypatch.setattr(owner, "write_latest_report", changing)
    with pytest.raises(ValueError):
        build_supersession(
            base, correction, output, {("case-a", "moli")}, reason="synthetic repair"
        )
    if failure == "concurrent-output":
        assert (output / "retained").read_bytes() == b"other writer"
    else:
        assert not output.exists()
    assert not list(tmp_path.glob(".supersession-*"))


@pytest.mark.parametrize(
    "damage", ["duplicate-primary", "boolean-retry", "missing-contract", "empty-scope"]
)
def test_scope_cannot_accept_ambiguous_call_history(damage):
    base = [_row("a", "moli", 1, "fail")]
    correction = [_row("a", "moli", 1, "pass")]
    scope = {("a", "moli")}
    if damage == "duplicate-primary":
        base.append(copy.deepcopy(base[0]))
    elif damage == "boolean-retry":
        correction[0]["is_retry"] = 0
    elif damage == "missing-contract":
        base[0].pop("task_contract_sha256")
        correction[0].pop("task_contract_sha256")
    else:
        scope.clear()
    with pytest.raises(ValueError):
        supersede_rows(base, correction, scope)


def test_preparation_failure_is_retained_without_invented_page_evidence(tmp_path):
    base, correction = write_supersession_fixture(tmp_path)
    path = correction / "attempts.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    row = rows[0]
    directory = Path(row["evidence_directory"])
    for name in ("network.har", "final.png", "final-page.json"):
        (directory / name).unlink()
    row.update(
        {
            "process_status": "fail",
            "evidence_status": "fail",
            "error": "synthetic reset failure",
            "trace": [{"action": "prepare_environment", "status": "fail", "error": "reset failed"}],
            "validation": [],
            "network_har": {"status": "error", "reason": "environment_prepare_failed"},
            "final_page_evidence": {"status": "error", "reason": "environment_prepare_failed"},
            "process_validation": {
                "expected_actions": ["click"],
                "actual_actions": [],
                "all_calls_passed": False,
                "passed": False,
            },
        }
    )
    (directory / "attempt.json").write_text(json.dumps(row))
    raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
    path.write_bytes(raw)
    manifest_path = correction / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["artifact_provenance"]["attempts_sha256"] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    output = tmp_path / "derived"
    result = build_supersession(
        base, correction, output, {("case-a", "moli")}, reason="synthetic full rerun"
    )
    assert result["functional"]["moli"]["physical_attempts"] == 2
    derived = output / "run/tasks/case-a/moli/attempt-1"
    assert {path.name for path in derived.iterdir()} == {"attempt.json"}
    assert json.loads((derived / "attempt.json").read_bytes())["error"] == "synthetic reset failure"


def _row(task: str, engine: str, attempt: int, status: str, contract: str = "same"):
    return {
        "task_id": task,
        "engine": engine,
        "attempt": attempt,
        "is_retry": attempt == 2,
        "status": status,
        "task_contract_sha256": contract,
    }


def test_supersedes_the_complete_task_browser_scope():
    base = [
        _row("a", "chromium", 1, "pass"),
        _row("a", "moli", 1, "fail"),
        _row("a", "moli", 2, "fail"),
        _row("b", "chromium", 1, "pass"),
    ]
    correction = [_row("a", "moli", 1, "pass")]

    merged = supersede_rows(base, correction, {("a", "moli")})

    assert [(row["task_id"], row["engine"], row["attempt"]) for row in merged] == [
        ("a", "chromium", 1),
        ("a", "moli", 1),
        ("b", "chromium", 1),
    ]
    assert merged[1]["status"] == "pass"


def test_rejects_partial_or_best_attempt_corrections():
    base = [_row("a", "moli", 1, "fail"), _row("a", "moli", 2, "fail")]

    with pytest.raises(ValueError, match="attempt numbering"):
        supersede_rows(base, [_row("a", "moli", 2, "pass")], {("a", "moli")})

    with pytest.raises(ValueError, match="retried after a passing result"):
        supersede_rows(
            base,
            [_row("a", "moli", 1, "pass"), _row("a", "moli", 2, "pass")],
            {("a", "moli")},
        )


def test_rejects_contract_or_scope_drift():
    base = [_row("a", "moli", 1, "fail")]

    with pytest.raises(ValueError, match="scope mismatch"):
        supersede_rows(base, [_row("b", "moli", 1, "pass")], {("a", "moli")})

    with pytest.raises(ValueError, match="task contract changed"):
        supersede_rows(
            base,
            [_row("a", "moli", 1, "pass", contract="changed")],
            {("a", "moli")},
        )


def test_rejects_correction_run_binding_drift():
    base = {
        "suite_sha256": "suite",
        "protocol": {
            "sample_interval_ms": 100,
            "post_reset_settle_ms": 200,
            "task_timeout_ms": 300,
            "cpus_per_browser": 1,
            "memory_per_browser": "1g",
        },
        "environment": {
            "images": {"moli": {"id": "sha256:image"}},
            "browser_versions": {"moli": "1.0.6"},
        },
    }
    correction = {
        **base,
        "protocol": dict(base["protocol"]),
        "environment": {
            "images": {"moli": dict(base["environment"]["images"]["moli"])},
            "browser_versions": dict(base["environment"]["browser_versions"]),
        },
    }
    validate_correction_run_binding(base, correction, engine="moli")
    for field, value, message in (
        ("suite_sha256", "other", "suite differs"),
        ("protocol.task_timeout_ms", 301, "task_timeout_ms"),
        ("environment.images.moli.id", "sha256:other", "image differs"),
        ("environment.browser_versions.moli", "1.0.7", "version differs"),
    ):
        changed = {
            **correction,
            "protocol": dict(correction["protocol"]),
            "environment": {
                "images": {"moli": dict(correction["environment"]["images"]["moli"])},
                "browser_versions": dict(correction["environment"]["browser_versions"]),
            },
        }
        target = changed
        parts = field.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
        with pytest.raises(ValueError, match=message):
            validate_correction_run_binding(base, changed, engine="moli")
