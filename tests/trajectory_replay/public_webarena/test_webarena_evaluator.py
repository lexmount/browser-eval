from __future__ import annotations

import json
from urllib.parse import urlencode

import pytest

from harness.trajectory_replay.public_webarena.evaluators import (
    TaskEvidence,
    WebArenaVerifiedEvaluator,
)
from harness.trajectory_replay.public_webarena.suite import BrowserTask


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
    ("directions_engine", "expected_status"),
    [("fossgis_osrm_car", "pass"), ("fossgis_osrm_bicycle", "fail")],
)
def test_official_task_760_checks_the_hoboken_car_route(
    tmp_path, directions_engine, expected_status
):
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
    route = (
        "/route/v1/driving/"
        "-75.44225386838299,40.651163100000005;-74.0323752,40.7433066"
    )
    har.write_text(
        json.dumps(
                {
                    "log": {
                        "entries": [
                            {
                                "request": {
                                    "method": "GET",
                                    "url": "http://map.test/directions",
                                    "headers": [
                                        {"name": "Accept", "value": "text/html"},
                                        {"name": "Sec-Fetch-Dest", "value": "document"},
                                        {"name": "Sec-Fetch-Mode", "value": "navigate"},
                                        {"name": "Sec-Fetch-User", "value": "?1"},
                                    ],
                                },
                                "response": {
                                    "status": 200,
                                    "redirectURL": "",
                                    "headers": [],
                                    "cookies": [],
                                    "content": {"mimeType": "text/html", "text": ""},
                                },
                            },
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
        evaluator={"kind": "webarena_verified", "task_id": 760, "revision": 6},
        environment={"kind": "webarena_verified"},
    )
    evidence = TaskEvidence(
        outputs={},
        process_passed=True,
        agent_response={"task_type": "navigate", "status": "SUCCESS", "retrieved_data": None},
        network_har_path=har,
    )

    result = WebArenaVerifiedEvaluator(config_path=config).evaluate(task, evidence)

    assert result.status == expected_status


def test_official_task_678_checks_the_order_grid_xhr(tmp_path):
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
                                    "url": "http://shopping-admin.test/admin/sales/order/",
                                    "headers": [
                                        {"name": "Accept", "value": "text/html"},
                                        {"name": "Sec-Fetch-Dest", "value": "document"},
                                        {"name": "Sec-Fetch-Mode", "value": "navigate"},
                                        {"name": "Sec-Fetch-User", "value": "?1"},
                                    ],
                                },
                                "response": {
                                    "status": 200,
                                    "redirectURL": "",
                                    "headers": [],
                                    "cookies": [],
                                    "content": {"mimeType": "text/html", "text": ""},
                                },
                            },
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
        evaluator={"kind": "webarena_verified", "task_id": 678, "revision": 3},
        environment={"kind": "webarena_verified"},
    )
    evidence = TaskEvidence(
        outputs={},
        process_passed=True,
        agent_response={"task_type": "navigate", "status": "SUCCESS", "retrieved_data": None},
        network_har_path=har,
    )

    result = WebArenaVerifiedEvaluator(config_path=config).evaluate(task, evidence)

    assert result.status == "pass"


@pytest.mark.parametrize(
    ("assignee_ids", "expected_status"),
    [([2330], "pass"), ([2331], "fail")],
)
def test_official_task_811_compares_singleton_array_as_an_array(
    tmp_path, assignee_ids, expected_status
):
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
                                    "text": json.dumps(
                                        {"issue": {"assignee_ids": assignee_ids}}
                                    ),
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
        evaluator={"kind": "webarena_verified", "task_id": 811, "revision": 3},
        environment={"kind": "webarena_verified"},
    )
    evidence = TaskEvidence(
        outputs={},
        process_passed=True,
        agent_response={"task_type": "mutate", "status": "SUCCESS", "retrieved_data": None},
        network_har_path=har,
    )

    result = WebArenaVerifiedEvaluator(config_path=config).evaluate(task, evidence)

    assert result.status == expected_status
    assert result.checks[1]["status"] == (
        "success" if expected_status == "pass" else "failure"
    )


