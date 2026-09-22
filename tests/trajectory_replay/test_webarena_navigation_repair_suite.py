import json

from scripts.build_webarena_navigation_repair_suite import build_navigation_suite


def test_navigation_suite_uses_page_url_and_graphql_referer(tmp_path):
    official = [
        {
            "task_id": 0,
            "intent": "open cart",
            "revision": 1,
            "eval": [
                {"expected": {"task_type": "navigate", "status": "SUCCESS"}},
                {"expected": {"url": "__SHOPPING__/checkout/cart/"}},
            ],
        },
        {
            "task_id": 1,
            "intent": "filter issues",
            "revision": 1,
            "eval": [
                {"expected": {"task_type": "navigate", "status": "SUCCESS"}},
                {"expected": {"url": "^__GITLAB__/group/project/-/issues.*$"}},
                {
                    "expected": {
                        "url": "__GITLAB__/api/graphql",
                        "http_method": "POST",
                        "headers": {
                            "referer": "__GITLAB__/group/project/-/issues/?state=opened"
                        },
                    }
                },
            ],
        },
    ]
    ledger = {"rows": [{"task_id": 0, "status": "fail"}, {"task_id": 1, "status": "fail"}]}
    sites = {
        "sites": {
            "shopping": "https://shop.test/",
            "shopping_admin": "https://admin.test/admin",
            "reddit": "https://reddit.test/",
            "gitlab": "https://git.test/",
            "map": "https://map.test",
            "wikipedia": "https://wiki.test/",
        }
    }
    paths = []
    for name, value in (("official", official), ("ledger", ledger), ("sites", sites)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths.append(path)
    result = build_navigation_suite(*paths)
    assert [task["trajectory"]["actions"][0]["url"] for task in result["tasks"]] == [
        "https://shop.test/checkout/cart/",
        "https://git.test/group/project/-/issues/?state=opened",
    ]


def test_navigation_suite_excludes_passes_not_found_and_regex_only(tmp_path):
    def row(task_id, status, url):
        return {
            "task_id": task_id,
            "intent": str(task_id),
            "revision": 1,
            "eval": [
                {"expected": {"task_type": "navigate", "status": status}},
                {"expected": {"url": url}},
            ],
        }

    official = [
        row(0, "SUCCESS", "__SHOPPING__/ok"),
        row(1, "SUCCESS", "__SHOPPING__/passed"),
        row(2, "NOT_FOUND_ERROR", "__SHOPPING__/history"),
        row(3, "SUCCESS", "^.*/route/v1/.*$"),
    ]
    ledger = {
        "rows": [
            {"task_id": 0, "status": "fail"},
            {"task_id": 1, "status": "pass"},
            {"task_id": 2, "status": "fail"},
            {"task_id": 3, "status": "fail"},
        ]
    }
    sites = {
        "sites": {
            "shopping": "https://shop.test/",
            "shopping_admin": "https://admin.test",
            "reddit": "https://reddit.test",
            "gitlab": "https://git.test",
            "map": "https://map.test",
            "wikipedia": "https://wiki.test",
        }
    }
    paths = []
    for name, value in (("official", official), ("ledger", ledger), ("sites", sites)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths.append(path)
    result = build_navigation_suite(*paths)
    assert [task["id"] for task in result["tasks"]] == ["webarena-0"]
