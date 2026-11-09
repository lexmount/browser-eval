"""Model diagnostics use the same field rules and reference scope as Web Extract."""

import pytest

from harness.record_io import canonical_json_sha256, file_sha256
from harness.web_extract.analysis import score_model_comparison
from harness.web_extract.model_comparison import collect
from harness.web_extract.pipeline import read_json, save_json
from tests.web_extract.test_model_comparison import plan, server


@pytest.mark.parametrize(
    "name,expected_f1,valid,complete",
    [(["A"], 1, True, True), (["B"], 0, True, True), ("A", 0, False, True),
     (float("nan"), 0, False, False)],
)
def test_unavailable_reference_is_excluded_without_a_perfect_page_claim(
    tmp_path, name, expected_f1, valid, complete
):
    import json

    events = [
        {"message": {"content": json.dumps({"name": name, "phone": ["unannotated"]})}},
        {"done": True},
    ]
    with server(events) as (url, calls):
        value = plan(url)
        case = value["cases"][0]
        case.update(
            dataset="swde",
            case_id="book-fixture-1",
            schema={
                "type": "object",
                "properties": {
                    "name": {"type": "array", "items": {"type": "string"}},
                    "phone": {"type": "array", "items": {"type": "string"}},
                },
            },
            reference={"domain": "book", "site": "fixture", "values": {"name": ["A"], "phone": []}},
        )
        contract = {
            "stages": {"swde": {"screening": [case], "validation": []}},
            "bindings": {
                "swde": {
                    "reference_policy": {
                        "text_matching": "whitespace",
                        "unavailable_reference_groups": [["book", "fixture", "phone"]],
                    }
                }
            },
        }
        contract_path, path, output = (
            tmp_path / "contract.json",
            tmp_path / "plan.json",
            tmp_path / "run",
        )
        save_json(contract_path, contract)
        value["case_contract_sha256"] = file_sha256(contract_path)
        save_json(path, value)
        collect(path, output, phase="preflight")
        collect(path, output, phase="comparison")
        summary = score_model_comparison(path, output, contract_path, tmp_path, stage="screening")
        group = summary["groups"]["local"]
        assert group["f1"] == expected_f1 and group["schema_valid"] == int(valid)
        assert group["page_exact_denominator"] == 0
        assert summary["details"][0]["fields"]["phone"]["status"] == "unavailable"
        assert summary["details"][0]["page_exact"] is None
        assert group["timings"]["complete_json_seconds"]["count"] == int(complete)
        assert group["timings"]["elapsed_seconds"]["count"] == 1
        assert len(calls) == 2
        target = output / "calls" / (canonical_json_sha256(value["calls"][1]) + ".json")
        record = read_json(target)
        record["response"]["data"] = {"name": ["tampered"]}
        save_json(target, record)
        with pytest.raises(ValueError, match="changed model call"):
            score_model_comparison(path, output, contract_path, tmp_path, stage="screening")
        assert len(calls) == 2


def test_model_plan_cannot_replace_its_authoritative_reference(tmp_path):
    value = plan("http://127.0.0.1:1")
    original = {**value["cases"][0], "dataset": "swde"}
    contract = {"stages": {"swde": {"screening": [original]}}}
    contract_path, path, output = (
        tmp_path / "contract.json",
        tmp_path / "plan.json",
        tmp_path / "run",
    )
    save_json(contract_path, contract)
    value["case_contract_sha256"] = file_sha256(contract_path)
    value["cases"] = [{**original, "reference": "replacement answer"}]
    save_json(path, value)
    save_json(output / "binding.json", {"plan_sha256": file_sha256(path)})
    save_json(output / "call-hashes.json", {})
    with pytest.raises(ValueError, match="authoritative reference or input"):
        score_model_comparison(path, output, contract_path, tmp_path, stage="screening")
