"""Paired official-evaluator controls for the two navigation diagnostics."""

from __future__ import annotations

import json

import pytest

from harness.environments.webarena.runtime import (
    configure_benchmark,
)


@pytest.mark.parametrize(
    ("task_id", "passing_url", "nearby_wrong_url"),
    [
        (
            156,
            "http://192.168.5.2:8023/dashboard/merge_requests?assignee_username=byteblaze",
            "http://192.168.5.2:8023/dashboard/merge_requests?assignee_username=other",
        ),
        (
            374,
            "http://host.docker.internal:7780/admin/admin/system_design_theme/edit/id/1/",
            "http://host.docker.internal:7780/admin/admin/system_design_theme/edit/id/2/",
        ),
    ],
)
def test_navigation_requires_exact_official_get_event(
    tmp_path, task_id: int, passing_url: str, nearby_wrong_url: str
) -> None:
    benchmark = configure_benchmark(hostname="192.168.5.2")
    response = {"task_type": "NAVIGATE", "status": "SUCCESS", "retrieved_data": None}
    har = tmp_path / "network.har"

    def score(url: str, agent_response: dict = response) -> float:
        har.write_text(
            json.dumps(
                {
                    "log": {
                        "version": "1.2",
                        "creator": {"name": "navigation-control", "version": "1"},
                        "entries": [
                            {
                                "request": {
                                    "method": "GET",
                                    "url": url,
                                    "headers": [{"name": "Accept", "value": "text/html"}],
                                },
                                "response": {
                                    "status": 200,
                                    "redirectURL": "",
                                    "headers": [],
                                    "cookies": [],
                                    "content": {"mimeType": "text/html", "text": "ok"},
                                },
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )
        return benchmark.evaluate_task(
            task_id=task_id, agent_response=agent_response, network_trace=har
        ).score

    assert score(passing_url) == 1.0
    assert score(nearby_wrong_url) == 0.0
    assert score(passing_url, {**response, "status": "FAILURE"}) == 0.0
