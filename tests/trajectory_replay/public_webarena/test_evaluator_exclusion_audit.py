from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from harness.trajectory_replay.public_webarena.diagnostics import verify_exclusions
from harness.trajectory_replay.report_inputs import replay_input_path

ROOT = Path(__file__).resolve().parents[3]


def _write_trace(path, *, network_urls=(), network_requests=(), page_count):
    requests = [
        {"method": "GET", "url": url} for url in network_urls
    ] + list(network_requests)
    network = "".join(
        json.dumps(
            {
                "type": "resource-snapshot",
                "snapshot": {"request": request},
            }
        )
        + "\n"
        for request in requests
    )
    trace = "".join(
        json.dumps(
            {
                "type": "event",
                "class": "Page",
                "method": "__create__",
                "params": {"guid": f"page-{index}"},
            }
        )
        + "\n"
        for index in range(page_count)
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("trace.network", network)
        archive.writestr("trace.trace", trace)


def test_evaluator_exclusion_audit_binds_original_trace_anomalies(tmp_path):
    _write_trace(
        tmp_path / "678.trace.zip",
        network_urls=["https://admin.test/report"],
        page_count=1,
    )
    _write_trace(
        tmp_path / "760.trace.zip",
        network_urls=[
            "https://map.test/route/v1/driving/"
            "-75.4712794,40.6022059;-74.4041622,40.0757384"
        ],
        page_count=3,
    )
    _write_trace(
        tmp_path / "811.trace.zip",
        network_requests=[
            {
                "method": "GET",
                "url": (
                    "https://gitlab.test/a11yproject/a11yproject.com/"
                    "-/issues/1478.json"
                ),
            }
        ],
        page_count=1,
    )
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "webarena-678",
                        "environment": {"start_urls": ["__SHOPPING_ADMIN__"]},
                        "trajectory": {"actions": []},
                    },
                    {
                        "id": "webarena-760",
                        "environment": {"start_urls": ["__MAP__", "__SHOPPING_ADMIN__"]},
                        "trajectory": {"actions": [{"tab_index": 2}]},
                    },
                    {
                        "id": "webarena-811",
                        "environment": {"start_urls": ["__GITLAB__"]},
                        "trajectory": {"actions": []},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    exclusions = tmp_path / "exclusions.json"
    rows = []
    for task_id in (678, 760, 811):
        trace_path = tmp_path / f"{task_id}.trace.zip"
        rows.append(
            {
                "task_id": f"webarena-{task_id}",
                "evidence_id": f"evidence-{task_id}",
                "source_trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
            }
        )
    exclusions.write_text(
        json.dumps(
            {"schema": "lex_webarena_evaluator_exclusions/1", "excluded_tasks": rows}
        ),
        encoding="utf-8",
    )

    result = verify_exclusions(tmp_path, suite, exclusions)

    assert result["complete"] is True
    assert result["checks"][0]["sales_order_grid_event_count"] == 0
    assert result["checks"][1]["created_page_count"] == 3
    assert result["checks"][1]["max_action_tab_index"] == 2
    assert result["checks"][2]["recorded_methods"] == ["GET"]
    assert result["checks"][2]["expected_put_event_count"] == 0


def test_committed_exclusion_audit_is_bound_to_current_task_definitions():
    suite_path = replay_input_path(current="human-candidates")
    audit_path = (
        ROOT / "evaluation/browser-use-public/webarena-evaluator-exclusion-audit.json"
    )
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    excluded_ids = {row["task_id"] for row in audit["checks"]}
    selected = [row for row in suite["tasks"] if str(row["id"]) in excluded_ids]
    serialized = json.dumps(
        selected, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()

    assert hashlib.sha256(suite_path.read_bytes()).hexdigest() == audit["suite_sha256"]
    assert len(selected) == len(excluded_ids)
    assert (
        hashlib.sha256(serialized).hexdigest()
        == audit["excluded_task_definitions_sha256"]
    )
    assert audit["excluded_task_definitions_unchanged"] is True
