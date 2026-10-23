"""A release counts complete opinions, binds their outputs and closes findings."""

import json

import pytest
from bs4 import BeautifulSoup

from harness.web_search import review
from harness.web_search.example import example
from harness.web_search.report import build, check


def audited_fixture(tmp_path, source_mode="frozen", packet_bound=False, scope="all"):
    example(tmp_path)
    report = tmp_path / "report"
    root = tmp_path / "review"
    contract = review.freeze(report, root, model="test-model", scope=scope, authorization="Explicit full-review test scenario" if scope == "all" else None, source_mode=source_mode)
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
        text = review._input_text(contract, packet, entry["sha256"])
        attempt = job / "attempt-001"
        attempt.mkdir()
        (attempt / "input.txt").write_text(text)
        (attempt / "result.json").write_bytes(submission.read_bytes())
        schema_path = root / "schema.json"
        schema_receipt = {}
        if packet_bound:
            schema_path = attempt / "schema.json"
            review.save(schema_path, review._packet_schema(review.SCHEMA, packet, entry["sha256"]))
            schema_receipt = {"schema_protocol": "packet-identities/1", "schema_sha256": review.sha(schema_path.read_bytes())}
        review.save(job / "status.json", {"status":"completed","backend":"codex-cli","returncode":0,"attempt":attempt.name,"command":review._review_command("codex", contract["model"], attempt, schema_path.resolve(), (attempt / "result.json").resolve(), source_mode=review._source_mode(contract)),"packet_sha256":entry["sha256"],"contract_sha256":review.sha(review.encoded(contract)),"input_sha256":review.sha(text.encode()),"result_sha256":review.sha(submission.read_bytes()), **schema_receipt})
    evidence = review.collect(root, require_complete=True)
    evidence["adjudications"] = {}
    return report, evidence


def test_scoring_input_check_validates_evidence_without_rechecking_html(tmp_path, monkeypatch):
    _, evidence = audited_fixture(tmp_path)
    path = tmp_path / "review.json"
    review.save(path, evidence)
    out = tmp_path / "released"
    build(out, source=tmp_path / "fixture", review=path)
    def unexpected_html(*args, **kwargs):
        raise AssertionError("Evidence-only scoring must not parse report pages")
    monkeypatch.setattr("harness.web_search.report.BeautifulSoup", unexpected_html)
    assert check(out, evidence_only=True)
    (out / "independent-review.json").write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch|SHA|Hash"):
        check(out, evidence_only=True)


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
    assert coverage['data-review-percent'] == "100"
    assert "100%" in coverage.get_text()
    summary = BeautifulSoup((out / "review.html").read_text(), "html.parser").select_one("[data-independent-review]")
    assert "100%" in summary.get_text()
    if layout == 'comparison-report':
        assert coverage['data-reviewed-queries'] == f"{evidence['case_reviewed']}/{evidence['case_total']}"
        assert coverage['data-reviewed-outputs'] == "4/4"
    else:
        assert "4/4" in coverage.get_text()
    assert soup.select_one('a[href="review.html"]')
    manifest = json.loads((out / "manifest.json").read_bytes())
    assert "review.html" in manifest["routes"]
    assert "Complete result audit" in manifest["profile"]["protocol"]["review"]
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


@pytest.mark.parametrize("defect", ["outputs", "percent-attribute", "percent-text", "summary-percent"])
def test_summary_rejects_tampered_review_scope(tmp_path, defect):
    _, evidence = audited_fixture(tmp_path)
    path = tmp_path / "review.json"
    review.save(path, evidence)
    output = tmp_path / "released"
    content = tmp_path / "content.json"
    content.write_text(json.dumps({"presentation": "comparison-report"}))
    build(output, source=tmp_path / "fixture", review=path, content=content)
    homepage = output / "index.html"
    doc = BeautifulSoup(homepage.read_text(), "html.parser")
    coverage = doc.select_one("[data-review-coverage]")
    if defect == "outputs":
        coverage["data-reviewed-outputs"] = "999/999"
    elif defect == "percent-attribute":
        coverage["data-review-percent"] = "99"
    elif defect == "percent-text":
        coverage.string = coverage.get_text().replace("100%", "99%")
    else:
        summary = output / "review.html"
        summary.write_text(summary.read_text().replace("100%", "99%"))
    homepage.write_text(str(doc))
    with pytest.raises(ValueError, match="coverage|summary"):
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


