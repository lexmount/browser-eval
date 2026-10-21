import hashlib

import pytest

from harness.web_search.relevance import score, verify_provenance


def test_pooled_preview_relevance_rewards_useful_first_result_and_penalizes_duplicates():
    profile = {"protocol": {"top_k": 5}, "cases": [{"id": "q", "family": "web"}],
               "methods": [{"id": "a"}, {"id": "b"}]}
    good = "https://example.org/answer"
    bad = "https://example.org/off-topic"
    candidate = lambda url: hashlib.sha256(("q\0" + url).encode()).hexdigest()[:12]
    rows = [{"case_id": "q", "method": "a", "results": [{"url": good}, {"url": bad}]},
            {"case_id": "q", "method": "b", "results": [{"url": bad}, {"url": good}, {"url": good}]}]
    labels = {"q": {candidate(good): 3, candidate(bad): 0}}
    result = score(profile, rows, {"cutoff": 5, "case_ids": ["q"]}, labels)
    assert result[0]["ndcg"] == 1
    assert result[0]["precision"] == 0.2
    assert result[1]["ndcg"] < result[0]["ndcg"]
    assert result[1]["precision"] == 0.2
    with pytest.raises(ValueError, match="pooled candidates"):
        score(profile, rows, {"cutoff": 5, "case_ids": ["q"]}, {"q": {candidate(good): 3}})


def test_published_blind_packet_must_match_measured_urls_and_order():
    url = "https://example.org/answer"
    candidate = hashlib.sha256(("q\0" + url).encode()).hexdigest()[:12]
    profile = {"methods": [{"id": "a"}], "cases": [{"id": "q"}]}
    rows = [{"case_id": "q", "method": "a", "results": [{"url": url}]}]
    evidence = {"judge": {"kind": "human", "name": "Test reviewer"},
                "plan": {"scope": "all_custom", "case_ids": ["q"], "cutoff": 5},
                "labels": {"q": {candidate: 3}},
                "blind_candidate_packets": [{"case_id": "q", "candidates": [{"candidate_id": candidate, "url": url}]}],
                "provider_rankings": {"q": {"a": [candidate]}}}
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


def test_full_pool_is_blind_deterministic_and_keeps_empty_queries():
    from harness.web_search.relevance import candidate_packet
    p, rows = all_case_fixture()
    packet, mappings = candidate_packet(p, rows)
    assert packet["plan"]["case_ids"] == ["question", "empty"]
    assert len(packet["blind_candidate_packets"][0]["candidates"]) == 1
    assert packet["blind_candidate_packets"][1]["candidates"] == []
    assert "vendor-one" not in str(packet)
    assert "vendor-two" not in str(packet)
    assert candidate_packet(p, list(reversed(rows)))[0] == packet
    assert set(mappings["question"]) == {"vendor-one", "vendor-two"}
    with pytest.raises(ValueError, match="Incomplete"):
        candidate_packet(p, rows[1:])


def test_complete_human_input_scores_all_zero_and_empty_cases_without_dropping_them():
    from harness.web_search.relevance import candidate_packet, import_annotations
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
    from harness.web_search.relevance import candidate_packet, import_annotations
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
    from harness.web_search.relevance import candidate_packet, JUDGE_PROMPT, parse_judgments, verify_provenance
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
    verify_provenance(profile, rows, evidence)
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