def _reddit_entry(url, post_data):
    return {
        "request": {
            "method": "POST",
            "url": url,
            "headers": [{"name": "content-type", "value": "application/x-www-form-urlencoded"}],
            "postData": {
                "mimeType": "application/x-www-form-urlencoded",
                "text": urlencode(post_data),
                "params": [{"name": key, "value": str(value)} for key, value in post_data.items()],
            },
        },
        "response": {
            "status": 302,
            "redirectURL": "",
            "headers": [],
            "cookies": [],
            "content": {"mimeType": "text/html", "text": ""},
        },
    }


def _official_task(task_id):
    return BrowserTask(
        task_id=f"webarena-{task_id}",
        title=f"Official task {task_id}",
        tool_calls=(),
        assertions=(),
        tags=(),
        difficulty="simple",
        ground_truth={"expected_actions": []},
        source={"url": f"https://example.test/{task_id}.trace.zip"},
        evaluator={"kind": "webarena_verified", "task_id": task_id, "revision": 2},
        environment={"kind": "webarena_verified"},
    )


@pytest.mark.parametrize(
    ("retrieved_data", "expected_status"),
    [(None, "pass"), ([0], "fail")],
)
def test_official_task_319_preserves_null_retrieved_data_key(
    tmp_path, retrieved_data, expected_status
):
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
                    "entries": [
                        {
                            "request": {
                                "method": "GET",
                                "url": "http://shopping.test/sales/order/history/",
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
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = WebArenaVerifiedEvaluator(config_path=config).evaluate(
        _official_task(319),
        TaskEvidence(
            outputs={},
            process_passed=True,
            agent_response={
                "task_type": "retrieve",
                "status": "NOT_FOUND_ERROR",
                "retrieved_data": retrieved_data,
            },
            network_har_path=har,
        ),
    )

    assert result.status == expected_status


def test_official_task_604_binds_dynamic_forum_id_from_the_submitted_form(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"environments": {"__REDDIT__": {"urls": ["http://reddit.test"]}}}),
        encoding="utf-8",
    )
    har = tmp_path / "network.har"
    har.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        _reddit_entry(
                            "http://reddit.test/submit",
                            {
                                "submission[title]": "what is the SOTA web navigation agent repo",
                                "submission[forum]": "10043",
                            },
                        )
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    result = WebArenaVerifiedEvaluator(config_path=config).evaluate(
        _official_task(604),
        TaskEvidence(
            outputs={},
            process_passed=True,
            agent_response={"task_type": "mutate", "status": "SUCCESS", "retrieved_data": None},
            network_har_path=har,
        ),
    )

    assert result.status == "pass"


@pytest.mark.parametrize(
    ("comment_key", "expected_status"),
    [("reply_to_submission_69[comment]", "pass"), ("reply_to_submission_70[comment]", "fail")],
)
def test_official_task_611_requires_one_post_id_in_comment_url_and_form_key(
    tmp_path, comment_key, expected_status
):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"environments": {"__REDDIT__": {"urls": ["http://reddit.test"]}}}),
        encoding="utf-8",
    )
    har = tmp_path / "network.har"
    har.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        _reddit_entry(
                            "http://reddit.test/submit",
                            {"submission[title]": "Harry Potter", "submission[forum]": "10037"},
                        ),
                        _reddit_entry(
                            "http://reddit.test/f/books/69/-/comment",
                            {comment_key: "Wonderful journey"},
                        ),
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    result = WebArenaVerifiedEvaluator(config_path=config).evaluate(
        _official_task(611),
        TaskEvidence(
            outputs={},
            process_passed=True,
            agent_response={"task_type": "mutate", "status": "SUCCESS", "retrieved_data": None},
            network_har_path=har,
        ),
    )

    assert result.status == expected_status
