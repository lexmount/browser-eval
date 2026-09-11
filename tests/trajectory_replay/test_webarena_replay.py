from harness.trajectory_replay.webarena_replay import _rebind_url, select_tasks


def test_select_tasks_requires_clean_canonical_candidates() -> None:
    manifest = {
        "tasks": [
            {"task_id": 1, "replay_status": "ready"},
            {"task_id": 2, "replay_status": "all_methods_failed"},
        ]
    }

    assert [task["task_id"] for task in select_tasks(manifest, [1])] == [1]


def test_rebind_url_changes_only_known_local_site_ports() -> None:
    assert (
        _rebind_url("http://192.168.5.2:8023/group/project/-/issues?state=opened", "192.168.9.2")
        == "http://192.168.9.2:8023/group/project/-/issues?state=opened"
    )
    assert _rebind_url("https://example.com:8023/path", "192.168.9.2") == (
        "https://192.168.9.2:8023/path"
    )
    assert _rebind_url("https://example.com/path", "192.168.9.2") == ("https://example.com/path")
