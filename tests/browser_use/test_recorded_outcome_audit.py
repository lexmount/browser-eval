from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.audit_webarena_recorded_outcomes import site_origins

ROOT = Path(__file__).resolve().parents[2]


def test_recorded_outcome_audit_derives_per_trace_origins_by_official_ports():
    requests = [
        {"url": "http://old.example:7770/a"},
        {"url": "http://current.example:7770/a"},
        {"url": "http://current.example:7770/b"},
        {"url": "http://admin.example:7780/admin"},
        {"url": "data:text/plain,ignored"},
    ]

    assert site_origins(requests, ["shopping", "shopping_admin"]) == {
        "shopping": "http://current.example:7770",
        "shopping_admin": "http://admin.example:7780",
    }


def test_historical_recorded_outcome_audit_binds_current_trace_sources():
    suite_path = ROOT / "evaluation/browser-use-public/webarena-human-suite.json"
    audit_path = (
        ROOT / "evaluation/browser-use-public/webarena-recorded-outcome-audit.json"
    )
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    current_sources = {
        int(str(row["id"]).removeprefix("webarena-")): row["trajectory"]["provenance"][
            "source_sha256"
        ]
        for row in suite["tasks"]
    }
    audited_sources = {
        int(row["task_id"]): row["source_trace_sha256"] for row in audit["tasks"]
    }

    assert hashlib.sha256(suite_path.read_bytes()).hexdigest() == audit[
        "current_suite_sha256"
    ]
    assert len(current_sources) == 179
    assert audited_sources == current_sources
    assert audit["current_suite_trace_sources_unchanged"] is True
