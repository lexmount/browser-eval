import hashlib

import pytest

from harness.web_search.relevance import verify_provenance
from lexbench_websearch.relevance import score




def test_published_blind_packet_must_match_measured_urls_and_order():
    url = "https://example.org/answer"
    candidate = hashlib.sha256(("q\0" + url).encode()).hexdigest()[:12]
    profile = {"protocol": {"top_k": 5}, "methods": [{"id": "a"}], "cases": [{"id": "q", "query": "Find answer"}]}
    rows = [{"case_id": "q", "method": "a", "results": [{"url": url}]}]
    evidence = {"judge": {"kind": "human", "name": "Test reviewer"},
                "plan": {"scope": "all_custom", "case_ids": ["q"], "cutoff": 5},
                "labels": {"q": {candidate: 3}},
                "blind_candidate_packets": [{"case_id": "q", "candidates": [{"candidate_id": candidate, "url": url}]}],
                "provider_rankings": {"q": {"a": [candidate]}}}
    from lexbench_websearch.relevance import candidate_packet
    evidence["blind_candidate_packets"] = candidate_packet(profile, rows)[0]["blind_candidate_packets"]
    verify_provenance(profile, rows, evidence)
    evidence["provider_rankings"]["q"]["a"] = []
    with pytest.raises(ValueError, match="rank mapping"):
        verify_provenance(profile, rows, evidence)


def all_case_fixture():
    profile = {"protocol": {"top_k": 5}, "cases": [
        {"id": "question", "query": "Find the installation guide", "family": "web"},
        {"id": "empty", "query": "Find unavailable information", "family": "web"},
        {"id": "public", "query": "Who?", "family": "web", "reference_answer": "Ada"}],
        "methods": [{"id": "vendor-one"}, {"id": "vendor-two"}]}
    rows = [{"case_id": c["id"], "method": m["id"], "results": (
        [{"url": "https://example.org/guide", "title": "Installation", "snippet": "Steps"}]
        if c["id"] == "question" else [])}
        for c in profile["cases"] for m in profile["methods"]]
    return profile, rows




def test_complete_human_input_scores_all_zero_and_empty_cases_without_dropping_them():
    from harness.web_search.relevance import import_annotations
    from lexbench_websearch.relevance import candidate_packet
    p, rows = all_case_fixture()
    packet, _ = candidate_packet(p, rows)
    submission = {"packet_sha256": packet["packet_sha256"],
                  "reviewed_case_ids": packet["plan"]["case_ids"],
                  "judge": {"kind": "human", "name": "Test reviewer"},
                  "labels": {c["case_id"]: {x["candidate_id"]: 0 for x in c["candidates"]}
                             for c in packet["blind_candidate_packets"]}}
    evidence = import_annotations(p, rows, submission)
    scores = score(p, rows, evidence["plan"], evidence["labels"])
    assert len(scores) == 4
    assert all(s["ndcg"] == s["precision"] == 0 for s in scores)
    submission["reviewed_case_ids"] = ["question"]
    with pytest.raises(ValueError, match="Every case"):
        import_annotations(p, rows, submission)
    submission["reviewed_case_ids"] = packet["plan"]["case_ids"]
    submission["judge"] = {"kind": "model", "name": "fake"}
    with pytest.raises(ValueError, match="complete frozen prompts and calls"):
        import_annotations(p, rows, submission)


def test_candidate_changes_reject_old_annotations():
    from harness.web_search.relevance import import_annotations
    from lexbench_websearch.relevance import candidate_packet
    p, rows = all_case_fixture()
    packet, _ = candidate_packet(p, rows)
    rows[0]["results"][0]["snippet"] = "Changed preview"
    with pytest.raises(ValueError, match="does not match"):
        import_annotations(p, rows, {"packet_sha256": packet["packet_sha256"]})


def test_annotation_export_escapes_source_markup_and_never_prelabels(tmp_path):
    import json
    from bs4 import BeautifulSoup
    from harness.web_search.relevance import export_annotations
    p, rows = all_case_fixture()
    p["cases"][0]["query"] = '</script><img src=x onerror="alert(1)">'
    page = export_annotations(p, rows, tmp_path / "annotation")
    soup = BeautifulSoup(page.read_text(), "html.parser")
    assert not soup.find("img")
    payload = json.loads(soup.select_one("#packet").string)
    assert "labels" not in payload
    assert payload["blind_candidate_packets"][0]["query"] == p["cases"][0]["query"]


