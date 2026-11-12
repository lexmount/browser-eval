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
    with pytest.raises(ValueError, match='Factual source identities require'):
        apply_report_revision(profile, rows, dict(revision, regression_ids=['equivalence-canonical']))
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


@pytest.mark.parametrize('adapter,payload', [
    ('serper', {'organic': [{'link': 'https://user:secret@unsafe.test/'}, {'link': 'https://good.test/fact'}]}),
    ('brightdata', {'organic': [{'link': 'honmaple:////broken'}, {'link': 'https://good.test/fact'}]}),
    ('browserbase', {'results': [{'url': 'javascript:bad'}, {'url': 'https://good.test/fact'}]}),
    ('tencent-wsa', {'Response': {'Version': 'standard', 'Pages': [json.dumps({'url': 'javascript:bad'}), json.dumps({'url': 'https://good.test/fact'})]}}),
])
def test_invalid_native_slot_does_not_erase_other_results_or_promote_ranks(adapter, payload):
    from harness.web_search.core import normalize_response

    protocol = dict(top_k=5, normalize_top_k=True, result_url_resolution='request-url/2')
    results = normalize_response(payload, {'adapter': adapter}, protocol)
    assert len(results) == 2
    assert results[0]['invalid_slot'] is True
    assert results[1]['url'] == 'https://good.test/fact'
    metrics = evaluate({'anchors': ['https://good.test/fact']}, results)
    assert metrics['reciprocal_rank'] == 0.5
    assert metrics['anchor_hit_5'] == 1


def test_dead_slots_keep_query_and_factual_rank_without_fake_candidates():
    from lexbench_websearch.relevance import candidate_packet, score
    from harness.web_search.core import normalize_response

    case = dict(id='query', family='web', query='Find official fact', anchors=['https://good.test/fact'])
    profile = dict(cases=[case], methods=[dict(id='a')], protocol=dict(top_k=5))
    results = normalize_response({'results': [{'url': 'https://user:pass@bad.test/'},
                                            {'url': 'https://good.test/fact'}]},
                                 {'adapter': 'lexmount'}, dict(top_k=5, normalize_top_k=True,
                                                              result_url_resolution='request-url/2'))
    rows = [dict(case_id='query', method='a', results=results)]
    packet, ranking = candidate_packet(profile, rows)
    assert len(packet['blind_candidate_packets'][0]['candidates']) == 1
    assert ranking['query']['a'][0] is None
    cid = ranking['query']['a'][1]
    scored = score(profile, rows, packet['plan'], {'query': {cid: 3}})[0]
    assert scored['ranked_grades'] == [0, 3]
    assert scored['precision'] == 0.2
    factual = evaluate(dict(reference_answer='42', reference_urls=['https://good.test/fact']), results)
    assert factual['labeled_source_rr_5'] == 0.5


def test_delivered_preview_and_contamination_corrections_have_paired_controls():
    from harness.web_search.core import normalize_response
    from harness.web_search.revisions import validate_revision_controls

    revision = dict(schema='web-search-assessment-revision/1', provider_calls=0,
                    root_cause='Isolate native previews and exclude structured evaluation rows',
                    regression_ids=['delivered-preview-isolation', 'structured-dataset-contamination',
                                    'invalid-result-slots', 'relative-serp-links', 'unsafe-serp-links', 'top-five-boundary'],
                    normalization='request-url/2', regressions_sha256=digest(NATIVE_CONTROLS.read_bytes()),
                    relevance_input='delivered-previews/1',
                    factual_contamination_rule='question-with-structured-dataset-rows/4')
    validate_revision_controls(revision, normalize_response)
    profile = dict(protocol={},cases=[dict(id='f',query='When?',reference_answer='1971')])
    corrected, _, _ = apply_report_revision(profile, [], revision, normalizer=normalize_response)
    assert corrected['protocol']['relevance_input'] == 'delivered-previews/1'
    assert corrected['cases'][0]['factual_contamination_rule'].endswith('/4')


@pytest.mark.parametrize('pages', [['not-json', json.dumps({'url': 'https://good.test/'})],
                                   [None, json.dumps({'url': 'https://good.test/'})]])
def test_unparseable_page_keeps_the_other_native_slot(pages):
    from harness.web_search.core import normalize_response

    results = normalize_response({'Response': {'Version': 'lite', 'Pages': pages}},
                                 {'adapter': 'tencent-wsa'}, dict(top_k=5, normalize_top_k=True,
                                 result_url_resolution='request-url/2'))
    assert results[0]['invalid_slot'] and results[1]['url'] == 'https://good.test/'


def test_identified_native_no_results_is_empty_not_a_parse_error():
    from harness.web_search.core import normalize_response

    payload = dict(Response=dict(Query='no results', Version='lite', RequestId='fixture'))
    results = normalize_response(payload, dict(adapter='tencent-wsa', expected_response_version='lite'),
                                 dict(top_k=5, normalize_top_k=True, result_url_resolution='request-url/2'))
    assert results == []


