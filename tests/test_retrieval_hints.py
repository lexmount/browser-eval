import json
from copy import deepcopy
from pathlib import Path

import pytest

from harness.web_fetch.config import load_dataset
from harness.web_fetch.retrieval_hints import complete_text_wait_value, parse_retrieval_hints


def _write_dataset(tmp_path, case):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps({"tool": "web_fetch", "cases": [case]}))
    return path


def _case():
    return {
        "id": "dynamic-test",
        "name": "Dynamic test",
        "url": "https://example.test/",
        "category": "dynamic",
        "track": "dynamic",
        "expected_any": [],
        "retrieval_hints": {
            "version": 1,
            "wait_until": "domcontentloaded",
            "completion": {
                "selector": "#results > article.card",
                "minimum_count": 10,
                "timeout_ms": 30000,
                "delay_ms": 250,
            },
        },
    }


def test_valid_dynamic_retrieval_hints_are_loaded(tmp_path):
    _, cases = load_dataset(_write_dataset(tmp_path, _case()))
    assert cases[0].retrieval_hints["completion"]["minimum_count"] == 10


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("wait_until",), "done"),
        (("wait_until",), []),
        (("completion", "selector"), ':has-text("answer")'),
        (("completion", "frame_selector"), ':has-text("answer")'),
        (("completion", "minimum_count"), 0),
        (("completion", "minimum_count"), True),
        (("completion", "timeout_ms"), 60001),
        (("completion", "delay_ms"), -1),
        (("completion", "delay_ms"), 10001),
    ],
)
def test_dynamic_retrieval_hints_fail_closed_on_unsafe_or_unbounded_values(tmp_path, path, value):
    case = _case()
    target = case["retrieval_hints"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError, match="retrieval_hints"):
        load_dataset(_write_dataset(tmp_path, case))


def test_dynamic_retrieval_hints_reject_unknown_fields(tmp_path):
    case = _case()
    case["retrieval_hints"]["completion"]["answer_text"] = "expected answer"
    with pytest.raises(ValueError, match="must contain only"):
        load_dataset(_write_dataset(tmp_path, case))


def test_completion_delay_must_fit_inside_timeout(tmp_path):
    case = _case()
    case["retrieval_hints"]["completion"].update(timeout_ms=1000, delay_ms=1000)
    with pytest.raises(ValueError, match="below timeout_ms"):
        load_dataset(_write_dataset(tmp_path, case))


def test_non_dynamic_case_cannot_carry_retrieval_hints(tmp_path):
    case = deepcopy(_case())
    case["track"] = "web"
    with pytest.raises(ValueError, match="only valid for dynamic"):
        load_dataset(_write_dataset(tmp_path, case))


def test_dynamic_case_cannot_omit_retrieval_hints(tmp_path):
    case = _case()
    del case["retrieval_hints"]
    with pytest.raises(ValueError, match="dynamic cases require retrieval_hints"):
        load_dataset(_write_dataset(tmp_path, case))


def test_all_current_dynamic_cases_have_bounded_generic_completion_signals():
    _, cases = load_dataset(Path("evaluation/dataset.json"))
    dynamic = [case for case in cases if case.track == "dynamic"]
    assert len(dynamic) == 10
    assert all(case.retrieval_hints for case in dynamic)
    assert all(case.retrieval_hints["version"] in {1, 2} for case in dynamic)
    assert all(case.retrieval_hints["completion"]["selector"] for case in dynamic)
    assert not any("http" in json.dumps(case.retrieval_hints) for case in dynamic)
    cart = next(case for case in dynamic if case.id == "dynamic-react-cart")
    assert cart.retrieval_hints["version"] == 2
    assert cart.retrieval_hints["extraction"] == {
        "selector": "main main > div > div",
        "maximum_count": 16,
        "exclude_selectors": ["button"],
        "fallback": "body",
    }


def test_v2_separates_completion_from_bounded_answer_free_extraction():
    hints = _case()["retrieval_hints"]
    hints["version"] = 2
    hints["extraction"] = {
        "selector": "main article",
        "maximum_count": 10,
        "exclude_selectors": ["button"],
        "fallback": "body",
    }
    plan = parse_retrieval_hints("dynamic", hints, identifier="products")
    assert plan.selector == "#results > article.card"
    assert plan.extraction_selector == "main article"
    assert plan.extraction_maximum_count == 10
    assert plan.extraction_exclude_selectors == ("button",)
    assert plan.worker_dict()["extraction"]["fallback"] == "body"


