import hashlib
import json

import pytest

from harness.trajectory_replay import catalog_acceptance as acceptance


def _run(
    tmp_path,
    name,
    ids,
    statuses,
    *,
    preparation="same-reset",
    backend="browsertool",
    catalog="catalog",
    schema="lex_webarena_full_replay_run/1",
    engine=None,
):
    directory = tmp_path / name
    directory.mkdir()
    inputs = {
        "schema": schema,
        "execution_order": ids,
        "run_count_per_task": 1,
        "site_reset_receipt_sha256": preparation,
        "binary_sha256": "same-chromium",
        "catalog_sha256": catalog,
        "portable_suite_sha256": "suite",
        "site_map_sha256": "sites",
        "backend": backend,
        "runner_revision": "revision",
        "cowagent_revision": "cowagent",
    }
    if schema.endswith("/2"):
        inputs.update(
            {
                "engine": engine or "chromium",
                "action_timeout_ms": 30_000,
                "environment_profile_sha256": "environment",
                "corrections_sha256": "corrections",
                "evaluator_fixes_sha256": "fixes",
            }
        )
    (directory / "inputs.json").write_text(json.dumps(inputs))
    (directory / "progress.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "task_id": task_id,
                    "status": statuses.get(task_id, "pass"),
                    "score": 1,
                    "action_count": 1,
                }
            )
            + "\n"
            for task_id in ids
        )
    )
    return directory


def _scope(tmp_path):
    path = tmp_path / "scope.json"
    path.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_verified_full_scope/1",
                "official_dataset_sha256": "official-data",
                "task_ids": list(range(812)),
            }
        )
    )
    return path


def _fake_verified(directory, row, **_kwargs):
    return (dict(row, progress_sha256="progress", evidence_sha256={}), True)


def test_acceptance_keeps_one_ordered_sweep_and_only_repairs_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "_verified_attempt", _fake_verified)
    scope = _scope(tmp_path)
    early = _run(tmp_path, "early", list(range(400)), {66: "fail"})
    late = _run(tmp_path, "late", list(range(400, 812)), {})
    repair = _run(tmp_path, "repair", [66], {})

    ledger = acceptance.audit(scope, [early, late], [repair])

    assert ledger["counts"] == {
        "total": 812,
        "pass": 812,
        "unresolved": 0,
        "physical_attempts": 813,
        "excluded_duplicate_attempts": 0,
    }
    assert [attempt["status"] for attempt in ledger["rows"][66]["attempts"]] == [
        "fail",
        "pass",
    ]
    assert len(ledger["rows"][67]["attempts"]) == 1


def test_acceptance_binds_repair_on_a_fresh_site_without_repeating_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "_verified_attempt", _fake_verified)
    scope = _scope(tmp_path)
    sweep = _run(tmp_path, "sweep", list(range(812)), {496: "fail"})
    repair = _run(tmp_path, "repair", [496], {}, preparation="spare-slice")

    ledger = acceptance.audit(scope, [sweep], [repair])

    assert ledger["counts"]["pass"] == 812
    assert ledger["site_preparation_receipt_sha256s"] == ["same-reset", "spare-slice"]
    assert ledger["runs"][1]["site_reset_receipt_sha256"] == "spare-slice"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"preparation": "other"}, "ordered sweep changed its execution contract"),
        ({"catalog": "other"}, "ordered sweep changed its execution contract"),
        ({"backend": "playwright"}, "different execution backend"),
    ],
)
def test_acceptance_rejects_mixed_sweep_contracts(tmp_path, monkeypatch, change, message):
    monkeypatch.setattr(acceptance, "_verified_attempt", _fake_verified)
    scope = _scope(tmp_path)
    early = _run(tmp_path, "early", list(range(400)), {})
    late = _run(tmp_path, "late", list(range(400, 812)), {}, **change)

    with pytest.raises(ValueError, match=message):
        acceptance.audit(scope, [early, late], [])


def test_acceptance_rejects_rerunning_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "_verified_attempt", _fake_verified)
    scope = _scope(tmp_path)
    sweep = _run(tmp_path, "sweep", list(range(812)), {})
    repeated = _run(tmp_path, "repeated", [66], {})

    with pytest.raises(ValueError, match="repair does not follow a failure"):
        acceptance.audit(scope, [sweep], [repeated])


def test_acceptance_records_an_explicit_duplicate_without_replacing_first_pass(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(acceptance, "_verified_attempt", _fake_verified)
    scope = _scope(tmp_path)
    sweep = _run(tmp_path, "sweep", list(range(812)), {500: "fail"})
    repair = _run(tmp_path, "repair", [499, 500], {})

    ledger = acceptance.audit(scope, [sweep], [repair], excluded_duplicate_repair_tasks={499})

    assert len(ledger["rows"][499]["attempts"]) == 1
    assert [row["status"] for row in ledger["rows"][500]["attempts"]] == [
        "fail",
        "pass",
    ]
    assert ledger["counts"]["physical_attempts"] == 814
    assert ledger["excluded_attempts"][0]["task_id"] == 499


def test_acceptance_rejects_an_order_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "_verified_attempt", _fake_verified)
    scope = _scope(tmp_path)
    gap = _run(tmp_path, "gap", [task_id for task_id in range(812) if task_id != 500], {})

    with pytest.raises(ValueError, match="incomplete or changed official order"):
        acceptance.audit(scope, [gap], [])


def test_acceptance_rejects_tampered_failure_evidence(tmp_path):
    run = tmp_path / "run"
    task = run / "task-7"
    task.mkdir(parents=True)
    trace = task / "trace.json"
    trace.write_text("[]\n", encoding="utf-8")
    row = {
        "task_id": 7,
        "status": "fail",
        "score": 0,
        "action_count": 1,
        "evidence_sha256": {"trace": hashlib.sha256(trace.read_bytes()).hexdigest()},
    }
    (run / "progress.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    trace.write_text('[{"changed":true}]\n', encoding="utf-8")

    with pytest.raises(ValueError, match="evidence hash changed"):
        acceptance._verified_attempt(
            run,
            row,
            expected_data_checksum="official-data",
            benchmark=object(),
            corrections=object(),
        )


def test_acceptance_records_v2_participant_and_execution_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "_verified_attempt", _fake_verified)
    scope = _scope(tmp_path)
    sweep = _run(
        tmp_path,
        "sweep",
        list(range(812)),
        {},
        schema="lex_webarena_full_replay_run/2",
        engine="moli",
    )

    ledger = acceptance.audit(scope, [sweep], [])

    assert ledger["participant"] == {
        "engine": "moli",
        "backend": "browsertool",
        "binary_sha256": "same-chromium",
        "site_map_sha256": "sites",
    }
    assert ledger["runs"][0]["action_timeout_ms"] == 30_000
    assert ledger["runs"][0]["environment_profile_sha256"] == "environment"
