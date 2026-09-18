import hashlib
import json

import pytest

from harness.trajectory_replay.webarena_report import (
    _corrected_acceptance_by_task,
    _task_page,
)


def _attempt(tmp_path, name, **changes):
    evaluation = {
        "task_id": 279,
        "contract_correction_id": "catalog-correction",
        "corrected_task_sha256": "a" * 64,
        "evaluators_results": [{"evaluator_name": "AgentResponseEvaluator",
                                "expected": {"retrieved_data": ["corrected item"]}}],
        **changes,
    }
    path = (tmp_path / name).resolve()
    path.write_text(json.dumps(evaluation))
    return {"task_id": 279, "evidence": {"evaluation": {
        "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }}}


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_corrected_contract_shown_separately_from_historical_dataset(tmp_path, lang):
    attempts = [_attempt(tmp_path, "chromium.json"), _attempt(tmp_path, "moli.json")]
    corrected = _corrected_acceptance_by_task(attempts)
    task = {"task_id": 279, "task": {"intent": "Find products"},
            "evaluation_input": {"task_type": "RETRIEVE", "retrieved_data": ["old item"]}}
    html = _task_page(lang, task, [], corrected_acceptance=corrected[279])
    assert "corrected item" in html and "old item" in html
    assert html.index("corrected item") < html.index("old item")
    assert "data-corrected-acceptance" in html
    assert task["evaluation_input"]["retrieved_data"] == ["old item"]
    assert ("历史数据集验收输入" if lang == "zh" else "Historical dataset evaluation input") in html


@pytest.mark.parametrize("changes", [
    {"task_id": 280}, {"corrected_task_sha256": None}, {"evaluators_results": []},
])
def test_corrected_contract_requires_complete_bound_evidence(tmp_path, changes):
    with pytest.raises(ValueError):
        _corrected_acceptance_by_task([_attempt(tmp_path, "bad.json", **changes)])


def test_corrected_contract_rejects_cross_method_difference(tmp_path):
    attempts = [_attempt(tmp_path, "one.json"),
                _attempt(tmp_path, "two.json", corrected_task_sha256="b" * 64)]
    with pytest.raises(ValueError, match="differs across methods"):
        _corrected_acceptance_by_task(attempts)


def test_corrected_contract_rejects_changed_artifact(tmp_path):
    attempt = _attempt(tmp_path, "changed.json")
    (tmp_path / "changed.json").write_text("{}")
    with pytest.raises(ValueError, match="missing or changed"):
        _corrected_acceptance_by_task([attempt])


def test_uncorrected_and_unexecuted_evaluation_do_not_invent_corrections(tmp_path):
    assert _corrected_acceptance_by_task([
        _attempt(tmp_path, "original.json", contract_correction_id=None),
        {"task_id": 279, "evidence": {}},
    ]) == {}
