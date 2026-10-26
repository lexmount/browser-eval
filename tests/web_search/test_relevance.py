import hashlib

import pytest

from harness.web_search.relevance import verify_provenance
from lexbench_websearch.relevance import SOURCE_RULE_REGRESSIONS
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


def test_historical_source_packet_requires_its_exact_registered_evidence(tmp_path):
    import copy
    import hashlib
    import json
    from harness.web_search import relevance

    profile, rows = all_case_fixture()
    packet, rankings = relevance.candidate_packet(profile, rows, {}, packet_format="serp/1")
    calls, labels = [], {}
    for item in packet["blind_candidate_packets"]:
        judgments = [{"candidate_id": c["candidate_id"], "grade": 3,
                      "reason": "Direct result", "evidence": {"field": "snippet", "preview_index": 0}}
                     for c in item["candidates"]]
        calls.append({"case_id": item["case_id"],
                      "request": {"model": "test-model", "temperature": 0,
                                  "messages": [{"role": "system", "content": relevance.JUDGE_PROMPT},
                                               {"role": "user", "content": json.dumps(relevance.judge_input(item), ensure_ascii=False, sort_keys=True)}]},
                      "response": {"model": "test-model", "choices": [{"message": {"content": json.dumps({"judgments": judgments})}}]}})
        labels[item["case_id"]] = {j["candidate_id"]: j["grade"] for j in judgments}
    evidence = {**packet, "provider_rankings": rankings, "labels": labels,
                "judge": {"kind": "model", "name": "test-model", "prompt": relevance.JUDGE_PROMPT, "calls": calls}}
    registry = tmp_path / "legacy.json"
    registry.write_text('{"model": []}')
    with pytest.raises(ValueError, match="hash-pinned"):
        relevance.verify_provenance(profile, rows, evidence, authority=json.loads(registry.read_bytes()))
    identity = hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    registry.write_text(json.dumps({"model": [identity]}))
    relevance.verify_provenance(profile, rows, evidence, authority=json.loads(registry.read_bytes()))
    changed = copy.deepcopy(evidence)
    changed["judge"]["calls"][0]["response"]["model"] = "different-model"
    with pytest.raises(ValueError, match="hash-pinned"):
        relevance.verify_provenance(profile, rows, changed, authority=json.loads(registry.read_bytes()))


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
    judgment["evidence"] = {"field": "title", "preview_index": 0}
    assert parse_judgments(packet, response(judgment))[0]["evidence"] == "Title"
    judgment["evidence"].pop("preview_index")
    candidate["previews"].append({"title": "Another title", "snippet": "Another preview"})
    with pytest.raises(ValueError, match="does not identify"):
        parse_judgments(packet, response(judgment))


def test_only_explicit_site_cases_pass_domain_constraints_to_judge():
    from harness.web_search.relevance import candidate_packet
    profile, rows = all_case_fixture()
    profile["cases"][0]["domains"] = ["reference.example"]
    packet, _ = candidate_packet(profile, rows, packet_format="serp/1")
    assert packet["blind_candidate_packets"][0]["required_domains"] == []
    profile["cases"][0]["family"] = "site"
    packet, _ = candidate_packet(profile, rows, packet_format="serp/1")
    assert packet["blind_candidate_packets"][0]["required_domains"] == ["reference.example"]


def test_explicit_source_rules_accept_official_alternatives_and_hide_urls():
    import json
    from harness.web_search.relevance import candidate_packet, judge_input, parse_judgments
    profile, rows = all_case_fixture()
    profile["cases"][0]["domains"] = ["diagnostic.example"]
    rows[0]["results"][0]["url"] = "https://github.com/official/guide"
    rules = {"question": {"domains": ["official.example"], "url_prefixes": ["https://github.com/official/"]}}
    packet, _ = candidate_packet(profile, rows, rules)
    p = packet["blind_candidate_packets"][0]
    assert {c["url"]: c["source_eligible"] for c in p["candidates"]} == {
        "https://github.com/official/guide": True, "https://example.org/guide": False}
    supplied = judge_input(p)
    assert all("url" not in c and "source_eligible" in c for c in supplied["candidates"])
    response = {"choices": [{"message": {"content": json.dumps({"judgments": [
        {"candidate_id": c["candidate_id"], "grade": 3, "reason": "Useful", "evidence": {"field": "title", "preview_index": 0}}
        for c in p["candidates"]]})}}]}
    judgments = parse_judgments(p, response)
    assert [x["grade"] for x in judgments] == [3 if c["source_eligible"] else 0 for c in p["candidates"]]
    assert all(x["model_grade"] == 3 for x in judgments)
    profile["cases"][0]["family"] = "site"
    strict, _ = candidate_packet(profile, rows, rules)
    assert all(not c["source_eligible"] for c in strict["blind_candidate_packets"][0]["candidates"])
    profile["cases"][0].update(family="vertical", source="native")
    native, _ = candidate_packet(profile, rows, {}, packet_format="serp/1")
    assert all(not c["source_eligible"] for c in native["blind_candidate_packets"][0]["candidates"])
    profile["cases"][0].pop("source")
    profile["cases"][0]["family"] = "web"
    ordinary, _ = candidate_packet(profile, rows, {})
    assert all(c["source_eligible"] for c in ordinary["blind_candidate_packets"][0]["candidates"])


