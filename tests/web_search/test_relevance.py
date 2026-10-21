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
    profile = {"methods": [{"id": "a"}]}
    rows = [{"case_id": "q", "method": "a", "results": [{"url": url}]}]
    evidence = {"plan": {"case_ids": ["q"], "cutoff": 5},
                "labels": {"q": {candidate: 3}},
                "blind_candidate_packets": [{"case_id": "q", "candidates": [{"candidate_id": candidate, "url": url}]}],
                "provider_rankings": {"q": {"a": [candidate]}}}
    verify_provenance(profile, rows, evidence)
    evidence["provider_rankings"]["q"]["a"] = []
    with pytest.raises(ValueError, match="rank mapping"):
        verify_provenance(profile, rows, evidence)
