from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.browser_use.selection import (
    TIER_COUNTS,
    PilotSelection,
    PilotTask,
    _task_exclusion,
    _validate_json_schema,
    load_pilot,
    validate_against_official,
)


def test_pilot_has_pre_registered_tier_counts() -> None:
    selection = load_pilot(Path("evaluation/browser-use/webarena-verified-pilot.json"))
    assert len(selection.tasks) == 30
    assert {
        tier: sum(task.tier == tier for task in selection.tasks) for tier in TIER_COUNTS
    } == TIER_COUNTS
    assert sum(task.official_hard for task in selection.tasks) == 16
    assert len(selection.sha256) == 64


def test_unstable_task_exclusions_are_machine_readable() -> None:
    assert "results_schema" in _task_exclusion(68)["reason"]
    assert "category membership" in _task_exclusion(142)["reason"]
    assert _task_exclusion(44) is None


@pytest.mark.parametrize(
    ("value", "schema"),
    [
        ({"value": "ok"}, {"type": "object"}),
        (["ok"], {"type": "array"}),
        ("ok", {"type": "string"}),
        (1.5, {"type": "number"}),
        (1, {"type": "integer"}),
        (True, {"type": "boolean"}),
        (None, {"type": "null"}),
    ],
)
def test_json_schema_accepts_supported_types(value: object, schema: dict[str, object]) -> None:
    _validate_json_schema(value, schema, path="expected.retrieved_data")


@pytest.mark.parametrize(
    ("value", "schema"),
    [
        (None, {"type": "string"}),
        (True, {"type": "number"}),
        (1.5, {"type": "integer"}),
        (0, {"type": "boolean"}),
    ],
)
def test_json_schema_rejects_wrong_types(value: object, schema: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="expected.retrieved_data: expected"):
        _validate_json_schema(value, schema, path="expected.retrieved_data")


def test_json_schema_validates_nested_contract_keywords() -> None:
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["kind", "value"],
            "properties": {
                "kind": {"enum": ["count", "label"]},
                "value": {
                    "oneOf": [
                        {"type": "integer", "const": 3},
                        {"type": "string"},
                    ]
                },
                "optional": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
            },
        },
    }
    _validate_json_schema(
        [{"kind": "count", "value": 3, "optional": None}],
        schema,
        path="expected.retrieved_data",
    )

    with pytest.raises(ValueError, match="missing required property 'value'"):
        _validate_json_schema([{"kind": "count"}], schema, path="expected.retrieved_data")
    with pytest.raises(ValueError, match="expected one of"):
        _validate_json_schema(
            [{"kind": "other", "value": "ok"}],
            schema,
            path="expected.retrieved_data",
        )
    with pytest.raises(ValueError, match="does not match anyOf"):
        _validate_json_schema(
            [{"kind": "label", "value": "ok", "optional": 1}],
            schema,
            path="expected.retrieved_data",
        )


def test_official_contract_validation_rejects_task_68_null_author() -> None:
    evaluator = SimpleNamespace(
        evaluator="AgentResponseEvaluator",
        model_dump=lambda **_: {
            "expected": {
                "retrieved_data": [
                    {"book": "The Hobbit", "author": "Tolkien"},
                    {"book": "A Christmas Carol", "author": None},
                ]
            },
            "results_schema": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "book": {"type": "string"},
                        "author": {"type": "string"},
                    },
                },
            },
        },
    )
    task = SimpleNamespace(
        eval=[evaluator],
        sites=[SimpleNamespace(value="reddit")],
        intent="intent",
        intent_template_id=17,
        revision=0,
        start_urls=["__REDDIT__"],
    )
    selection = PilotSelection(
        tasks=(PilotTask(68, "hard", 12, "rationale", True),),
        source_path=Path("selection.json"),
        sha256="0" * 64,
        metadata={},
    )

    with pytest.raises(
        ValueError,
        match=r"task 68: excluded from new Browser Use runs: .*results_schema",
    ):
        validate_against_official(selection, SimpleNamespace(get_task=lambda _: task))

    with pytest.raises(
        ValueError,
        match=(
            r"task 68: AgentResponseEvaluator expected\.retrieved_data violates "
            r"results_schema: expected\.retrieved_data\[1\]\.author: expected string, got null"
        ),
    ):
        from harness.browser_use.selection import _validate_agent_response_contract

        _validate_agent_response_contract(68, task)

    resolved = validate_against_official(
        selection,
        SimpleNamespace(get_task=lambda _: task),
        enforce_contract_integrity=False,
    )
    assert resolved[0]["task_id"] == 68
