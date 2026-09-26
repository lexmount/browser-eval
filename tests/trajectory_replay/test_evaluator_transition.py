from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.trajectory_replay import evaluator_transition


def _transition():
    return evaluator_transition.load_transition(
        Path(
            "evaluation/trajectory-replay/webarena-verified-full/evaluator-transition.json"
        )
    )


def _receipt(transition):
    return {
        "schema": evaluator_transition.RECEIPT_SCHEMA,
        "transition_sha256": evaluator_transition.json_sha256(transition),
        "from_revision": transition["from_revision"],
        "compatible_from_revisions": transition["compatible_from_revisions"],
        "from_evaluator_checksum": transition["from_evaluator_checksum"],
        "from_data_checksum": transition["from_data_checksum"],
        "to_revision": transition["to_revision"],
        "to_evaluator_checksum": transition["to_evaluator_checksum"],
        "to_data_checksum": transition["to_data_checksum"],
        "row_count": 1,
        "rows": [
            {
                "task_id": 7,
                "old_evaluation_sha256": "1" * 64,
                "actual_response_sha256": "2" * 64,
                "network_har_sha256": "3" * 64,
                "current_evaluation_sha256": "4" * 64,
                "run_inputs_sha256": "5" * 64,
            }
        ],
    }


def test_transition_authority_is_bound_to_the_installed_fork_revision():
    transition = _transition()

    assert transition["from_revision"] != transition["to_revision"]
    assert transition["to_evaluator_checksum"] == (
        evaluator_transition.compute_evaluator_checksum()
    )


def test_transition_receipt_binds_each_old_evaluation_once(tmp_path):
    transition = _transition()
    receipt = _receipt(transition)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))

    indexed = evaluator_transition.load_receipt(path, transition)

    assert indexed[(7, "1" * 64)]["current_evaluation_sha256"] == "4" * 64


@pytest.mark.parametrize("change", ["extra_row", "wrong_transition", "wrong_count"])
def test_transition_receipt_rejects_unbound_or_duplicate_evidence(tmp_path, change):
    transition = _transition()
    receipt = _receipt(transition)
    if change == "extra_row":
        receipt["rows"].append(dict(receipt["rows"][0]))
        receipt["row_count"] = 2
    elif change == "wrong_transition":
        receipt["transition_sha256"] = "0" * 64
    else:
        receipt["row_count"] = 2
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))

    with pytest.raises(ValueError, match="transition receipt"):
        evaluator_transition.load_receipt(path, transition)
