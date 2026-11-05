"""ScrapeGraph's complete human_eval split for the shared Web Extract runner."""

from __future__ import annotations

import html
import json
import math
import runpy
import tempfile
from pathlib import Path

CARD = Path(__file__).resolve().parents[1] / "benchmarks/cards/scrapegraph-human-eval.json"
INSTRUCTIONS = ""
INSTRUCTION_DELIVERY = "original-schema/1"
SCORE_REQUIRES_HOME = True
MISSING_OUTPUT_NONE = True
SCORING_REVISION = {
    "id": "human-eval-stable-value-sum/1",
    "root_cause": (
        "The upstream value_score sums scores over a hash-randomized key set. "
        "Use the same upstream leaf comparisons with math.fsum in sorted key order; "
        "aggregate cases with math.fsum too."
    ),
    "regression_ids": [
        "human-eval-missing-empty-reference", "human-eval-delivered-empty-reference",
        "human-eval-correct-record-pairs", "human-eval-swapped-record-pairs",
        "human-eval-process-independent-value-score",
    ],
    "provider_calls_added": 0,
}
PARTICIPANT_OPTIONS = {
    "scrapegraph-extract": {"input_route": "markdown"},
    "firecrawl-parse-json": {},
    "cloudflare-json": {"input_route": "html", "setJavaScriptEnabled": False},
}
INPUT_CONTRACT = {
    "source": "Original Markdown, original JSON Schema and human-corrected response; all 100 rows",
    "native_markdown": ["scrapegraph-extract"],
    "html_adapter": (
        "firecrawl-parse-json and cloudflare-json receive identical Markdown as escaped "
        "preformatted text; Firecrawl rejects native Markdown uploads"
    ),
    "schema": "Original JSON Schema without added field descriptions or answer-derived prompts",
    "prompt": "Extract the fields defined by the schema.",
    "scope": "Content-to-JSON extraction; not original HTML retrieval or DOM extraction",
    "retry": "No quality retry; the shared access-repair policy retains physical calls",
}
PREFIX = '<!doctype html><html><head><meta charset="utf-8"></head><body><pre>'
SUFFIX = "</pre></body></html>"


def wrap_markdown(content):
    """Keep Markdown syntax, links and original characters as inert visible text."""
    return PREFIX + html.escape(content, quote=False) + SUFFIX


def unwrap_markdown(document):
    if not document.startswith(PREFIX) or not document.endswith(SUFFIX):
        raise ValueError("Markdown upload requires a frozen lossless Markdown wrapper")
    content = html.unescape(document[len(PREFIX):-len(SUFFIX)])
    if wrap_markdown(content) != document:
        raise ValueError("Noncanonical Markdown wrapper")
    return content


def prepare(home: Path, source: Path):
    """Compile only hash-pinned upstream files; never fetch a moving dataset alias."""
    import pyarrow.parquet as pq

    from harness.web_extract.pipeline import (
        digest,
        read_json,
        require_external_output,
        save_json,
        validate_dataset,
    )

    require_external_output(home)
    card = read_json(CARD)
    sources = {"source/human_eval.parquet": "human_eval.parquet", "evaluate.py": "metrics.py"}
    for target, filename in sources.items():
        path = source / filename
        if path.is_symlink() or digest(path.read_bytes()) != card["dataset"]["files"][target]:
            raise ValueError("Pinned human_eval source differs: " + filename)
    if (home / "dataset/manifest.json").exists():
        return validate_dataset(home, CARD)
    if (home / "dataset").exists():
        raise ValueError("Incomplete dataset exists; use a new destination")
    rows = pq.read_table(source / "human_eval.parquet").to_pylist()
    if len(rows) != card["dataset"]["cases"]:
        raise ValueError("Incomplete human_eval split")
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".human-eval-", dir=home) as temporary:
        root = Path(temporary) / "dataset"
        (root / "html").mkdir(parents=True)
        (root / "markdown").mkdir()
        (root / "source").mkdir()
        truth, schemas = {}, {}
        for index, row in enumerate(rows):
            case_id = f"human-eval-{index:03d}"
            schema, gold = json.loads(row["schema"]), json.loads(row["response"])
            if schema.get("type") != "object" or not isinstance(gold, dict):
                raise ValueError("Unsupported upstream root; declare its native contract first")
            schemas[case_id] = schema
            truth[case_id] = {
                "id": case_id,
                "source_row": index,
                "url": card["source"]["dataset_url"],
                "schema_id": case_id,
                "response": gold,
                "schema_text": row["schema"],
                "response_text": row["response"],
            }
            (root / "html" / (case_id + ".html")).write_text(
                wrap_markdown(row["content"]), encoding="utf-8"
            )
            (root / "markdown" / (case_id + ".md")).write_text(row["content"], encoding="utf-8")
        save_json(root / "dataset/ground-truth.json", truth)
        save_json(root / "schemas.json", schemas)
        for target, filename in sources.items():
            (root / target).write_bytes((source / filename).read_bytes())
        inventory = {
            path.relative_to(root).as_posix(): {
                "sha256": digest(path.read_bytes()), "bytes": path.stat().st_size
            }
            for path in sorted(root.rglob("*")) if path.is_file()
        }
        save_json(root / "manifest.json", {
            "benchmark": "human-eval", "commit": card["dataset"]["commit"],
            "cases": len(truth), "files": inventory,
        })
        root.rename(home / "dataset")
    return validate_dataset(home, CARD)


