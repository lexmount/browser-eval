"""Every reported level has one supported metric contract and a complete population."""

from copy import deepcopy

import pytest

from harness.web_search.output_levels import assessed_levels, level_inputs
from lexbench_websearch.relevance import candidate_packet
from tests.web_search.test_field_admission import example


def human_evidence(profile, rows):
    packet, rankings = candidate_packet(profile, rows)
    return dict(
        packet,
        provider_rankings=rankings,
        labels={
            p["case_id"]: {c["candidate_id"]: 3 for c in p["candidates"]}
            for p in packet["blind_candidate_packets"]
        },
        judge={"kind": "human", "name": "Synthetic fixture annotator"},
    )


def complete_levels():
    profile, rows, admission, artifacts = example()
    profile["cases"][0].pop("reference_answer")
    profile["factual_quality_exclusions"] = [profile["cases"][-1]["id"]]
    admissions = {"web": admission}
    evidence = {}
    for level in ("l1", "l2"):
        p, r = level_inputs(profile, rows, level, admissions=admissions, artifacts=artifacts)
        evidence[level] = human_evidence(p, r)
    return profile, rows, admissions, artifacts, evidence


def test_each_level_uses_its_own_text_metrics_and_same_two_benchmarks():
    profile, rows, admissions, artifacts, evidence = complete_levels()
    views = assessed_levels(
        profile, rows, relevance=evidence, admissions=admissions, artifacts=artifacts
    )
    assert set(views) == {"l1", "l2"}
    for view in views.values():
        benchmarks = view["benchmarks"]
        assert set(benchmarks) == {"scenario", "factual"}
        assert len(benchmarks["scenario"]["case_ids"]) == 1
        assert len(benchmarks["factual"]["case_ids"]) == 8
        assert len(benchmarks["factual"]["timing_case_ids"]) == 9
        assert benchmarks["scenario"]["primary_metric"] == "ndcg"
    l1 = views["l1"]["benchmarks"]["factual"]
    l2 = views["l2"]["benchmarks"]["factual"]
    assert l1["primary_metric"] == "source_rr"
    assert l2["primary_metric"] == "answer_match"
    assert {m["key"] for m in l1["metrics"]} == {"source_rr", "source_hit", "answer_match"}
    assert {m["key"] for m in l2["metrics"]} == {"answer_match"}
    assert all(s["answer_match"] == 1 for s in l1["scores"])
    assert all(s["answer_match"] == 0 for s in l2["scores"])
    assert all("source_rr" not in s and "source_hit" not in s for s in l2["scores"])
    assert views["l1"]["rows"][0]["results"][0]["snippet"] == "Primary preview"
    assert views["l2"]["rows"][0]["results"][0]["snippet"].startswith(
        "An independently published paragraph"
    )


@pytest.mark.parametrize(
    "defect", ["missing-level", "swapped-level", "missing-grade", "changed-text"]
)
def test_report_level_scores_reject_incomplete_or_mixed_judgments(defect):
    profile, rows, admissions, artifacts, evidence = complete_levels()
    if defect == "missing-level":
        del evidence["l2"]
    elif defect == "swapped-level":
        evidence["l2"] = deepcopy(evidence["l1"])
    elif defect == "missing-grade":
        evidence["l2"]["labels"].clear()
    else:
        rows[0]["results"][0]["snippet"] = "Another primary preview"
    with pytest.raises((ValueError, KeyError)):
        assessed_levels(
            profile, rows, relevance=evidence, admissions=admissions, artifacts=artifacts
        )


def test_configuration_without_extra_text_is_absent_from_l2():
    profile, rows, admissions, artifacts, evidence = complete_levels()
    profile["methods"].append(dict(id="preview-only", adapter="serper"))
    rows += [dict(row, method="preview-only") for row in deepcopy(rows)]
    p, r = level_inputs(profile, rows, "l1")
    evidence["l1"] = human_evidence(p, r)
    views = assessed_levels(
        profile, rows, relevance=evidence, admissions=admissions, artifacts=artifacts
    )
    assert [m["id"] for m in views["l1"]["profile"]["methods"]] == ["web", "preview-only"]
    assert [m["id"] for m in views["l2"]["profile"]["methods"]] == ["web"]
    assert {r["method"] for r in views["l2"]["rows"]} == {"web"}


