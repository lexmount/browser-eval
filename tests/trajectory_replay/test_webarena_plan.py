from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.webarena_plan import (
    load_replay_plan,
    replay_calls_binding_sha256,
)


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _profile(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    selection = _write(
        repo / "evaluation/browser-use/selection.json",
        {"tasks": [{"task_id": 1}, {"task_id": 2}]},
    )
    repairs = _write(
        repo / "evaluation/trajectory-replay/repairs.json",
        {
            "schema": "repairs",
            "repairs": [
                {
                    "task_id": task_id,
                    "candidate_id": f"candidate-{task_id}",
                    "source": {"kind": "fixture"},
                    "repair_basis": {"root_cause": "fixture"},
                    "tool_calls": [{"action": "snapshot"}],
                    "expected_response": {"status": "SUCCESS"},
                }
                for task_id in (1, 2)
            ],
        },
    )
    profile = {
        "schema": "lex_webarena_trajectory_replay_profile/1",
        "task_selection": {
            "path": "evaluation/browser-use/selection.json",
            "sha256": hashlib.sha256(selection.read_bytes()).hexdigest(),
            "task_count": 2,
        },
        "seed_inventory": {
            "expert_repairs_path": "evaluation/trajectory-replay/repairs.json",
            "expert_repairs_sha256": hashlib.sha256(repairs.read_bytes()).hexdigest(),
            "required_ready_tasks": 2,
            "canonical_trajectories_per_task": 1,
        },
        "methods": [
            {"id": "chromium", "engine": "chromium"},
            {"id": "moli", "engine": "moli"},
        ],
        "qualification": {"reference_engine": "chromium", "required_task_count": 2},
        "execution": {
            "environment_pool_count": 1,
            "method_parallelism": 1,
            "task_parallelism": 1,
            "reset_before_every_task": True,
            "teardown_between_methods": True,
            "trusted_source_site_hosts": ["host.docker.internal"],
        },
        "runs_per_task": 1,
        "scoring": {"model_calls": 0},
    }
    profile_path = _write(
        repo / "evaluation/trajectory-replay/webarena-verified/current-run.json", profile
    )
    seeds = _write(
        tmp_path / "seeds.json",
        {
            "schema": "lex_browser_use_replay_seeds/2",
            "source": {"selection_sha256": profile["task_selection"]["sha256"]},
            "expert_repairs": {
                "repair_spec_sha256": profile["seed_inventory"]["expert_repairs_sha256"]
            },
            "tasks": [
                {
                    "task_id": task_id,
                    "replay_status": "ready",
                    "canonical_candidate_id": f"candidate-{task_id}",
                    "candidates": [
                        {
                            "id": f"candidate-{task_id}",
                            "candidate_kind": "expert_repair",
                            "action_count": 1,
                            "tool_calls": [{"action": "snapshot"}],
                            "recorded_response": {"status": "SUCCESS"},
                            "source": {
                                "kind": "expert_repair_spec",
                                "repair_spec_sha256": profile["seed_inventory"][
                                    "expert_repairs_sha256"
                                ],
                                "details": {"kind": "fixture"},
                            },
                            "repair_basis": {"root_cause": "fixture"},
                        }
                    ],
                }
                for task_id in (1, 2)
            ],
        },
    )
    return profile_path, seeds


def test_plan_binds_all_tasks_and_serial_methods(tmp_path: Path) -> None:
    profile, seeds = _profile(tmp_path)

    plan = load_replay_plan(profile, seeds)

    assert plan["task_ids"] == [1, 2]
    assert plan["engines"] == ["chromium", "moli"]
    assert len(plan["profile_sha256"]) == len(plan["seed_manifest_sha256"]) == 64


@pytest.mark.parametrize("supersession", [None, "wrong", "correct"])
def test_plan_state_opt_in_requires_exact_legacy_supersession(tmp_path, monkeypatch, supersession):
    from types import SimpleNamespace

    from harness import webarena_state
    from harness.trajectory_replay import webarena_corrections

    profile, seeds = _profile(tmp_path)
    assert load_replay_plan(profile, seeds)["state_contracts"] == {}
    definition = {}
    if supersession:
        definition["supersedes_replay_correction"] = {
            "correction_id": "old-rule",
            "record_sha256": "old-hash" if supersession == "correct" else "wrong",
        }
    contract = SimpleNamespace(definition=definition)
    monkeypatch.setattr(webarena_state, "load_profile_contracts", lambda *_: {1: contract})
    monkeypatch.setattr(
        webarena_corrections,
        "load_corrections",
        lambda: SimpleNamespace(
            records={1: {"correction_id": "old-rule"}}, record_sha256s={1: "old-hash"}
        ),
    )
    payload = json.loads(profile.read_text())
    payload["scoring"]["observed_state"] = {"explicit_opt_in": True}
    _write(profile, payload)
    if supersession == "correct":
        assert load_replay_plan(profile, seeds)["state_contracts"] == {1: contract}
    else:
        with pytest.raises(ValueError, match="explicitly supersede"):
            load_replay_plan(profile, seeds)


def test_plan_uses_frozen_order_not_seed_file_order(tmp_path: Path) -> None:
    profile, seeds = _profile(tmp_path)
    payload = json.loads(seeds.read_text(encoding="utf-8"))
    payload["tasks"].reverse()
    seeds.write_text(json.dumps(payload), encoding="utf-8")

    plan = load_replay_plan(profile, seeds)

    assert plan["task_ids"] == [1, 2]


@pytest.mark.parametrize("field", ["method_parallelism", "task_parallelism"])
def test_plan_rejects_parallel_work_on_one_environment(tmp_path: Path, field: str) -> None:
    profile, seeds = _profile(tmp_path)
    payload = json.loads(profile.read_text(encoding="utf-8"))
    payload["execution"][field] = 2
    profile.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="serial methods and serial tasks"):
        load_replay_plan(profile, seeds)


