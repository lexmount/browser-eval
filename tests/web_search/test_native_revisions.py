"""Saved-call correction and cumulative reference quarantine scenarios."""
import copy
import json
from urllib.parse import urljoin
import pytest
from lexbench_websearch.inputs import digest
from lexbench_websearch.scoring import evaluate, normalize_results
from harness.web_search.revisions import apply_report_revision, NATIVE_CONTROLS

def test_reference_quarantine_applies_to_all_methods_without_changing_calls():
    case = {
        "id": "fact",
        "family": "factual",
        "query": "How many?",
        "reference_answer": "42",
        "reference_urls": [],
    }
    profile = {"cases": [case]}
    rows = [
        {
            "id": method,
            "case_id": "fact",
            "results": [],
            "elapsed_ms": 17,
            "cost": 1,
            "metrics": evaluate(case, []),
        }
        for method in ("a", "b")
    ]
    original = copy.deepcopy((profile, rows))
    decision = {
        "case_id": "fact",
        "query": "How many?",
        "reference_answer": "42",
        "quality_excluded": True,
        "reason": "Independent source contradicts the reference",
        "evidence": [{"url": "https://reference.test/", "excerpt": "43"}],
    }
    revision = {
        "schema": "web-search-assessment-revision/1",
        "provider_calls": 0,
        "root_cause": "Incorrect factual reference",
        "regression_ids": ["unqualified-reference-rejected"],
        "reference_adjudications": [decision],
    }
    revised, updated, _ = apply_report_revision(profile, rows, revision)
    assert revised["factual_quality_exclusions"] == ["fact"]
    assert updated == rows
    assert (profile, rows) == original
    for key, value in (
        ("query", "Another query"),
        ("reference_answer", "43"),
        ("evidence", []),
    ):
        changed = copy.deepcopy(revision)
        changed["reference_adjudications"][0][key] = value
        with pytest.raises(ValueError):
            apply_report_revision(profile, rows, changed)




def test_native_revision_requires_adapter_and_rejects_incorrect_projection():
    revision = {
        "schema": "web-search-assessment-revision/1",
        "provider_calls": 0,
        "root_cause": "Native URL projection correction",
        "normalization": "request-url/1",
        "regression_ids": [
            "relative-serp-links",
            "unsafe-serp-links",
            "top-five-boundary",
        ],
        "regressions_sha256": digest(
            NATIVE_CONTROLS.read_bytes()
        ),
    }
    with pytest.raises(ValueError, match="execution adapter"):
        apply_report_revision({"protocol": {}, "cases": []}, [], revision)
    with pytest.raises(ValueError, match="Normalization regression failed"):
        apply_report_revision(
            {"protocol": {}, "cases": []}, [], revision, normalizer=lambda *_: []
        )




def test_native_correction_preserves_calls_and_checks_artifacts(tmp_path, monkeypatch):
    from harness.web_search import revisions

    case = {"id": "q", "family": "web", "anchors": ["https://source.test/answer"]}
    protocol = {"result_url_resolution": "request-url/1"}
    request = {"url": "https://source.test/search"}
    method = {"id": "a"}
    payload = {"results": [{"url": "/answer"}]}
    raw = json.dumps(payload).encode()

    def project(value, _method, policy, source):
        assert policy["result_url_resolution"] == "request-url/1"
        return normalize_results(
            {
                "results": [
                    dict(item, url=urljoin(source["url"], item["url"]))
                    for item in value["results"]
                ]
            }
        )

    control = {
        "id": "relative-link",
        "payload": payload,
        "method": method,
        "protocol": protocol,
        "request": request,
        "case": case,
        "error": False,
        "expected_urls": ["https://source.test/answer"],
        "expected_metrics": {"anchor_hit_5": 1},
    }
    controls = tmp_path / "controls.json"
    controls.write_text(json.dumps({"regressions": [{"controls": [control]}]}))
    monkeypatch.setattr(revisions, "NATIVE_CONTROLS", controls)
    revision = {
        "schema": "web-search-assessment-revision/1",
        "provider_calls": 0,
        "root_cause": "Request-relative URL",
        "normalization": "request-url/1",
        "regression_ids": [
            "relative-serp-links",
            "unsafe-serp-links",
            "top-five-boundary",
        ],
        "regressions_sha256": digest(controls.read_bytes()),
    }
    row = {
        "id": "q-a",
        "case_id": "q",
        "method": "a",
        "http_status": 200,
        "error": "Result URL must be absolute",
        "results": [],
        "request": request,
        "elapsed_ms": 123,
        "cost": 7,
        "evidence": {"path": "call.json", "sha256": digest(raw)},
        "metrics": evaluate(case, []),
    }
    profile = {"cases": [case], "methods": [method], "protocol": {}}
    original = copy.deepcopy((profile, row))
    _, updated, changes = apply_report_revision(
        profile, [row], revision, artifacts={"call.json": raw}, normalizer=project
    )
    assert updated[0]["metrics"]["anchor_hit_5"] == 1
    assert updated[0]["error"] == ""
    assert updated[0]["elapsed_ms"] == 123 and updated[0]["cost"] == 7
    assert updated[0]["evidence"] == row["evidence"]
    assert changes[0]["normalization"]["before_count"] == 0
    assert (profile, row) == original
    for artifacts in (None, {}, {"call.json": b"changed"}):
        with pytest.raises(ValueError, match="artifact"):
            apply_report_revision(
                profile, [row], revision, artifacts=artifacts, normalizer=project
            )
    invalid = b'{"results": [{"url": "javascript:invalid"}]}'
    bad_row = copy.deepcopy(row)
    bad_row["evidence"]["sha256"] = digest(invalid)
    _, preserved, _ = apply_report_revision(
        profile,
        [bad_row],
        revision,
        artifacts={"call.json": invalid},
        normalizer=project,
    )
    assert preserved == [bad_row]




