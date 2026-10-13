import json
import tempfile
from copy import deepcopy
from pathlib import Path

import pytest

from harness.web_fetch.config import load_dataset
from harness.web_fetch.retrieval_hints import (
    complete_text_wait_value,
    native_contract_for_case,
    parse_retrieval_hints,
)


def test_native_execution_keeps_dynamic_scenario_and_acceptance_without_injected_hints():
    from copy import deepcopy

    from harness.web_fetch.models import EvalCase
    from harness.web_fetch.retrieval_hints import native_fetch_dataset, plan_for_case
    dataset={'tool':'web_fetch','coverage_plan':{'dynamic':['browser']},'cases':[
        {'id':'native-page','name':'Dynamic site','url':'https://example.com/',
         'category':'dynamic_content','track':'dynamic','expected_any':[],
         'task_kind':'body','acceptance_contract':{'version':1,'schema':'body_text'},
         'min_content_chars':1000}]}
    original = deepcopy(dataset)
    selected = native_fetch_dataset(dataset)
    assert dataset==original
    assert selected['cases'][0]['track']=='dynamic'
    assert selected['coverage_plan']==original['coverage_plan']
    assert selected['cases'][0]['category']=='dynamic_content'
    assert 'source_benchmark' not in selected['cases'][0]
    assert selected['cases'][0]['acceptance_contract']==original['cases'][0]['acceptance_contract']
    assert selected['cases'][0]['min_content_chars']==1000
    assert plan_for_case(EvalCase(**selected['cases'][0])) is None
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'dataset.json'
        path.write_text(json.dumps(selected))
        _, cases = load_dataset(path)
        assert cases[0].track == 'dynamic'
        assert plan_for_case(cases[0]) is None
    dataset['cases'][0]['retrieval_hints']={'version':1,'completion':{'selector':'.record'}}
    with pytest.raises(ValueError,match='cannot discard'):
        native_fetch_dataset(dataset)


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


def test_all_current_dynamic_cases_have_bounded_generic_completion_signals(historical_dataset):
    _, cases = historical_dataset
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


def test_current_completion_signals_are_bound_to_independent_capture_shape(historical_dataset):
    _, cases = historical_dataset
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


def test_committed_native_100_dataset_uses_only_native_answer_free_contracts():
    from harness.web_fetch.retrieval_hints import plan_for_case
    from tests.conftest import read_frozen_dataset_bytes
    path = Path("evaluation/dynamic-content-100-moli-webfetch/dataset.json")
    read_frozen_dataset_bytes(path)
    raw, cases = load_dataset(path)
    assert len(cases) == 100
    assert all(plan_for_case(c) is None for c in cases)
    assert all(
        not case.get("retrieval_hints")
        or (
            case["retrieval_hints"].get("version") == 3
            and case["retrieval_hints"].get("mode") == "native"
        )
        for case in raw["cases"]
    )


def test_native_default_contract_must_be_valid(tmp_path):
    case = _case()
    del case["retrieval_hints"]
    case["acceptance_contract"] = {"version": 1, "schema": "unknown"}
    with pytest.raises(ValueError):
        load_dataset(_write_dataset(tmp_path, case))


def test_named_native_contract_resolves_a_source_bound_page_shape():
    case = {"id": "dynamic-iframe", "track": "dynamic", "retrieval_hints": {
        "version": 3, "mode": "native", "contract": "iframe-fourteen-family-headings"}}
    assert parse_retrieval_hints("dynamic", case["retrieval_hints"], identifier=case["id"]) is None
    contract = native_contract_for_case(case)
    assert contract.include_subdocuments is True
    plan = contract.completion_plan(45_000)
    assert plan.wait_kind == "script" and plan.timeout_ms == 45_000
    assert "h3.family-name" in plan.wait_value and ".length >= 14" in plan.wait_value


def test_public_dataset_loader_binds_named_contract_to_the_current_task_definition(tmp_path):
    from tests.conftest import read_frozen_dataset_bytes

    dataset = json.loads(read_frozen_dataset_bytes(
        Path("evaluation/dynamic-content-100-moli-webfetch/dataset.json")
    ))
    original = next(case for case in dataset["cases"] if case["id"] == "dynamic-iframe")
    load_dataset(_write_dataset(tmp_path, deepcopy(original)))

    changed_url = deepcopy(original)
    changed_url["url"] += "?different-task=1"
    changed_body_requirement = deepcopy(original)
    changed_body_requirement["min_content_chars"] += 1
    changed_acceptance = deepcopy(original)
    changed_acceptance["acceptance_contract"]["minimum_records"] -= 1
    for changed in (changed_url, changed_body_requirement, changed_acceptance):
        with pytest.raises(ValueError, match="does not match the current task definition"):
            load_dataset(_write_dataset(tmp_path, changed))