def test_source_rule_uses_url_evidence_even_when_unused_preview_is_empty():
    import json
    from harness.web_search.relevance import parse_judgments
    candidate = {"candidate_id": "x", "url": "https://unofficial.example/", "source_eligible": False,
                 "previews": [{"title": "A title", "snippet": ""}]}
    packet = {"candidates": [candidate]}
    response = {"choices": [{"message": {"content": json.dumps({"judgments": [
        {"candidate_id": "x", "grade": 2, "reason": "Partial", "evidence": {"field": "title", "preview_index": 0}}]})}}]}
    item = parse_judgments(packet, response)[0]
    assert item["grade"] == 0 and item["model_grade"] == 2
    assert item["evidence"] == candidate["url"]
    candidate["source_eligible"] = True
    response["choices"][0]["message"]["content"] = response["choices"][0]["message"]["content"].replace('"field": "title"', '"field": "snippet"')
    with pytest.raises(ValueError, match="exact quote"):
        parse_judgments(packet, response)


def test_changed_frozen_rubric_requires_exact_zero_call_revision():
    import copy
    import json
    from harness.web_search.relevance import verify_protocol
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    profile = {"protocol": {"model_relevance": {"name": "fixed-model", "temperature": 0,
               "max_tokens": 8192, "enable_thinking": False, "prompt_sha256": sha("old rubric")}}}
    evidence = {"judge": {"kind": "model", "name": "fixed-model", "prompt": "corrected rubric",
                 "configuration": {"temperature": 0, "max_tokens": 8192, "extra_params": {"enable_thinking": False}}},
                "plan": {"case_ids": ["all-cases"]}, "packet_sha256": "bound-packet",
                "source_requirements": {}, "source_policy": "explicit-source/2"}
    with pytest.raises(ValueError, match="requires a zero-call"):
        verify_protocol(profile, evidence)
    revision = {"provider_calls": 0, "root_cause": "Explicit source requirement was ignored",
                "regression_ids": ["source-positive", "source-negative"],
                "before_prompt_sha256": sha("old rubric"), "prompt_sha256": sha("corrected rubric"),
                "packet_sha256": "bound-packet", "model_case_ids": ["all-cases"],
                "source_policy": "explicit-source/2", "source_requirements_sha256": sha(json.dumps({}, ensure_ascii=False, indent=2))}
    verify_protocol(profile, evidence, revision)
    for key, value in (("provider_calls", 1), ("packet_sha256", "other-packet"), ("model_case_ids", [])):
        bad = copy.deepcopy(revision)
        bad[key] = value
        with pytest.raises(ValueError):
            verify_protocol(profile, evidence, bad)


