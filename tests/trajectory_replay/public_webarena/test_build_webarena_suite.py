from scripts.build_webarena_suite import _recorded_start_urls


def test_recorded_tab_order_reorders_multi_site_start_urls():
    row = {
        "task_id": 682,
        "sites": ["reddit", "gitlab"],
        "start_urls": ["__REDDIT__", "__GITLAB__"],
    }
    trajectory = {
        "recorded_tab_urls": [
            "http://old.example:8023/project",
            "http://old.example:9999/f/technology",
        ]
    }

    assert _recorded_start_urls(row, trajectory) == ["__GITLAB__", "__REDDIT__"]


def test_unknown_or_incomplete_recorded_tab_order_keeps_declared_order():
    row = {
        "task_id": 1,
        "sites": ["reddit", "gitlab"],
        "start_urls": ["__REDDIT__", "__GITLAB__"],
    }

    assert _recorded_start_urls(
        row, {"recorded_tab_urls": ["https://example.com/"]}
    ) == ["__REDDIT__", "__GITLAB__"]


def test_historical_reddit_trace_port_is_recognized():
    row = {
        "task_id": 566,
        "sites": ["gitlab", "reddit"],
        "start_urls": ["__GITLAB__", "__REDDIT__"],
    }
    trajectory = {
        "recorded_tab_urls": [
            "http://old.example:8023/project",
            "http://old.example:4399/f/diy",
        ]
    }

    assert _recorded_start_urls(row, trajectory) == ["__GITLAB__", "__REDDIT__"]
