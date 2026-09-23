import hashlib
import json
from pathlib import Path

import pytest

from scripts.audit_webarena_full_runs import build_ledger


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return path


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    scope = _write(
        tmp_path / "scope.json",
        {"schema": "lex_webarena_verified_full_scope/1", "task_ids": [0, 1]},
    )
    inherited_evaluation = _write(tmp_path / "inherited-evaluation.json", {"score": 1})
    inherited = _write(
        tmp_path / "inherited.json",
        {
            "attempts": [
                {
                    "task_id": 0,
                    "engine": "chromium",
                    "status": "pass",
                    "evidence": {
                        "evaluation": {
                            "path": str(inherited_evaluation),
                            "sha256": _hash(inherited_evaluation),
                        }
                    },
                }
            ]
        },
    )
    queue = _write(
        tmp_path / "queue.json",
        {
            "scope_sha256": _hash(scope),
            "inherited_results_sha256": _hash(inherited),
            "rows": [
                {"task_id": 0, "status": "pass", "evaluation_sha256": _hash(inherited_evaluation)},
                {"task_id": 1, "status": "pending_first_sweep"},
            ],
        },
    )
    sweep = tmp_path / "sweep-1"
    task = sweep / "task-1"
    evaluation = _write(task / "evaluation.json", {"score": 1, "replay_assertions": []})
    response = _write(task / "actual_response.json", {"status": "SUCCESS"})
    trace = _write(task / "trace.json", [{"status": "pass"}])
    har = _write(task / "network.har", {"log": {"entries": []}})
    _write(
        sweep / "progress.jsonl",
        {},
    ).write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"task_id": 0, "status": "pass"},
                {
                    "task_id": 1,
                    "status": "pass",
                    "score": 1,
                    "action_count": 1,
                    "evidence_sha256": {
                        "evaluation": _hash(evaluation),
                        "actual_response": _hash(response),
                        "trace": _hash(trace),
                        "network_har": _hash(har),
                    },
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return scope, queue, inherited, sweep


def test_ledger_accepts_one_evaluated_pass_and_one_inherited_pass(tmp_path: Path) -> None:
    scope, queue, inherited, sweep = _fixture(tmp_path)
    ledger = build_ledger(scope, queue, inherited, [sweep])
    assert ledger["counts"] == {
        "total": 2,
        "pass": 2,
        "unresolved": 0,
        "physical_attempts": 1,
    }
    assert ledger["rows"][0]["pass_source"] == "independent_90_case_split"


def test_ledger_rejects_a_second_physical_attempt_after_pass(tmp_path: Path) -> None:
    scope, queue, inherited, sweep = _fixture(tmp_path)
    second = tmp_path / "sweep-2"
    second.mkdir()
    (second / "progress.jsonl").write_text(
        json.dumps({"task_id": 0, "status": "pass"})
        + "\n"
        + json.dumps(
            {
                "task_id": 1,
                "status": "fail",
                "action_count": 1,
                "evidence_sha256": {"trace": "ignored"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="repeated after its first pass"):
        build_ledger(scope, queue, inherited, [sweep, second])


def test_ledger_rejects_changed_evaluation_evidence(tmp_path: Path) -> None:
    scope, queue, inherited, sweep = _fixture(tmp_path)
    (sweep / "task-1" / "evaluation.json").write_text('{"score":0}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="evidence hash changed"):
        build_ledger(scope, queue, inherited, [sweep])


def test_ledger_verifies_filename_keyed_first_sweep_evidence(tmp_path: Path) -> None:
    scope, queue, inherited, sweep = _fixture(tmp_path)
    task_dir = sweep / "task-1"
    (task_dir / "actual_response.json").rename(task_dir / "response.json")
    progress_path = sweep / "progress.jsonl"
    rows = [json.loads(line) for line in progress_path.read_text(encoding="utf-8").splitlines()]
    rows[1]["evidence_sha256"] = {
        name: _hash(task_dir / name)
        for name in ("evaluation.json", "network.har", "response.json", "trace.json")
    }
    progress_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    assert build_ledger(scope, queue, inherited, [sweep])["counts"]["pass"] == 2


def test_ledger_accepts_bound_offline_rescore_without_new_physical_attempt(
    tmp_path: Path,
) -> None:
    scope, queue, inherited, sweep = _fixture(tmp_path)
    progress_path = sweep / "progress.jsonl"
    rows = [json.loads(line) for line in progress_path.read_text().splitlines()]
    rows[1]["status"] = "fail"
    rows[1]["score"] = 0
    progress_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    source_evidence = rows[1]["evidence_sha256"]
    rescore = tmp_path / "offline-rescore"
    evaluation = _write(
        rescore / "task-1" / "evaluation.json",
        {
            "score": 1,
            "contract_correction_id": "wv-1-proven-correction",
            "corrected_task_sha256": "a" * 64,
        },
    )
    _write(
        rescore / "rescoring.json",
        {
            "schema": "lex_webarena_verified_offline_rescore/1",
            "rows": [
                {
                    "task_id": 1,
                    "source_evidence_sha256": source_evidence,
                    "evaluation_sha256": _hash(evaluation),
                }
            ],
        },
    )
    ledger = build_ledger(scope, queue, inherited, [sweep], [rescore])
    assert ledger["counts"] == {
        "total": 2,
        "pass": 2,
        "unresolved": 0,
        "physical_attempts": 1,
    }
    assert ledger["rows"][1]["pass_source"] == "offline_rescore"


def test_ledger_accepts_runtime_corrected_offline_rescore(tmp_path: Path) -> None:
    scope, queue, inherited, sweep = _fixture(tmp_path)
    progress_path = sweep / "progress.jsonl"
    rows = [json.loads(line) for line in progress_path.read_text().splitlines()]
    rows[1]["status"] = "fail"
    rows[1]["score"] = 0
    progress_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    rescore = tmp_path / "runtime-rescore"
    evaluation = _write(
        rescore / "task-1" / "evaluation.json",
        {
            "score": 1,
            "runtime_correction_id": "remote-same-host-transport-v1",
            "corrected_runtime_sha256": "b" * 64,
        },
    )
    _write(
        rescore / "rescoring.json",
        {
            "schema": "lex_webarena_verified_offline_rescore/1",
            "rows": [
                {
                    "task_id": 1,
                    "source_evidence_sha256": rows[1]["evidence_sha256"],
                    "evaluation_sha256": _hash(evaluation),
                }
            ],
        },
    )
    ledger = build_ledger(scope, queue, inherited, [sweep], [rescore])
    assert ledger["counts"]["pass"] == 2
    assert ledger["rows"][1]["offline_rescore"] == {
        "source_evidence_sha256": rows[1]["evidence_sha256"],
        "evaluation_sha256": _hash(evaluation),
        "runtime_correction_id": "remote-same-host-transport-v1",
        "corrected_runtime_sha256": "b" * 64,
    }
