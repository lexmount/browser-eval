"""Equivalent pages count as hits; superficially similar URLs do not."""

from lexbench_websearch.inputs import definition_path
import json
import unittest

from lexbench_websearch.scoring import normalize_results as normalize
from lexbench_websearch.scoring import classify, evaluate


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

    registry = json.loads(definition_path("scoring-regressions.json").read_text())
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
