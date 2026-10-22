"""A release counts complete opinions, binds their outputs and closes findings."""

import json

import pytest
from bs4 import BeautifulSoup

from harness.web_search import review
from harness.web_search.example import example
from harness.web_search.report import build, check


def audited_fixture(tmp_path, scope="all"):
    example(tmp_path)
    report = tmp_path / "report"
    root = tmp_path / "review"
    contract = review.freeze(report, root, model="test-model", scope=scope, authorization="Explicit full-review test scenario" if scope == "all" else None)
    for entry in contract["packets"]:
        packet = json.loads((root / entry["path"]).read_bytes())
        result = {"packet_sha256": entry["sha256"], "cases": []}
        for case in packet["cases"]:
            result["cases"].append(
                {
                    "case_id": case["case"]["id"],
                    "dataset_review": {
                        "status": "accept",
                        "reason": "Synthetic definition is coherent.",
                        "source_checks": [],
                    },
                    "candidate_grades": [
                        dict(
                            candidate_id=c["candidate_id"],
                            grade=3,
                            reason="Direct fixture match.",
                            evidence="Fixture only",
                        )
                        for c in case["candidates"]
                    ],
                    "output_reviews": [
                        dict(
                            output_id=o["output_id"],
                            status="confirmed",
                            reason="Inspected retained response.",
                            evidence=f"{len(o['items'])} returned items.",
                        )
                        for o in case["outputs"]
                    ],
                    "findings": [],
                }
            )
        submission = root / (entry["id"] + ".json")
        review.save(submission, result)
        job = root / "jobs" / entry["id"]
        job.mkdir(parents=True)
        (job / "result.json").write_bytes(submission.read_bytes())
        text = review.PROMPT + json.dumps({"packet_sha256":entry["sha256"], **packet},ensure_ascii=False)
        review.save(job / "status.json", {"status":"completed","backend":"codex-cli","returncode":0,"packet_sha256":entry["sha256"],"contract_sha256":review.sha(review.encoded(contract)),"input_sha256":review.sha(text.encode()),"result_sha256":review.sha(submission.read_bytes())})
    evidence = review.collect(root, require_complete=True)
    evidence["adjudications"] = {}
    return report, evidence


@pytest.mark.parametrize("layout", [None, "comparison-report"])
def test_review_release_counts_failures_and_retains_opinions(tmp_path, layout):
    _, evidence = audited_fixture(tmp_path)
    path = tmp_path / "review.json"
    review.save(path, evidence)
    out = tmp_path / "released"
    content = tmp_path / "content.json"
    content.write_text(json.dumps({"presentation": layout}))
    build(out, source=tmp_path / "fixture", review=path, content=content)
    assert check(out)
    soup = BeautifulSoup((out / "index.html").read_text(), "html.parser")
    coverage = soup.select_one("[data-review-coverage]")
    assert coverage is not None
    if layout == 'comparison-report':
        assert coverage['data-reviewed-queries'] == f"{evidence['case_reviewed']}/{evidence['case_total']}"
        assert coverage['data-reviewed-outputs'] == "4/4"
    else:
        assert "4/4" in coverage.get_text()
    assert soup.select_one('a[href="review.html"]')
    manifest = json.loads((out / "manifest.json").read_bytes())
    assert "review.html" in manifest["routes"]
    assert "Complete independent Codex" in manifest["profile"]["protocol"]["review"]
    assert (
        manifest["collection_profile"]["protocol"]["review"]
        != manifest["profile"]["protocol"]["review"]
    )
    assert all(
        "data-independent-opinion" in (out / route).read_text()
        for route in manifest["routes"]
        if route.startswith("details/")
    )
    (out / "independent-review.json").write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        check(out)


@pytest.mark.parametrize(
    "defect", ["counts", "missing-output", "raw-identity", "open-count", "open-finding", "stale-decision"]
)
def test_partial_or_unsupported_review_never_enters_release(tmp_path, defect):
    report, evidence = audited_fixture(tmp_path)
    profile, rows = check(report, return_data=True)
    if defect == "open-count":
        evidence["open_issue_count"] = 1
    elif defect == "counts":
        evidence["output_reviewed"] += 1
    elif defect == "missing-output":
        evidence["cases"][0]["output_reviews"].pop()
    elif defect == "raw-identity":
        evidence["cases"][0]["output_reviews"][0]["raw_sha256"] = "0" * 64
    else:
        evidence["cases"][0]["findings"].append(
            dict(
                kind="reference-error",
                reason="Wrong entity",
                evidence="Independent source",
                affected_output_ids=[],
            )
        )
        if defect == "stale-decision":
            key = next(iter(review.issue_inventory(evidence)))
            evidence["adjudications"][key] = dict(
                opinion_sha256="0" * 64, resolution="fixed", reason="Changed", evidence="Regression"
            )
    with pytest.raises(ValueError):
        review.validate_public_review(profile, rows, evidence)