def test_failure_only_audit_cannot_change_quality_denominator(tmp_path):
    report, evidence = audited_fixture(tmp_path, scope="failures")
    profile, rows = check(report, return_data=True, evidence_only=True)
    evidence["adjudications"]["fact:dataset"] = {"quality_excluded": True}
    with pytest.raises(ValueError, match="Failure-only review"):
        review.validate_public_review(profile, rows, evidence)


@pytest.mark.parametrize("counts,expected", [
    ((2, 2, 4, 4, 1981, 1982), 99),
    ((1, 2, 4, 4, 1982, 1982), 50),
    ((2, 2, 3, 4, 1982, 1982), 75),
    ((2, 2, 4, 4, 0, 0), 100),
    ((0, 0, 0, 0, 0, 0), 0),
])
def test_review_percentage_requires_all_three_populations(counts, expected):
    evidence = dict(zip(("case_reviewed", "case_total", "output_reviewed", "output_total", "candidate_reviewed", "candidate_total"), counts))
    assert review.coverage_percent(evidence) == expected


@pytest.mark.parametrize("defect", ["model", "packet", "input", "output-bytes", "raw", "private-path"])
def test_public_provenance_rejects_altered_execution_and_source_identity(tmp_path, defect):
    report, evidence = audited_fixture(tmp_path)
    profile, rows = check(report, return_data=True)
    proof = evidence["provenance"]
    if defect == "model":
        proof["executions"][0]["verified_command_policy"]["model"] = "wrong-model"
    elif defect == "packet":
        proof["packets"][0]["cases"][0]["outputs"][0]["items"] = []
    elif defect == "input":
        proof["executions"][0]["input_sha256"] = "0" * 64
    elif defect == "output-bytes":
        proof["executions"][0]["output_utf8"] += "\n"
    elif defect == "raw":
        evidence["cases"][0]["output_reviews"][0]["raw_sha256"] = "0" * 64
    else:
        proof["contract"]["source_bindings"][0]["path"] = "/Users/private-home/manifest.json"
        proof["public_contract_sha256"] = review.sha(review.encoded(proof["contract"]))
    with pytest.raises(ValueError):
        review.validate_public_review(profile, rows, evidence)


def test_public_review_keeps_exact_readable_opinion_bytes_without_local_paths(tmp_path):
    report, evidence = audited_fixture(tmp_path)
    profile, rows = check(report, return_data=True)
    assert review.validate_public_review(profile, rows, evidence)
    text = json.dumps(evidence)
    assert str(tmp_path) not in text
    assert '"command"' not in text and '"output_bytes_hex"' not in text
    for execution in evidence["provenance"]["executions"]:
        raw = execution["output_utf8"].encode("utf-8")
        assert review.sha(raw) == execution["result_sha256"]
        assert execution["verified_command_policy"]["web_search"] == "disabled"


@pytest.mark.parametrize("source_mode,web_search", [("frozen", "disabled"), ("live", "live")])
def test_source_modes_bind_complete_execution_and_public_review(tmp_path, source_mode, web_search):
    report, evidence = audited_fixture(tmp_path, source_mode=source_mode)
    contract = review.load_frozen(tmp_path / "review")
    assert contract["source_mode"] == source_mode
    assert contract["prompt_sha256"] == review.sha(review.REVIEW_PROMPTS[source_mode].encode())
    for entry in contract["packets"]:
        assert review._successful(tmp_path / "review", entry, contract)
    assert all(e["verified_command_policy"]["web_search"] == web_search
               for e in evidence["provenance"]["executions"])
    profile, rows = check(report, return_data=True)
    assert review.validate_public_review(profile, rows, evidence)
    review.save(tmp_path / "opinion.json", evidence)
    build(tmp_path / "mode-report", source=tmp_path / "fixture", review=tmp_path / "opinion.json")
    assert check(tmp_path / "mode-report")


