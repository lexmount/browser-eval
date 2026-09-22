from __future__ import annotations

import importlib.resources
from pathlib import Path

from webarena_verified.types.task import WebArenaSite

from harness.trajectory_replay.remote_runtime import rebind_remote_calls
from scripts.build_webarena_authored_suite import build_authored_suite
from scripts.run_webarena_full_chromium import TRUSTED_RECORDED_HOSTS


def test_unrecorded_nolan_paths_use_the_frozen_wikipedia_source() -> None:
    official = Path(
        str(
            importlib.resources.files("webarena_verified").joinpath(
                "assets/dataset/webarena-verified.json"
            )
        )
    )
    suite = build_authored_suite(official)
    by_id = {int(task["id"].removeprefix("webarena-")): task for task in suite["tasks"]}
    for task_id in range(556, 562):
        actions = by_id[task_id]["trajectory"]["actions"]
        assert actions[0]["action"] == "navigate"
        assert "wikipedia.example.com:8888/content/" in actions[0]["url"]
        assert actions[1]["action"] in {"get_text", "get_all_text"}
        rebound = rebind_remote_calls(
            actions[:2],
            {WebArenaSite.WIKIPEDIA: "https://frozen-wiki.test"},
            set(TRUSTED_RECORDED_HOSTS),
        )
        assert rebound[0]["url"].startswith("https://frozen-wiki.test/content/")
    for task_id in range(763, 768):
        actions = by_id[task_id]["trajectory"]["actions"]
        assert next(action for action in actions if action["action"] == "select")["value"] == "0"
