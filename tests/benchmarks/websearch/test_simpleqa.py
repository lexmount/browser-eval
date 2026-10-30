"""Frozen population selection follows input limits, never provider outcomes."""

import csv
import io

import pytest

from lexbench_websearch.simpleqa import eligible_cases, rebuild_dataset


def csv_fixture(questions):
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(
        ["original_index", "problem", "answer", "topic", "answer_type", "urls"]
    )
    for index, question in enumerate(questions):
        writer.writerow(
            [index, question, "Answer", "Topic", "Text", "https://example.org/a)"]
        )
    return text.getvalue().encode()


def test_shared_limit_is_inclusive_unicode_character_count_and_keeps_order():
    cases = eligible_cases(csv_fixture(["A" * 128, "B" * 129, "\u4e2d" * 128, "short"]))
    assert [c["id"] for c in cases] == ["sqv-0", "sqv-2", "sqv-3"]
    assert cases[1]["query"] == "\u4e2d" * 128
    assert cases[0]["reference_urls"] == ["https://example.org/a"]


def test_duplicate_publisher_ids_rejected():
    raw = csv_fixture(["one", "two"]).replace(b"1,two", b"0,two")
    with pytest.raises(ValueError, match="Duplicate"):
        eligible_cases(raw)


def test_builder_rejects_unpinned_csv_before_selection():
    with pytest.raises(ValueError, match="SHA256"):
        rebuild_dataset(csv_fixture(["one"]))


def test_builder_retains_source_artifact_metadata():
    from lexbench_websearch import simpleqa

    raw = csv_fixture(["one"])
    manifest = simpleqa.source_manifest()
    manifest["sha256"] = simpleqa.digest(raw)
    # Correctly hashed but partial publisher inputs must still be rejected.
    with pytest.raises(ValueError, match="Frozen population"):
        simpleqa.rebuild_dataset(raw, manifest=manifest)
