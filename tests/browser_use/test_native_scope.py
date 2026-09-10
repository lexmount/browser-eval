from __future__ import annotations

from pathlib import Path

from scripts.build_webarena_native_scope import build_scope


def test_amd64_scope_excludes_non_native_and_unscoreable_tasks():
    result = build_scope(
        Path("evaluation/browser-use-public/webarena-human-suite.json"),
        Path("evaluation/browser-use-public/webarena-environment-images.json"),
        "amd64",
    )

    assert result["candidate_count"] == 179
    assert result["included_count"] == 172
    assert result["excluded_count"] == 7
    assert [row["task_id"] for row in result["excluded_tasks"]] == [
        "webarena-97",
        "webarena-268",
        "webarena-426",
        "webarena-678",
        "webarena-741",
        "webarena-760",
        "webarena-811",
    ]
    by_id = {row["task_id"]: row for row in result["excluded_tasks"]}
    for task_id in ("webarena-97", "webarena-268", "webarena-426", "webarena-741"):
        assert by_id[task_id]["incompatible_sites"] == ["wikipedia"]
        assert by_id[task_id]["exclusion_kind"] == "non_native_site_image"
    for task_id in ("webarena-678", "webarena-760", "webarena-811"):
        assert by_id[task_id]["incompatible_sites"] == []
        assert by_id[task_id]["exclusion_kind"] == "confirmed_upstream_evaluator_defect"
        assert by_id[task_id]["evidence_id"].startswith("wv-1.2.3-")
    assert {
        row["id"]: row["task_count"] for row in result["qualification_batches"]
    } == {
        "gitlab": 36,
        "gitlab+reddit": 4,
        "map": 26,
        "reddit": 19,
        "reddit+shopping": 1,
        "shopping": 46,
        "shopping_admin": 40,
    }
    assert sum(row["task_count"] for row in result["qualification_batches"]) == 172
    assert {
        row["id"]: row["shard_count"] for row in result["qualification_batches"]
    } == {
        "gitlab": 3,
        "gitlab+reddit": 1,
        "map": 3,
        "reddit": 2,
        "reddit+shopping": 1,
        "shopping": 4,
        "shopping_admin": 4,
    }
