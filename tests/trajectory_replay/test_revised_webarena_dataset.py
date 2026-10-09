from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.trajectory_replay.webarena_corrections import revise_dataset as revise

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "evaluation/trajectory-replay/webarena-verified/trajectories.json"
CORRECTIONS = ROOT / "evaluation/trajectory-replay/webarena-verified/task-corrections.json"
REVISED = ROOT / "evaluation/trajectory-replay/webarena-verified/trajectories-corrected.json"
SELECTION = ROOT / "evaluation/trajectory-replay/webarena-verified/selection.json"
FROZEN_CORRECTIONS = CORRECTIONS.parent / "dataset-67-225-243-corrections.json"


def test_revised_dataset_is_complete_bound_and_reproducible() -> None:
    result = revise(
        BASE,
        FROZEN_CORRECTIONS,
        [67, 225, 243],
        SELECTION,
        require_current_upstream=False,
    )
    committed = json.loads(REVISED.read_text(encoding="utf-8"))
    original = json.loads(BASE.read_text(encoding="utf-8"))
    assert result == committed
    assert len(result["trajectories"]) == len(original["trajectories"]) == 90
    assert result["selection_sha256"] == hashlib.sha256(SELECTION.read_bytes()).hexdigest()
    assert (
        result["revision"]["base_dataset_sha256"] == hashlib.sha256(BASE.read_bytes()).hexdigest()
    )
    assert (
        result["revision"]["correction_catalog_sha256"]
        == hashlib.sha256(FROZEN_CORRECTIONS.read_bytes()).hexdigest()
    )
    changed = {item["task_id"] for item in result["revision"]["changed_tasks"]}
    assert changed == {67, 225, 243}
    old_rows = {item["task_id"]: item for item in original["trajectories"]}
    for row in result["trajectories"]:
        if row["task_id"] not in changed:
            assert row == old_rows[row["task_id"]]
            continue
        if row["task_id"] == 67:
            assert row["evaluation_input"]["retrieved_data"] == ["The Hobbit"]
            assert len(row["tool_calls"]) == 4
            assert row["source"]["kind"] == "corrected_retrieval_candidate_pending_requalification"
        elif row["task_id"] == 243:
            assert "nickname on the unique lowest-rated review" in row["task"]["intent"]
            assert row["evaluation_input"]["retrieved_data"] == ["Hannah Lim"]
            assert row["evaluation_input"] != old_rows[243]["evaluation_input"]
            assert row["source"]["kind"] == "corrected_retrieval_candidate_pending_requalification"
            assert all("customer/index" not in json.dumps(call) for call in row["tool_calls"])
            assert row["tool_calls"][-1]["action"] == "evaluate"
        else:
            assert "titles contain both Sephora and Brush" in row["task"]["intent"]
            assert row["evaluation_input"] == old_rows[225]["evaluation_input"]
        assert row["verification"]["process_status"] == "pending_requalification"


def test_unrelated_catalog_updates_preserve_frozen_task_contracts() -> None:
    historical = revise(
        BASE,
        FROZEN_CORRECTIONS,
        [67, 225, 243],
        SELECTION,
        require_current_upstream=False,
    )
    current = revise(BASE, CORRECTIONS, [67, 225, 243], SELECTION)
    assert (
        current["revision"]["correction_catalog_sha256"]
        == hashlib.sha256(CORRECTIONS.read_bytes()).hexdigest()
    )
    assert (
        current["revision"]["correction_catalog_sha256"]
        != historical["revision"]["correction_catalog_sha256"]
    )
    current["revision"].pop("correction_catalog_sha256")
    historical["revision"].pop("correction_catalog_sha256")
    assert current == historical


def test_revised_dataset_rejects_missing_or_repeated_scope() -> None:
    with pytest.raises(ValueError, match="nonempty and unique"):
        revise(BASE, CORRECTIONS, [])
    with pytest.raises(ValueError, match="nonempty and unique"):
        revise(BASE, CORRECTIONS, [225, 225])
    with pytest.raises(ValueError, match="revision task not in"):
        revise(BASE, CORRECTIONS, [999999])


def test_revision_uses_declared_population_instead_of_historical_size(tmp_path: Path) -> None:
    source = json.loads(BASE.read_bytes())
    source["trajectories"] = [row for row in source["trajectories"] if row["task_id"] == 225]
    source["counts"] = {"verified_tasks": 1, "verified_trajectories": 1}
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source))
    result = revise(path, CORRECTIONS, [225])
    assert result["counts"] == source["counts"]
    assert [row["task_id"] for row in result["trajectories"]] == [225]
    assert result["revision"]["changed_tasks"][0]["task_id"] == 225
    assert result["trajectories"][0]["verification"]["process_status"] == "pending_requalification"


@pytest.mark.parametrize("count", [0, 2, True])
def test_revision_rejects_inconsistent_declared_population(tmp_path: Path, count) -> None:
    source = json.loads(BASE.read_bytes())
    source["trajectories"] = [row for row in source["trajectories"] if row["task_id"] == 225]
    source["counts"] = {"verified_tasks": count, "verified_trajectories": 1}
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="population"):
        revise(path, CORRECTIONS, [225])


