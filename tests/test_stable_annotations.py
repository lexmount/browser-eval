"""Reviewed registry against independent source text, never provider outputs."""
from copy import deepcopy
import json
from pathlib import Path
import re
import unittest

from harness.web_fetch.metrics import fact_in_text, normalize_for_match
from harness.web_fetch.stable_annotations import annotate_case, CASE_ANNOTATIONS, SNAPSHOT_METRIC_SCOPE
from harness.web_fetch.stable_tasks import evaluate_stable_task, validate_acceptance_contract


ROOT = Path(__file__).resolve().parents[1]
from harness.web_fetch.storage import current_evidence_dir


class StableAnnotationTests(unittest.TestCase):
    def case(self, identifier):
        rule = CASE_ANNOTATIONS[identifier]
        return {"id": identifier, "url": rule["url"], "track": rule["track"],
            "task_prompt": "Original historical instructions", "required_facts": [{"id": "old", "alternatives": ["Old literal"]}],
            "ground_truth_main_content": self.reference(identifier),
            "source_verification": {"reference_sha256": "original hash", "checked_at": "2000-01-01T00:00:00Z"},
            "source_benchmark": {"original_metadata": "preserve"}}

    def reference(self, identifier):
        return (ROOT / "evaluation/sources" / identifier / "reference.txt").read_text()

    def score(self, identifier, text):
        annotated = annotate_case(self.case(identifier))
        return evaluate_stable_task(text, annotated["acceptance_contract"], annotated["url"])

    def test_exact_twenty_case_registry_and_valid_minimum_contracts(self):
        expected = {
            "dynamic-quotes-api": 10, "dynamic-products-api": 12, "dynamic-catalog": 6,
            "dynamic-delayed": 10, "dynamic-reviews-graphql": 20, "dynamic-react-cart": 16,
            "dynamic-angular-phones": 20, "dynamic-swagger": 20, "dynamic-product-reviews": 5,
            "dynamic-iframe": 14,
            **{f"challenge-{i:02d}": 3 for i in range(1, 10)}, "challenge-10-justwatch": 3}
        self.assertEqual(set(CASE_ANNOTATIONS), set(expected))
        for identifier, count in expected.items():
            case = annotate_case(self.case(identifier))
            validate_acceptance_contract(case["acceptance_contract"])
            self.assertEqual(case["acceptance_contract"]["minimum_records"], count)
            self.assertNotIn("maximum_records", case["acceptance_contract"])
            self.assertIn(f"At least {count}", case["source_benchmark"]["stable_annotation"]["record_requirement"])

    def test_annotations_preserve_source_facts_prompt_and_are_pure_idempotent(self):
        for identifier in CASE_ANNOTATIONS:
            case = self.case(identifier)
            original = deepcopy(case)
            annotated = annotate_case(case)
            self.assertEqual(case, original)
            self.assertEqual(annotate_case(annotated), annotated)
            for field in ("ground_truth_main_content", "required_facts", "source_verification"):
                self.assertEqual(annotated[field], original[field])
            if original['track']=='challenge':
                self.assertEqual(annotated['source_benchmark']['captured_task_prompt'],original['task_prompt'])
                self.assertEqual(annotated['task_prompt'],annotated['reference_answer'])
            else:
                self.assertEqual(annotated['task_prompt'],original['task_prompt'])
            self.assertEqual(annotated["source_benchmark"]["original_metadata"], "preserve")
            self.assertEqual(annotated["source_benchmark"]["snapshot_metric_scope"], SNAPSHOT_METRIC_SCOPE)
            self.assertIsNone(annotated["source_benchmark"]["stable_annotation"]["live_factual_correctness"])
            annotated["acceptance_contract"]["minimum_records"] = 999
            self.assertNotEqual(annotate_case(case)["acceptance_contract"]["minimum_records"], 999)

    def test_unknown_cases_untouched_and_known_scope_mismatch_rejected(self):
        case = {"id": "unknown", "task_prompt": "untouched", "nested": {"key": []}}
        copied = annotate_case(case)
        self.assertEqual(copied, case)
        self.assertIsNot(copied["nested"], case["nested"])
        for field, value in (("url", "https://wrong.test/"), ("track", "web")):
            bad = self.case("dynamic-swagger")
            bad[field] = value
            with self.assertRaises(ValueError):
                annotate_case(bad)

    def test_all_anchors_have_independent_evidence_no_invented_headers(self):
        for identifier in CASE_ANNOTATIONS:
            annotated = annotate_case(self.case(identifier))
            evidence = annotated["source_benchmark"]["stable_annotation"]["anchor_evidence"]
            self.assertEqual(bool(evidence), bool(annotated["acceptance_contract"]["stable_text_anchors"]))
            if evidence:
                self.assertTrue((ROOT / "evaluation" / evidence).is_file())
            reference = normalize_for_match((ROOT / 'evaluation' / evidence).read_text()) if evidence else ''
            for anchor in annotate_case(self.case(identifier))["acceptance_contract"]["stable_text_anchors"]:
                self.assertTrue(any(fact_in_text(reference, normalize_for_match(value)) for value in anchor["alternatives"]),
                                (identifier, anchor["id"]))
        self.assertEqual(len(annotate_case(self.case("challenge-09"))["acceptance_contract"]["stable_text_anchors"]), 2)
        self.assertEqual(len(annotate_case(self.case("challenge-10-justwatch"))["acceptance_contract"]["stable_text_anchors"]), 3)
        self.assertEqual(annotate_case(self.case("challenge-04"))["acceptance_contract"]["stable_text_anchors"], [])

    def test_all_ten_independent_dynamic_references_are_positive(self):
        for identifier, rule in CASE_ANNOTATIONS.items():
            if rule["track"] != "dynamic":
                continue
            result = self.score(identifier, self.reference(identifier))
            self.assertTrue(result["stable_task_pass"], (identifier, result["stable_task_errors"]))
            self.assertEqual(result["stable_unique_record_count"], rule["contract"]["minimum_records"])

    def test_saved_markdown_layout_regressions_against_independent_contracts(self):
        # Outputs supply layout examples only; every fixed value still comes
        # from the independently reviewed registry, not from these artifacts.
        evidence=current_evidence_dir()
        if not (evidence/'artifacts').is_dir():self.skipTest('requires persisted benchmark evidence')
        layouts = {
            "dynamic-quotes-api": ("crawl4ai", "tavily", "cloudflare-markdown"),
            "dynamic-products-api": ("crawl4ai", "firecrawl", "cloudflare-markdown"),
            "dynamic-angular-phones": ("crawl4ai", "cloudflare-markdown"),
            "dynamic-swagger": ("firecrawl", "tavily", "cloudflare-markdown", "crawl4ai"),
        }
        for identifier, sources in layouts.items():
            for source in sources:
                path = evidence / "artifacts" / source / identifier / "run-001.txt"
                result = self.score(identifier, path.read_text())
                self.assertTrue(result["stable_task_pass"], (identifier, source, result["stable_task_errors"]))
                self.assertEqual(result["stable_unique_record_count"], CASE_ANNOTATIONS[identifier]["contract"]["minimum_records"])

    def test_lost_tag_boundaries_and_empty_shells_still_fail(self):
        evidence=current_evidence_dir()
        if not (evidence/'artifacts').is_dir():self.skipTest('requires persisted benchmark evidence')
        for source, identifier in (("firecrawl", "dynamic-quotes-api"),
                                   ("tavily", "dynamic-products-api"),
                                   ("tavily", "dynamic-angular-phones")):
            path = evidence / "artifacts" / source / identifier / "run-001.txt"
            result = self.score(identifier, path.read_text())
            self.assertFalse(result["stable_task_pass"], (source, identifier))
            if identifier == "dynamic-quotes-api":
                self.assertTrue(result["stable_structure_pass"])
                self.assertFalse(result["stable_content_pass"])

    def test_fixed_phone_descriptions_and_swagger_operations_not_replaced_by_count(self):
        phone = self.reference("dynamic-angular-phones")
        changed = phone.replace("The next generation of DOES.", "A new unrelated description.")
        result = self.score("dynamic-angular-phones", changed)
        self.assertTrue(result["stable_structure_pass"])
        self.assertFalse(result["stable_content_pass"])
        swagger = self.reference("dynamic-swagger")
        changed = swagger.replace("Add a new pet to the store", "An unrelated operation description")
        result = self.score("dynamic-swagger", changed)
        self.assertTrue(result["stable_structure_pass"])
        self.assertFalse(result["stable_content_pass"])

    def test_fixed_tuples_cannot_swap_descriptions_between_valid_records(self):
        for identifier, first, second in (
            ("dynamic-angular-phones", "The next generation of DOES.", "An experience to cheer about."),
            ("dynamic-swagger", "uploads an image", "Add a new pet to the store")):
            text = self.reference(identifier).replace(first, "TEMP_PLACEHOLDER").replace(second, first).replace("TEMP_PLACEHOLDER", second)
            result = self.score(identifier, text)
            self.assertTrue(result["stable_structure_pass"])
            self.assertFalse(result["stable_content_pass"])

    def test_angular_and_swagger_markdown_table_equivalents_are_positive(self):
        for identifier, header in (("dynamic-angular-phones", "Name | Description"),
                                   ("dynamic-swagger", "Method | Path | Summary")):
            blocks = self.reference(identifier).strip().split("\n\n")
            rows = [" | ".join(block.splitlines()) for block in blocks]
            text = "| " + header + " |\n| " + " | ".join("---" for _ in header.split("|")) + " |\n"
            text += "\n".join("| " + row + " |" for row in rows)
            self.assertTrue(self.score(identifier, text)["stable_task_pass"], identifier)

    def test_catalog_prices_change_but_fixed_descriptions_remain_required(self):
        text = self.reference("dynamic-catalog")
        changed_prices = re.sub(r"\$[\d,.]+", "$999.99", text)
        self.assertTrue(self.score("dynamic-catalog", changed_prices)["stable_task_pass"])
        result = self.score("dynamic-catalog", changed_prices.replace("4GB, 500GB, Linux", "4GB, 500GB, Different OS"))
        self.assertTrue(result["stable_structure_pass"])
        self.assertFalse(result["stable_content_pass"])

    def test_additional_complete_records_and_unstructured_navigation_are_allowed(self):
        extras = {
            "dynamic-angular-phones": "Extra device\nAn additional device description.",
            "dynamic-swagger": "GET\n/extra\nAn additional operation",
            "dynamic-catalog": "$999\nExtra model\nAn additional model description\n4 reviews",
        }
        for identifier, extra in extras.items():
            text = "Home\n\n" + self.reference(identifier).strip() + "\n\n" + extra + "\n\nContact"
            result = self.score(identifier, text)
            self.assertTrue(result["stable_task_pass"], (identifier, result["stable_task_errors"]))
            self.assertEqual(result["stable_unique_record_count"], CASE_ANNOTATIONS[identifier]["contract"]["minimum_records"] + 1)

    def test_all_challenge_titles_and_values_are_nonliteral(self):
        paths = {"challenge-01": "/shop/new-{i}", "challenge-02": "/{i}.html",
            "challenge-03": "/explore/abc{i}", "challenge-04": "/question/{i}", "challenge-05": "/subject/{i}",
            "challenge-06": "/shop/shop{i}", "challenge-07": "/dp/ABCDEFGHI{i}",
            "challenge-08": "/r/programming/comments/abc{i}/new-title", "challenge-09": "/title/tt{i}",
            "challenge-10-justwatch": "/us/tv-show/new-{i}"}
        for identifier, path in paths.items():
            rules = CASE_ANNOTATIONS[identifier]["contract"]
            prefix = '\n'.join(a['alternatives'][0] for a in rules['stable_text_anchors']) + '\n'
            text = prefix + "\n".join(f"[Completely new title {i}](https://{rules['hosts'][0]}{path.format(i=i)})" for i in range(1, 4))
            result = self.score(identifier, text)
            self.assertTrue(result["stable_task_pass"], identifier)
            self.assertFalse(self.score(identifier, text.replace(rules['hosts'][0], "wrong.test"))["stable_task_pass"])

    def test_popular_body_and_list_are_both_required_and_values_can_change(self):
        identifier = 'challenge-09'
        anchors = 'IMDb Top 250 movies\nAs rated by regular IMDb voters.\n'
        links = '\n'.join(f'[{i}. New film {i}](https://www.imdb.com/title/tt{i}) Rating: {i}.2' for i in range(1, 4))
        complete = self.score(identifier, anchors + links)
        self.assertTrue(complete['stable_task_pass'])
        self.assertEqual(complete['stable_anchor_coverage'], 1)
        structure_only = self.score(identifier, links)
        self.assertTrue(structure_only['stable_structure_pass'])
        self.assertFalse(structure_only['stable_content_pass'])
        self.assertEqual(structure_only['stable_anchor_coverage'], 0)
        missing_explanation = self.score(identifier, 'IMDb Top 250 movies\n' + links)
        self.assertFalse(missing_explanation['stable_task_pass'])
        self.assertEqual(missing_explanation['stable_anchor_coverage'], .5)
        body_only = self.score(identifier, anchors)
        self.assertFalse(body_only['stable_task_pass'])
        self.assertEqual(body_only['stable_anchor_coverage'], 1)
        changed = anchors + '\n'.join(reversed(links.replace('New film', 'Different film').replace('Rating:', 'Price: $').splitlines()))
        self.assertTrue(self.score(identifier, changed)['stable_task_pass'])
        self.assertEqual(self.score(identifier, changed + '\nAdditional legitimate content')['stable_anchor_coverage'], 1)

    def test_unannotated_text_is_not_perfect_text_coverage(self):
        result = self.score('challenge-04', '')
        self.assertIsNone(result['stable_anchor_coverage'])
        self.assertEqual(result['stable_text_scope'], 'not_annotated')

    def test_amazon_product_names_do_not_supply_page_search_context(self):
        links='\n'.join(f'[Wireless headphones model {i}](https://www.amazon.com/dp/ABCDEFGHI{i})' for i in range(1,4))
        result=self.score('challenge-07',links)
        self.assertTrue(result['stable_structure_pass'])
        self.assertFalse(result['stable_task_pass'])
        self.assertEqual(result['stable_anchor_coverage'],0)
        self.assertTrue(self.score('challenge-07','1-16 of over 100,000 results for "headphones"\n'+links)['stable_task_pass'])


if __name__ == "__main__":
    unittest.main()