def test_current_completion_signals_are_bound_to_independent_capture_shape():
    _, cases = load_dataset(Path("evaluation/dataset.json"))
    by_id = {case.id: case for case in cases}
    captures = []
    for path in (
        Path("research/dynamic-browser-captures.json"),
        Path("research/dynamic-expansion-captures.json"),
    ):
        captures.extend(json.loads(path.read_text()))
    normalized_selector = {
        "button Add to cart → parent card": "button",
        "iframe#iframe → h3.family-name (child document independently opened)": "h3.family-name",
    }
    for capture in captures:
        completion = by_id[capture["id"]].retrieval_hints["completion"]
        assert completion["selector"] == normalized_selector.get(
            capture["selector"], capture["selector"]
        )
        assert completion["minimum_count"] == capture["expected_count"]
    iframe = by_id["dynamic-iframe"].retrieval_hints["completion"]
    assert iframe["frame_selector"] == "iframe#iframe"


def test_normalized_frame_plan_contains_only_generated_wait_code():
    hints = {
        "version": 1,
        "wait_until": "domcontentloaded",
        "completion": {
            "frame_selector": "iframe#iframe",
            "selector": "h3.family-name",
            "minimum_count": 14,
            "timeout_ms": 45000,
            "delay_ms": 3000,
        },
    }
    plan = parse_retrieval_hints("dynamic", hints, identifier="frame").worker_dict()
    assert plan["wait_kind"] == "script"
    assert "frame.contentDocument" in plan["wait_value"]
    assert "h3.family-name" in plan["wait_value"]
    assert set(plan) == {
        "version",
        "wait_until",
        "selector",
        "minimum_count",
        "timeout_ms",
        "delay_ms",
        "frame_selector",
        "minimum_text_fragments",
        "wait_kind",
        "wait_value",
    }
    assert plan["minimum_text_fragments"] == 0


def test_complete_text_wait_rejects_empty_placeholder_records_without_answer_text():
    hints = _case()["retrieval_hints"]
    hints["completion"]["minimum_text_fragments"] = 2
    plan = parse_retrieval_hints("dynamic", hints, identifier="products")
    wait = complete_text_wait_value(plan)
    assert 'document.querySelectorAll("#results > article.card")' in wait
    assert "nodes.length >= 10" in wait
    assert "nodes.slice(0, 10).every" in wait
    assert "innerText.split" in wait
    assert "Chaz Kangeroo Hoodie" not in wait
    assert "$52" not in wait


@pytest.mark.parametrize("value", [0, -1, True, 21])
def test_complete_text_wait_rejects_invalid_fragment_counts(value):
    hints = _case()["retrieval_hints"]
    hints["completion"]["minimum_text_fragments"] = value
    with pytest.raises(ValueError, match="minimum_text_fragments"):
        parse_retrieval_hints("dynamic", hints, identifier="products")


def test_frame_complete_text_wait_checks_child_record_fields():
    hints = _case()["retrieval_hints"]
    hints["completion"].update(frame_selector="iframe#catalog", minimum_text_fragments=2)
    plan = parse_retrieval_hints("dynamic", hints, identifier="frame-products")
    assert "frame.contentDocument.querySelectorAll" in plan.wait_value
    assert "nodes.slice(0, 10).every" in plan.wait_value
    assert "innerText.split" in plan.wait_value


def test_committed_native_100_dataset_loads_without_wait_or_extraction_overrides():
    from harness.web_fetch.retrieval_hints import plan_for_case
    raw, cases = load_dataset(Path("evaluation/dynamic-content-100-moli-webfetch/dataset.json"))
    assert len(cases) == 100
    assert all(not c.retrieval_hints and plan_for_case(c) is None for c in cases)
    assert all("retrieval_hints" not in c for c in raw["cases"])


def test_native_default_contract_must_be_valid(tmp_path):
    case = _case()
    del case["retrieval_hints"]
    case["acceptance_contract"] = {"version": 1, "schema": "unknown"}
    with pytest.raises(ValueError):
        load_dataset(_write_dataset(tmp_path, case))
