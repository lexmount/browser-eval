from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.trajectory_replay import catalog_release
from harness.trajectory_replay.catalog_action_audit import (
    scan_function_body_actions,
    validate_catalog_action_audit,
    validate_function_body_overrides,
)
from harness.trajectory_replay.catalog_contracts import file_sha256
from harness.trajectory_replay.webarena_replay import _json_sha256


def _write_inputs(tmp_path: Path, scripts: list[str]) -> tuple[Path, Path]:
    rows = []
    for task_id, script in enumerate(scripts, 1):
        action = {"action": "evaluate", "script": script}
        rows.append(
            {
                "task_id": task_id,
                "mode": "direct",
                "source_actions": [action],
                "bound_calls_sha256": _json_sha256([action]),
            }
        )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"task_ids": list(range(1, len(rows) + 1)), "rows": rows}))
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"schema": "lex_browser_use_tasks/4", "tasks": []}))
    return catalog, suite


def test_static_audit_only_selects_script_invalid_function_body_valid_evaluate(tmp_path: Path):
    catalog, suite = _write_inputs(
        tmp_path,
        [
            "() => { return true; }",
            "const value = 1; return value;",
        ],
    )

    audit = scan_function_body_actions(catalog, suite)

    assert audit["evaluate_action_count"] == 2
    assert [(row["task_id"], row["action_index"]) for row in audit["function_body_findings"]] == [
        (2, 1)
    ]


def test_static_audit_rejects_script_invalid_under_both_grammars(tmp_path: Path):
    catalog, suite = _write_inputs(tmp_path, ["return }"])
    with pytest.raises(ValueError, match="invalid under both grammars"):
        scan_function_body_actions(catalog, suite)


def test_static_audit_accepts_embedded_function_body_mode(tmp_path: Path):
    catalog, suite = _write_inputs(tmp_path, ["const value = 1; return value;"])
    payload = json.loads(catalog.read_text(encoding="utf-8"))
    payload["rows"][0]["source_actions"][0]["script_mode"] = "function_body"
    catalog.write_text(json.dumps(payload), encoding="utf-8")

    audit = scan_function_body_actions(catalog, suite)

    assert audit["evaluate_action_count"] == 1
    assert audit["function_body_findings"] == []


def _write_overrides(path: Path, audit: dict) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_replay_action_overrides/1",
                "amendments": [
                    {
                        "task_id": row["task_id"],
                        "action_index": row["action_index"],
                        "precondition_action_sha256": row["precondition_action_sha256"],
                        "set": {"script_mode": "function_body"},
                    }
                    for row in audit["function_body_findings"]
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_release_validation_binds_audit_to_catalog_suite_and_live_scan(tmp_path: Path):
    catalog, suite = _write_inputs(tmp_path, ["return true;"])
    audit = scan_function_body_actions(catalog, suite)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    overrides = tmp_path / "overrides.json"
    _write_overrides(overrides, audit)

    assert validate_catalog_action_audit(audit_path, catalog, suite, overrides) == audit

    changed = dict(audit, catalog_sha256="0" * 64)
    audit_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="catalog or suite binding"):
        validate_catalog_action_audit(audit_path, catalog, suite, overrides)


def test_release_validation_detects_new_unamended_top_level_return(tmp_path: Path):
    catalog, suite = _write_inputs(tmp_path, ["return true;"])
    audit = scan_function_body_actions(catalog, suite)
    overrides = tmp_path / "overrides.json"
    _write_overrides(overrides, audit)

    payload = json.loads(catalog.read_text(encoding="utf-8"))
    action = {"action": "evaluate", "script": "const next = 2; return next;"}
    payload["task_ids"].append(2)
    payload["rows"].append(
        {
            "task_id": 2,
            "mode": "direct",
            "source_actions": [action],
            "bound_calls_sha256": _json_sha256([action]),
        }
    )
    catalog.write_text(json.dumps(payload), encoding="utf-8")
    audit["catalog_sha256"] = file_sha256(catalog)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    with pytest.raises(ValueError, match="differs from the live catalog scan"):
        validate_catalog_action_audit(audit_path, catalog, suite, overrides)


def test_versioned_override_data_matches_complete_812_action_audit():
    root = Path(__file__).resolve().parents[2]
    directory = root / "evaluation/trajectory-replay/webarena-verified-full"
    audit = json.loads((directory / "action-overrides-audit.json").read_text(encoding="utf-8"))

    assert audit["evaluate_action_count"] == 804
    assert len(audit["function_body_findings"]) == 21
    assert (
        len({(row["task_id"], row["action_index"]) for row in audit["function_body_findings"]})
        == 21
    )
    validate_function_body_overrides(audit, directory / "action-overrides.json")


def test_catalog_release_gate_runs_live_action_audit(monkeypatch, tmp_path: Path):
    profile = tmp_path / "current-report.json"
    data_home = tmp_path / "evidence"
    seen = {}
    monkeypatch.setattr(catalog_release, "verify_webarena_runtime_source", lambda: None)
    monkeypatch.setattr(catalog_release, "repository_root", lambda _path: tmp_path)
    monkeypatch.setattr(
        catalog_release,
        "validate_catalog_report_profile",
        lambda _profile, _data_home: {
            "evidence_root": "run",
                "accepted_catalog": {"path": "catalog.json"},
                "accepted_portable_suite": {"path": "suite.json"},
                "report_manifest_path": "report/manifest.json",
                "report_archive_path": "report.zip",
                "publication": {"share_url": "https://artifact.example/report"},
        },
    )
    monkeypatch.setattr(
        catalog_release,
        "validate_catalog_action_audit",
        lambda *args: seen.setdefault("paths", args),
    )
    monkeypatch.setattr(
        catalog_release,
        "validate_catalog_acceptance_report",
        lambda *args, **kwargs: seen.setdefault("report", (args, kwargs)),
    )
    monkeypatch.setattr(
        catalog_release,
        "validate_catalog_comparison_release",
        lambda *args: {
            "publication": {"share_url": "https://artifact.example/comparison"}
        },
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "release-check",
            "--profile",
            str(profile),
            "--data-home",
            str(data_home),
        ],
    )

    catalog_release.main()

    assert seen["paths"] == (
        tmp_path
        / "evaluation/trajectory-replay/webarena-verified-full/action-overrides-audit.json",
        data_home / "run/catalog.json",
        data_home / "run/suite.json",
        tmp_path / "evaluation/trajectory-replay/webarena-verified-full/action-overrides.json",
    )
    assert seen["report"][0] == (
        data_home / "run/report",
        data_home / "run/report.zip",
    )
