"""Rebuild the preregistered length-qualified SimpleQA retrieval population."""

import csv
import io
import re

from .inputs import digest


def source_manifest():
    from harness.benchmarks.data import definition_path
    import json

    return json.loads(definition_path("simpleqa-verified", "factual-source.json").read_bytes())


def _urls(value):
    urls = []
    for part in value.split(","):
        match = re.match(r"https?://\S+", part.strip())
        if not match:
            continue
        url = match.group(0)
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        if url not in urls:
            urls.append(url)
    return urls


def eligible_cases(csv_bytes, *, manifest=None):
    """Select by the pre-call shared input limit, retaining original CSV order."""
    manifest = manifest or source_manifest()
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
    if len({row["original_index"] for row in rows}) != len(rows):
        raise ValueError("Duplicate publisher question identity")
    return [
        {
            **{k: v for k, v in manifest["case"].items() if k != "id_prefix"},
            "id": manifest["case"]["id_prefix"] + row["original_index"],
            "query": row["problem"],
            "origin": manifest["url"],
            "reference_answer": row["answer"],
            "reference_urls": _urls(row["urls"]),
            "topic": row["topic"],
            "answer_type": row["answer_type"],
        }
        for row in rows
        if len(row["problem"]) <= manifest["selection"]["max_query_characters"]
    ]


def rebuild_dataset(csv_bytes, *, manifest=None):
    """Verify the pinned publisher input before rebuilding the declared population."""
    manifest = manifest or source_manifest()
    if digest(csv_bytes) != manifest["sha256"]:
        raise ValueError("Publisher CSV SHA256 does not match the frozen source")
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
    cases = eligible_cases(csv_bytes, manifest=manifest)
    cases = [{key: case[key] for key in manifest["case_field_order"]} for case in cases]
    if (
        len(rows) != manifest["selection"]["source_count"]
        or len(cases) != manifest["selection"]["selected_count"]
    ):
        raise ValueError("Frozen population differs from the pinned source selection")
    return {
        "schema": "lexbench-websearch-dataset/1",
        "id": manifest["dataset"],
        "cases": cases,
        "reference_checks": {},
        "reference_artifacts": {
            manifest["url"]: {
                "sha256": manifest["sha256"],
                "license": manifest["license"],
                "scope": manifest["scope"],
            }
        },
    }
