from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay import webarena_report


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def test_build_catalog_report_keeps_setup_failures_out_of_physical_count(
    monkeypatch, tmp_path: Path
) -> None:
    current_evaluation = {"score": 1.0, "status": "pass", "evaluator": "current"}
    transition = {
        "task_id": 0,
        "old_evaluation_sha256": "1" * 64,
        "actual_response_sha256": "2" * 64,
        "network_har_sha256": "3" * 64,
        "current_evaluation_sha256": webarena_report.json_sha256(current_evaluation),
    }
    evidence = tmp_path / "evidence"
    run = evidence / "repair"
    progress = run / "progress.jsonl"
    progress.parent.mkdir(parents=True)
    progress.write_text(
        json.dumps(
            {
                "task_id": 0,
                "status": "pass",
                "score": 1.0,
                "duration_ms": 1250,
                "action_count": 1,
                "completed_actions": 1,
                "executed_actions_sha256": "a" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(progress.read_bytes()).hexdigest()
    for name, value in {
        "evaluation.json": {"score": 1.0, "status": "pass"},
        "actual_response.json": {"task_type": "retrieve", "status": "SUCCESS"},
        "trace.json": {"events": []},
    }.items():
        _write_json(run / "task-0" / name, value)

    ledger = tmp_path / "ledger.json"
    _write_json(
        ledger,
        {
            "counts": {
                "total": 1,
                "pass": 1,
                "unresolved": 0,
                "physical_attempts": 1,
                "excluded_duplicate_attempts": 0,
            },
            "participant": {"engine": "moli", "backend": "playwright"},
            "binary_sha256s": ["b" * 64],
            "runs": [{"kind": "segment"}, {"kind": "repair"}],
            "rows": [
                {
                    "task_id": 0,
                    "attempts": [
                        {
                            "status": "candidate_setup_failure",
                            "progress_sha256": "c" * 64,
                        },
                        {
                            "status": "pass",
                            "progress_sha256": digest,
                            "binary_sha256": "b" * 64,
                            "evaluator_transition": transition,
                        },
                    ],
                }
            ],
        },
    )
    catalog = tmp_path / "catalog.json"
    _write_json(
        catalog,
        {
            "task_ids": [0],
            "rows": [
                {
                    "task_id": 0,
                    "mode": "direct",
                    "source_kind": "recorded",
                    "source_actions": [{"action": "navigate", "url": "https://example.test"}],
                }
            ],
        },
    )
    suite = tmp_path / "suite.json"
    _write_json(suite, {"tasks": []})
    scope = tmp_path / "scope.json"
    _write_json(scope, {"task_ids": [0], "official_dataset_sha256": "data"})
    site_map = tmp_path / "sites.json"
    _write_json(site_map, {"sites": {}})
    monkeypatch.setattr(
        webarena_report,
        "_catalog_official_tasks",
        lambda: [
            {
                "task_id": 0,
                "intent": "Open the example",
                "sites": ["wikipedia"],
                "eval": [{"evaluator": "URLExactEvaluator"}],
            }
        ],
    )
    monkeypatch.setattr(webarena_report, "load_remote_sites", lambda _path: {})
    monkeypatch.setattr(webarena_report, "remote_benchmark", lambda _sites: object())
    monkeypatch.setattr(webarena_report, "load_corrections", lambda: object())
    monkeypatch.setattr(
        webarena_report,
        "reevaluate_pass",
        lambda *_args, **_kwargs: (current_evaluation, transition),
    )
    output = tmp_path / "report"
    archive = tmp_path / "report.zip"

    result = webarena_report.build_catalog_acceptance_report(
        evidence_root=evidence,
        ledger_path=ledger,
        scope_path=scope,
        catalog_path=catalog,
        suite_path=suite,
        site_map_path=site_map,
        output=output,
        archive=archive,
    )

    assert result["archive_sha256"] == webarena_report._catalog_file_sha256(archive)
    assert "1 / 1" in (output / "index.html").read_text(encoding="utf-8")
    assert "物理执行：1 次" in (output / "case/000.html").read_text(encoding="utf-8")
    assert '<base href="/">' in (output / "case/000.html").read_text(encoding="utf-8")
    public = json.loads((output / "evidence/000.json").read_text())
    assert public["physical_attempt_count"] == 1
    assert public["participant"]["binary_sha256"] == "b" * 64
    assert public["evaluation"] == current_evaluation
    assert public["evaluator_transition"] == transition
    profile = {
        "browser": "moli",
        "execution_backend": "playwright",
        "acceptance_ledger_sha256": webarena_report._catalog_file_sha256(ledger),
        "accepted_catalog": {"sha256": webarena_report._catalog_file_sha256(catalog)},
        "accepted_portable_suite": {
            "sha256": webarena_report._catalog_file_sha256(suite)
        },
        "accepted_counts": {
            "total": 1,
            "physical_attempts": 1,
            "excluded_duplicate_attempts": 0,
        },
    }
    assert (
        webarena_report.validate_catalog_acceptance_report(
            output, archive, profile=profile
        )["pass"]
        == 1
    )
    profile["browser"] = "chromium"
    with pytest.raises(ValueError, match="presentation changed its release binding"):
        webarena_report.validate_catalog_acceptance_report(
            output, archive, profile=profile
        )
