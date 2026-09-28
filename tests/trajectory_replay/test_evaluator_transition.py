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
        "compatible_sources": transition["compatible_sources"],
        "from_evaluator_checksum": transition["from_evaluator_checksum"],
        "from_data_checksum": transition["from_data_checksum"],
        "to_revision": transition["to_revision"],
        "to_evaluator_checksum": transition["to_evaluator_checksum"],
        "to_data_checksum": transition["to_data_checksum"],
        "row_count": 1,
        "excluded_task_ids": [],
        "rows": [
            {
                "task_id": 7,
                "old_evaluation_sha256": "1" * 64,
                "actual_response_sha256": "2" * 64,
                "network_har_sha256": "3" * 64,
                "current_evaluation_sha256": "4" * 64,
                "run_inputs_sha256": "5" * 64,
                "source": transition["compatible_sources"][0],
            }
        ],
    }


def test_transition_authority_is_bound_to_the_installed_fork_revision():
    transition = _transition()

    assert transition["from_revision"] != transition["to_revision"]
    assert transition["to_evaluator_checksum"] == (
        evaluator_transition.compute_evaluator_checksum()
    )


def test_transition_resolves_revision_and_legacy_evaluator_sources():
    transition = _transition()

    revision = evaluator_transition.compatible_source(
        transition,
        revision="4c3b14e5cda91889f5ee883c8661bccd1d96c1af",
        evaluator_fixes_sha256=None,
    )
    legacy = evaluator_transition.compatible_source(
        transition,
        revision=None,
        evaluator_fixes_sha256=(
            "58b451900c239ed6fb9a9a99633409bc56190676b9f2273ed45c472e8eb1ff08"
        ),
    )

    assert revision["evaluator_checksum"] == (
        "7f6b1efd6ae2f88643f623a8ab4b1aeb107c0fa912b59f5ffd753de20337a226"
    )
    assert legacy["data_checksum"] == (
        "7ab0ef7e61af98efc5e16ba2b4303ddc0b8f5b66b5c88c58cb76e89e9235b57a"
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
