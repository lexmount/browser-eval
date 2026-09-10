from scripts.propose_webarena_observed_repair import propose_observed_repair


def _suite():
    return {
        "tasks": [
            {
                "id": "webarena-1",
                "trajectory": {"actions": [{"action": "click", "selector": "#open"}]},
            }
        ]
    }


def test_observed_repair_appends_only_declared_dom_observations():
    proposal = propose_observed_repair(
        _suite(),
        {
            "task_id": "webarena-1",
            "observation_actions": [
                {
                    "action": "get_all_text",
                    "selector": "tbody td.name",
                    "save_as": "names",
                },
                {
                    "action": "get_all_text",
                    "selector": "tbody td.email",
                    "save_as": "emails",
                },
            ],
            "response_builder": {
                "kind": "observed_json_zip",
                "task_type": "retrieve",
                "fields": {"name": "names", "email": "emails"},
            },
            "reason": "Read the filtered result table.",
        },
    )

    assert [action["action"] for action in proposal["replace_actions"]] == [
        "click",
        "get_all_text",
        "get_all_text",
    ]
    assert proposal["response_builder"]["fields"] == {
        "name": "names",
        "email": "emails",
    }


def test_observed_repair_rejects_actions_or_outputs_outside_declared_observation():
    base = {
        "task_id": "webarena-1",
        "observation_actions": [
            {"action": "get_all_text", "selector": "tbody td", "save_as": "rows"}
        ],
        "response_builder": {
            "kind": "observed_json",
            "task_type": "retrieve",
            "output": "other",
        },
        "reason": "Read rows.",
    }
    try:
        propose_observed_repair(_suite(), base)
    except ValueError as exc:
        assert "exactly match" in str(exc)
    else:
        raise AssertionError("undeclared output must be rejected")

    base["observation_actions"] = [
        {"action": "click", "selector": "#answer", "save_as": "other"}
    ]
    try:
        propose_observed_repair(_suite(), base)
    except ValueError as exc:
        assert "read-only" in str(exc)
    else:
        raise AssertionError("mutating repair action must be rejected")
