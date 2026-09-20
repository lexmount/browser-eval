import copy

import pytest

from scripts.audit_replay_comparison_inputs import audit_dataset_changes, audit_inputs


def inputs():
    profile = {"methods": [{"id": "a"}, {"id": "b"}], "task_selection": {"task_count": 1}}
    results = {"attempts": [{"participant_id": method, "task_id": 1, "planned_actions": [{"action": "click", "selector": "button"}], "task_contract_sha256": "same"} for method in ("a", "b")]}
    corrections = {"corrections": [{"task_id": 1, "correction_id": "changed", "operations": [{"op": "remove", "path": "/eval/1"}]}]}
    return results, profile, corrections


def test_equal_inputs_do_not_hide_removed_acceptance():
    audit = audit_inputs(*inputs())
    assert audit["unequal_actions_task_ids"] == []
    assert audit["local_contract_edits"][0]["removed_evaluators"] == ["/eval/1"]
    assert audit["local_contract_edits"][0]["has_bound_root_cause_and_regressions"] is False


def test_browser_specific_actions_and_contracts_are_detected():
    results, profile, corrections = copy.deepcopy(inputs())
    results["attempts"][1]["planned_actions"][0]["selector"] = "#bypass"
    results["attempts"][1]["task_contract_sha256"] = "different"
    audit = audit_inputs(results, profile, corrections)
    assert audit["unequal_actions_task_ids"] == [1]
    assert audit["unequal_contract_task_ids"] == [1]


@pytest.mark.parametrize("mode", ["missing", "duplicate", "unknown"])
def test_incomplete_or_ambiguous_matrix_is_rejected(mode):
    results, profile, corrections = inputs()
    if mode == "missing":
        results["attempts"].pop()
    elif mode == "duplicate":
        results["attempts"].append(results["attempts"][0])
    else:
        results["attempts"][0]["participant_id"] = "unregistered"
    with pytest.raises(ValueError):
        audit_inputs(results, profile, corrections)


def test_fork_inventory_keeps_out_of_scope_changes_and_exact_fields():
    old = [{"task_id": 1, "intent": "email"}, {"task_id": 2, "intent": "unchanged"}]
    new = [{"task_id": 1, "intent": "nickname"}, {"task_id": 3, "intent": "new"}]
    audit = audit_dataset_changes(old, new, {1})
    assert [row["change"] for row in audit] == ["modified", "removed", "added"]
    assert [row["selected"] for row in audit] == [True, False, False]
    assert audit[0]["fields"]["intent"] == {"before": "email", "after": "nickname"}


def test_duplicate_fork_ids_cannot_hide_changed_contract():
    with pytest.raises(ValueError):
        audit_dataset_changes([{"task_id": 1}, {"task_id": 1}], [], {1})