@pytest.mark.parametrize("source_mode", ["frozen", "live"])
def test_known_historical_prompt_hash_selects_mode_without_rewriting_inputs(tmp_path, source_mode):
    report, _ = audited_fixture(tmp_path, source_mode=source_mode)
    root = tmp_path / "review"
    contract = json.loads((root / "contract.json").read_bytes())
    del contract["source_mode"]
    review.save(root / "contract.json", contract)
    original_inputs = {}
    for entry in contract["packets"]:
        job = root / "jobs" / entry["id"]
        state = json.loads((job / "status.json").read_bytes())
        state["contract_sha256"] = review.sha(review.encoded(contract))
        review.save(job / "status.json", state)
        original_inputs[entry["id"]] = (job / state["attempt"] / "input.txt").read_bytes()
    assert review._source_mode(review.load_frozen(root)) == source_mode
    evidence = review.collect(root, require_complete=True)
    evidence["adjudications"] = {}
    profile, rows = check(report, return_data=True)
    assert review.validate_public_review(profile, rows, evidence)
    assert all((root / "jobs" / entry["id"] / "attempt-001/input.txt").read_bytes()
               == original_inputs[entry["id"]] for entry in contract["packets"])


@pytest.mark.parametrize("source_mode", ["frozen", "live"])
def test_source_mode_rejects_opposite_search_policy_and_unknown_prompt(tmp_path, source_mode):
    _, _ = audited_fixture(tmp_path, source_mode=source_mode)
    root = tmp_path / "review"
    contract = review.load_frozen(root)
    entry = contract["packets"][0]
    state_path = root / "jobs" / entry["id"] / "status.json"
    state = json.loads(state_path.read_bytes())
    state["command"][state["command"].index("-c") + 1] = (
        'web_search="live"' if source_mode == "frozen" else 'web_search="disabled"'
    )
    review.save(state_path, state)
    assert review._successful(root, entry, contract) is None
    with pytest.raises(ValueError, match="incomplete"):
        review.collect(root, require_complete=True)
    contract["prompt_sha256"] = "0" * 64
    review.save(root / "contract.json", contract)
    with pytest.raises(ValueError, match="Unknown|inconsistent"):
        review.load_frozen(root)


def test_new_freeze_defaults_to_frozen_sources_and_rejects_unknown_mode(tmp_path):
    example(tmp_path)
    contract = review.freeze(tmp_path / "report", tmp_path / "new-review", model="test-model")
    assert contract["source_mode"] == "frozen"
    assert review._source_mode(contract) == "frozen"
    assert review.PROMPT == review.FROZEN_PROMPT
    with pytest.raises(ValueError, match="source mode"):
        review.freeze(tmp_path / "report", tmp_path / "invalid-review", model="test-model", source_mode="unknown")


@pytest.mark.parametrize("source_mode", ["frozen", "live"])
def test_packet_bound_schema_public_proof_recomputes_from_frozen_inputs(tmp_path, source_mode):
    report, evidence = audited_fixture(tmp_path, source_mode=source_mode, packet_bound=True)
    profile, rows = check(report, return_data=True)
    assert review.validate_public_review(profile, rows, evidence)
    execution = evidence["provenance"]["executions"][0]
    assert execution["schema_protocol"] == "packet-identities/1"
    execution["schema_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="schema"):
        review.validate_public_review(profile, rows, evidence)


def test_packet_bound_schema_unknown_public_protocol_is_rejected(tmp_path):
    report, evidence = audited_fixture(tmp_path, packet_bound=True)
    profile, rows = check(report, return_data=True)
    evidence["provenance"]["executions"][0]["schema_protocol"] = "packet-identities/unknown"
    with pytest.raises(ValueError, match="schema protocol"):
        review.validate_public_review(profile, rows, evidence)
