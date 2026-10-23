import copy
import unittest
from lexbench_websearch.scoring import normalize_results as normalize
from lexbench_websearch.scoring import evaluate
from harness.web_search.revisions import apply_report_revision


class AssessmentRevisionScenarios(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "cases": [
                {"id": "known", "anchors": ["https://docs.test/old"], "family": "web"},
                {"id": "new", "family": "web"},
                {
                    "id": "fact",
                    "family": "web",
                    "reference_answer": "150 (acceptable range: anything between 148 and 152)",
                    "reference_urls": ["https://docs.test/fact"],
                },
            ]
        }
        self.rows = []
        for c in self.profile["cases"]:
            results = normalize(
                {
                    "results": [
                        {
                            "url": "https://docs.test/" + c["id"],
                            "title": "There are 150 engravings.",
                        }
                    ]
                }
            )
            self.rows.append(
                {
                    "id": c["id"],
                    "case_id": c["id"],
                    "results": results,
                    "metrics": evaluate(c, results),
                    "elapsed_ms": 123,
                    "cost": 0.005,
                    "evidence": {"path": "original"},
                }
            )
        self.revision = {
            "schema": "web-search-assessment-revision/1",
            "root_cause": "Missing independent source labels and annotation text was matched as answer text",
            "regression_ids": [
                "annotations-preserve-calls",
                "published-answer-range-boundaries",
            ],
            "provider_calls": 0,
            "answer_match_rule": "published-answers/2",
            "cases": [
                {
                    "case_id": "new",
                    "anchors": ["https://docs.test/new"],
                    "qualification": "Independent official manual covers the named feature",
                    "sources": [
                        {
                            "url": "https://docs.test/new",
                            "title": "Feature manual",
                            "excerpt": "The named feature works this way.",
                            "verified_at": "2026-10-21",
                        }
                    ],
                }
            ],
        }

    def test_annotations_and_ranges_rescore_without_rewriting_calls(self):
        before = copy.deepcopy(self.rows)
        profile, rows, changes = apply_report_revision(
            self.profile, self.rows, self.revision
        )
        self.assertEqual(self.rows, before)
        self.assertNotIn("anchors", self.profile["cases"][1])
        self.assertEqual(rows[1]["metrics"]["anchor_hit_5"], 1)
        self.assertEqual(rows[2]["metrics"]["answer_in_preview_5"], 1)
        for old, new in zip(before, rows):
            self.assertEqual(
                {k: v for k, v in old.items() if k != "metrics"},
                {k: v for k, v in new.items() if k != "metrics"},
            )
        self.assertEqual(len(changes), 2)

    def test_unqualified_or_replaced_reference_rejected(self):
        for mutate in [
            lambda r: r["cases"][0].update(anchors=["https://unknown.test/"]),
            lambda r: r["cases"][0].update(case_id="known"),
            lambda r: r.update(provider_calls=1),
            lambda r: r["cases"].append(r["cases"][0]),
        ]:
            r = copy.deepcopy(self.revision)
            mutate(r)
            with self.assertRaises(ValueError):
                apply_report_revision(self.profile, self.rows, r)

    def test_frozen_profile_keeps_original_literal_scoring(self):
        case = self.profile["cases"][2]
        results = normalize(
            {"results": [{"url": "https://docs.test/fact", "title": "150 engravings"}]}
        )
        self.assertEqual(evaluate(case, results)["answer_in_preview_5"], 0)
        revised, _, _ = apply_report_revision(self.profile, self.rows, self.revision)
        self.assertEqual(
            evaluate(revised["cases"][2], results)["answer_in_preview_5"], 1
        )
        self.assertEqual(evaluate(case, results)["answer_in_preview_5"], 0)

    def test_revised_rules_reject_wrong_units_and_out_of_range(self):
        profile = copy.deepcopy(self.profile)
        profile["cases"][2]["reference_answer"] = (
            "390 metres (acceptable range: anything between 386 and 394 metres)"
        )
        revised, _, _ = apply_report_revision(profile, self.rows, self.revision)
        case = revised["cases"][2]
        for text, expected in [
            ("390 metres", 1),
            ("386 metres", 1),
            ("395 metres", 0),
            ("390 feet", 0),
            ("1390 metres", 0),
            ("-390 metres", 0),
        ]:
            with self.subTest(text=text):
                results = normalize(
                    {"results": [{"url": "https://docs.test/fact", "title": text}]}
                )
                self.assertEqual(
                    evaluate(case, results)["answer_in_preview_5"], expected
                )

    def test_reference_answer_cannot_bridge_different_fields_or_results(self):
        profile = copy.deepcopy(self.profile)
        profile["cases"][2]["reference_answer"] = (
            "1 year, 161 days (acceptable range: 1 year, anything between 160 and 162 days)"
        )
        revised, _, _ = apply_report_revision(profile, self.rows, self.revision)
        case = revised["cases"][2]
        for payload in [
            [{"url": "https://docs.test/a", "title": "1 year", "snippet": "161 days"}],
            [
                {"url": "https://docs.test/a", "title": "1 year"},
                {"url": "https://docs.test/b", "title": "161 days"},
            ],
            [{"url": "https://docs.test/a", "title": "11 year, 161 days"}],
        ]:
            self.assertEqual(
                evaluate(case, normalize({"results": payload}))["answer_in_preview_5"],
                0,
            )
        valid = normalize(
            {
                "results": [
                    {
                        "url": "https://docs.test/a",
                        "summary": "It lasted 1 year, 161 days.",
                    }
                ]
            }
        )
        metrics = evaluate(case, valid)
        self.assertEqual(metrics["answer_in_preview_5"], 0)
        self.assertEqual(metrics["answer_in_delivered_text_5"], 1)

    def test_numeric_evidence_preserves_percent_and_compound_unit(self):
        pairs = [
            ("150", "There were 150 attendees.", 1),
            ("150", "The increase was 150%.", 0),
            ("150", "The value was -150.", 0),
            (
                "390 m (acceptable range: anything between 386 and 394 m)",
                "It spans 390 m.",
                1,
            ),
            (
                "390 m (acceptable range: anything between 386 and 394 m)",
                "It travels at 390 m/s.",
                0,
            ),
        ]
        for reference, text, expected in pairs:
            with self.subTest(reference=reference, text=text):
                case = {
                    "id": "fact",
                    "family": "web",
                    "reference_answer": reference,
                    "answer_match_rule": "published-answers/2",
                    "reference_urls": [],
                }
                results = normalize(
                    {"results": [{"url": "https://docs.test/fact", "title": text}]}
                )
                self.assertEqual(
                    evaluate(case, results)["answer_in_preview_5"], expected
                )


def test_unapproved_authored_revision_is_rejected():
    import pytest
    from harness.web_search.revisions import validate_revision_binding

    with pytest.raises(ValueError, match="external scoring authority"):
        validate_revision_binding(
            {
                "schema": "web-search-assessment-revision/1",
                "cases": [],
                "provider_calls": 0,
            }
        )
