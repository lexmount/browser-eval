"""Build a compact, deterministic sample manifest from WebMainBench-545."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path


OFFICIAL_SHA256 = "0efaa4b49a45e320a27fe6e5a0b6aad5b57259fc3321ac3448519cacc74c537e"
DEFAULT_SEED = "browser-eval:webmainbench-545:v1:200"
DECLARED_AXES = ("language", "style", "level", "table", "code", "equation")
# The official 545 file has meta.style=null on every row, despite the README schema.
AXES = ("language", "level", "table", "code", "equation")


def _hash(value: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}\0{value}".encode()).hexdigest()


def _category(meta: dict, axis: str) -> str:
    value = meta.get(axis)
    if axis == "style" and isinstance(value, dict):
        value = value.get("category")
    if axis in {"table", "code", "equation"}:
        if not value:
            return "none"
        if not isinstance(value, list):
            raise ValueError(f"meta.{axis} must be a list")
        return "+".join(sorted(str(item) for item in value))
    if value is None or value == "":
        return "unavailable"
    if not isinstance(value, str):
        raise ValueError(f"meta.{axis} must be a string or null")
    return value.strip()


def _labels(record: dict) -> set[str]:
    meta = record["meta"]
    return {f"{axis}:{_category(meta, axis)}" for axis in AXES}


def load_records(source: Path) -> tuple[list[dict], str, int]:
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != OFFICIAL_SHA256:
        raise ValueError(f"Unexpected WebMainBench-545 SHA-256: {digest}")
    records = [json.loads(line) for line in payload.splitlines() if line.strip()]
    if len(records) != 545:
        raise ValueError(f"Expected 545 records, found {len(records)}")
    required = {"track_id", "url", "html", "main_html", "convert_main_content", "groundtruth_content", "meta"}
    for index, record in enumerate(records, 1):
        missing = sorted(required - record.keys())
        if missing:
            raise ValueError(f"Record {index} is missing: {', '.join(missing)}")
        _labels(record)
    ids = [record["track_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate track_id in WebMainBench-545")
    return records, digest, len(payload)


def select_records(records: list[dict], size: int = 200, seed: str = DEFAULT_SEED) -> list[dict]:
    """Cover every observed official category, then fill by seeded hash order."""
    if not 0 < size <= len(records):
        raise ValueError("Sample size must be between 1 and the parent size")
    universe = set().union(*(_labels(record) for record in records))
    uncovered = set(universe)
    selected: list[dict] = []
    remaining = list(records)
    while uncovered:
        remaining.sort(
            key=lambda record: (
                -len(_labels(record) & uncovered),
                _hash(record["track_id"], seed),
            )
        )
        choice = remaining.pop(0)
        selected.append(choice)
        uncovered -= _labels(choice)
    if len(selected) > size:
        raise ValueError(f"Category coverage requires {len(selected)} records, over sample size {size}")
    remaining.sort(key=lambda record: _hash(record["track_id"], seed))
    selected.extend(remaining[: size - len(selected)])
    return sorted(selected, key=lambda record: record["track_id"])


def _distribution(records: list[dict]) -> dict[str, dict[str, int]]:
    return {
        axis: dict(sorted(Counter(_category(record["meta"], axis) for record in records).items()))
        for axis in AXES
    }


def _blank(value: object) -> bool:
    return value is None or not str(value).strip()


def build_manifest(source: Path, output: Path, size: int = 200, seed: str = DEFAULT_SEED) -> dict:
    records, digest, byte_count = load_records(source)
    selected = select_records(records, size=size, seed=seed)
    manifest = {
        "schema_version": 1,
        "dataset_id": "webmainbench-545-derived-200",
        "status": "sample_frozen_no_provider_results",
        "parent": {
            "name": "WebMainBench-545",
            "records": len(records),
            "source": "https://huggingface.co/datasets/opendatalab/WebMainBench/blob/main/WebMainBench_545.jsonl",
            "sha256": digest,
            "bytes": byte_count,
            "license": "Apache-2.0",
        },
        "sampling": {
            "size": len(selected),
            "seed": seed,
            "algorithm": "greedy coverage of every observed marginal official category, then SHA-256 order fill",
            "official_axes": list(AXES),
            "declared_but_unavailable_axes": ["style"],
            "leaderboard_comparability": "derived subset; do not compare its aggregate with the official 545-sample leaderboard",
        },
        "parent_distribution": _distribution(records),
        "sample_distribution": _distribution(selected),
        "quality_audit": {
            "unique_track_ids": len({record["track_id"] for record in records}),
            "unique_urls": len({record["url"] for record in records}),
            "blank_urls": sum(_blank(record["url"]) for record in records),
            "empty_html": sum(_blank(record["html"]) for record in records),
            "empty_main_html": sum(_blank(record["main_html"]) for record in records),
            "empty_convert_main_content": sum(_blank(record["convert_main_content"]) for record in records),
            "empty_groundtruth_content": sum(_blank(record["groundtruth_content"]) for record in records),
            "non_null_style": sum(record["meta"].get("style") is not None for record in records),
            "non_null_language": sum(record["meta"].get("language") is not None for record in records),
        },
        "cases": [
            {
                "track_id": record["track_id"],
                "url": record["url"],
                "meta": {axis: _category(record["meta"], axis) for axis in AXES},
            }
            for record in selected
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
