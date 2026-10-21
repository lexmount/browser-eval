"""Human annotations must grade the same frozen text that the report displays."""

import copy

import pytest

from harness.web_search.relevance import candidate_packet, verify_provenance


@pytest.mark.parametrize("field", ["query", "title", "snippet"])
def test_human_labels_reject_changed_query_or_candidate_text(field):
    profile = {
        "protocol": {"top_k": 5},
        "methods": [{"id": "a"}],
        "cases": [{"id": "q", "query": "Install package", "family": "web"}],
    }
    rows = [
        {
            "case_id": "q",
            "method": "a",
            "results": [
                {
                    "url": "https://example.org/install",
                    "title": "Install",
                    "snippet": "pip install package",
                }
            ],
        }
    ]
    packet, mappings = candidate_packet(profile, rows)
    candidate = packet["blind_candidate_packets"][0]["candidates"][0]["candidate_id"]
    evidence = {
        **packet,
        "provider_rankings": mappings,
        "labels": {"q": {candidate: 3}},
        "judge": {"kind": "human", "name": "Named reviewer"},
    }
    verify_provenance(profile, rows, evidence)
    tampered = copy.deepcopy(evidence)
    question = tampered["blind_candidate_packets"][0]
    if field == "query":
        question[field] = "Different task"
    else:
        question["candidates"][0]["previews"][0][field] = "Invented information"
    with pytest.raises(ValueError, match="Human candidate text differs"):
        verify_provenance(profile, rows, tampered)
