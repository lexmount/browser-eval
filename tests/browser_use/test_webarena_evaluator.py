from __future__ import annotations

import json

import pytest

from harness.browser_use.evaluators import TaskEvidence, WebArenaVerifiedEvaluator
from harness.browser_use.suite import BrowserTask


def test_official_webarena_verified_evaluator_accepts_programmatic_evidence(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "environments": {
                    "__SHOPPING_ADMIN__": {
                        "urls": ["http://shopping-admin.test/admin"],
                        "credentials": {"username": "admin", "password": "admin1234"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    har = tmp_path / "network.har"
    har.write_text(
        json.dumps(
            {
                "log": {
                    "version": "1.2",
                    "creator": {"name": "test", "version": "1"},
                    "entries": [
                        {
                            "request": {
                                "method": "GET",
                                "url": "http://shopping-admin.test/admin",
                                "headers": [],
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
    task = BrowserTask(
        task_id="webarena-4",
        title="Get the top sellers",
        tool_calls=(),
        assertions=(),
        tags=(),
        difficulty="simple",
        ground_truth={"expected_actions": []},
        source={"url": "https://example.test/4.trace.zip"},
        evaluator={"kind": "webarena_verified", "task_id": 4, "revision": 2},
        environment={"kind": "webarena_verified"},
    )
    evidence = TaskEvidence(
        outputs={},
        process_passed=True,
        agent_response={
            "task_type": "retrieve",
            "status": "SUCCESS",
            "retrieved_data": [
                "Impulse Duffle",
                "Overnight Duffle",
                "Hawkeye Yoga Short-32-Blue",
            ],
        },
        network_har_path=har,
    )

    result = WebArenaVerifiedEvaluator(config_path=config).evaluate(task, evidence)

    assert result.status == "pass"
    assert result.score == 1.0


def test_webarena_evaluator_fails_closed_on_empty_har_without_trace_fallback(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "environments": {
                    "__SHOPPING_ADMIN__": {"urls": ["http://shopping-admin.test/admin"]}
                }
            }
        ),
        encoding="utf-8",
    )
    har = tmp_path / "network.har"
    har.write_text(json.dumps({"log": {"entries": []}}), encoding="utf-8")
    task = BrowserTask(
        task_id="webarena-4",
        title="Get the top sellers",
        tool_calls=(),
        assertions=(),
        tags=(),
        difficulty="simple",
        ground_truth={"expected_actions": []},
        source={"url": "https://example.test/4.trace.zip"},
        evaluator={"kind": "webarena_verified", "task_id": 4, "revision": 2},
        environment={"kind": "webarena_verified"},
    )

    result = WebArenaVerifiedEvaluator(config_path=config).evaluate(
        task,
        TaskEvidence(
            outputs={},
            process_passed=False,
            agent_response={
                "task_type": "retrieve",
                "status": "SUCCESS",
                "retrieved_data": [],
            },
            network_har_path=har,
        ),
    )

    assert result.status == "error"
    assert result.error == "network.har evidence contains no entries"


def test_official_webarena_verified_evaluator_can_read_safe_flash_cookie(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"environments": {"__SHOPPING__": {"urls": ["http://shopping.test"]}}}),
        encoding="utf-8",
    )
    har = tmp_path / "network.har"
    har.write_text(
        json.dumps(
            {
                "log": {
                    "version": "1.2",
                    "creator": {"name": "test", "version": "1"},
                    "entries": [
                        {
                            "request": {
                                "method": "POST",
                                "url": "http://shopping.test/wishlist/index/add/",
                                "headers": [],
                                    "postData": {
                                        "mimeType": "application/x-www-form-urlencoded",
                                        "text": "product=123",
                                },
                            },
                            "response": {
                                "status": 302,
                                "redirectURL": "http://shopping.test/wishlist/",
                                "headers": [],
                                "cookies": [
                                    {
                                        "name": "mage-messages",
                                        "value": (
                                            "White%20desk%20has%20been%20added%20to%20your%20"
                                            "wish%20list."
                                        ),
                                    }
                                ],
                                "content": {"mimeType": "text/html", "text": ""},
                            },
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    task = BrowserTask(
        task_id="webarena-514",
        title="Add a white desk to my wish list",
        tool_calls=(),
        assertions=(),
        tags=(),
        difficulty="simple",
        ground_truth={"expected_actions": []},
        source={"url": "https://example.test/514.trace.zip"},
        evaluator={"kind": "webarena_verified", "task_id": 514, "revision": 2},
        environment={"kind": "webarena_verified"},
    )
    evidence = TaskEvidence(
        outputs={},
        process_passed=True,
        agent_response={"task_type": "mutate", "status": "SUCCESS", "retrieved_data": None},
        network_har_path=har,
    )

    result = WebArenaVerifiedEvaluator(config_path=config).evaluate(task, evidence)

    assert result.status == "pass", result
    assert result.score == 1.0


@pytest.mark.parametrize(
    "directions_engine",
    ["fossgis_osrm_car", "fossgis_osrm_bicycle"],
)
def test_official_task_760_has_navigate_get_xhr_evaluator_defect(tmp_path, directions_engine):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "environments": {
                    "__MAP__": {"urls": ["http://map.test"]},
                    "__SHOPPING_ADMIN__": {"urls": ["http://shopping-admin.test"]},
                }
            }
        ),
        encoding="utf-8",
    )
    har = tmp_path / "network.har"
    route = "/route/v1/driving/-75.4716115,40.6022552;-74.4041622,40.0757384"
    har.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "method": "GET",
                                "url": f"http://map.test{route}",
                                "headers": [
                                    {
                                        "name": "Cookie",
                                        "value": (
                                            f"_osm_directions_engine={directions_engine}; "
                                            "[REDACTED]"
                                        ),
                                    }
                                ],
                            },
                            "response": {
                                "status": 200,
                                "redirectURL": "",
                                "headers": [],
                                "cookies": [],
                                "content": {"mimeType": "application/json", "text": "{}"},
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    task = BrowserTask(
        task_id="webarena-760",
        title="Show an OSRM route",
        tool_calls=(),
        assertions=(),
        tags=(),
        difficulty="simple",
        ground_truth={"expected_actions": []},
        source={"url": "https://example.test/760.trace.zip"},
        evaluator={"kind": "webarena_verified", "task_id": 760, "revision": 2},
        environment={"kind": "webarena_verified"},
    )
    evidence = TaskEvidence(
        outputs={},
        process_passed=True,
        agent_response={"task_type": "navigate", "status": "SUCCESS", "retrieved_data": None},
        network_har_path=har,
    )

    result = WebArenaVerifiedEvaluator(config_path=config).evaluate(task, evidence)

    # v1.2.3 selects only main-frame navigation events for every navigate+GET
    # task, so it never considers this expected OSRM XHR for either engine.
    assert result.status == "fail"
    assert result.checks[1]["actual"] == []


def test_official_task_678_has_navigate_get_xhr_evaluator_defect(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "environments": {
                    "__SHOPPING_ADMIN__": {"urls": ["http://shopping-admin.test/admin"]}
                }
            }
        ),
        encoding="utf-8",
    )
    har = tmp_path / "network.har"
    query = (
        "namespace=sales_order_grid&filters%5Bplaceholder%5D=true&"
        "filters%5Bstatus%5D=canceled&search=&keywordUpdated=false"
    )
    har.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "method": "GET",
                                "url": f"http://shopping-admin.test/admin/mui/index/render/?{query}",
                                "headers": [
                                    {
                                        "name": "Referer",
                                        "value": "http://shopping-admin.test/admin/sales/order/",
                                    }
                                ],
                            },
                            "response": {
                                "status": 200,
                                "redirectURL": "",
                                "headers": [],
                                "cookies": [],
                                "content": {"mimeType": "application/json", "text": "{}"},
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    task = BrowserTask(
        task_id="webarena-678",
        title="Go to canceled orders",
        tool_calls=(),
        assertions=(),
        tags=(),
        difficulty="simple",
        ground_truth={"expected_actions": []},
        source={"url": "https://example.test/678.trace.zip"},
        evaluator={"kind": "webarena_verified", "task_id": 678, "revision": 2},
        environment={"kind": "webarena_verified"},
    )
    evidence = TaskEvidence(
        outputs={},
        process_passed=True,
        agent_response={"task_type": "navigate", "status": "SUCCESS", "retrieved_data": None},
        network_har_path=har,
    )

    result = WebArenaVerifiedEvaluator(config_path=config).evaluate(task, evidence)

    assert result.status == "fail"
    assert result.checks[1]["actual"] == []


def test_official_task_811_has_invalid_single_item_alternatives_schema(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"environments": {"__GITLAB__": {"urls": ["http://gitlab.test"]}}}),
        encoding="utf-8",
    )
    har = tmp_path / "network.har"
    har.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "method": "PUT",
                                "url": (
                                    "http://gitlab.test/a11yproject/"
                                    "a11yproject.com/-/issues/1478.json"
                                ),
                                "headers": [{"name": "content-type", "value": "application/json"}],
                                "postData": {
                                    "mimeType": "application/json",
                                    "text": '{"issue":{"assignee_ids":[2330]}}',
                                },
                            },
                            "response": {
                                "status": 200,
                                "redirectURL": "",
                                "headers": [],
                                "cookies": [],
                                "content": {"mimeType": "application/json", "text": "{}"},
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    task = BrowserTask(
        task_id="webarena-811",
        title="Assign issue 404 to myself",
        tool_calls=(),
        assertions=(),
        tags=(),
        difficulty="simple",
        ground_truth={"expected_actions": []},
        source={"url": "https://example.test/811.trace.zip"},
        evaluator={"kind": "webarena_verified", "task_id": 811, "revision": 2},
        environment={"kind": "webarena_verified"},
    )
    evidence = TaskEvidence(
        outputs={},
        process_passed=True,
        agent_response={"task_type": "mutate", "status": "SUCCESS", "retrieved_data": None},
        network_har_path=har,
    )

    result = WebArenaVerifiedEvaluator(config_path=config).evaluate(task, evidence)

    assert result.status == "error"
    assert result.checks[1]["status"] == "error"
    assert "Alternatives require 2+ items" in str(result.checks[1]["error_msg"])
