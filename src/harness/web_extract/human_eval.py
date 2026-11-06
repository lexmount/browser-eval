"""ScrapeGraph's complete human_eval split for the shared Web Extract runner."""

from __future__ import annotations

import html
import json
import math
import os
import runpy
import subprocess
import sys
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path

CARD = Path(__file__).resolve().parents[1] / "benchmarks/cards/scrapegraph-human-eval.json"
INSTRUCTIONS = ""
INSTRUCTION_DELIVERY = "original-schema/1"
SCORE_REQUIRES_HOME = True
MISSING_OUTPUT_NONE = True
SCORING_REVISION = {
    "id": "human-eval-record-items/3",
    "root_cause": (
        "The upstream value_score sums scores over a hash-randomized key set. "
        "Use the same upstream leaf comparisons with math.fsum in sorted key order; "
        "aggregate cases with math.fsum too. The official wildcard scorer loses record "
        "associations and scores complete array fields all-or-nothing. Add a separate "
        "duplicate-preserving exact item F1, including numeric-equivalent nested array "
        "multisets; keep every official metric unchanged."
    ),
    "regression_ids": [
        "human-eval-missing-empty-reference", "human-eval-delivered-empty-reference",
        "human-eval-correct-record-pairs", "human-eval-swapped-record-pairs",
        "human-eval-process-independent-value-score",
        "human-eval-item-partial-recall", "human-eval-item-extra-record",
        "human-eval-item-number-equivalence", "human-eval-item-boolean-type",
        "human-eval-nested-number-equivalence", "human-eval-nested-array-multiplicity",
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


def item_identity(value):
    """Canonical JSON values; numeric serializers agree, Boolean and null stay distinct."""
    if type(value) in (int, float):
        return ("number", Decimal(str(value)))
    if isinstance(value, dict):
        return ("object", tuple(sorted((k, item_identity(v)) for k, v in value.items())))
    if isinstance(value, list):
        return ("array", frozenset(Counter(item_identity(v) for v in value).items()))
    return (type(value).__name__, value)


def exact_items(value, path=()):
    """Array elements are whole records; ordinary object fields keep their full paths."""
    if isinstance(value, dict) and value:
        return Counter(item for key, child in value.items()
                       for item in exact_items(child, (*path, key)).elements())
    if isinstance(value, list) and value:
        return Counter((path, item_identity(item)) for item in value)
    return Counter({(path, item_identity(value)): 1})


def item_scores(reference, prediction):
    """Strict structural diagnostics, not semantic equivalence or task acceptance."""
    expected = exact_items(reference)
    actual = exact_items(prediction) if isinstance(prediction, dict) else Counter()
    hits = sum((expected & actual).values())
    precision = hits / sum(actual.values()) if actual else 0.0
    recall = hits / sum(expected.values()) if expected else 0.0
    return {"item_precision": precision, "item_recall": recall,
            "item_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}


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
            **item_scores(case["response"], prediction),
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
            check_hash_seed_scores(home)
        else:
            result = evaluate({"case": control["reference"]}, {"case": control["prediction"]})
            actual = result["per_case"]["case"]
            if any(actual[key] != value for key, value in control["expected"].items()):
                raise ValueError("Scoring regression failed: " + control["id"])
    identifiers = [control["id"] for control in controls]
    if identifiers != SCORING_REVISION["regression_ids"]:
        raise ValueError("Scoring revision omits a registered regression control")
    return identifiers


def check_hash_seed_scores(home):
    """Fractional leaf comparisons must survive reordered keys and process hash seeds."""
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa".split()
    gold = {f"field-{i}": " ".join(words) for i in range(151)}
    predicted = {key: " ".join(words[:4 + i % 7]) for i, key in enumerate(gold)}
    evaluator = runpy.run_path(str(home / "dataset/evaluate.py"))
    leaves = [evaluator["_compare_values"](predicted[k], gold[k]) for k in sorted(gold)]
    if not any(0 < score < 1 for score in leaves):
        raise ValueError("Hash-seed control requires fractional leaf comparisons")
    expected = (math.fsum(leaves) / len(leaves)).hex()
    program = """
import json, sys
from pathlib import Path
from harness.web_extract import human_eval, dataset, pipeline
human_eval.CARD = Path(sys.argv[2])
dataset.BENCHMARKS['human-eval']['profile'] = str(human_eval.CARD)
gold, prediction = json.load(sys.stdin)
truth = {'control': {'response': gold, 'schema_text': '{"type":"object"}'}}
rows = [{'method': 'cloudflare-json', 'case_id': 'control', 'http_status': 200,
         'normalized_fields': prediction}]
result = pipeline.score_rows(Path(sys.argv[1]), truth, rows, ['cloudflare-json'])
print(result['cloudflare-json']['per_case']['control']['value_score'].hex())
"""
    for index, seed in enumerate((1, 2, 7, 19)):
        ordered = dict(reversed(list(gold.items()))) if index % 2 else gold
        result = subprocess.run(
            [sys.executable, "-c", program, str(home), str(CARD)],
            input=json.dumps([ordered, predicted]), text=True, capture_output=True, check=True,
            env={**os.environ, "PYTHONHASHSEED": str(seed)}, timeout=60,
        )
        if result.stdout.strip() != expected:
            raise ValueError("Value scores depend on field order or process hash seed")


def verify_predecessor_scores(saved, current, case_ids):
    """Only explicitly additive diagnostics may be absent from an original score."""
    additive = {"item_precision", "item_recall", "item_f1"}
    if not isinstance(saved, dict) or set(saved) != set(current):
        return False

    def metrics(original, expected):
        if (not isinstance(original, dict)
                or not set(expected) - additive <= set(original) <= set(expected)):
            return False
        return all(
            type(original[key]) is bool and original[key] == value
            if type(value) is bool else type(original[key]) in (int, float)
            and math.isclose(original[key], value, rel_tol=0, abs_tol=1e-14)
            for key, value in expected.items() if key in original
        )

    for method, expected in current.items():
        original = saved[method]
        if (not isinstance(original, dict)
                or type(original.get("population")) is not int
                or original["population"] != len(case_ids)
                or type(original.get("delivery_count")) is not int
                or original["delivery_count"] != expected["delivery_count"]
                or not isinstance(original.get("per_case"), dict)
                or set(original["per_case"]) != set(case_ids)
                or not metrics(original.get("mean"), expected["mean"])
                or any(not metrics(original["per_case"][cid], expected["per_case"][cid])
                       for cid in case_ids)):
            return False
    return True


def load_reference_revision(home, path):
    """Apply source-bound, explicitly adjudicated replacements without rewriting the dataset."""
    import copy

    from harness.web_extract.pipeline import digest, read_json, validate_dataset

    validate_dataset(home, CARD)
    authority = read_json(path)
    original = home / "dataset/dataset/ground-truth.json"
    if (authority.get("schema") != "source-json-reference-revision/1"
            or authority.get("original_reference_sha256") != digest(original.read_bytes())
            or not authority.get("root_cause")
            or authority.get("regression_ids") != check_scoring_controls(home)):
        raise ValueError("Reference correction lacks frozen provenance and executable controls")
    truth, seen = copy.deepcopy(read_json(original)), set()
    for change in authority["changes"]:
        cid, pointer = change["case_id"], change["path"]
        if (cid, pointer) in seen or not pointer.startswith("/"):
            raise ValueError("Duplicate or invalid reference correction")
        seen.add((cid, pointer))
        source = (home / "dataset/markdown" / (cid + ".md")).read_bytes()
        if (digest(source) != change["source_sha256"] or not change["reason"]
                or not change["source_quote"]
                or change["source_quote"] not in source.decode("utf-8")):
            raise ValueError("Reference correction lacks exact frozen source evidence")
        keys = [k.replace("~1", "/").replace("~0", "~") for k in pointer[1:].split("/")]
        parent = truth[cid]["response"]
        for key in keys[:-1]:
            parent = parent[int(key)] if isinstance(parent, list) else parent[key]
        key = int(keys[-1]) if isinstance(parent, list) else keys[-1]
        if record_identity(parent[key]) != record_identity(change["before"]):
            raise ValueError("Reference correction predecessor differs")
        parent[key] = change["after"]
    if not seen:
        raise ValueError("Empty reference revision")
    return truth, {}


def reference_revision_receipt(home, path):
    from harness.web_extract.pipeline import digest, read_json

    load_reference_revision(home, path)
    authority = read_json(path)
    return {"schema": authority["schema"], "authority_sha256": digest(Path(path).read_bytes()),
            "original_reference_sha256": authority["original_reference_sha256"],
            "root_cause": authority["root_cause"], "regression_ids": authority["regression_ids"],
            "changed_paths": [{"case_id": c["case_id"], "path": c["path"]}
                              for c in authority["changes"]], "provider_calls_added": 0}
