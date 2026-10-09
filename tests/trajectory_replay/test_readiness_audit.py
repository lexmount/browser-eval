from scripts.audit_replay_readiness import snapshot_readiness_gaps


def test_disabled_snapshot_is_not_relabelled_as_failed_dispatch():
    trace = [
        {"index": 5, "result": "[30] button disabled: Add to Cart"},
        {
            "index": 6,
            "arguments": {"action": "click", "selector": 'button:visible:has-text("Add to Cart")'},
            "status": "success",
        },
    ]
    rows = snapshot_readiness_gaps(trace)
    assert rows[0]["click_step"] == 6
    assert rows[0]["dispatch_state_known"] is False
    assert trace[1]["status"] == "success"
    trace[1]["action_diagnostics"] = {"click_dispatch_state": {"disabled": False}}
    assert snapshot_readiness_gaps(trace)[0]["dispatch_state_known"] is True


def test_audit_does_not_match_other_button_or_cross_a_wait():
    snapshot = {"index": 1, "result": "button disabled: Other"}
    click = {"index": 3, "arguments": {"action": "click", "selector": 'button:has-text("Act")'}}
    assert snapshot_readiness_gaps([snapshot, click]) == []
    snapshot["result"] = "button disabled: Act"
    assert (
        snapshot_readiness_gaps([snapshot, {"index": 2, "result": "Wait completed."}, click]) == []
    )