def test_preview_grades_never_borrow_text_or_duplicate_the_same_document_in_ideal_ranking():
    from lexbench_websearch.relevance import candidate_packet, score

    case = dict(id='q', family='web', query='Find the install command')
    profile = dict(cases=[case], methods=[dict(id='a'), dict(id='b')],
                   protocol=dict(top_k=5, relevance_input='delivered-previews/1'))
    rows = [dict(case_id='q',method='a',results=[dict(url='https://docs.test/',title='Docs',snippet='')]),
            dict(case_id='q',method='b',results=[dict(url='https://docs.test/',title='Docs',snippet='pip install example'),
                                               dict(url='https://docs.test/',title='Docs',snippet='Duplicate full instructions')])]
    packet, _ = candidate_packet(profile, rows)
    labels = {'q': {c['candidate_id']: (1 if not c['previews'][0]['snippet'] else 3)
                    for c in packet['blind_candidate_packets'][0]['candidates']}}
    scored = {r['method']:r for r in score(profile,rows,packet['plan'],labels)}
    assert scored['a']['ranked_grades'] == [1]
    assert scored['b']['ranked_grades'] == [3,0]
    assert scored['b']['duplicates'] == [False,True]
    assert scored['b']['ideal_grades'] == [3]
    assert scored['b']['ndcg'] == 1


def test_paired_query_intervals_distinguish_observed_order_from_supported_difference():
    from lexbench_websearch.relevance import paired_bootstrap

    rows = [dict(case_id=str(i),method=m,ndcg=v)for i in range(10)
            for m,v in [('a',0.5),('b',0.5)]]
    intervals = paired_bootstrap(rows,['a','b'],samples=100)
    assert intervals['methods']['b']['difference_to_best'] == [0,0]
    separated = [dict(r,ndcg=0 if r['method']=='b' else 1) for r in rows]
    assert paired_bootstrap(separated,['a','b'],samples=100)['methods']['b']['difference_to_best'] == [-1,-1]
    with pytest.raises(ValueError,match='complete paired'):
        paired_bootstrap(rows[:-1],['a','b'],samples=100)


def test_invalid_slot_never_supplies_reference_or_lexical_evidence():
    result = dict(url='https://invalid-result-slot.invalid/1', invalid_slot=True, snippet='42')
    case = dict(anchors=[result['url']], reference_urls=[result['url']], reference_answer='42')
    metrics = evaluate(case,[result])
    assert metrics['anchor_hit_5'] == 0
    assert metrics['labeled_source_hit_5'] == 0
    assert metrics['answer_in_preview_5'] == 0


def test_invalid_only_outputs_do_not_count_as_available_in_aggregates():
    from harness.web_search.core import aggregate

    bad = [dict(url='https://invalid-result-slot.invalid/1', invalid_slot=True)]
    good = [dict(url='https://origin.test/fact')]
    rows = [dict(method='a',http_status=200,error='',results=results,cost=0,elapsed_ms=10,
                 metrics=evaluate({},results)) for results in (bad,bad+good)]
    totals = aggregate(rows)['a']
    assert totals['n'] == 2 and totals['nonempty'] == 1
    assert totals['all_latency']['n'] == 2 and totals['success_latency']['n'] == 1


def test_report_build_binds_and_replays_the_selected_factual_rule(tmp_path):
    from harness.record_io import bytes_sha256
    from harness.web_search.example import example
    from harness.web_search.report import build, check
    from lexbench_websearch.evidence_quality import DATASET_ROWS_RULE

    example(tmp_path, comparison=True)
    source = tmp_path / 'fixture'
    profile = json.loads((source / 'profile.json').read_bytes())
    case = next(c for c in profile['cases'] if 'reference_answer' in c)
    rows = [json.loads(line) for line in (source / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
    for row in rows:
        if row['case_id'] != case['id'] or row['http_status'] != 200:
            continue
        payload = {'results': [dict(url='https://corpus.test/data',
                                   snippet='Dataset Viewer ' + case['query'] + ' ' + case['reference_answer'])]}
        path = source / row['evidence']['path']
        path.write_text(json.dumps(payload), encoding='utf-8')
        row['evidence']['sha256'] = bytes_sha256(path.read_bytes())
        row['results'] = normalize_results(payload)
        row['metrics'] = evaluate(case, row['results'])
    result_file = source / 'results.jsonl'
    result_file.write_text(''.join(json.dumps(row)+'\n' for row in rows), encoding='utf-8')
    receipt = json.loads((source / 'receipt.json').read_bytes())
    receipt['results_sha256'] = bytes_sha256(result_file.read_bytes())
    (source / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    revision = dict(schema='web-search-assessment-revision/1',provider_calls=0,
                    root_cause='Structured evaluation rows must not supply factual evidence',
                    regression_ids=['structured-dataset-contamination'],factual_contamination_rule=DATASET_ROWS_RULE)
    instance = revision_instance(tmp_path,revision)
    content = tmp_path / 'content.json'
    content.write_text(json.dumps({'assessment_revision': revision}), encoding='utf-8')
    output = tmp_path / 'corrected'
    build(output,source=source,content=content,profile_path=instance)
    assert check(output)
    sensitivity = json.loads((output / 'factual-sensitivity.json').read_bytes())
    assert sensitivity['rule'] == DATASET_ROWS_RULE
    matched = [r for r in sensitivity['results'].values() if r['excluded_ranks']]
    assert matched and all(r['metrics']['answer_in_preview_5'] == 0 for r in matched)