def test_frozen_model_labels_require_exact_candidate_coverage_and_quotes():
    from lexbench_websearch.rubric import JUDGE_PROMPT
    from harness.web_search.relevance import parse_judgments, verify_provenance
    from lexbench_websearch.relevance import candidate_packet
    import json
    import copy
    profile, rows = all_case_fixture()
    packet, rankings = candidate_packet(profile, rows)
    calls, labels = [], {}
    for p in packet["blind_candidate_packets"]:
        items = [{"candidate_id": c["candidate_id"], "grade": 3, "reason": "Directly useful", "evidence": c["url"]} for c in p["candidates"]]
        response = {"model": "test-model", "choices": [{"message": {"content": json.dumps({"judgments": items})}}]}
        request = {"model": "test-model", "temperature": 0, "messages": [{"role": "system", "content": JUDGE_PROMPT}, {"role": "user", "content": json.dumps(p, ensure_ascii=False, sort_keys=True)}]}
        calls.append({"case_id": p["case_id"], "request": request, "response": response})
        labels[p["case_id"]] = {x["candidate_id"]: x["grade"] for x in items}
    evidence = {**packet, "provider_rankings": rankings, "labels": labels, "judge": {"kind": "model", "name": "test-model", "prompt": JUDGE_PROMPT, "calls": calls}}
    from tests.web_search.model_fixture import strict_model_evidence
    evidence = strict_model_evidence(evidence)
    verify_provenance(profile, rows, evidence)
    inconsistent = copy.deepcopy(evidence)
    inconsistent["judge"]["configuration"] = {"prompt": JUDGE_PROMPT, "model": "test-model", "packet_sha256": packet["packet_sha256"], "extra_params": {"enable_thinking": False}}
    with pytest.raises(ValueError, match="configuration"):
        verify_provenance(profile, rows, inconsistent)
    from harness.web_search.comparison import comparison_data
    display_profile = dict(profile, methods=[dict(m, label=m["id"]) for m in profile["methods"]])
    data = comparison_data(display_profile, rows, relevance=evidence, detail_link=lambda r: "result.html")
    graded = [item for case in data["cases"] for output in case["outputs"].values() for item in output["items"]]
    assert graded and all(item["grade"] == 3 and item["grade_reason"] == "Directly useful" and item["grade_evidence"] for item in graded)
    scored = score(profile, rows, evidence["plan"], labels)
    assert any(x["success"] == 1 for x in scored)
    assert any(x["success"] == 0 for x in scored)
    bad = copy.deepcopy(evidence)
    case = next(c for c in packet["blind_candidate_packets"] if c["candidates"])
    bad["labels"][case["case_id"]][case["candidates"][0]["candidate_id"]] = 0
    with pytest.raises(ValueError, match="disagree"):
        verify_provenance(profile, rows, bad)
    response = {"choices": [{"message": {"content": json.dumps({"judgments": [{"candidate_id": c["candidate_id"], "grade": 3, "reason": "reason", "evidence": "invented supporting quote"} for c in case["candidates"]]})}}]}
    with pytest.raises(ValueError, match="exact quote"):
        parse_judgments(case, response)




def test_model_evidence_reference_displays_exact_original_not_generated_quote():
    import json
    from harness.web_search.relevance import parse_judgments
    candidate = {"candidate_id": "x", "url": "https://example.org/", "previews": [{"title": "Title", "snippet": "原文（标点和空格不改）"}]}
    packet = {"candidates": [candidate]}
    judgment = {"candidate_id": "x", "grade": 2, "reason": "partial", "evidence": {"field": "snippet", "preview_index": 0}}
    def response(item):
        return {"choices": [{"message": {"content": json.dumps({"judgments": [item]})}}]}
    assert parse_judgments(packet, response(judgment))[0]["evidence"] == candidate["previews"][0]["snippet"]
    judgment["evidence"]["preview_index"] = 1
    with pytest.raises(ValueError, match="does not identify"):
        parse_judgments(packet, response(judgment))


def test_rank_grades_query_score_and_dataset_average_are_one_chain():
    from lexbench_websearch.relevance import query_metrics, summary
    # A deliberately poor order: the grade-3 document is second, grade-2 third.
    metrics = query_metrics(['irrelevant', 'answer', 'partial'], {'irrelevant': 0, 'answer': 3, 'partial': 2})
    assert metrics['contributions'] == pytest.approx([0, 4.416508275, 1.5])
    assert metrics['idcg'] == pytest.approx(8.892789261)
    assert metrics['ndcg'] == pytest.approx(0.665315246)
    assert metrics['useful'] == 2 and metrics['precision'] == .4 and metrics['success'] == 1
    empty = query_metrics([], {})
    result = summary([dict(metrics, method='a'), dict(empty, method='a')], ['a'])['a']
    assert result['n'] == 2 and result['ndcg'] == pytest.approx(metrics['ndcg'] / 2)
    assert result['precision'] == .2 and result['success'] == .5


