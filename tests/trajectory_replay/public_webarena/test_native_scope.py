from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.public_webarena.qualification import build_native_scope
from harness.trajectory_replay.report_inputs import replay_input_path


@pytest.fixture
def native_inputs(tmp_path):
    suite, images = tmp_path / "suite.json", tmp_path / "images.json"
    suite.write_text(json.dumps({"tasks": [
        {"id": f"custom-{n}", "environment": {"sites": ["one"]}} for n in range(3)
    ] + [{"id": "other", "environment": {"sites": ["two"]}}]}))
    images.write_text(json.dumps({"images": {
        "one": {"architecture": "arm64"}, "two": {"architecture": "amd64"}
    }}))
    return suite, images


@pytest.mark.parametrize("size,shards", [(1, 3), (2, 2), (12, 1)])
def test_arbitrary_population_and_declared_shard_size(native_inputs, size, shards):
    suite, images = native_inputs
    before = {path: path.read_bytes() for path in native_inputs}
    result = build_native_scope(suite, images, "arm64", None,
                                max_tasks_per_qualification_shard=size)
    assert result["included_task_ids"] == ["custom-0", "custom-1", "custom-2"]
    assert result["excluded_tasks"][0]["task_id"] == "other"
    assert result["qualification_batches"][0]["shard_count"] == shards
    assert result["suite_sha256"] == hashlib.sha256(before[suite]).hexdigest()
    assert all(path.read_bytes() == raw for path, raw in before.items())


@pytest.mark.parametrize("damage", ["duplicates", "unbound-site", "invalid-architecture", "linked"])
def test_invalid_scope_is_rejected(native_inputs, damage, tmp_path):
    suite, images = native_inputs
    if damage == "linked":
        alias = tmp_path / "linked"
        alias.symlink_to(images)
        images = alias
    elif damage == "invalid-architecture":
        images.write_text(json.dumps({"images": {"one": {"architecture": None}}}))
    else:
        data = json.loads(suite.read_bytes())
        if damage == "duplicates":
            data["tasks"].append(data["tasks"][0])
        else:
            data["tasks"][0]["environment"]["sites"] = ["unbound"]
        suite.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        build_native_scope(suite, images, "arm64", None)


def test_scope_rejects_source_change_during_selection(native_inputs, monkeypatch):
    from harness.trajectory_replay.public_webarena import qualification as q

    suite, images = native_inputs
    original = q.safe_identifier

    def validate(value):
        images.write_text("{}")
        return original(value)

    monkeypatch.setattr(q, "safe_identifier", validate)
    with pytest.raises(ValueError, match="input changed"):
        build_native_scope(suite, images, "arm64", None)


def test_scope_command_preserves_existing_output_before_inspecting_inputs(tmp_path):
    from harness.trajectory_replay.public_webarena.qualification import main_native_scope

    output = tmp_path / "scope.json"
    output.write_bytes(b"retained")
    with pytest.raises(ValueError, match="already exists"):
        main_native_scope(["--suite", str(tmp_path / "missing"), "--output", str(output)])
    assert output.read_bytes() == b"retained"


def test_amd64_scope_excludes_non_native_and_unscoreable_tasks():
    result = build_native_scope(
        replay_input_path(current="human-candidates"),
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
