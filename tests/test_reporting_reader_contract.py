"""Reports explain metrics and convert usage without changing their measured evidence."""

import pytest
from bs4 import BeautifulSoup

from harness.reporting.presentation import check_metric_guide, metric_guide
from harness.reporting.pricing import usd_per_thousand


@pytest.fixture
def definitions():
    return [
        {
            "id": "quality",
            "label": "F1",
            "meaning": "Balances precision and recall.",
            "unit": "0–1",
            "population": "All 140 pages, including failures.",
            "direction": "Higher is better",
        }
    ]


def test_metric_guide_retains_the_reader_contract(definitions):
    html = metric_guide(
        definitions, title="Metrics", headings=["Metric", "Meaning", "Unit", "Scope", "Direction"]
    )
    check_metric_guide(
        html,
        definitions,
        title="Metrics",
        headings=["Metric", "Meaning", "Unit", "Scope", "Direction"],
    )
    for removed in ["meaning", "unit", "population", "direction"]:
        bad = [dict(definitions[0], **{removed: ""})]
        with pytest.raises(ValueError):
            metric_guide(bad, title="Metrics", headings=[])


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "wrong-definition",
        "wrong-target",
        "merged-cells",
        "shifted-cells",
        "merged-headings",
    ],
)
def test_metric_guide_gate_rejects_lost_or_misleading_explanations(definitions, damage):
    headings = ["Metric", "Meaning", "Unit", "Scope", "Direction"]
    soup = BeautifulSoup(
        metric_guide(definitions, title="Metrics", headings=headings), "html.parser"
    )
    if damage == "missing":
        soup.details.decompose()
    elif damage == "wrong-definition":
        soup.select("td")[1].string = "Always perfect"
    elif damage == "wrong-target":
        soup.select_one("tbody tr")["id"] = "unrelated"
    elif damage == "merged-headings":
        cells = soup.select("thead th")
        cells[0].append(" " + cells[1].get_text())
        cells[1].decompose()
    else:
        cells = soup.select("tbody td")
        cells[0].append(" " + cells[1].get_text())
        if damage == "merged-cells":
            cells[1].decompose()
        else:
            cells[1].clear()
    with pytest.raises(ValueError):
        check_metric_guide(soup, definitions, title="Metrics", headings=headings)


def test_usage_cost_conversion_is_independent_of_free_credits():
    components = [
        {"quantity": 2_000_000, "usd_per_unit": 1.34 / 1_000_000},
        {"quantity": 100_000, "usd_per_unit": 6.67 / 1_000_000},
    ]
    assert usd_per_thousand(800, components) == pytest.approx([4.18375, 4.18375])
    assert usd_per_thousand(
        1000, [{"quantity": [10, 20], "usd_per_unit": 0.09 / 3600}]
    ) == pytest.approx([0.00025, 0.0005])


@pytest.mark.parametrize(
    "calls,components",
    [
        (0, []),
        (1, []),
        (True, [{"quantity": 2, "usd_per_unit": 1}]),
        (10, [{"quantity": [-1, 2], "usd_per_unit": 1}]),
    ],
)
def test_missing_or_invalid_cost_inputs_are_not_reported_as_zero(calls, components):
    with pytest.raises(ValueError):
        usd_per_thousand(calls, components)


def test_report_price_cannot_drift_from_its_declared_conversion():
    from harness.web_extract.report import price_quote

    value = {
        "usd_per_1000_range": [1, 1],
        "cost_conversion": {
            "calls": 1000,
            "components": [{"quantity": 1000, "usd_per_unit": 0.001}],
        },
    }
    assert price_quote(value, "en") == "$1"
    value["usd_per_1000_range"] = [2, 2]
    with pytest.raises(ValueError, match="usage conversion"):
        price_quote(value, "en")
    with pytest.raises(ValueError, match="numeric"):
        price_quote({"pricing_formula": True, "usd_per_1000": "Usage-based"}, "en")


