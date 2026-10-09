"""Recorded-trace diagnostics obey the declared population and immutable sources."""

import hashlib
import json
import zipfile
from types import SimpleNamespace

import pytest

from harness.trajectory_replay.public_webarena import diagnostics as d


@pytest.fixture
def recording(tmp_path, monkeypatch):
    import webarena_verified

    traces = tmp_path / "traces"
    traces.mkdir()
    trace = traces / "1.trace.zip"
    network = (
        json.dumps(
            {
                "type": "resource-snapshot",
                "snapshot": {
                    "request": {"url": "http://recorded.test:7770/product", "method": "GET"}
                },
            }
        )
        + "\n"
    )
    with zipfile.ZipFile(trace, "w") as archive:
        archive.writestr("trace.network", network)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps({"count": 1, "traces": [{"task_id": 1, "file_name": "1.trace.zip"}]})
    )
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "webarena-1",
                        "trajectory": {
                            "provenance": {
                                "source_sha256": hashlib.sha256(trace.read_bytes()).hexdigest()
                            }
                        },
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(
        d,
        "_official_dataset",
        lambda: [
            {
                "task_id": 1,
                "sites": ["shopping"],
                "eval": [
                    {
                        "evaluator": "AgentResponseEvaluator",
                        "expected": {"data": "Synthetic answer"},
                    }
                ],
            }
        ],
    )
    calls = []

    class Evaluator:
        def __init__(self, *, config):
            assert json.loads(config.read_bytes())["environments"]["__SHOPPING__"]["urls"] == [
                "http://recorded.test:7770"
            ]

        def evaluate_task(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                model_dump=lambda **_: {"status": "success", "score": 1, "evaluators_results": []}
            )

    monkeypatch.setattr(webarena_verified, "WebArenaVerified", Evaluator)
    return catalog, traces, suite, trace, calls


def test_recording_population_comes_from_catalog_without_fixed_size(recording):
    catalog, traces, suite, trace, calls = recording
    originals = {path: path.read_bytes() for path in (catalog, suite, trace)}
    result = d.audit_recorded_outcomes(catalog, traces, suite)
    assert result["task_count"] == 1 and result["passing_task_ids"] == [1]
    assert result["current_environment_replay"] is False
    assert calls[0]["agent_response"] == {"data": "Synthetic answer"}
    assert calls[0]["network_trace"] == trace
    assert all(path.read_bytes() == raw for path, raw in originals.items())


@pytest.mark.parametrize("bad", ["count", "duplicate", "suite-duplicate", "hash", "escape", "link"])
def test_bad_recording_bindings_stop_before_evaluator_calls(recording, bad, tmp_path):
    catalog, traces, suite, trace, calls = recording
    if bad in {"count", "duplicate", "escape"}:
        data = json.loads(catalog.read_bytes())
        if bad == "count":
            data["count"] = 2
        elif bad == "duplicate":
            data["count"] = 2
            data["traces"] *= 2
        else:
            data["traces"][0]["file_name"] = "../outside.zip"
        catalog.write_text(json.dumps(data))
    elif bad == "suite-duplicate":
        data = json.loads(suite.read_bytes())
        data["tasks"] *= 2
        suite.write_text(json.dumps(data))
    elif bad == "hash":
        trace.write_bytes(b"Changed trace")
    else:
        outside = tmp_path / "outside.zip"
        trace.rename(outside)
        trace.symlink_to(outside)
    with pytest.raises(ValueError):
        d.audit_recorded_outcomes(catalog, traces, suite)
    assert not calls


def test_recording_rejects_trace_changes_during_evaluator_inspection(recording, monkeypatch):
    catalog, traces, suite, trace, calls = recording
    original = d._recorded_network_requests

    def change(path):
        requests = original(path)
        trace.write_bytes(b"Changed after observation")
        return requests

    monkeypatch.setattr(d, "_recorded_network_requests", change)
    with pytest.raises(ValueError, match="input changed"):
        d.audit_recorded_outcomes(catalog, traces, suite)
