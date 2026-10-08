"""Frozen three-method cohort checks; no remote browser is launched here."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from webarena_verified.types.task import WebArenaSite

from harness.trajectory_replay import remote_execution as cohort
from harness.trajectory_replay.remote_runtime import file_sha256
from harness.trajectory_replay.webarena_replay import _json_sha256
from tests.trajectory_replay.test_aggregate_webarena_three_method_cohorts import _reset_receipt


def _fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, retrieval: bool = False, task_ids=None
):
    binary = tmp_path / "browser"
    binary.write_bytes(b"fixed browser binary")
    site_map = tmp_path / "sites.json"
    site_map.write_text("{}", encoding="utf-8")
    reset_hook = tmp_path / "reset_hook.py"
    reset_hook.write_text("# frozen method reset hook\n", encoding="utf-8")
    cowagent = tmp_path / "cowagent"
    tool_dir = cowagent / "agent/tools/browser"
    tool_dir.mkdir(parents=True)
    for name in ("browser_tool.py", "browser_service.py"):
        (tool_dir / name).write_text("# frozen\n", encoding="utf-8")
    ids = list(range(1, 90)) if task_ids is None else list(task_ids)
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps({"schema": "lex_webarena_verified_pilot/1",
                    "tasks": [{"task_id": item} for item in ids]}), encoding="utf-8"
    )
    monkeypatch.setattr(
        cohort,
        "load_remote_sites",
        lambda _path: {WebArenaSite.SHOPPING_ADMIN: "https://admin.example/admin"},
    )
    monkeypatch.setattr(cohort, "validate_remote_cohort_reset_receipt", lambda *a, **k: {})
    monkeypatch.setattr(cohort, "rebind_remote_calls", lambda calls, *_: calls)
    monkeypatch.setattr(
        cohort,
        "remote_benchmark",
        lambda _sites: SimpleNamespace(
            get_task=lambda task_id: SimpleNamespace(is_retrieve_task=retrieval and task_id == 1)
        ),
    )
    actions = [{"action": "goto", "url": "https://example.com"}]
    dataset = tmp_path / "dataset.json"
    dataset_rows = [
        {"task_id": task_id, "task": {"intent": f"Task {task_id}"}, "evaluation_input": {}}
        for task_id in ids
    ]
    dataset.write_text(json.dumps({"trajectories": dataset_rows}), encoding="utf-8")
    rows = [
        {
            "task_id": task_id,
            "task_contract_sha256": _json_sha256(
                {"task": dataset_rows[task_id - 1]["task"], "evaluation_input": {}}
            ),
            "actions": actions,
            "bound_calls_sha256": _json_sha256(actions),
            "source_actions": actions,
            "source_calls_sha256": _json_sha256(actions),
            "readiness": [],
        }
        for task_id in ids
    ]
    manifest = {
        "schema": "webarena_three_method_inputs/1",
        "promotable": True,
        "method_id": "moli-1.1.3",
        "engine": "moli",
        "browser_binary_sha256": file_sha256(binary),
        "dataset_sha256": file_sha256(dataset),
        "selection_sha256": file_sha256(selection),
        "site_map_sha256": file_sha256(site_map),
        "run_count": 1,
        "retry_count": 0,
        "site_reset_between_tasks": False,
        "task_ids": ids,
        "tasks": rows,
    }
    manifest_path = tmp_path / "manifest.json"
    args = argparse.Namespace(
        tasks=None,
        selection=selection,
        manifest=manifest_path,
        dataset=dataset,
        method_id="moli-1.1.3",
        engine="moli",
        binary=binary,
        site_map=site_map,
        cowagent=cowagent,
        output=tmp_path / "output",
        app_id=17,
        cohort_reset_hook=reset_hook,
    )

    def save() -> None:
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    save()
    return args, manifest, save


def test_task_execution_order_matches_upstream_numeric_order(tmp_path, monkeypatch):
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_verified_pilot/1",
                "tasks": [{"task_id": n} for n in [645, 66, 610]],
            }
        )
    )
    assert cohort.selected_task_ids(selection) == [66, 610, 645]


@pytest.mark.parametrize("fault", [None, "upgrade", "scripts", "loader"])
def test_browser_readiness_rejects_origin_and_magento_setup_errors(monkeypatch, fault):
    page = SimpleNamespace(
        url="https://admin.example/admin",
        goto=lambda *a, **k: SimpleNamespace(status=200),
        locator=lambda *_: SimpleNamespace(
            evaluate_all=lambda *_: [
                "http://admin.example/script.js"
                if fault == "scripts"
                else "https://admin.example/script.js"
            ]
        ),
        evaluate=lambda *_: "undefined" if fault == "loader" else "function",
    )
    if fault == "upgrade":
        page.url = "https://other.example/admin"
    service = SimpleNamespace(_page=page, _submit=lambda callback: callback())
    closed = []
    tool = SimpleNamespace(_get_service=lambda: service, close=lambda: closed.append(True))
    monkeypatch.setattr(cohort, "prepare_browser_viewport", lambda *_: None)
    sites = {WebArenaSite.SHOPPING_ADMIN: "https://admin.example/admin"}
    if fault:
        with pytest.raises(ValueError):
            cohort.verify_browser_session(tool, sites)
    else:
        assert cohort.verify_browser_session(tool, sites)[0]["status"] == 200
    assert closed == [True]


def test_certificate_accepts_measured_failures() -> None:
    tasks = [{"task_id": task_id, "bound_calls_sha256": "bound"} for task_id in range(1, 90)]
    rows = [
        {
            "task_id": task_id,
            "executed_actions_sha256": "bound",
            "status": "fail",
            "score": 0.0,
            "evidence_sha256": {"site_reset": str(task_id)},
        }
        for task_id in range(1, 90)
    ]
    manifest = {"task_ids": list(range(1, 90)), "tasks": tasks}
    assert cohort.certify_cohort(
        manifest=manifest,
        rows=rows,
        before={"runner": "hash"},
        after={"runner": "hash"},
        evidence_valid=True,
    )
    assert not cohort.certify_cohort(
        manifest=manifest,
        rows=rows[:-1],
        before={},
        after={},
        evidence_valid=True,
    )
    assert not cohort.certify_cohort(
        manifest=manifest,
        rows=rows,
        before={"runner": "a"},
        after={"runner": "b"},
        evidence_valid=True,
    )


def test_preflight_accepts_exact_frozen_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, manifest, _save = _fixture(tmp_path, monkeypatch)
    found, _sites, _benchmark, selected = cohort.preflight_cohort(args)
    assert found == manifest
    assert list(selected) == list(range(1, 90))


def test_preflight_accepts_chromium_method(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args, manifest, save = _fixture(tmp_path, monkeypatch)
    args.method_id = "chromium"
    args.engine = "chromium"
    manifest["method_id"] = "chromium"
    manifest["engine"] = "chromium"
    save()
    assert len(cohort.preflight_cohort(args)[3]) == 89


def test_preflight_rejects_missing_retrieval_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _manifest, _save = _fixture(tmp_path, monkeypatch, retrieval=True)
    with pytest.raises(ValueError, match="RETRIEVE lacks current-run observation contract"):
        cohort.preflight_cohort(args)


def test_preflight_rejects_drift_and_frozen_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, manifest, save = _fixture(tmp_path, monkeypatch)
    manifest["tasks"][0]["bound_calls_sha256"] = "wrong"
    save()
    with pytest.raises(ValueError, match="frozen source/bound actions changed"):
        cohort.preflight_cohort(args)
    manifest["tasks"][0]["bound_calls_sha256"] = _json_sha256(manifest["tasks"][0]["actions"])
    manifest["tasks"][0]["recorded_response"] = {"task_type": "RETRIEVE"}
    save()
    with pytest.raises(ValueError, match="frozen answers"):
        cohort.preflight_cohort(args)


def test_preflight_rejects_dataset_or_task_contract_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, manifest, save = _fixture(tmp_path, monkeypatch)
    manifest["dataset_sha256"] = "wrong"
    save()
    with pytest.raises(ValueError, match="frozen method, binary, site map or run policy"):
        cohort.preflight_cohort(args)
    manifest["dataset_sha256"] = file_sha256(args.dataset)
    manifest["tasks"][0]["task_contract_sha256"] = "wrong"
    save()
    with pytest.raises(ValueError, match="frozen source/bound actions changed"):
        cohort.preflight_cohort(args)


def test_preflight_rejects_partial_method_and_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, manifest, save = _fixture(tmp_path, monkeypatch)
    manifest["tasks"].pop()
    save()
    with pytest.raises(ValueError, match="exact ordered selected tasks"):
        cohort.preflight_cohort(args)
    manifest["tasks"].append(
        {
            "task_id": 89,
            "task_contract_sha256": _json_sha256(
                {"task": {"intent": "Task 89"}, "evaluation_input": {}}
            ),
            "actions": [{"action": "goto", "url": "https://example.com"}],
            "bound_calls_sha256": _json_sha256([{"action": "goto", "url": "https://example.com"}]),
            "source_actions": [{"action": "goto", "url": "https://example.com"}],
            "source_calls_sha256": _json_sha256([{"action": "goto", "url": "https://example.com"}]),
            "readiness": [],
        }
    )
    save()
    args.output.mkdir()
    with pytest.raises(ValueError, match="append-only"):
        cohort.preflight_cohort(args)


def test_complete_evidence_requires_current_run_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cohort, "_verify_result_evidence", lambda *_args: None)
    row = {
        "task_id": 1,
        "status": "fail",
        "score": 0.0,
        "error": "",
        "evidence_sha256": {"trace": "x", "readiness": "y", "site_reset": "s"},
    }
    with pytest.raises(ValueError, match="evaluator/current-run evidence"):
        cohort.verify_cohort_evidence(tmp_path, [row], {1: {}})
    row["evidence_sha256"].update(network_har="h", actual_response="a", evaluation="e")
    cohort.verify_cohort_evidence(tmp_path, [row], {1: {}})
    with pytest.raises(ValueError, match="evaluator/current-run evidence"):
        cohort.verify_cohort_evidence(tmp_path, [row], {1: {"retrieval_contract": {}}})
    row["evidence_sha256"]["acceptance"] = "c"
    cohort.verify_cohort_evidence(tmp_path, [row], {1: {"retrieval_contract": {}}})


@pytest.mark.parametrize("configured", [False, True])
def test_https_admin_reset_requires_restored_application_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: bool
) -> None:
    receipt = _reset_receipt("chromium")
    receipt["completed_at_unix"] = time.time()
    if configured:
        receipt["sites"]["shopping_admin"]["https_admin_config"] = {
            "web_secure_base_url": "https://admin.example/",
            "web_secure_use_in_adminhtml": 1,
            "config_cache_cleaned": True,
            "public_static_resource_scheme": "https",
        }
    monkeypatch.setattr(
        cohort.subprocess,
        "run",
        lambda *_a, **_k: (tmp_path / "site-reset.json").write_text(json.dumps(receipt)),
    )
    args = argparse.Namespace(
        output=tmp_path,
        cohort_reset_hook=Path("hook.py"),
        method_id="chromium",
        app_id=195,
        site_map=Path("sites.json"),
    )
    manifest = {"site_map_sha256": "d" * 64}
    sites = {WebArenaSite.SHOPPING_ADMIN: "https://admin.example/admin"}
    if configured:
        assert cohort.reset_cohort(args, manifest, sites)
    else:
        with pytest.raises(ValueError, match="admin HTTPS configuration"):
            cohort.reset_cohort(args, manifest, sites)


def test_browser_session_setup_error_closes_probe_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = []
    service = SimpleNamespace(_submit=lambda fn: fn())
    tool = SimpleNamespace(_get_service=lambda: service, close=lambda: closed.append(True))

    def reject(_service):
        raise RuntimeError("CowAgent lacks the required owned CDP context contract")

    monkeypatch.setattr(cohort, "prepare_browser_viewport", reject)
    with pytest.raises(RuntimeError, match="owned CDP context"):
        cohort.verify_browser_session(tool)
    assert closed == [True]
