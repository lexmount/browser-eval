import json

import pytest

from scripts import audit_webarena_full_acceptance as acceptance


def _run(tmp_path, name, ids, statuses, *, preparation="same-reset"):
    directory = tmp_path / name
    directory.mkdir()
    (directory / "inputs.json").write_text(json.dumps({
        "schema": "lex_webarena_full_replay_run/1",
        "execution_order": ids,
        "run_count_per_task": 1,
        "site_reset_receipt_sha256": preparation,
        "binary_sha256": "same-chromium",
        "catalog_sha256": "catalog",
        "runner_revision": "revision",
    }))
    (directory / "progress.jsonl").write_text("".join(
        json.dumps({"task_id": task_id, "status": statuses.get(task_id, "pass"), "score": 1}) + "\n"
        for task_id in ids
    ))
    return directory


def _scope(tmp_path):
    path = tmp_path / "scope.json"
    path.write_text(json.dumps({
        "schema": "lex_webarena_verified_full_scope/1",
        "task_ids": list(range(812)),
    }))
    return path


def test_acceptance_keeps_one_ordered_sweep_and_only_repairs_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "_verified_attempt", lambda directory, row: row)
    scope = _scope(tmp_path)
    early = _run(tmp_path, "early", list(range(400)), {66: "fail"})
    late = _run(tmp_path, "late", list(range(400, 812)), {})
    repair = _run(tmp_path, "repair", [66], {})

    ledger = acceptance.audit(scope, [early, late], [repair])

    assert ledger["counts"] == {
        "total": 812, "pass": 812, "unresolved": 0, "physical_attempts": 813,
    }
    assert [attempt["status"] for attempt in ledger["rows"][66]["attempts"]] == ["fail", "pass"]
    assert len(ledger["rows"][67]["attempts"]) == 1


def test_acceptance_binds_repair_on_a_fresh_site_without_repeating_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "_verified_attempt", lambda directory, row: row)
    scope = _scope(tmp_path)
    sweep = _run(tmp_path, "sweep", list(range(812)), {496: "fail"})
    repair = _run(tmp_path, "repair", [496], {}, preparation="spare-slice")

    ledger = acceptance.audit(scope, [sweep], [repair])

    assert ledger["counts"]["pass"] == 812
    assert ledger["site_preparation_receipt_sha256s"] == ["same-reset", "spare-slice"]
    assert ledger["runs"][1]["site_reset_receipt_sha256"] == "spare-slice"
    assert len(ledger["rows"][495]["attempts"]) == 1


def test_acceptance_rejects_changing_sites_during_ordered_sweep(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "_verified_attempt", lambda directory, row: row)
    scope = _scope(tmp_path)
    early = _run(tmp_path, "early", list(range(400)), {})
    late = _run(tmp_path, "late", list(range(400, 812)), {}, preparation="other")

    with pytest.raises(ValueError, match="ordered sweep used different site preparations"):
        acceptance.audit(scope, [early, late], [])


def test_acceptance_rejects_rerunning_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "_verified_attempt", lambda directory, row: row)
    scope = _scope(tmp_path)
    sweep = _run(tmp_path, "sweep", list(range(812)), {})
    repeated = _run(tmp_path, "repeated", [66], {})

    with pytest.raises(ValueError, match="repair does not follow a failure"):
        acceptance.audit(scope, [sweep], [repeated])


def test_acceptance_rejects_an_order_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "_verified_attempt", lambda directory, row: row)
    scope = _scope(tmp_path)
    gap = _run(tmp_path, "gap", [task_id for task_id in range(812) if task_id != 500], {})

    with pytest.raises(ValueError, match="incomplete or changed official order"):
        acceptance.audit(scope, [gap], [])