def task_schema(home, case):
    from harness.web_extract.pipeline import read_json

    return read_json(home / "dataset/schemas.json")[case["schema_id"]]


def probe_cases(truth):
    """A deterministic predeclared probe, without inspecting expected answers."""
    return sorted(truth)[:3]


def record_identity(value):
    """Ignore array order while preserving whole records, duplicates and JSON types."""
    if isinstance(value, dict):
        return ("object", tuple(sorted((k, record_identity(v)) for k, v in value.items())))
    if isinstance(value, list):
        return ("array", tuple(sorted((record_identity(v) for v in value), key=repr)))
    return (type(value).__name__, value)


def score(truth, predictions, *, home):
    """Reproduce upstream per-case metrics and keep every task in the denominator."""
    from harness.web_extract.pipeline import validate_dataset

    validate_dataset(home, CARD)
    scorer = runpy.run_path(str(home / "dataset/evaluate.py"))
    details = {}
    for case_id, case in truth.items():
        prediction = predictions.get(case_id)
        delivered = isinstance(prediction, dict)
        # The upstream CLI skips absent predictions. Here run_all receives an empty
        # invalid response, so all 100 tasks contribute, including empty gold objects.
        metrics = scorer["run_all"](
            json.dumps(prediction, ensure_ascii=False, allow_nan=False) if delivered else "",
            json.loads(case["schema_text"]), case["response"],
        )
        if delivered:
            flat_prediction = scorer["_flatten_dict"](prediction)
            flat_gold = scorer["_flatten_dict"](case["response"])
            metrics["value_score"] = (
                math.fsum(
                    scorer["_compare_values"](flat_prediction[key], flat_gold[key])
                    if key in flat_prediction else 0.0
                    for key in sorted(flat_gold)
                ) / len(flat_gold)
                if flat_gold else float(not flat_prediction)
            )
        if not delivered:
            metrics.update(scorer["magic_metric_failed"]())
        details[case_id] = {
            **metrics, "delivered": delivered,
            "exact_record_match": delivered
            and record_identity(prediction) == record_identity(case["response"]),
        }
    keys = list(next(iter(details.values())))
    return {
        "population": len(truth),
        "delivery_count": sum(row["delivered"] for row in details.values()),
        "mean": {key: math.fsum(row[key] for row in details.values()) / len(truth) for key in keys},
        "per_case": details,
        "scoring_scope": "All declared cases; missing outputs score zero, without subset selection",
        "exact_record_match": "Type-sensitive exact JSON; unordered arrays of whole records",
        "scoring_implementation": SCORING_REVISION["id"],
    }


def check_scoring_controls(home):
    """Exercise paired controls before writing a score-only correction receipt."""
    from harness.web_extract.pipeline import read_json, score_rows

    registry = Path(__file__).resolve().parents[1] / "benchmarks/scoring-regressions.json"
    controls = read_json(registry)["web_extract_cases"]

    def evaluate(references, predictions):
        truth = {
            cid: {"response": value, "schema_text": '{"type":"object"}'}
            for cid, value in references.items()
        }
        rows = [
            {"method": "cloudflare-json", "case_id": cid, "http_status": 200,
             "normalized_fields": predictions.get(cid)} for cid in references
        ]
        return score_rows(home, truth, rows, ["cloudflare-json"])["cloudflare-json"]

    for control in controls:
        if control.get("check") == "case-and-field-order-invariance":
            references = {
                str(n): {str(k): k for k in range(n)} for n in range(2, 80)
            }
            predictions = {cid: {**gold, "0": -1} for cid, gold in references.items()}
            first = evaluate(references, predictions)
            second = evaluate(dict(reversed(list(references.items()))), predictions)
            if first != second:
                raise ValueError("Value scores depend on input order")
        else:
            result = evaluate({"case": control["reference"]}, {"case": control["prediction"]})
            actual = result["per_case"]["case"]
            if any(actual[key] != value for key, value in control["expected"].items()):
                raise ValueError("Scoring regression failed: " + control["id"])
    identifiers = [control["id"] for control in controls]
    if identifiers != SCORING_REVISION["regression_ids"]:
        raise ValueError("Scoring revision omits a registered regression control")
    return identifiers
