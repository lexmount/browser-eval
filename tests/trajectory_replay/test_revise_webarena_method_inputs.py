import copy
import json

import pytest
from webarena_verified.types.task import WebArenaSite

from harness.trajectory_replay.retrieval_acceptance import digest
from scripts.revise_webarena_method_inputs import revise


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
    result = revise(prior, revision, {WebArenaSite.REDDIT: "http://reddit.test"})
    assert prior == saved
    assert result["task_ids"] == [1, 2]
    assert result["tasks"][0]["actions"][0]["text"] == "http://reddit.test/image.jpg"
    assert result["tasks"][1]["actions"] == calls
    revision["2"]["prior_calls_sha256"] = "wrong"
    with pytest.raises(ValueError, match="predecessor"):
        revise(prior, revision, {WebArenaSite.REDDIT: "http://reddit.test"})
