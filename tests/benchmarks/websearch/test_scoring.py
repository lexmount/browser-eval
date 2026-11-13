"""Equivalent pages count as hits; superficially similar URLs do not."""

from lexbench_websearch.inputs import definition_path
import json
import unittest

from lexbench_websearch.scoring import normalize_results as normalize
from lexbench_websearch.scoring import classify, evaluate


def test_generic_url_equivalence_is_versioned_and_preserves_document_boundaries():
    from lexbench_websearch.scoring import canonical_url, factual_evidence
    from harness.web_search.revisions import validate_revision_controls

    validate_revision_controls({'root_cause': 'Literal host matching rejected web transport and Wikimedia aliases',
                                'regression_ids': ['generic-document-urls']})
    target = 'https://example.org/article'
    assert canonical_url('http://www.example.org/article', policy='document-url/2') == target
    assert canonical_url('https://m.example.org/article', policy='document-url/2') == target
    assert canonical_url('http://www.example.org/article') != target
    case = {'reference_answer': 'answer', 'reference_urls': [target],
            'equivalent_reference_urls': ['https://mirror.test/article'],
            'url_equivalence': 'document-url/2'}
    results = [{'url': 'https://unrelated.test/answer'}, {'url': 'https://mirror.test/article'}]
    metrics = factual_evidence(case, results)
    assert metrics['labeled_source_hit_5'] == 1
    assert metrics['labeled_source_rr_5'] == .5
    assert factual_evidence(case, results[:1])['labeled_source_hit_5'] == 0


class IdentityScenarios(unittest.TestCase):
    def test_verified_equivalent_url_hits(self):
        case = {
            "family": "web",
            "anchors": ["https://docs.test/page.html"],
            "identity_aliases": {
                "https://docs.test/page": "https://docs.test/page.html"
            },
        }
        results = normalize({"results": [{"url": "https://docs.test/page"}]})
        metrics = evaluate(case, results)
        row = dict(results=results, metrics=metrics, http_status=200, error="")
        self.assertEqual(classify(case, row), ("pass", "target_hit"))
        for url in ("https://docs.test/page?id=other", "https://www.docs.test/page"):
            with self.subTest(url=url):
                changed = normalize({"results": [{"url": url}]})
                row.update(results=changed, metrics=evaluate(case, changed))
                self.assertEqual(classify(case, row), ("fail", "target_miss"))


def test_correction_provenance_executes_paired_controls_and_rejects_unknown_ids():
    from lexbench_websearch.qualification import validate_regressions

    registry = json.loads(definition_path("scoring-regressions.json").read_text(encoding="utf-8"))
    validate_regressions({"root_cause": "fixture", "regression_ids": list(registry)})
    import pytest

    with pytest.raises(ValueError, match="Unknown"):
        validate_regressions(
            {"root_cause": "fixture", "regression_ids": ["invented-control"]}
        )
    with pytest.raises(ValueError, match="unique"):
        validate_regressions(
            {"root_cause": "fixture", "regression_ids": [next(iter(registry))] * 2}
        )
