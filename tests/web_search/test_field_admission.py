"""Extra text enters scoring only after a complete, replayable source sample."""

import json
from copy import deepcopy

import pytest

from harness.record_io import bytes_sha256
from harness.web_search.output_levels import field_sample_plan, level_inputs, verify_field_admission


def example():
    text = "An independently published paragraph describing the document and its original contents."
    profile = dict(
        protocol=dict(top_k=5),
        methods=[dict(id="web", adapter="bocha", output_options=dict(summary=True))],
        cases=[
            dict(
                id=f"q{i}",
                query="Find the document",
                family="web",
                reference_answer="needle",
                reference_urls=[],
            )
            for i in range(10)
        ],
    )
    rows = [
        dict(
            id=c["id"] + "-web",
            case_id=c["id"],
            method="web",
            evidence=dict(path=c["id"] + ".json", sha256="a" * 64),
            results=[
                dict(
                    url=f"https://example.test/{i}-{j}",
                    title="needle",
                    snippet="Primary preview",
                    summary=text,
                )
                for j in range(5)
            ],
            metrics={"answer_in_preview_5": 1},
        )
        for i, c in enumerate(profile["cases"])
    ]
    plan = field_sample_plan(profile, rows, "web")
    payload = dict(
        results=[
            dict(id=s["url"], url=s["url"], text="# Document\n\n" + text) for s in plan["samples"]
        ],
        statuses=[dict(id=s["url"], status="success", source="crawled") for s in plan["samples"]],
    )
    raw = json.dumps(payload).encode()
    capture = dict(
        request=dict(urls=[s["url"] for s in plan["samples"]], maxAgeHours=0),
        sha256=bytes_sha256(raw),
        http_status=200,
        transport_exit=0,
        completed_at="2026-11-13T00:00:00Z",
    )
    meta = json.dumps(capture).encode()
    receipt = dict(
        schema="web-search-field-admission/1",
        plan=plan,
        pages=[
            dict(
                url=s["url"],
                evidence=dict(path="pages.json", sha256=bytes_sha256(raw)),
                capture=dict(path="capture.json", sha256=bytes_sha256(meta)),
            )
            for s in plan["samples"]
        ],
    )
    return profile, rows, receipt, {"pages.json": raw, "capture.json": meta}


def test_source_sample_is_deterministic_and_admission_replays_original_bytes():
    profile, rows, receipt, artifacts = example()
    assert field_sample_plan(profile, list(reversed(rows)), "web") == receipt["plan"]
    qualification = verify_field_admission(profile, rows, dict(receipt, qualified=False), artifacts)
    assert qualification["qualified"] is True and qualification["sample_size"] == 50
    selected, values = level_inputs(
        profile, rows, "l2", admissions={"web": receipt}, artifacts=artifacts
    )
    assert len(selected["cases"]) == len(values) == 10
    assert values[0]["metrics"]["answer_in_extra_text_5"] == 0
    assert "labeled_source_hit_5" not in values[0]["metrics"]
    assert selected["methods"][0]["output_fields"]["extra_text"] == ["summary"]


@pytest.mark.parametrize(
    "defect", ["missing-page", "changed-native", "changed-source", "cached-page"]
)
def test_missing_changed_or_nonlive_evidence_cannot_qualify_a_field(defect):
    profile, rows, receipt, artifacts = example()
    if defect == "missing-page":
        receipt["pages"].pop()
    elif defect == "changed-native":
        rows[0]["results"][0]["summary"] = "A different provider output"
    elif defect == "changed-source":
        artifacts["pages.json"] += b"\n"
    else:
        payload = json.loads(artifacts["pages.json"])
        payload["statuses"][0]["source"] = "cached"
        artifacts["pages.json"] = json.dumps(payload).encode()
        capture = json.loads(artifacts["capture.json"])
        capture["sha256"] = bytes_sha256(artifacts["pages.json"])
        artifacts["capture.json"] = json.dumps(capture).encode()
        for page in receipt["pages"]:
            page["evidence"]["sha256"] = bytes_sha256(artifacts["pages.json"])
            page["capture"]["sha256"] = bytes_sha256(artifacts["capture.json"])
    with pytest.raises(ValueError):
        level_inputs(profile, rows, "l2", admissions={"web": receipt}, artifacts=artifacts)


def test_failed_original_text_check_excludes_the_method_instead_of_creating_a_zero_row():
    profile, rows, receipt, artifacts = example()
    payload = json.loads(artifacts["pages.json"])
    for page in payload["results"]:
        page["text"] = "A different document with no matching original wording."
    artifacts["pages.json"] = json.dumps(payload).encode()
    capture = json.loads(artifacts["capture.json"])
    capture["sha256"] = bytes_sha256(artifacts["pages.json"])
    artifacts["capture.json"] = json.dumps(capture).encode()
    for page in receipt["pages"]:
        page["evidence"]["sha256"] = bytes_sha256(artifacts["pages.json"])
        page["capture"]["sha256"] = bytes_sha256(artifacts["capture.json"])
    assert not verify_field_admission(profile, rows, dict(receipt, qualified=True), artifacts)[
        "qualified"
    ]
    selected, values = level_inputs(
        profile, rows, "l2", admissions={"web": receipt}, artifacts=artifacts
    )
    assert selected["methods"] == [] and values == []


def test_basic_search_needs_no_page_fetch_and_keeps_only_top_five_native_slots():
    profile, rows, _, _ = example()
    untouched = deepcopy(rows)
    rows[0]["results"].append(
        dict(url="https://example.test/outside-cutoff", snippet="Do not promote", summary="Extra")
    )
    selected, values = level_inputs(profile, rows, "l1")
    assert len(values[0]["results"]) == 5
    assert values[0]["metrics"]["answer_in_preview_5"] == 1
    assert values[0]["results"][0]["snippet"] == "Primary preview"
    assert "summary" not in values[0]["results"][0]
    assert rows[1:] == untouched[1:]