def test_explicit_source_rules_are_uniform_and_model_cannot_override_them():
    import json
    from harness.web_search.relevance import judge_input, parse_judgments
    from lexbench_websearch.relevance import candidate_packet
    p, rows = all_case_fixture()
    p['cases'][0]['domains'] = ['example.org']
    for row in rows:
        if row['case_id'] == 'question':
            row['results'] += [{'url': 'https://example.org.attacker.test/guide', 'title': 'Same title', 'snippet': 'Same relevant content'}]
    requirements = {'question': {'domains': ['example.org'], 'sources': ['https://example.org/']}}
    packet, _ = candidate_packet(p, rows, requirements)
    question = packet['blind_candidate_packets'][0]
    view = judge_input(question)
    assert 'url' not in str(view) and 'example.org' not in json.dumps(view)
    assert sorted(c['source_eligible'] for c in view['candidates']) == [False, True]
    response = {'choices': [{'message': {'content': json.dumps({'judgments': [
        {'candidate_id': c['candidate_id'], 'grade': 3, 'reason': 'Directly useful', 'evidence': {'field': 'snippet', 'preview_index': 0}}
        for c in question['candidates']]})}}]}
    labels = {j['candidate_id']: j for j in parse_judgments(question, response)}
    for c in question['candidates']:
        judged = labels[c['candidate_id']]
        assert judged['grade'] == (3 if c['source_eligible'] else 0)
        assert judged['model_grade'] == 3
        if not c['source_eligible']:
            assert judged['grade_source'] == 'source-rule' and judged['evidence'] == c['url']


def test_empty_selected_field_can_evidence_absence_but_not_positive_relevance():
    import json
    from harness.web_search.relevance import parse_judgments
    packet = {'candidates':[{'candidate_id':'x','url':'https://example.org/', 'previews':[{'title':'Unclear result','snippet':''}]}]}
    def response(grade):
        return {'choices':[{'message':{'content':json.dumps({'judgments':[{'candidate_id':'x','grade':grade,'reason':'No useful information in the preview','evidence':{'field':'snippet','preview_index':0}}]})}}]}
    assert parse_judgments(packet,response(0))[0]['evidence_empty'] is True
    with pytest.raises(ValueError,match='exact quote'):
        parse_judgments(packet,response(3))


def test_official_repository_is_not_blocked_by_a_diagnostic_target_domain():
    from lexbench_websearch.relevance import candidate_packet

    profile, rows = all_case_fixture()
    profile["cases"][0].update(family="web", domains=["official.example"])
    for row in rows:
        if row["case_id"] == "question":
            row["results"] = [
                dict(
                    url="https://github.com/official/spec/README.md",
                    title="Official spec",
                    snippet="Protocol definition",
                ),
                dict(
                    url="https://github.com/unrelated/spec/README.md",
                    title="Copied spec",
                    snippet="Protocol definition",
                ),
            ]
    requirement = {
        "question": {
            "domains": ["official.example"],
            "url_prefixes": ["https://github.com/official/"],
        }
    }
    packet, _ = candidate_packet(profile, rows, requirement)
    candidates = {
        c["url"]: c["source_eligible"] for c in packet["blind_candidate_packets"][0]["candidates"]
    }
    assert candidates["https://github.com/official/spec/README.md"] is True
    assert candidates["https://github.com/unrelated/spec/README.md"] is False
    # Old judgments retain the original policy and remain verifiable.
    old, _ = candidate_packet(
        profile, rows, requirement, source_policy="legacy-domain-intersection/1"
    )
    assert all(not c["source_eligible"] for c in old["blind_candidate_packets"][0]["candidates"])


def test_web_diagnostic_domain_does_not_become_an_unrequested_source_constraint():
    from lexbench_websearch.relevance import candidate_packet

    profile, rows = all_case_fixture()
    profile["cases"][0].update(family="web", domains=["unrequested.example"])
    packet, _ = candidate_packet(profile, rows, {})
    assert all(c["source_eligible"] for c in packet["blind_candidate_packets"][0]["candidates"])
    profile["cases"][0]["family"] = "site"
    packet, _ = candidate_packet(profile, rows, {})
    assert all(not c["source_eligible"] for c in packet["blind_candidate_packets"][0]["candidates"])


def test_source_correction_regressions_execute_through_current_packet_owner():
    from lexbench_websearch.revisions import validate_regressions

    validate_regressions(
        {
            "root_cause": "Diagnostic domains incorrectly excluded official alternatives",
            "regression_ids": [
                "official-source-alternative-domain",
                "diagnostic-domain-versus-required-site",
            ],
        }
    )