def source_correction_fixture(packet_format="search/1"):
    import copy
    import json
    from harness.web_search import relevance
    from tests.web_search.model_fixture import strict_model_evidence
    profile = {"protocol": {"top_k": 5, "model_relevance": {
        "name": "test-model", "temperature": 0, "max_tokens": 32,
        "enable_thinking": None, "prompt_sha256": hashlib.sha256(relevance.JUDGE_PROMPT.encode()).hexdigest()}},
        "methods": [{"id": "provider"}], "cases": [
            {"id": "official", "query": "Find official documentation", "family": "web"},
            {"id": "unchanged", "query": "Find the manual", "family": "web"},
            {"id": "empty", "query": "Find missing content", "family": "web"}]}
    rows = [{"case_id": c["id"], "method": "provider", "results": [] if c["id"] == "empty" else [
        {"url": "https://community.test/guide", "snippet": "Complete installation guide"}]} for c in profile["cases"]]

    def make(requirements):
        packet, rankings = relevance.candidate_packet(profile, rows, requirements, packet_format=packet_format)
        calls, labels = [], {}
        for p in packet["blind_candidate_packets"]:
            judgments = [{"candidate_id": c["candidate_id"], "grade": 3 if c["source_eligible"] else 0,
                          "reason": "Useful preview if source qualifies", "evidence": {"field": "snippet", "preview_index": 0}}
                         for c in p["candidates"]]
            calls.append({"case_id": p["case_id"], "response": {"model": "test-model", "choices": [
                {"message": {"content": json.dumps({"judgments": judgments})}}]}})
            labels[p["case_id"]] = {j["candidate_id"]: j["grade"] for j in judgments}
        evidence = strict_model_evidence({**packet, "source_requirements": requirements, "provider_rankings": rankings,
            "labels": labels, "judge": {"kind": "model", "name": "test-model", "prompt": relevance.JUDGE_PROMPT, "calls": calls}})
        evidence["judge"]["configuration"]["packet_format"] = packet_format
        return evidence

    before = make({})
    after = make({"official": {"domains": ["official.test"]},
                  "unchanged": {"domains": ["community.test"]}})
    identity = hashlib.sha256(json.dumps(before, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    after["judge"]["configuration"].update(receipt_schema=3, predecessor_sha256=identity)
    after["judge"].update(predecessor=before, inherited_case_ids=["unchanged", "empty"])
    for i in (1, 2):
        after["judge"]["calls"][i] = copy.deepcopy(before["judge"]["calls"][i])
    correction = {"schema": "web-search-source-rule-correction/1", "root_cause": "Explicit source constraint omitted",
                  "provider_calls": 0, "regression_ids": list(SOURCE_RULE_REGRESSIONS),
                  "before_relevance_sha256": identity, "changed_case_ids": ["official"], "new_judge_case_ids": ["official"]}
    return profile, rows, after, {"source_rule_correction": correction}


def test_source_correction_reuses_unchanged_judge_inputs_and_scores_source_changes():
    from harness.web_search import relevance
    profile, rows, evidence, revision = source_correction_fixture()
    relevance.verify_provenance(profile, rows, evidence)
    relevance.verify_protocol(profile, evidence, revision)
    scored = relevance.score(profile, rows, evidence["plan"], evidence["labels"])
    assert [s["success"] for s in scored] == [0, 1, 0]
    # Registering a source requirement with the same eligibility flag did not
    # consume another judge call, and an empty pool retained its original call.
    assert evidence["judge"]["calls"][1:] == evidence["judge"]["predecessor"]["judge"]["calls"][1:]


@pytest.mark.parametrize("defect", ["schema", "cause", "calls", "regressions", "predecessor", "changed", "new", "unnecessary-call", "old-response"])
def test_source_correction_rejects_unbound_or_unnecessary_judge_work(defect):
    from harness.web_search import relevance
    profile, _, evidence, revision = source_correction_fixture()
    correction = revision["source_rule_correction"]
    if defect == "schema":
        correction["schema"] = "unknown"
    elif defect == "cause":
        correction["root_cause"] = ""
    elif defect == "calls":
        correction["provider_calls"] = 1
    elif defect == "regressions":
        correction["regression_ids"] = ["source-positive", "source-negative"]
    elif defect == "predecessor":
        correction["before_relevance_sha256"] = "0" * 64
    elif defect == "changed":
        correction["changed_case_ids"].append("unchanged")
    elif defect == "new":
        correction["new_judge_case_ids"] = []
    elif defect == "unnecessary-call":
        evidence["judge"]["inherited_case_ids"].remove("unchanged")
        correction["new_judge_case_ids"].append("unchanged")
    else:
        import json
        evidence["judge"]["predecessor"]["judge"]["calls"][0]["response"]["model"] = "other"
        identity = hashlib.sha256(json.dumps(evidence["judge"]["predecessor"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        evidence["judge"]["configuration"]["predecessor_sha256"] = identity
        correction["before_relevance_sha256"] = identity
    with pytest.raises(ValueError):
        relevance.verify_protocol(profile, evidence, revision)


def test_source_rule_revision_executes_negative_controls_through_production_pool(monkeypatch):
    from harness.web_search import relevance
    original = relevance.candidate_packet

    def incorrectly_accept_all(*args, **kwargs):
        packet, rankings = original(*args, **kwargs)
        for p in packet["blind_candidate_packets"]:
            for candidate in p["candidates"]:
                candidate["source_eligible"] = True
        return packet, rankings

    from lexbench_websearch import relevance as scoring_relevance
    monkeypatch.setattr(scoring_relevance, "candidate_packet", incorrectly_accept_all)
    with pytest.raises(ValueError, match="regression failed"):
        scoring_relevance.validate_source_rule_regressions(list(SOURCE_RULE_REGRESSIONS))


def repaired_inheritance_fixture():
    import base64
    import copy
    import json
    from harness.web_search import relevance
    profile, rows, evidence, revision = source_correction_fixture()
    predecessor = evidence["judge"]["predecessor"]
    packet = predecessor["blind_candidate_packets"][1]
    call = predecessor["judge"]["calls"][1]
    base_request = copy.deepcopy(call["request"])
    prior = copy.deepcopy(call)
    payload = json.loads(prior["response"]["choices"][0]["message"]["content"])
    payload["judgments"][0]["grade"] = 4
    prior["response"]["choices"][0]["message"]["content"] = json.dumps(payload)
    raw = json.dumps(prior["response"]).encode()
    prior["attempts"][0].update(response_bytes_base64=base64.b64encode(raw).decode(), response_sha256=hashlib.sha256(raw).hexdigest())
    call["format_repair"] = {"prior_call": prior}
    call["request"]["messages"] = relevance.reference_repair_messages(packet, base_request["messages"])
    call["attempts"][0]["request_sha256"] = hashlib.sha256(json.dumps(call["request"], sort_keys=True).encode()).hexdigest()
    evidence["judge"]["calls"][1] = copy.deepcopy(call)
    identity = hashlib.sha256(json.dumps(predecessor, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    evidence["judge"]["configuration"]["predecessor_sha256"] = identity
    revision["source_rule_correction"]["before_relevance_sha256"] = identity
    return profile, rows, evidence, revision, packet, call, base_request


def test_valid_format_repair_is_inherited_without_another_judge_call():
    from harness.web_search import relevance
    profile, rows, evidence, revision, packet, call, request = repaired_inheritance_fixture()
    relevance.verify_provenance(profile, rows, evidence)
    relevance.verify_protocol(profile, evidence, revision)
    assert call["request"] != request
    assert relevance.reusable_judge_call(packet, call, request)


@pytest.mark.parametrize("change", ["prompt", "input", "model", "max_tokens", "extra_params", "repair", "receipt"])
def test_format_repair_never_reuses_changed_inputs_or_invalid_proof(change):
    import copy
    from harness.web_search import relevance
    _, _, evidence, _, packet, call, request = repaired_inheritance_fixture()
    request = copy.deepcopy(request)
    if change in ("prompt", "input"):
        request["messages"][0 if change == "prompt" else 1]["content"] += " changed"
    elif change in ("model", "max_tokens", "extra_params"):
        request[change] = "changed"
    elif change == "repair":
        call["request"]["messages"][0]["content"] += " wrong suffix"
        with pytest.raises(ValueError, match="Format repair changed"):
            relevance.reusable_judge_call(packet, call, request)
        return
    else:
        evidence["judge"]["predecessor"]["judge"]["calls"][1]["attempts"][0]["request_sha256"] = "0" * 64
        with pytest.raises(ValueError):
            relevance.validate_model_evidence(evidence)
        return
    assert not relevance.reusable_judge_call(packet, call, request)


def test_inherited_native_packet_retains_platform_gate_when_source_rules_change():
    from harness.web_search import relevance
    profile = {"protocol": {"top_k": 5}, "methods": [{"id": "provider"}], "cases": [
        {"id": "native", "family": "web", "query": "Find papers", "source": "native", "domains": ["papers.test"]},
        {"id": "official", "family": "web", "query": "Find official docs"}]}
    rows = [{"case_id": c["id"], "method": "provider", "results": [{"url": "https://community.test/guide", "snippet": "Useful content"}]} for c in profile["cases"]]
    before, _ = relevance.candidate_packet(profile, rows, {}, packet_format="serp/1")
    assert relevance.inherited_packet_format(profile, rows, before) == "serp/1"
    after, _ = relevance.candidate_packet(profile, rows, {"official": {"domains": ["official.test"]}},
        packet_format=relevance.inherited_packet_format(profile, rows, before), source_policy=before["source_policy"])
    old = {p["case_id"]: p["candidates"][0]["source_eligible"] for p in before["blind_candidate_packets"]}
    new = {p["case_id"]: p["candidates"][0]["source_eligible"] for p in after["blind_candidate_packets"]}
    assert old == {"native": False, "official": True}
    assert new == {"native": False, "official": False}
    before["packet_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="frozen search outputs"):
        relevance.inherited_packet_format(profile, rows, before)


def test_strict_serp_inheritance_requires_explicit_packet_format_binding():
    from harness.web_search import relevance
    profile, rows, evidence, revision = source_correction_fixture(packet_format="serp/1")
    relevance.verify_provenance(profile, rows, evidence)
    relevance.verify_protocol(profile, evidence, revision)
    del evidence["judge"]["configuration"]["packet_format"]
    with pytest.raises(ValueError, match="packet hash"):
        relevance.validate_model_evidence(evidence)




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
    from harness.web_search.revisions import validate_regressions

    validate_regressions(
        {
            "root_cause": "Diagnostic domains incorrectly excluded official alternatives",
            "regression_ids": [
                "official-source-alternative-domain",
                "diagnostic-domain-versus-required-site",
            ],
        }
    )
