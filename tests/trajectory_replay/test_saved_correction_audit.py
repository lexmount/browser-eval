import hashlib
import json

import pytest

from scripts.audit_saved_replay_correction import audit_saved_correction


def fixture(tmp_path):
    task = tmp_path / "task-448"
    task.mkdir()
    for name in ("network.har", "trace.json"):
        (task / name).write_text("{}")
    site_map = tmp_path / "sites.json"
    site_map.write_text("{}")
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text("{}")
    after.write_text("{}")
    digest = hashlib.sha256(b"{}").hexdigest()
    receipt = {"method_id": "release", "site_map_sha256": digest, "results": [{
        "task_id": 448, "status": "fail", "score": None, "completed_actions": 3,
        "action_count": 8, "evidence_sha256": {"trace": digest, "network_har": digest},
    }]}
    (tmp_path / "receipt.json").write_text(json.dumps(receipt))
    return dict(task_dir=task, task_id=448, site_map=site_map,
                before_catalog=before, after_catalog=after)


def test_runtime_failure_is_not_rescored_using_an_invented_success_response(tmp_path):
    result = audit_saved_correction(**fixture(tmp_path))
    assert result["evaluation_reached"] is False
    assert result["saved_execution_status"] == "fail"
    assert result["browser_calls"] == 0
    assert result["score_changed"] is False
    assert "before" not in result and "after" not in result


@pytest.mark.parametrize("change", ["trace", "sites", "missing", "duplicate", "symlink"])
def test_audit_rejects_changed_or_ambiguous_evidence(tmp_path, change):
    inputs = fixture(tmp_path)
    if change == "trace":
        (inputs["task_dir"] / "trace.json").write_text("changed")
    elif change == "sites":
        inputs["site_map"].write_text("changed")
    elif change == "missing":
        (inputs["task_dir"] / "trace.json").unlink()
    elif change == "duplicate":
        path = tmp_path / "receipt.json"
        receipt = json.loads(path.read_text())
        receipt["results"] *= 2
        path.write_text(json.dumps(receipt))
    else:
        linked = tmp_path / "linked.json"
        linked.symlink_to(inputs["before_catalog"])
        inputs["before_catalog"] = linked
    with pytest.raises(ValueError):
        audit_saved_correction(**inputs)