def test_review_opinion_cannot_be_removed_or_changed_in_rendered_details(tmp_path):
    _, evidence = audited_fixture(tmp_path)
    path = tmp_path / "review.json"
    review.save(path, evidence)
    out = tmp_path / "released"
    build(out, source=tmp_path / "fixture", review=path)
    detail = next((out / "details").glob("*.html"))
    soup = BeautifulSoup(detail.read_text(), "html.parser")
    soup.select_one("[data-independent-opinion]").decompose()
    detail.write_text(str(soup))
    with pytest.raises(ValueError, match="review detail"):
        check(out)


def test_current_review_metadata_must_agree_with_bound_evidence(tmp_path):
    _, evidence = audited_fixture(tmp_path)
    path = tmp_path / "review.json"
    review.save(path, evidence)
    out = tmp_path / "released"
    build(out, source=tmp_path / "fixture", review=path)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["profile"]["protocol"]["review"] = "No independent review"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="metadata contradicts"):
        check(out)


def test_reference_exclusion_cannot_target_a_provider_output(tmp_path):
    report, evidence = audited_fixture(tmp_path)
    profile, rows = check(report, return_data=True)
    case = evidence["cases"][0]
    case["dataset_review"].update(status="issue", reason="Ambiguous reference")
    # Rebind the fixture opinion exactly as a completed independent submission.
    run = next(r for r in evidence["review_runs"] if case["case_id"] in r["case_ids"])
    original_cases = [{**c, "output_reviews": [{k: v for k, v in o.items() if k not in ("row_id", "method", "raw_sha256")} for o in c["output_reviews"]]} for c in evidence["cases"] if c["case_id"] in run["case_ids"]]
    run["opinion_sha256"] = review.sha(review.encoded({"packet_sha256": run["packet_sha256"], "cases": original_cases}))
    key = case["case_id"] + ":dataset"
    evidence["adjudications"][key] = {"opinion_sha256": review.sha(review.encoded(case["dataset_review"])), "resolution": "quarantined", "quality_excluded": True, "reason": "Defect", "evidence": "Source"}
    with pytest.raises(ValueError, match="factual reference defect|receipt differs"):
        review.validate_public_review(profile, rows, evidence)


def test_anonymous_opinion_cannot_be_reassigned_to_another_method(tmp_path):
    _, evidence = audited_fixture(tmp_path)
    profile, rows = check(tmp_path / "report", return_data=True)
    opinions = evidence["cases"][0]["output_reviews"]
    first, second = opinions[:2]
    for key in ("row_id", "method", "raw_sha256"):
        first[key], second[key] = second[key], first[key]
    with pytest.raises(ValueError, match="identity|receipt|opinion"):
        review.validate_public_review(profile, rows, evidence)


def test_summary_rejects_tampered_review_scope(tmp_path):
    _, evidence = audited_fixture(tmp_path)
    path = tmp_path / "review.json"
    review.save(path, evidence)
    output = tmp_path / "released"
    content = tmp_path / "content.json"
    content.write_text(json.dumps({"presentation": "comparison-report"}))
    build(output, source=tmp_path / "fixture", review=path, content=content)
    homepage = output / "index.html"
    doc = BeautifulSoup(homepage.read_text(), "html.parser")
    doc.select_one("[data-review-coverage]")["data-reviewed-outputs"] = "999/999"
    homepage.write_text(str(doc))
    with pytest.raises(ValueError, match="coverage"):
        check(output)


def test_failure_only_review_can_build_and_reports_accepted_unreviewed_passes(tmp_path):
    _, evidence = audited_fixture(tmp_path, scope="failures")
    path = tmp_path / "review.json"
    review.save(path, evidence)
    out = tmp_path / "released"
    build(out, source=tmp_path / "fixture", review=path)
    assert check(out)
    manifest = json.loads((out / "manifest.json").read_bytes())
    assert manifest["report_assessment"]["independent_review_scope"] == "failures"
    assert evidence["accepted_unreviewed_passes"] > 0
    assert evidence["output_reviewed"] < evidence["output_total"]