def test_sequential_reference_quarantines_preserve_prior_decisions():
    cases = [
        {"id": cid, "query": cid, "reference_answer": "42"} for cid in ("a", "b", "c")
    ]
    profile = {"cases": cases}
    revision = {
        "schema": "web-search-assessment-revision/1",
        "provider_calls": 0,
        "root_cause": "Incorrect references",
        "regression_ids": ["unqualified-reference-rejected"],
    }
    decisions = [
        {
            "case_id": c["id"],
            "query": c["query"],
            "reference_answer": "42",
            "quality_excluded": True,
            "reason": "Contradicted by source",
            "evidence": [{"url": "https://reference.test/", "excerpt": "43"}],
        }
        for c in cases[:2]
    ]
    first, _, _ = apply_report_revision(
        profile, [], {**revision, "reference_adjudications": decisions[:1]}
    )
    second, _, _ = apply_report_revision(
        first, [], {**revision, "reference_adjudications": decisions[1:]}
    )
    assert second["factual_quality_exclusions"] == ["a", "b"]
    assert second["reference_adjudications"] == decisions
    assert [
        c["id"]
        for c in second["cases"]
        if c["id"] not in second["factual_quality_exclusions"]
    ] == ["c"]
    replay, _, _ = apply_report_revision(
        second, [], {**revision, "reference_adjudications": decisions}
    )
    assert replay == second
    assert "factual_quality_exclusions" not in profile
    for invalid in (
        [decisions[0], decisions[0]],
        [{**decisions[0], "reason": "Changed decision"}],
    ):
        with pytest.raises(ValueError, match="Duplicate or conflicting"):
            apply_report_revision(
                second, [], {**revision, "reference_adjudications": invalid}
            )


def revision_instance(root, revision):
    """Bind synthetic correction authority through the public external-instance path."""
    identity = digest(json.dumps(revision, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    authority = root / "assessment-authority.json"
    authority.write_text(json.dumps({"assessment_revisions": {
        "schema": "search-assessment-authority/1", "approved_sha256": [identity],
        "provenance": [{**{k: revision[k] for k in ("root_cause", "regression_ids", "provider_calls")}, "sha256": identity}]}}))
    instance = root / "instance.json"
    instance.write_text(json.dumps({"assessment_authority": {"path": authority.name, "sha256": digest(authority.read_bytes())}}))
    return instance


def test_qualified_answer_derivations_are_scoped_and_preserve_calls():
    from lexbench_websearch.answer_rules import RULE_VERSION
    from lexbench_websearch.scoring import task_acceptance
    from copy import deepcopy
    cases = [{"id": "footnote", "query": "How many?", "reference_answer": "42[7]"},
             {"id": "literal", "query": "First array item?", "reference_answer": "v[0]"}]
    profile = {"cases": cases}
    rows = [{"id": c["id"], "case_id": c["id"], "results": [{"url": "https://source.test/", "snippet": text}],
             "metrics": evaluate(c, [{"url": "https://source.test/", "snippet": text}]),
             "elapsed_ms": 17, "cost": 1} for c, text in zip(cases, ("42", "v[1]"))]
    original = deepcopy((profile, rows))
    item = {"case_id": "footnote", "query": "How many?", "reference": "42[7]", "derived_answer": "42",
            "kind": "terminal-citation-metadata", "qualification": "Independent source confirms 42 without footnote",
            "sources": [{"url": "https://source.test/", "excerpt": "42"}]}
    revision = {"schema": "web-search-assessment-revision/1", "provider_calls": 0,
                "root_cause": "Qualified reference footnote", "answer_match_rule": "published-answers/2",
                "answer_annotation_rule": RULE_VERSION,
                "regression_ids": ["published-answer-citation-metadata", "literal-bracketed-answer"],
                "answer_derivations": [item]}
    revised, updated, _ = apply_report_revision(profile, rows, revision)
    assert task_acceptance(revised["cases"][0], updated[0], "answer") is True
    assert task_acceptance(revised["cases"][1], updated[1], "answer") is False
    for before, after in zip(rows, updated):
        assert all(before[k] == after[k] for k in ("results", "elapsed_ms", "cost"))
    assert (profile, rows) == original
    for change in ({"query": "Other question"}, {"reference": "43[7]"}, {"sources": []}):
        with pytest.raises(ValueError):
            apply_report_revision(profile, rows, {**revision, "answer_derivations": [{**item, **change}]})
    with pytest.raises(ValueError, match="bound scoring revision"):
        apply_report_revision(profile, rows, {**revision, "regression_ids": ["published-answer-citation-metadata"]})

    with pytest.raises(ValueError, match="unsupported annotation rule"):
        apply_report_revision(profile, rows, {**revision, "answer_annotation_rule": "reference-answer-evidence/2", "answer_derivations": []})
