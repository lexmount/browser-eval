import hashlib
import json
from types import SimpleNamespace

import pytest

from harness.trajectory_replay import diagnostics as d


@pytest.fixture
def saved_retrieval(tmp_path, monkeypatch):
    import webarena_verified.api

    from harness.trajectory_replay import remote_runtime

    task = tmp_path / "task-7"
    task.mkdir()
    names = {
        "actual_response": "actual_response.json",
        "network_har": "network.har",
        "trace": "trace.json",
        "acceptance": "acceptance.json",
        "evaluation": "evaluation.json",
    }
    for name in names.values():
        (task / name).write_text("{}")
    hashes = {
        key: hashlib.sha256((task / name).read_bytes()).hexdigest() for key, name in names.items()
    }
    result = {
        "task_id": 7,
        "action_count": 1,
        "completed_actions": 1,
        "status": "pass",
        "score": 1,
        "evidence_sha256": hashes,
    }
    (task / "result.json").write_text(json.dumps(result))
    sites, dataset = tmp_path / "sites.json", tmp_path / "new-dataset.json"
    sites.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_remote_sites/1",
                "sites": {
                    "reddit": "http://reddit.test",
                    "gitlab": "http://gitlab.test",
                    "shopping": "http://shopping.test",
                    "shopping_admin": "http://admin.test/admin",
                },
            }
        )
    )
    dataset.write_text("[]")
    receipt = {
        "site_map_sha256": hashlib.sha256(sites.read_bytes()).hexdigest(),
        "results": [result],
    }
    (tmp_path / "receipt.json").write_text(json.dumps(receipt))
    calls = []

    class Evaluator:
        def __init__(self, *, config):
            assert config.test_data_file == dataset

        def get_task(self, task_id):
            assert task_id == 7
            return SimpleNamespace(is_retrieve_task=True)

        def evaluate_task(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(model_dump=lambda **_: {"score": 0, "status": "failure"})

    monkeypatch.setattr(webarena_verified.api, "WebArenaVerified", Evaluator)
    monkeypatch.setattr(
        remote_runtime,
        "remote_benchmark",
        lambda _: SimpleNamespace(config=SimpleNamespace(environments={})),
    )
    return dict(task_dir=task, task_id=7, dataset=dataset, site_map=sites), calls


@pytest.mark.parametrize("layout", ["current", "legacy-parent", "receipt-only"])
def test_rescore_uses_saved_response_and_preserves_every_source(saved_retrieval, layout):
    inputs, calls = saved_retrieval
    task = inputs["task_dir"]
    if layout == "legacy-parent":
        (task / "result.json").rename(task.parent / "result.json")
    elif layout == "receipt-only":
        (task / "result.json").unlink()
    before = {path: path.read_bytes() for path in task.parent.rglob("*") if path.is_file()}
    result = d.rescore_saved_retrieval(**inputs)
    assert result["score"] == 0
    assert result["scope"] == "diagnostic_only"
    assert result["browser_calls"] == 0 and result["source_verdict_changed"] is False
    assert len(calls) == 1 and calls[0]["agent_response"] == {}
    assert calls[0]["network_trace"] == task / "network.har"
    assert all(path.read_bytes() == raw for path, raw in before.items())


@pytest.mark.parametrize(
    "damage",
    [
        "counts-missing",
        "counts-boolean",
        "incomplete",
        "wrong-task",
        "receipt-disagrees",
        "typed-receipt-disagrees",
        "sites",
        "trace",
        "linked-parent",
    ],
)
def test_invalid_saved_evidence_stops_before_evaluator(saved_retrieval, damage, tmp_path):
    inputs, calls = saved_retrieval
    task = inputs["task_dir"]
    result_path = task / "result.json"
    result = json.loads(result_path.read_bytes())
    if damage in {"counts-missing", "counts-boolean", "incomplete", "wrong-task"}:
        if damage == "counts-missing":
            result.pop("action_count")
            result.pop("completed_actions")
        elif damage == "counts-boolean":
            result["action_count"] = result["completed_actions"] = True
        elif damage == "incomplete":
            result["completed_actions"] = 0
        else:
            result["task_id"] = 8
        result_path.write_text(json.dumps(result))
        receipt_path = task.parent / "receipt.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["results"] = [result]
        receipt_path.write_text(json.dumps(receipt))
    elif damage in {"receipt-disagrees", "typed-receipt-disagrees"}:
        result["score"] = 0 if damage == "receipt-disagrees" else True
        result_path.write_text(json.dumps(result))
    elif damage == "sites":
        inputs["site_map"].write_text("{}")
    elif damage == "trace":
        (task / "trace.json").write_text("[]")
    else:
        alias = tmp_path / "linked"
        alias.symlink_to(task, target_is_directory=True)
        inputs["task_dir"] = alias
    with pytest.raises(ValueError):
        d.rescore_saved_retrieval(**inputs)
    assert not calls


@pytest.mark.parametrize("changed", ["dataset", "site_map", "evidence", "result"])
def test_source_change_during_evaluation_cannot_emit_diagnostic(
    saved_retrieval, monkeypatch, changed
):
    inputs, _ = saved_retrieval
    import webarena_verified.api

    targets = {
        **inputs,
        "evidence": inputs["task_dir"] / "actual_response.json",
        "result": inputs["task_dir"] / "result.json",
    }
    evaluator = webarena_verified.api.WebArenaVerified.evaluate_task

    def mutate(self, **kwargs):
        value = evaluator(self, **kwargs)
        targets[changed].write_text("changed")
        return value

    monkeypatch.setattr(webarena_verified.api.WebArenaVerified, "evaluate_task", mutate)
    with pytest.raises(ValueError, match="input changed"):
        d.rescore_saved_retrieval(**inputs)


def test_command_refuses_occupied_output_before_optional_evaluation(tmp_path):
    output = tmp_path / "diagnostic.json"
    output.write_bytes(b"retained")
    with pytest.raises(ValueError, match="already exists"):
        d.main_saved_retrieval_rescore(
            [
                "--task-dir",
                str(tmp_path / "missing"),
                "--task-id",
                "7",
                "--dataset",
                str(tmp_path / "missing"),
                "--site-map",
                str(tmp_path / "missing"),
                "--output",
                str(output),
            ]
        )
    assert output.read_bytes() == b"retained"