def test_plan_rejects_incomplete_seed_coverage(tmp_path: Path) -> None:
    profile, seeds = _profile(tmp_path)
    payload = json.loads(seeds.read_text(encoding="utf-8"))
    payload["tasks"][1]["replay_status"] = "all_methods_failed"
    payload["tasks"][1]["canonical_candidate_id"] = None
    seeds.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="required ready-task coverage"):
        load_replay_plan(profile, seeds)


def test_plan_rejects_changed_task_selection(tmp_path: Path) -> None:
    profile, seeds = _profile(tmp_path)
    selection = profile.parents[2] / "browser-use/selection.json"
    changed = copy.deepcopy(json.loads(selection.read_text(encoding="utf-8")))
    changed["tasks"].append({"task_id": 3})
    selection.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match the frozen profile"):
        load_replay_plan(profile, seeds)


@pytest.mark.parametrize(
    ("section", "field", "message"),
    [
        ("source", "selection_sha256", "frozen task selection"),
        ("expert_repairs", "repair_spec_sha256", "frozen expert repairs"),
    ],
)
def test_plan_rejects_unbound_seed_sources(
    tmp_path: Path, section: str, field: str, message: str
) -> None:
    profile, seeds = _profile(tmp_path)
    payload = json.loads(seeds.read_text(encoding="utf-8"))
    payload[section][field] = "0" * 64
    seeds.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_replay_plan(profile, seeds)


def test_plan_rejects_expert_candidate_changed_after_review(tmp_path: Path) -> None:
    profile, seeds = _profile(tmp_path)
    payload = json.loads(seeds.read_text(encoding="utf-8"))
    payload["tasks"][0]["candidates"][0]["tool_calls"] = [
        {"action": "navigate", "url": "https://example.com"}
    ]
    seeds.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="repair spec"):
        load_replay_plan(profile, seeds)


def test_plan_rejects_ephemeral_refs_in_any_canonical_trajectory(tmp_path: Path) -> None:
    profile, seeds = _profile(tmp_path)
    payload = json.loads(seeds.read_text(encoding="utf-8"))
    payload["tasks"][0]["candidates"][0]["tool_calls"] = [
        {"action": "snapshot", "metadata": {"ref": 3}}
    ]
    seeds.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="ephemeral ref"):
        load_replay_plan(profile, seeds)


def test_replay_binding_does_not_normalize_an_unapproved_host() -> None:
    expected = [{"action": "navigate", "url": "http://host.docker.internal:8023/path"}]
    attacker = [{"action": "navigate", "url": "http://attacker.invalid:8023/path"}]

    assert replay_calls_binding_sha256(
        expected, allowed_hosts={"host.docker.internal"}
    ) != replay_calls_binding_sha256(attacker, allowed_hosts={"host.docker.internal"})