def test_every_template_inherits_the_same_reader_requirements():
    import json

    from harness.reporting.templates import ROOT, discover_template

    tracks = json.loads((ROOT / "configs/report-templates.json").read_text(encoding="utf-8"))[
        "tracks"
    ]
    contracts = [discover_template(track)["presentation_contract"] for track in tracks]
    assert all(c == contracts[0] for c in contracts)
    assert {"metric_guide", "highlights", "details", "release_checks"} <= set(contracts[0])


@pytest.mark.parametrize("records", [False, True])
def test_reader_examples_verify_fields_against_the_correct_native_contract(tmp_path, records):
    from harness.web_extract.pipeline import SCHEMA, digest, normalize, save_json
    from harness.web_extract.report import method_examples

    schema = (
        {"type": "object", "properties": {"price": {"type": "array", "items": {"type": "string"}}}}
        if records
        else SCHEMA
    )
    response = {
        "result": {"price": ["12"] if records else 12, "sku": "A", "availability": "InStock"}
    }
    fields = normalize("cloudflare-json", response, **({"schema": schema} if records else {}))
    value = {
        "benchmarks": [
            {
                "id": "example",
                "label": "Example dataset",
                "description": "A frozen page",
                "methods": [
                    {
                        "id": "cloudflare-json",
                        "label": "Cloudflare",
                        "implementation": "API",
                        "input_mode": "HTML",
                        "field_contract": "Price",
                        "custom_schema": True,
                        "output_path": ["result"],
                        "example": {
                            "case_id": "one",
                            "request": {"schema": schema},
                            "response": response,
                            "fields": fields,
                            "record_schema": records,
                            "source": {"sha256": "a" * 64},
                        },
                    }
                ],
            }
        ]
    }
    path = tmp_path / "example.json"
    save_json(path, value)
    binding = {"path": path.name, "sha256": digest(path.read_bytes())}
    assert "12" in method_examples(tmp_path, binding, "en")
    entry = value["benchmarks"][0]["methods"][0]
    entry["requested_fields"] = list(schema["properties"])
    del response["result"]["price"]
    entry["example"]["fields"] = normalize(
        "cloudflare-json", response, **({"schema": schema} if records else {})
    )
    save_json(path, value)
    binding["sha256"] = digest(path.read_bytes())
    reader = BeautifulSoup(method_examples(tmp_path, binding, "en"), "html.parser")
    shown = reader.select(".example-pair tbody tr")
    assert [r.select("td")[0].get_text() for r in shown] == list(schema["properties"])
    assert shown[0].select("td")[1].get_text() in {"Not returned", "Not found"}
    entry["example"]["fields"]["price"] = ["invented"]
    save_json(path, value)
    binding["sha256"] = digest(path.read_bytes())
    with pytest.raises(ValueError, match="native response"):
        method_examples(tmp_path, binding, "en")


def test_plan_rate_intervals_keep_native_quantities_unchanged():
    assert usd_per_thousand(1000, [{"quantity": 2_000_000, "usd_per_unit": [1e-6, 2e-6]}]) == [2, 4]


@pytest.mark.parametrize("language", ["en", "zh"])
def test_same_page_comparison_controls_use_complete_registry_and_reject_missing_controls(
    tmp_path, language
):
    from harness.web_extract.report import case_comparison_controls, check_case_comparison_controls

    methods = ["first", "second", "third", "fourth"]
    labels = {method: method.title() for method in methods}
    body = case_comparison_controls(methods, labels, language)
    soup = BeautifulSoup(body, "html.parser")
    for selector in ["[data-comparison-baseline]", "[data-comparison-methods]"]:
        assert [o["value"] for o in soup.select(selector + " option")] == methods
    assert soup.select_one("[data-comparison-methods]").has_attr("multiple")
    root = tmp_path / language
    root.mkdir()
    (root / "results.html").write_text(
        body + '<tr data-status="pass" data-case="one"></tr>', encoding="utf-8"
    )
    check_case_comparison_controls(tmp_path, methods, labels, language)
    soup.select_one("[data-comparison-baseline]").decompose()
    (root / "results.html").write_text(str(soup), encoding="utf-8")
    with pytest.raises(ValueError, match="same-page comparison"):
        check_case_comparison_controls(tmp_path, methods, labels, language)
