import hashlib
import json

import pytest

from harness.trajectory_replay import catalog_acceptance as acceptance
from harness.trajectory_replay.webarena_runtime_source import WEBARENA_VERIFIED_REVISION


@pytest.fixture(autouse=True)
def _isolate_acceptance_contract_tests(monkeypatch):
    monkeypatch.setattr(acceptance, "load_corrections", lambda: object())
    monkeypatch.setattr(acceptance, "verify_webarena_runtime_source", lambda: None)


def _run(
    tmp_path,
    name,
    ids,
    statuses,
    *,
    preparation="same-reset",
    backend="browsertool",
    catalog="catalog",
    schema="lex_trajectory_replay_catalog_run/1",
    engine=None,
    action_overrides=None,
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
    if action_overrides is not None:
        inputs["action_overrides_sha256"] = action_overrides
    if schema in {
        "lex_trajectory_replay_catalog_run/1",
        "lex_webarena_full_replay_run/2",
    }:
        inputs.update(
            {
                "engine": engine or "chromium",
                "action_timeout_ms": 30_000,
                "environment_profile_sha256": "environment",
                "corrections_sha256": "corrections",
                **(
                    {"webarena_revision": WEBARENA_VERIFIED_REVISION}
                    if schema == "lex_trajectory_replay_catalog_run/1"
                    else {"evaluator_fixes_sha256": "fixes"}
                ),
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


def _scope(tmp_path, ids=None):
    ids = list(range(812)) if ids is None else ids
    path = tmp_path / "scope.json"
    path.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_replay_scope/1",
                "official_dataset_sha256": "official-data",
                "task_count": len(ids),
                "task_ids": ids,
            }
        )
    )
    return path


def _fake_verified(directory, row, **_kwargs):
    return (dict(row, progress_sha256="progress", evidence_sha256={}), True)


def _action_overrides(tmp_path):
    path = tmp_path / "action-overrides.json"
    path.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_replay_action_overrides/1",
                "amendments": [
                    {
                        "task_id": 7,
                        "action_index": 1,
                        "precondition_action_sha256": "a" * 64,
                        "set": {"wait_for_selector_hidden": ".loading-mask"},
                    }
                ],
            }
        )
        + "\n"
    )
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_acceptance_still_reads_historical_run_schemas(tmp_path):
    legacy_v1 = _run(
        tmp_path,
        "legacy-v1",
        [4],
        {},
        schema="lex_webarena_full_replay_run/1",
    )
    legacy_v2 = _run(
        tmp_path,
        "legacy-v2",
        [4],
        {},
        schema="lex_webarena_full_replay_run/2",
    )

    assert acceptance._read_run(legacy_v1)[0]["schema"].endswith("/1")
    assert acceptance._read_run(legacy_v2)[0]["schema"].endswith("/2")


def test_acceptance_rejects_different_current_webarena_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "_verified_attempt", _fake_verified)
    scope = _scope(tmp_path, [7])
    sweep = _run(tmp_path, "sweep", [7], {})
    inputs = sweep / "inputs.json"
    payload = json.loads(inputs.read_text())
    payload["webarena_revision"] = "0" * 40
    inputs.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="different WebArena evaluator revision"):
        acceptance.audit(scope, [sweep], [])


def test_acceptance_allows_declared_predecessor_only_with_paired_transition_receipt(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(acceptance, "_verified_attempt", _fake_verified)
    transition = {
        "from_revision": "0" * 40,
        "to_revision": WEBARENA_VERIFIED_REVISION,
    }
    monkeypatch.setattr(acceptance, "load_transition", lambda _path: transition)
    monkeypatch.setattr(acceptance, "load_receipt", lambda _path, _transition: {})
    scope = _scope(tmp_path, [7])
    sweep = _run(tmp_path, "sweep", [7], {7: "fail"})
    inputs = sweep / "inputs.json"
    payload = json.loads(inputs.read_text())
    payload["webarena_revision"] = transition["from_revision"]
    inputs.write_text(json.dumps(payload))
    authority = tmp_path / "transition.json"
    receipt = tmp_path / "receipt.json"
    authority.write_text("{}")
    receipt.write_text("{}")

    ledger = acceptance.audit(
        scope,
        [sweep],
        [],
        evaluator_transition_path=authority,
        evaluator_transition_receipt_path=receipt,
    )

    assert ledger["counts"]["unresolved"] == 1
    assert ledger["evaluator_transition"]["reevaluated_passes"] == 0


def test_acceptance_requires_transition_authority_and_receipt_together(tmp_path):
    with pytest.raises(ValueError, match="must be supplied together"):
        acceptance.audit(
            _scope(tmp_path, [7]),
            [],
            [],
            evaluator_transition_path=tmp_path / "transition.json",
        )


def test_acceptance_requires_external_action_overrides_for_amended_run(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "_verified_attempt", _fake_verified)
    path, digest = _action_overrides(tmp_path)
    scope = _scope(tmp_path, [7])
    sweep = _run(tmp_path, "sweep", [7], {}, action_overrides=digest)

    with pytest.raises(ValueError, match="requires its external action overrides"):
        acceptance.audit(scope, [sweep], [])

    ledger = acceptance.audit(scope, [sweep], [], action_overrides_paths=[path])
    assert ledger["action_overrides_sha256s"] == [digest]
    assert ledger["runs"][0]["action_overrides_sha256"] == digest


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
    assert ledger["schema"] == "lex_trajectory_replay_catalog_acceptance/1"


def test_acceptance_supports_a_noncontiguous_catalog_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "_verified_attempt", _fake_verified)
    ids = [4, 11, 23]
    scope = _scope(tmp_path, ids)
    sweep = _run(tmp_path, "sweep", ids, {11: "fail"})
    repair = _run(tmp_path, "repair", [11], {})

    ledger = acceptance.audit(scope, [sweep], [repair])

    assert [row["task_id"] for row in ledger["rows"]] == ids
    assert ledger["counts"]["total"] == 3


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
