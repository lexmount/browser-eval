import hashlib
import json
from pathlib import Path

from harness.trajectory_replay.webarena_dataset import build_verified_trajectory_dataset


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_promotion_requires_same_candidate_to_pass_every_browser(tmp_path: Path) -> None:
    seeds = _write(
        tmp_path / "seeds.json",
        {
            "schema": "lex_browser_use_replay_seeds/1",
            "source": {"selection_sha256": "selection"},
            "tasks": [
                {
                    "task_id": 44,
                    "intent": "Open todos",
                    "sites": ["gitlab"],
                    "start_urls": ["__GITLAB__"],
                    "candidates": [
                        {
                            "id": "candidate-44",
                            "method": {"model": "model", "engine": "chromium"},
                            "source_execution": 1,
                            "action_count": 1,
                            "tool_calls": [{"action": "navigate", "url": "http://host"}],
                            "recorded_response": {"status": "SUCCESS"},
                            "source": {
                                "events_sha256": "events",
                                "response_sha256": "response",
                                "evaluation_sha256": "evaluation",
                            },
                        }
                    ],
                }
            ],
        },
    )
    receipt = _write(
        tmp_path / "receipt.json",
        {
            "schema": "lex_webarena_trajectory_replay_canary/1",
            "source_seed_manifest_sha256": _sha256(seeds),
            "run_id": "run-44",
            "attempts": [
                {
                    "task_id": 44,
                    "source_candidate_id": "candidate-44",
                    "engine": engine,
                    "process_status": "pass",
                    "evaluator_status": "pass",
                    "completed_action_count": 1,
                }
                for engine in ("chromium", "moli")
            ],
        },
    )

    result = build_verified_trajectory_dataset(seeds, [receipt])

    assert result["counts"] == {"verified_tasks": 1, "verified_trajectories": 1}
    assert result["trajectories"][0]["verification"]["model_calls"] == 0
    assert result["trajectories"][0]["verification"]["contract"] == {
        "kind": "official_task_contract"
    }
    assert result["trajectories"][0]["verification"]["receipt_run_ids"] == ["run-44"]
    assert result["semantics"]["browser_use_agent_visibility"] == "hidden"


def test_promotion_skips_partial_browser_verification(tmp_path: Path) -> None:
    seeds = _write(
        tmp_path / "seeds.json",
        {
            "schema": "lex_browser_use_replay_seeds/1",
            "source": {"selection_sha256": "selection"},
            "tasks": [
                {
                    "task_id": 1,
                    "candidates": [
                        {
                            "id": "candidate",
                            "method": {"model": "model", "engine": "chromium"},
                            "source_execution": 1,
                            "action_count": 1,
                            "tool_calls": [{"action": "snapshot"}],
                            "recorded_response": {},
                            "source": {
                                "events_sha256": "events",
                                "response_sha256": "response",
                                "evaluation_sha256": "evaluation",
                            },
                        }
                    ],
                }
            ],
        },
    )
    receipt = _write(
        tmp_path / "receipt.json",
        {
            "schema": "lex_webarena_trajectory_replay_canary/1",
            "source_seed_manifest_sha256": _sha256(seeds),
            "attempts": [
                {
                    "task_id": 1,
                    "source_candidate_id": "candidate",
                    "engine": "chromium",
                    "process_status": "pass",
                    "evaluator_status": "pass",
                    "completed_action_count": 1,
                }
            ],
        },
    )

    result = build_verified_trajectory_dataset(seeds, [receipt])

    assert result["counts"] == {"verified_tasks": 0, "verified_trajectories": 0}


def test_promotion_supports_hash_bound_expert_repair(tmp_path: Path) -> None:
    seeds = _write(
        tmp_path / "seeds.json",
        {
            "schema": "lex_browser_use_replay_seeds/1",
            "source": {"selection_sha256": "selection"},
            "tasks": [
                {
                    "task_id": 339,
                    "candidates": [
                        {
                            "id": "expert-339",
                            "candidate_kind": "expert_repair",
                            "action_count": 1,
                            "tool_calls": [{"action": "navigate", "url": "http://host"}],
                            "recorded_response": {"status": "SUCCESS"},
                            "source": {
                                "kind": "expert_repair_spec",
                                "repair_spec_sha256": "repair-sha",
                                "details": {"author": "reviewed"},
                            },
                            "repair_basis": {"root_cause": "wrong route selection"},
                        }
                    ],
                }
            ],
        },
    )
    receipt = _write(
        tmp_path / "receipt.json",
        {
            "schema": "lex_webarena_trajectory_replay_canary/1",
            "source_seed_manifest_sha256": _sha256(seeds),
            "run_id": "run-339",
            "attempts": [
                {
                    "task_id": 339,
                    "source_candidate_id": "expert-339",
                    "engine": engine,
                    "process_status": "pass",
                    "evaluator_status": "pass",
                    "completed_action_count": 1,
                    "contract_correction_id": "wv-339-correction",
                    "contract_correction_source_sha256": "catalog-sha",
                    "corrected_task_sha256": "task-sha",
                }
                for engine in ("chromium", "moli")
            ],
        },
    )

    result = build_verified_trajectory_dataset(seeds, [receipt])

    trajectory = result["trajectories"][0]
    assert trajectory["source"]["kind"] == "expert_repair_spec"
    assert trajectory["source"]["repair_spec_sha256"] == "repair-sha"
    assert trajectory["verification"]["contract"] == {
        "kind": "corrected_replay_contract",
        "correction_id": "wv-339-correction",
        "correction_source_sha256": "catalog-sha",
        "corrected_task_sha256": "task-sha",
    }

    prior = _write(
        tmp_path / "prior.json",
        {
            "schema": "lex_webarena_verified_trajectory_dataset/1",
            "official_data_checksum": None,
            "trajectories": [{"id": "webarena-verified-44-trajectory-1", "task_id": 44}],
        },
    )
    incremented = build_verified_trajectory_dataset(
        seeds, [receipt], prior_dataset_path=prior
    )
    assert [item["task_id"] for item in incremented["trajectories"]] == [44, 339]
    assert incremented["counts"] == {"verified_tasks": 2, "verified_trajectories": 2}
    assert incremented["provenance"]["prior_dataset_sha256"] == _sha256(prior)
