import copy
import hashlib
import json

import pytest
from webarena_verified.types.task import WebArenaSite

from harness.trajectory_replay.remote_runtime import revise_method_inputs
from harness.trajectory_replay.retrieval_acceptance import digest


@pytest.fixture
def revision_inputs(tmp_path):
    calls = [{"action": "snapshot"}]
    prior = {"tasks": [{"task_id": 1, "source_actions": calls,
                        "source_calls_sha256": digest(calls), "readiness": []}]}
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps({"task_id": 1, "tool_calls": [{"action": "back"}]}))
    revisions = {"1": {"cause": "observed navigation mismatch",
                       "prior_calls_sha256": digest(calls), "candidate": str(path)}}
    return prior, revisions, path


@pytest.mark.parametrize("damage", ["duplicate-task", "empty", "duplicate-revision", "stale-source",
                                   "empty-actions", "wrong-task", "linked-parent"])
def test_revision_rejects_ambiguous_or_unbound_inputs(revision_inputs, damage, tmp_path):
    prior, revisions, path = revision_inputs
    if damage == "duplicate-task":
        prior["tasks"].append(copy.deepcopy(prior["tasks"][0]))
    elif damage == "empty":
        revisions.clear()
    elif damage == "duplicate-revision":
        revisions["01"] = revisions["1"]
    elif damage == "stale-source":
        prior["tasks"][0]["source_actions"] = [{"action": "forward"}]
    elif damage in {"empty-actions", "wrong-task"}:
        data = json.loads(path.read_bytes())
        if damage == "empty-actions":
            data["tool_calls"] = []
        else:
            data["task_id"] = 2
        path.write_text(json.dumps(data))
    else:
        parent = tmp_path / "alias"
        parent.symlink_to(tmp_path, target_is_directory=True)
        revisions["1"]["candidate"] = str(parent / path.name)
    before = copy.deepcopy(prior)
    with pytest.raises(ValueError):
        revise_method_inputs(prior, revisions, {WebArenaSite.REDDIT: "http://reddit.test"})
    assert prior == before


def test_revision_rejects_candidate_changed_during_rebinding(revision_inputs, monkeypatch):
    from harness.trajectory_replay import remote_runtime as runtime

    prior, revisions, path = revision_inputs
    before = copy.deepcopy(prior)
    rebind = runtime.rebind_remote_calls

    def changing(*args):
        value = rebind(*args)
        path.write_text("{}")
        return value

    monkeypatch.setattr(runtime, "rebind_remote_calls", changing)
    with pytest.raises(ValueError, match="input changed"):
        revise_method_inputs(prior, revisions, {WebArenaSite.REDDIT: "http://reddit.test"})
    assert prior == before


def test_revision_command_preserves_existing_output_before_loading_inputs(tmp_path):
    from harness.trajectory_replay.remote_runtime import main_revise

    output = tmp_path / "manifest.json"
    output.write_bytes(b"retained")
    with pytest.raises(ValueError, match="append-only"):
        main_revise(["--prior", str(tmp_path / "missing"), "--revisions", str(tmp_path / "missing"),
                     "--site-map", str(tmp_path / "missing"), "--output", str(output)])
    assert output.read_bytes() == b"retained"


def test_revision_command_binds_new_manifest_and_preserves_sources(revision_inputs, tmp_path):
    from harness.trajectory_replay.remote_runtime import main_revise

    prior, revisions, _ = revision_inputs
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({"schema": "lex_webarena_remote_sites/1", "sites": {
        "reddit": "http://reddit.test", "gitlab": "http://gitlab.test",
        "shopping": "http://shopping.test", "shopping_admin": "http://admin.test/admin",
    }}))
    prior["site_map_sha256"] = hashlib.sha256(sites.read_bytes()).hexdigest()
    prior_path, revisions_path = tmp_path / "prior.json", tmp_path / "revisions.json"
    prior_path.write_text(json.dumps(prior))
    revisions_path.write_text(json.dumps(revisions))
    before = {path: path.read_bytes() for path in (prior_path, revisions_path, sites)}
    output = tmp_path / "new" / "manifest.json"
    args = ["--prior", str(prior_path), "--revisions", str(revisions_path),
            "--site-map", str(sites), "--output", str(output)]
    main_revise(args)
    result = json.loads(output.read_bytes())
    assert result["predecessor_manifest_sha256"] == hashlib.sha256(before[prior_path]).hexdigest()
    assert result["tasks"][0]["actions"] == [{"action": "back"}]
    assert all(path.read_bytes() == raw for path, raw in before.items())
    generated = output.read_bytes()
    with pytest.raises(ValueError, match="append-only"):
        main_revise(args)
    assert output.read_bytes() == generated


def test_repair_preserves_predecessor_and_binds_new_calls(tmp_path):
    old = [{"action": "fill", "text": "http://192.168.5.2:9999/image.jpg"}]
    prior = {
        "task_ids": [2, 1],
        "tasks": [
            {
                "task_id": n,
                "source_actions": old,
                "source_calls_sha256": digest(old),
                "readiness": [],
            }
            for n in [2, 1]
        ],
    }
    saved = copy.deepcopy(prior)
    candidate = tmp_path / "candidate.json"
    calls = [{"action": "snapshot"}]
    candidate.write_text(json.dumps({"task_id": 2, "tool_calls": calls}))
    revision = {
        "2": {
            "cause": "dynamic selector",
            "prior_calls_sha256": digest(old),
            "candidate": str(candidate),
        }
    }
    result = revise_method_inputs(prior, revision, {WebArenaSite.REDDIT: "http://reddit.test"})
    assert prior == saved
    assert result["task_ids"] == [1, 2]
    assert result["tasks"][0]["actions"][0]["text"] == "http://reddit.test/image.jpg"
    assert result["tasks"][1]["actions"] == calls
    revision["2"]["prior_calls_sha256"] = "wrong"
    with pytest.raises(ValueError, match="predecessor"):
        revise_method_inputs(prior, revision, {WebArenaSite.REDDIT: "http://reddit.test"})
