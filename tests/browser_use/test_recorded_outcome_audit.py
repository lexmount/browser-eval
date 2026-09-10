from __future__ import annotations

from scripts.audit_webarena_recorded_outcomes import site_origins


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
