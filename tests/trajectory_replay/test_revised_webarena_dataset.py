from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_revised_webarena_dataset import revise

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
    assert current["revision"]["correction_catalog_sha256"] == hashlib.sha256(
        CORRECTIONS.read_bytes()
    ).hexdigest()
    assert current["revision"]["correction_catalog_sha256"] != historical["revision"][
        "correction_catalog_sha256"
    ]
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