@pytest.mark.parametrize("field", ["completion", "include_subdocuments", "answer_text", "timeout_ms"])
def test_native_dataset_rows_reject_inline_capabilities_and_answers(field):
    hints = {"version": 3, "mode": "native", field: {"selector": ".record"}}
    with pytest.raises(ValueError, match="native retrieval_hints"):
        parse_retrieval_hints("dynamic", hints, identifier="control")


@pytest.mark.parametrize("hints", [
    {"version": 3, "mode": "native", "answer_text": "expected answer"},
    {"version": 3, "mode": "guided"}, {"version": 3},
])
def test_native_contract_rejects_unknown_or_non_native_fields(hints):
    with pytest.raises(ValueError, match="native retrieval_hints"):
        parse_retrieval_hints("dynamic", hints)


def test_named_contract_is_bound_to_one_case_and_unknown_names_fail_closed():
    with pytest.raises(ValueError, match="belongs to another case"):
        parse_retrieval_hints("dynamic", {"version": 3, "mode": "native",
            "contract": "iframe-fourteen-family-headings"}, identifier="another-case")
    with pytest.raises(ValueError, match="unknown native retrieval contract"):
        parse_retrieval_hints("dynamic", {"version": 3, "mode": "native",
            "contract": "unknown-contract"}, identifier="another-case")


@pytest.mark.parametrize("selector", [".answer", "#correct-answer"])
def test_registry_rejects_answer_derived_selectors(tmp_path, monkeypatch, selector):
    import harness.web_fetch.retrieval_hints as owner
    registry = {"schema_version": 1, "contracts": {"answer-derived": {
        "case_id": "control", "task_definition_sha256": "b" * 64,
        "source_authority": {"path": "source.json",
            "sha256": "a" * 64, "provider_output_used": False},
        "capabilities": {"completion": {"selector": selector, "minimum_count": 1}}}}}
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry))
    monkeypatch.setattr(owner, "_CONTRACT_REGISTRY_PATH", path)
    owner._native_contract_registry.cache_clear()
    monkeypatch.setattr(owner, "_verify_task_definition", lambda *_: None)
    monkeypatch.setattr(owner, "_verify_contract_source", lambda *_: None)
    with pytest.raises(ValueError, match="answer-derived"):
        owner.native_contract_for_case({"id": "control", "track": "dynamic",
            "retrieval_hints": {"version": 3, "mode": "native", "contract": "answer-derived"}})
    owner._native_contract_registry.cache_clear()


def test_registry_source_hash_must_match_bound_external_bytes(tmp_path, monkeypatch):
    import harness.web_fetch.retrieval_hints as owner
    registry = json.loads(Path("evaluation/native-retrieval-contracts.json").read_text())
    registry["contracts"]["delayed-ten-records"]["source_authority"]["sha256"] = "0" * 64
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry))
    monkeypatch.setattr(owner, "_CONTRACT_REGISTRY_PATH", path)
    owner._native_contract_registry.cache_clear()
    with pytest.raises(ValueError, match="source hash is not in the source manifest"):
        owner.native_contract_for_case({"id": "dynamic-delayed", "track": "dynamic",
            "retrieval_hints": {"version": 3, "mode": "native",
                                "contract": "delayed-ten-records"}})
    owner._native_contract_registry.cache_clear()


def test_native_contract_does_not_waive_missing_dynamic_execution_contract():
    assert parse_retrieval_hints("dynamic", {"version": 3, "mode": "native"}) is None
    with pytest.raises(ValueError, match="require retrieval_hints"):
        parse_retrieval_hints("dynamic", None)


@pytest.mark.parametrize("version", [True, 3.0, "3", None])
def test_native_completion_version_requires_an_integer(version):
    with pytest.raises(ValueError):
        parse_retrieval_hints("dynamic", {"version": version, "mode": "native"})


def test_worker_accepts_explicit_native_dynamic_contract_without_injected_plan():
    from harness.web_fetch.providers.headless_worker import require_normalized_retrieval_plan
    assert require_normalized_retrieval_plan({
        "track": "dynamic", "retrieval_plan": None,
        "retrieval_hints": {"version": 3, "mode": "native"}}) is None
    with pytest.raises(ValueError, match="explicit native contract"):
        require_normalized_retrieval_plan({"track": "dynamic", "retrieval_plan": None})
    with pytest.raises(ValueError, match="native completion cannot carry"):
        require_normalized_retrieval_plan({"track": "dynamic", "retrieval_plan": {"selector": "body"},
            "retrieval_hints": {"version": 3, "mode": "native"}})
