from harness.trajectory_replay.public_webarena.suite import _recorded_start_urls


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

    assert _recorded_start_urls(row, {"recorded_tab_urls": ["https://example.com/"]}) == [
        "__REDDIT__",
        "__GITLAB__",
    ]


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


def test_build_declared_population_and_preserve_existing_suite(tmp_path, monkeypatch):
    import hashlib
    import json

    import pytest

    from harness.trajectory_replay.public_webarena import webarena
    from harness.trajectory_replay.public_webarena.suite import main_build_suite

    rows = [
        {
            "task_id": task,
            "file_name": f"{task}.trace.zip",
            "download_url": f"https://drive.google.com/uc?id={task}",
            "sites": ["reddit"],
            "start_urls": ["__REDDIT__"],
            "verified_intent": f"Synthetic task {task}",
            "verified_revision": "synthetic",
            "intent_template_id": task,
            "verified_evaluators": [
                {"evaluator": "AgentResponseEvaluator", "expected": {"task_type": "retrieve"}}
            ],
        }
        for task in (1, 2)
    ]
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "count": 2,
                "traces": rows,
                "source": {
                    "folder_url": "https://drive.google.com/drive/folders/synthetic",
                    "webarena_commit": "synthetic",
                    "webarena_verified_commit": "synthetic",
                },
            }
        )
    )
    for row in rows:
        (tmp_path / row["file_name"]).write_bytes(b"synthetic trace")
    monkeypatch.setattr(
        webarena,
        "import_playwright_trace",
        lambda *args, **kwargs: {
            "schema": "lex_browser_trajectory/1",
            "actions": [{"action": "snapshot"}],
        },
    )
    output = tmp_path / "suite.json"
    argv = ["--catalog", str(catalog), "--trace-dir", str(tmp_path), "--output", str(output)]
    main_build_suite(argv)
    before = output.read_bytes()
    suite = json.loads(before)
    assert [task["id"] for task in suite["tasks"]] == ["webarena-1", "webarena-2"]
    assert suite["benchmark"]["trace_count"] == 2
    assert suite["benchmark"]["selection"] == "declared trace catalog population"
    assert suite["benchmark"]["catalog_sha256"] == hashlib.sha256(catalog.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="output already exists"):
        main_build_suite(argv)
    assert output.read_bytes() == before


def test_catalog_drift_cannot_publish_suite(tmp_path, monkeypatch):
    import json

    import pytest

    from harness.trajectory_replay.public_webarena import suite

    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"count": 1}))
    output = tmp_path / "suite.json"

    def build(document, traces):
        catalog.write_text("{}")
        return {"benchmark": {}}

    monkeypatch.setattr(suite, "build_suite", build)
    with pytest.raises(ValueError, match="catalog changed"):
        suite.main_build_suite(
            ["--catalog", str(catalog), "--trace-dir", str(tmp_path), "--output", str(output)]
        )
    assert not output.exists()