def test_unqualified_extra_text_removes_l2_without_zero_rows():
    def different_page(payload, plan):
        for item in payload["results"]:
            item["text"] = "A different original page with unrelated wording."

    profile, rows, admission, artifacts = example(source_mutator=different_page)
    projected, values = level_inputs(profile, rows, "l1")
    views = assessed_levels(
        profile,
        rows,
        relevance={"l1": human_evidence(projected, values)},
        admissions={"web": admission},
        artifacts=artifacts,
    )
    assert set(views) == {"l1"}


def write_assessment_bundle(root):
    import json

    from harness.record_io import bytes_sha256

    profile, rows, admissions, artifacts, evidence = complete_levels()

    def save(name, value):
        raw = json.dumps(value).encode()
        (root / name).write_bytes(raw)
        return dict(path=name, sha256=bytes_sha256(raw))

    for name, raw in artifacts.items():
        (root / name).write_bytes(raw)
    admission_binding = save("field-receipt.json", admissions["web"])
    fields = dict(
        schema="web-search-field-admissions/1",
        receipts={"web": admission_binding},
        artifacts=[dict(path=name, sha256=bytes_sha256(raw)) for name, raw in artifacts.items()],
    )
    manifest = dict(
        schema="web-search-level-assessments/1",
        relevance={level: save(level + ".json", value) for level, value in evidence.items()},
        field_admissions=save("fields.json", fields),
    )
    save("manifest.json", manifest)
    return profile, rows


def test_assessment_loader_retains_the_complete_verified_file_inventory(tmp_path):
    from harness.web_search.output_levels import load_assessed_levels

    profile, rows = write_assessment_bundle(tmp_path)
    views, files = load_assessed_levels(tmp_path / "manifest.json", profile, rows)
    assert set(views) == {"l1", "l2"}
    assert set(files) == {
        "manifest.json",
        "l1.json",
        "l2.json",
        "fields.json",
        "field-receipt.json",
        "pages.json",
        "capture.json",
    }
    assert all((tmp_path / name).read_bytes() == raw for name, raw in files.items())


@pytest.mark.parametrize("damage", ["source-bytes", "relevance-bytes", "manifest-symlink"])
def test_assessment_loader_rejects_changed_or_unsafe_evidence(tmp_path, damage):
    from harness.web_search.output_levels import load_assessed_levels

    profile, rows = write_assessment_bundle(tmp_path)
    if damage == "manifest-symlink":
        (tmp_path / "manifest.json").rename(tmp_path / "original.json")
        (tmp_path / "manifest.json").symlink_to(tmp_path / "original.json")
    else:
        target = tmp_path / ("pages.json" if damage == "source-bytes" else "l1.json")
        target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError):
        load_assessed_levels(tmp_path / "manifest.json", profile, rows)


def test_query_comparison_uses_the_same_level_namespaces_and_metric_scope():
    from harness.web_search.comparison import comparison_data

    profile, rows, admissions, artifacts, evidence = complete_levels()
    profile["methods"][0]["label"] = "Web search fixture"
    views = assessed_levels(
        profile, rows, relevance=evidence, admissions=admissions, artifacts=artifacts
    )
    for level, view in views.items():
        data = comparison_data(
            view["profile"],
            view["rows"],
            relevance=view["relevance"],
            primary_factual_metric="answer" if level == "l2" else "source",
        )
        scenario = next(case for case in data["cases"] if case["id"] == profile["cases"][0]["id"])
        output = scenario["outputs"]["web"]
        assert output["score"]["ndcg"] == 1
        assert all(item["grade"] == 3 for item in output["items"])
        fact = next(case for case in data["cases"] if case["id"] == profile["cases"][1]["id"])
        if level == "l2":
            assert fact["outputs"]["web"]["source"] is None
            assert fact["outputs"]["web"]["answer"] is False
            assert all(
                item["answer_fields"]["title"] is None for item in fact["outputs"]["web"]["items"]
            )


def test_public_level_bundle_preserves_native_bytes_without_internal_directory_names(tmp_path):
    from harness.web_search.output_levels import export_level_bundle, load_assessed_levels

    source = tmp_path / "private"
    source.mkdir()
    profile, rows = write_assessment_bundle(source)
    original = {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}
    destination = tmp_path / "public"
    result = export_level_bundle(source / "manifest.json", destination)
    views, files = load_assessed_levels(destination / result["path"], profile, rows)
    assert set(views) == {"l1", "l2"}
    assert all(
        name == "assessments.json"
        or name == "field-admissions.json"
        or name.startswith(("sources/", "fields/", "judgments/"))
        for name in files
    )
    assert original == {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}
    source_bytes = {raw for name, raw in original.items() if name in {"pages.json", "capture.json"}}
    assert source_bytes <= set(files.values())