@pytest.mark.parametrize(
    "field", ["task_sha256", "source_trajectory_sha256", "candidate", "reference"]
)
def test_bound_revision_rejects_changed_authority(monkeypatch, field) -> None:
    from harness.trajectory_replay import webarena_corrections as owner

    bindings = owner._load_dataset_revision_bindings()
    if field in ("candidate", "reference"):
        bindings[67][field]["sha256"] = "0" * 64
    else:
        bindings[67][field] = "0" * 64
    monkeypatch.setattr(owner, "_load_dataset_revision_bindings", lambda path=None: bindings)
    with pytest.raises(ValueError, match="authority differs|artifact hash mismatch"):
        revise(BASE, CORRECTIONS, [67])


def test_explicit_revision_bindings_do_not_require_historical_replacements(tmp_path: Path) -> None:
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps({"schema": "webarena_dataset_revision_bindings/1", "revisions": []}))
    result = revise(BASE, CORRECTIONS, [225], revision_bindings_path=path)
    assert [row["task_id"] for row in result["revision"]["changed_tasks"]] == [225]


@pytest.mark.parametrize("damage", ["duplicate", "invalid-hash", "escape"])
def test_revision_bindings_reject_invalid_records(tmp_path: Path, damage) -> None:
    from harness.trajectory_replay.webarena_corrections import _load_dataset_revision_bindings

    rows = list(_load_dataset_revision_bindings().values())
    if damage == "duplicate":
        rows.append(rows[0])
    elif damage == "invalid-hash":
        rows[0]["task_sha256"] = "not-a-hash"
    else:
        rows[0]["candidate"]["path"] = "../outside.json"
    path = tmp_path / "bindings.json"
    path.write_text(
        json.dumps({"schema": "webarena_dataset_revision_bindings/1", "revisions": rows})
    )
    with pytest.raises(ValueError):
        _load_dataset_revision_bindings(path)


def test_revision_command_preserves_sources_and_rejects_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "revision.json"
    before = {path: path.read_bytes() for path in (BASE, CORRECTIONS)}
    command = [
        sys.executable,
        str(ROOT / "scripts/build_revised_webarena_dataset.py"),
        "--base",
        str(BASE),
        "--corrections",
        str(CORRECTIONS),
        "--task-id",
        "225",
        "--output",
        str(output),
    ]
    subprocess.run(command, check=True, capture_output=True, cwd=tmp_path)
    assert json.loads(output.read_bytes()) == revise(BASE, CORRECTIONS, [225])
    saved = output.read_bytes()
    assert subprocess.run(command, capture_output=True, cwd=tmp_path).returncode != 0
    assert output.read_bytes() == saved
    assert all(path.read_bytes() == raw for path, raw in before.items())
    subprocess.run(command + ["--replace-generated"], check=True, capture_output=True, cwd=tmp_path)
    assert output.read_bytes() == saved


def test_revision_rejects_source_change_during_build(tmp_path: Path, monkeypatch) -> None:
    from harness.environments.webarena import runtime

    source = tmp_path / "source.json"
    source.write_bytes(BASE.read_bytes())
    benchmark = runtime.configure_benchmark()

    def changed_source():
        value = json.loads(source.read_bytes())
        value["unexpected_change"] = True
        source.write_text(json.dumps(value))
        return benchmark

    monkeypatch.setattr(runtime, "configure_benchmark", changed_source)
    with pytest.raises(ValueError, match="source changed during build"):
        revise(source, CORRECTIONS, [225])


@pytest.mark.parametrize(
    ("answer", "score"),
    [(["Hannah Lim"], 1.0), (["hannah.lim@gmail.com"], 0.0)],
)
def test_task243_official_evaluator_accepts_review_nickname_not_old_email(
    tmp_path: Path, answer: list[str], score: float
) -> None:
    from importlib.resources import files

    from webarena_verified.core.evaluation.evaluators.agent_response_evaluator import (
        AgentResponseEvaluator,
    )
    from webarena_verified.types.config import WebArenaVerifiedConfig
    from webarena_verified.types.eval import TaskEvalContext
    from webarena_verified.types.task import WebArenaVerifiedTask
    from webarena_verified.types.tracing import NetworkTrace

    tasks = json.loads(
        files("webarena_verified").joinpath("assets/dataset/webarena-verified.json").read_text()
    )
    task = WebArenaVerifiedTask.model_validate(next(row for row in tasks if row["task_id"] == 243))
    context = TaskEvalContext(
        task=task,
        agent_response_raw={
            "task_type": "RETRIEVE",
            "status": "SUCCESS",
            "retrieved_data": answer,
            "error_details": None,
        },
        network_trace=NetworkTrace(
            is_playwright=False, src_file=tmp_path / "unused.har", events=()
        ),
        config=WebArenaVerifiedConfig(),
    )
    assert AgentResponseEvaluator().evaluate(context=context, config=task.eval[0]).score == score
