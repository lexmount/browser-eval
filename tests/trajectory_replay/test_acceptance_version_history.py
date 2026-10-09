"""A report may append new scoring revisions, but must not hide their scope."""
import hashlib
import json

import pytest

from harness.trajectory_replay.webarena_report import _corrected_acceptance_by_task


def test_appended_scoring_revision_requires_exact_method_declaration(tmp_path):
    attempts = []
    declarations = {448: {}}
    for method, revision in (("historical", "old"), ("appended", "new")):
        identity = {"correction_id": revision, "corrected_task_sha256": revision * 32}
        declarations[448][method] = identity
        value = {
            "task_id": 448,
            "contract_correction_id": revision,
            "corrected_task_sha256": identity["corrected_task_sha256"],
            "evaluators_results": [
                {"evaluator_name": "network", "expected": {"last_event_only": revision == "new"}}
            ],
        }
        path = tmp_path / f"{method}.json"
        path.write_text(json.dumps(value))
        attempts.append(
            {
                "task_id": 448,
                "participant_id": method,
                "evidence": {
                    "evaluation": {
                        "path": str(path),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                },
            }
        )
    with pytest.raises(ValueError, match="acceptance differs"):
        _corrected_acceptance_by_task(attempts)
    actual = _corrected_acceptance_by_task(attempts, declared_revisions=declarations)
    versions = actual[448]["versions"]
    assert [row["methods"] for row in versions] == [["historical"], ["appended"]]
    assert [row["evaluators"][0]["expected"]["last_event_only"] for row in versions] == [
        False,
        True,
    ]
    declarations[448]["appended"] = declarations[448]["historical"]
    with pytest.raises(ValueError, match="declared acceptance"):
        _corrected_acceptance_by_task(attempts, declared_revisions=declarations)
