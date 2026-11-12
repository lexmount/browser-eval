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
    controls.write_text(json.dumps({"regressions": [{"controls": [control]}]}), encoding="utf-8")
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
        "provenance": [{**{k: revision[k] for k in ("root_cause", "regression_ids", "provider_calls")}, "sha256": identity}]}}), encoding="utf-8")
    instance = root / "instance.json"
    instance.write_text(json.dumps({"assessment_authority": {"path": authority.name, "sha256": digest(authority.read_bytes())}}), encoding="utf-8")
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
             "elapsed_ms": 17, "cost": 1} for c, text in zip(cases, ("42", "v[1]"), strict=True)]
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
    for before, after in zip(rows, updated, strict=True):
        assert all(before[k] == after[k] for k in ("results", "elapsed_ms", "cost"))
    assert (profile, rows) == original
    for change in ({"query": "Other question"}, {"reference": "43[7]"}, {"sources": []}):
        with pytest.raises(ValueError):
            apply_report_revision(profile, rows, {**revision, "answer_derivations": [{**item, **change}]})
    with pytest.raises(ValueError, match="bound scoring revision"):
        apply_report_revision(profile, rows, {**revision, "regression_ids": ["published-answer-citation-metadata"]})

    with pytest.raises(ValueError, match="unsupported annotation rule"):
        apply_report_revision(profile, rows, {**revision, "answer_annotation_rule": "reference-answer-evidence/2", "answer_derivations": []})


def test_verified_factual_aliases_rescore_every_method_without_changing_calls():
    from harness.web_search.revisions import factual_source_identity
    from lexbench_websearch.scoring import task_acceptance

    target, alias = 'https://reference.test/fact', 'https://reference.test/fact?click=17'
    case = dict(id='fact', query='Which fact?', reference_answer='42', reference_urls=[target])
    profile = {'cases': [case]}
    results = [{'url': 'https://other.test/unrelated'}, {'url': alias}]
    rows = [dict(id=method, case_id='fact', results=results, elapsed_ms=17,
                 metrics=evaluate(case, results)) for method in ('a', 'b')]
    identity = dict(case_id='fact', query='Which fact?', target=target, alias=alias,
                    observation=dict(url=alias, final=target, status=403, verified_at='2026-11-12',
                                     chain=[dict(url=alias, status=301, location=target)]))
    revision = dict(schema='web-search-assessment-revision/1', provider_calls=0,
                    root_cause='Reference URL aliases omitted equivalent document identities',
                    regression_ids=['equivalence-canonical', 'verified-factual-source-identities'],
                    factual_source_identities=[identity])
    before = copy.deepcopy((profile, rows))
    revised, updated, changes = apply_report_revision(profile, rows, revision)
    assert revised['cases'][0]['reference_urls'] == [target, alias]
    assert len(changes) == 2
    for row in updated:
        assert row['metrics']['labeled_source_rr_5'] == 0.5
        assert task_acceptance(revised['cases'][0], row, metric='source') is True
        assert row['results'] == results and row['elapsed_ms'] == 17
    assert (profile, rows) == before
    for key, value in [('query', 'Different question'), ('target', target + '/other')]:
        changed = copy.deepcopy(revision)
        changed['factual_source_identities'][0][key] = value
        with pytest.raises(ValueError, match='Unqualified factual'):
            apply_report_revision(profile, rows, changed)
    canonical = copy.deepcopy(identity)
    canonical['observation'].update(status=200, final=alias, chain=[], canonical=target,
                                     canonical_evidence=['<link rel="canonical" href="' + target + '">'])
    assert factual_source_identity(case, canonical)
    canonical['observation']['canonical_evidence'] = ['<link rel="canonical" href="https://reference.test/other">']
    assert not factual_source_identity(case, canonical)
    mirror = copy.deepcopy(identity)
    mirror['alias'] = 'https://mirror.test/fact'
    mirror['observation'].update(url=mirror['alias'], final=mirror['alias'], chain=[], status=200,
                                 canonical=target, canonical_evidence=['<link rel="canonical" href="' + target + '">'])
    assert not factual_source_identity(case, mirror)


def test_reference_identity_capture_and_offline_replay():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from harness.web_search.revisions import capture_source_identity, factual_source_identity

    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/redirect':
                self.send_response(301)
                self.send_header('Location', '/reference')
                self.end_headers()
            elif self.path == '/tracking':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(b'<html><head><link rel="canonical" href="/reference"></head></html>')
            else:
                self.send_response(403)
                self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Origin)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        observations = [capture_source_identity(base + path) for path in ('/redirect', '/tracking')]
        malformed = capture_source_identity(base + '/invalid space')
        assert 'error' in malformed
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    # The reference host is offline: only frozen metadata enters scoring.
    case = dict(id='fact', query='Which fact?', reference_answer='42', reference_urls=[base + '/reference'])
    assert observations[0]['status'] == 403
    assert observations[1]['canonical_evidence']
    for observed in observations:
        identity = dict(case_id='fact', query=case['query'], alias=observed['url'],
                        target=case['reference_urls'][0], observation=observed)
        assert factual_source_identity(case, identity)
        identity['target'] += '/different'
        assert not factual_source_identity(case, identity)
