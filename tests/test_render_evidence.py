import copy
import hashlib
import unittest

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.render_evidence import validate_render_reference


class RenderEvidenceTests(unittest.TestCase):
    def case(self):
        return {
            "id": "dynamic", "name": "Dynamic", "url": "https://example.test/",
            "category": "dynamic_content", "expected_any": [],
            "required_facts": [
                {"id": "name", "alternatives": ["Green Tea", "绿茶"]},
                {"id": "count", "alternatives": ["12 products"]},
            ],
            "ground_truth_main_content": "Green Tea — 12 products", "min_content_chars": 10,
            "source_benchmark": {"render_evidence": {
                "scope": "All 12 product cards in the first loaded list; no pagination",
                "rendered_truncated": False,
                "completion": {"condition": "12 product cards visible; loading indicator gone",
                               "timeout_ms": 10000, "satisfied": True},
            }},
        }

    def test_embedded_script_never_proves_js_required(self):
        case = self.case()
        html = b'<main>Loading</main><script>var data = {"name":"Green Tea","n":"12 products"};</script>'
        before = copy.deepcopy(case)
        result = validate_render_reference(case, html, case["ground_truth_main_content"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["classification"], "embedded_payload")
        self.assertEqual(result["coverage"]["initial_visible"]["hits"], 0)
        self.assertFalse(result["js_required_proven"])
        self.assertEqual(result["initial_html_sha256"], hashlib.sha256(html).hexdigest())
        self.assertEqual(case, before)

    def test_async_content_absent_from_initial_is_not_execution_proof(self):
        case = self.case()
        html = '<main>Loading</main><script>fetch("/api/products").then(render)</script>'
        result = validate_render_reference(EvalCase(**case), html, "Green Tea — 12 products")
        self.assertTrue(result["valid"])
        self.assertEqual(result["classification"], "absent_from_initial_evidence")
        self.assertFalse(result["js_required_proven"])
        for key in ("reference", "rendered"):
            self.assertEqual(result["coverage"][key]["coverage"], 1)

    def test_initial_visible_and_shared_normalizer(self):
        case = self.case()
        result = validate_render_reference(case, '<main>Ｇｒｅｅｎ Tea<br>12 products</main>',
                                           '**Green Tea** — 12 products')
        self.assertTrue(result["valid"])
        self.assertEqual(result["classification"], "initial_visible_content")
        self.assertEqual(len(result["coverage"]["rendered"]["groups"]), 2)

    def test_unicode_escaped_payload_and_hidden_content(self):
        case = self.case()
        html = r'<script>{"name":"\u7eff\u8336"}</script><div hidden>12 products</div>'
        result = validate_render_reference(case, html, "绿茶 — 12 products")
        self.assertEqual(result["classification"], "embedded_payload")
        self.assertEqual(result["coverage"]["initial_visible"]["hits"], 0)

    def test_missing_reference_and_rendered_fact(self):
        case = self.case()
        case["ground_truth_main_content"] = "Green Tea only"
        result = validate_render_reference(case, '<main>Loading</main>', "12 products only")
        self.assertFalse(result["valid"])
        self.assertIn("reference_missing_fact:count", result["errors"])
        self.assertIn("rendered_missing_fact:name", result["errors"])
        self.assertEqual(result["coverage"]["reference"]["coverage"], 0.5)

    def test_missing_reference_and_capture_attestation_fail_closed(self):
        case = self.case()
        del case["ground_truth_main_content"]
        case["source_benchmark"] = {}
        result = validate_render_reference(case, "", "Green Tea — 12 products")
        self.assertFalse(result["valid"])
        for error in ("initial_html_empty", "reference_empty", "reference_scope_missing",
                      "completion_condition_missing", "completion_timeout_not_bounded",
                      "completion_not_satisfied", "rendered_truncation_not_ruled_out"):
            self.assertIn(error, result["errors"])

    def test_partial_initial_content_is_not_all_absent(self):
        case = self.case()
        result = validate_render_reference(case, '<main aria-hidden>Green Tea</main>',
                                           "Green Tea — 12 products")
        self.assertEqual(result["classification"], "partial_initial_content")
        self.assertEqual(result["initial_fact_hits"], 1)

    def test_empty_facts_are_not_vacuous_success(self):
        case = self.case()
        case["required_facts"] = []
        result = validate_render_reference(case, "<main>Loading</main>", "Complete rendered text")
        self.assertFalse(result["valid"])
        self.assertIn("required_facts_empty", result["errors"])
        self.assertIsNone(result["coverage"]["reference"]["coverage"])
        self.assertEqual(result["classification"], "unclassified")

    def test_empty_group_and_numeric_boundaries(self):
        case = self.case()
        case["required_facts"].append({"id": "empty", "alternatives": ["\u200b"]})
        result = validate_render_reference(case, "<main>Loading</main>", "Green Tea 112 products")
        self.assertIn("empty_fact_group:empty", result["errors"])
        self.assertIn("rendered_missing_fact:count", result["errors"])

    def test_completion_and_short_reference_fail_closed(self):
        for key, value, error in [
            ("condition", "", "completion_condition_missing"),
            ("timeout_ms", float("inf"), "completion_timeout_not_bounded"),
            ("timeout_ms", 0, "completion_timeout_not_bounded"),
            ("satisfied", False, "completion_not_satisfied"),
        ]:
            with self.subTest(key=key, value=value):
                case = self.case()
                case["source_benchmark"]["render_evidence"]["completion"][key] = value
                result = validate_render_reference(case, "<main>Loading</main>", case["ground_truth_main_content"])
                self.assertFalse(result["valid"])
                self.assertIn(error, result["errors"])
        case = self.case()
        case["min_content_chars"] = 100
        case["source_benchmark"]["render_evidence"]["rendered_truncated"] = True
        result = validate_render_reference(case, "<main>Loading</main>", case["ground_truth_main_content"])
        for error in ("reference_below_min_content_chars", "rendered_below_min_content_chars",
                      "rendered_truncation_not_ruled_out"):
            self.assertIn(error, result["errors"])

    def test_hashes_use_exact_unnormalized_text(self):
        case = self.case()
        rendered = "**Green Tea** — 12 products\n"
        result = validate_render_reference(case, "<main>Loading</main>", rendered)
        self.assertEqual(result["rendered_text_sha256"], hashlib.sha256(rendered.encode()).hexdigest())
        self.assertEqual(result["reference_sha256"], hashlib.sha256(case["ground_truth_main_content"].encode()).hexdigest())
        self.assertNotEqual(result["rendered_text_sha256"], result["reference_sha256"])

    def test_supplied_elapsed_must_be_finite_nonnegative_and_within_timeout(self):
        for elapsed in (-1, float("inf"), float("-inf"), float("nan"), None, "10", True):
            with self.subTest(elapsed=elapsed):
                case = self.case()
                case["source_benchmark"]["render_evidence"]["completion"]["elapsed_ms"] = elapsed
                result = validate_render_reference(case, "<main>Loading</main>", case["ground_truth_main_content"])
                self.assertFalse(result["valid"])
                self.assertIn("completion_elapsed_invalid", result["errors"])
        case = self.case()
        case["source_benchmark"]["render_evidence"]["completion"].update(
            timeout_ms=30000, elapsed_ms=999999)
        result = validate_render_reference(case, "<main>Loading</main>", case["ground_truth_main_content"])
        self.assertFalse(result["valid"])
        self.assertIn("completion_deadline_exceeded", result["errors"])

    def test_elapsed_zero_exact_deadline_and_omission_are_allowed(self):
        for elapsed in (0, 30000, 12.5):
            with self.subTest(elapsed=elapsed):
                case = self.case()
                case["source_benchmark"]["render_evidence"]["completion"].update(
                    timeout_ms=30000, elapsed_ms=elapsed)
                self.assertTrue(validate_render_reference(
                    case, "<main>Loading</main>", case["ground_truth_main_content"])["valid"])
        case = self.case()
        self.assertTrue(validate_render_reference(
            case, "<main>Loading</main>", case["ground_truth_main_content"])["valid"])


if __name__ == "__main__":
    unittest.main()


class DeclaredContentReferenceTests(unittest.TestCase):
    def test_body_scope_is_sufficient_without_an_artificial_fact_group(self):
        from lexbench_webfetch.body_coverage import body_requirements
        text = 'An article explains how batteries store energy and how charging changes their capacity.'
        case = {'id': 'article', 'url': 'https://example.test/', 'required_facts': [],
                'ground_truth_main_content': text, 'body_requirements': body_requirements(text),
                'source_benchmark': {'render_evidence': {
                    'scope': 'Complete article body', 'rendered_truncated': False,
                    'completion': {'condition': 'Article loaded', 'satisfied': True, 'timeout_ms': 10000}}}}
        self.assertTrue(validate_render_reference(case, '<main>Loading</main>', text)['valid'])
        missing = validate_render_reference(case, '<main>Loading</main>', 'Home Terms Privacy')
        self.assertFalse(missing['valid'])
        self.assertIn('rendered_body_requirements_not_met', missing['errors'])

    def test_pattern_scope_is_checked_on_both_reference_and_observation(self):
        case = {'id': 'code', 'url': 'https://example.test/', 'required_facts': [],
                'ground_truth_main_content': 'revenue = 123',
                'required_patterns': [{'id': 'formula', 'pattern': r'revenue\s*=\s*\d+', 'minimum': 1}],
                'source_benchmark': {'render_evidence': {
                    'scope': 'Visible worked example', 'rendered_truncated': False,
                    'completion': {'condition': 'Example loaded', 'satisfied': True, 'timeout_ms': 10000}}}}
        self.assertTrue(validate_render_reference(case, '<main>Loading</main>', 'revenue = 123')['valid'])
        self.assertFalse(validate_render_reference(case, '<main>Loading</main>', 'Home Terms')['valid'])
        case['ground_truth_main_content'] = 'Home Terms'
        self.assertFalse(validate_render_reference(case, '<main>Loading</main>', 'revenue = 123')['valid'])
