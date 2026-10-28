import hashlib
import pytest
from lexbench_websearch.relevance import score


def test_pooled_preview_relevance_rewards_useful_first_result_and_penalizes_duplicates():
    profile = {
        "protocol": {"top_k": 5},
        "cases": [{"id": "q", "family": "web"}],
        "methods": [{"id": "a"}, {"id": "b"}],
    }
    good = "https://example.org/answer"
    bad = "https://example.org/off-topic"
    candidate = lambda url: hashlib.sha256(("q\0" + url).encode()).hexdigest()[:12]
    rows = [
        {"case_id": "q", "method": "a", "results": [{"url": good}, {"url": bad}]},
        {
            "case_id": "q",
            "method": "b",
            "results": [{"url": bad}, {"url": good}, {"url": good}],
        },
    ]
    labels = {"q": {candidate(good): 3, candidate(bad): 0}}
    result = score(profile, rows, {"cutoff": 5, "case_ids": ["q"]}, labels)
    assert result[0]["ndcg"] == 1
    assert result[0]["precision"] == 0.2
    assert result[1]["ndcg"] < result[0]["ndcg"]
    assert result[1]["precision"] == 0.2
    with pytest.raises(ValueError, match="pooled candidates"):
        score(
            profile, rows, {"cutoff": 5, "case_ids": ["q"]}, {"q": {candidate(good): 3}}
        )


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('streaming', [False, True])
def test_relevance_rejects_conflicting_rankings_for_one_method_case(reverse, streaming):
    from lexbench_websearch.scoring import candidate_identity
    profile = {'protocol': {'top_k': 2}, 'cases': [{'id': 'q', 'family': 'web'}],
               'methods': [{'id': 'a'}]}
    urls = ['https://example.org/relevant', 'https://example.org/off-topic']
    first = {'case_id': 'q', 'method': 'a', 'results': [{'url': url} for url in urls]}
    second = {**first, 'results': list(reversed(first['results']))}
    labels = {'q': {candidate_identity('q', url): grade for url, grade in zip(urls, [3, 0])}}
    plan = {'cutoff': 2, 'case_ids': ['q']}
    assert len(score(profile, iter([first]) if streaming else [first], plan, labels)) == 1
    rows = [second, first] if reverse else [first, second]
    with pytest.raises(ValueError, match='one final result'):
        score(profile, iter(rows) if streaming else rows, plan, labels)


def all_case_fixture():
    profile = {
        "protocol": {"top_k": 5},
        "cases": [
            {"id": "question", "query": "Find the installation guide", "family": "web"},
            {"id": "empty", "query": "Find unavailable information", "family": "web"},
            {
                "id": "public",
                "query": "Who?",
                "family": "web",
                "reference_answer": "Ada",
            },
        ],
        "methods": [{"id": "vendor-one"}, {"id": "vendor-two"}],
    }
    rows = [
        {
            "case_id": c["id"],
            "method": m["id"],
            "results": (
                [
                    {
                        "url": "https://example.org/guide",
                        "title": "Installation",
                        "snippet": "Steps",
                    }
                ]
                if c["id"] == "question"
                else []
            ),
        }
        for c in profile["cases"]
        for m in profile["methods"]
    ]
    return profile, rows


def test_full_pool_is_blind_deterministic_and_keeps_empty_queries():
    from lexbench_websearch.relevance import candidate_packet

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
