"""Real rendered cells supplied to the generated-client sorting contract."""

import tempfile
from pathlib import Path

from bs4 import BeautifulSoup

from harness.web_extract.example import create
from harness.web_extract.pipeline import digest, read_json, save_json
from harness.web_extract.report import build, operating_metrics


def generated_sorting_tables():
    def cells(html, selector):
        table = BeautifulSoup(html, "html.parser").select_one(selector)
        return [
            [{"text": td.get_text(), "sort": td.get("data-sort")} for td in tr.select("td")]
            for tr in table.select("tbody tr")
        ]

    with tempfile.TemporaryDirectory() as d:
        home = create(Path(d) / "first")
        run = home / "runs/example-v1"
        manifest = read_json(run / "manifest.json")
        for entry in manifest["calls"]:
            path = run / entry["path"]
            row = read_json(path)
            row["elapsed_seconds"] = 10.001 if row["method"] == "synthetic-success" else 2.001
            save_json(path, row)
            entry["sha256"] = digest(path.read_bytes())
        save_json(run / "manifest.json", manifest)
        out = Path(d) / "new"
        build(out, home / "content.json", home=home, profile=home / "profile.json")
        case = next((out / "zh").glob("case-*.html")).read_text(encoding="utf-8")
    rows = (
        [{"method": "a", "elapsed_seconds": 10}] * 10
        + [{"method": "b", "elapsed_seconds": 2}] * 2
        + [{"method": "b", "elapsed_seconds": None}] * 2
        + [{"method": "c", "elapsed_seconds": None}] * 12
    )
    prices = {
        m: {
            "usd_per_1000": "$0",
            "terms": {"en": "Synthetic", "zh": "合成数据"},
            "sources": ["https://example.org"],
            "verified_at": "2026-01-01",
        }
        for m in ("a", "b", "c")
    }
    operating = operating_metrics(["a", "b", "c"], rows, prices, "en", {})
    return [
        {"rows": cells(case, "[data-case-comparison]"), "column": 5},
        {"rows": cells(operating, "[data-operating-metrics]"), "column": 1},
        {"rows": cells(operating, "[data-operating-metrics]"), "column": 2},
    ]


def failed_unscored_record():
    """A real record failure has a delivery verdict but no reference coverage."""
    from harness.web_extract.report import record_inventory_outcome, record_result_fields

    result = record_result_fields(
        {"domain": "book", "site": "a", "values": {"title": []}},
        {
            "case_id": "unscored-failure",
            "method": "a",
            "state": "call_error",
            "http_status": None,
            "compatibility": "no_structured_output",
            "normalized_fields": None,
        },
        policy={
            "unavailable_reference_groups": [["book", "a", "title"]],
            "text_matching": "literal",
        },
    )
    status, outcome = record_inventory_outcome(result)
    return {
        "case": result["case_id"],
        "method": result["method"],
        "status": status,
        "outcome": outcome,
        "scoredFields": str(result["scored_fields"]),
        "stage": result["failure_stage"],
    }
