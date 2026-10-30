"""Value-invariant structural tests; all inputs synthetic, no provider/network."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

from lexbench_webfetch.dynamic_lists import named_entry_source_pair_hash
from lexbench_webfetch.metrics import evaluate_content, task_passed
from lexbench_webfetch.models import EvalCase
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import evaluate_stable_task, validate_acceptance_contract


def contract(schema, count=2, **extra):
    return {"version": 1, "schema": schema, "minimum_records": count, "maximum_records": count, **extra}


class StableTaskTests(unittest.TestCase):
    def check(self, text, schema="product", count=2, **extra):
        return evaluate_stable_task(text, contract(schema, count, **extra), "https://example.test/catalog")

    def test_invalid_absolute_url_without_hostname_is_not_a_record(self):
        rules=contract('named_link',1,hosts=['example.test'],entry_path=r'/item/\d+')
        with patch('lexbench_webfetch.stable_tasks.extract_named_entries',return_value=[
                {'url':'http:///item/1','title':'Invalid'}]):
            measured=evaluate_stable_task('',rules,'https://example.test/catalog')
        self.assertFalse(measured['stable_structure_pass'])
        self.assertEqual(measured['stable_record_count'],0)

    def test_body_text_contract_uses_only_stable_anchors_without_fake_records(self):
        rules={"version":1,"schema":"body_text","minimum_records":0,
               "stable_text_anchors":[
                   {"id":"identity","alternatives":["NASA Science seeks to discover"]},
                   {"id":"strategy","alternatives":["long-term roadmap"]},
               ]}
        changed_news=("NASA Science seeks to discover the universe\n"
                      "Entirely new rotating news cards\nlong-term roadmap")
        result=evaluate_stable_task(changed_news,rules)
        self.assertTrue(result["stable_task_pass"],result)
        self.assertEqual(result["stable_record_count"],0)
        missing=evaluate_stable_task("Entirely new rotating news cards",rules)
        self.assertFalse(missing["stable_task_pass"])
        self.assertIn("missing_stable_anchor:identity",missing["stable_task_errors"])

    def test_link_text_inside_emphasis_matches_visible_body_anchor(self):
        rules = {
            "version": 1,
            "schema": "body_text",
            "minimum_records": 0,
            "stable_text_anchors": [
                {
                    "id": "subtitle",
                    "alternatives": ["the official resource for the Team Fortress series."],
                }
            ],
        }
        result = evaluate_stable_task(
            "the official resource for the *[Team Fortress](/wiki/Team_Fortress_series)* series.",
            rules,
        )
        self.assertTrue(result["stable_task_pass"], result)

    def test_markdown_character_reference_matches_visible_body_anchor(self):
        rules = {
            "version": 1,
            "schema": "body_text",
            "minimum_records": 0,
            "stable_text_anchors": [
                {"id": "filter", "alternatives": ["Beds & baths"]}
            ],
        }
        result = evaluate_stable_task("For sale\n\nBeds &amp; baths\n", rules)
        self.assertTrue(result["stable_task_pass"], result)

    def test_body_text_contract_rejects_record_rules_or_no_stable_identity(self):
        with self.assertRaisesRegex(ValueError,"do not declare records"):
            validate_acceptance_contract({"version":1,"schema":"body_text",
                                          "minimum_records":1,
                                          "stable_text_anchors":[{"id":"x","alternatives":["stable"]}]})
        with self.assertRaisesRegex(ValueError,"require text anchors"):
            validate_acceptance_contract({"version":1,"schema":"body_text",
                                          "minimum_records":0})

    def test_stable_anchor_accepts_and_validates_source_evidence_metadata(self):
        rules = {
            "version": 1,
            "schema": "body_text",
            "minimum_records": 0,
            "stable_text_anchors": [{
                "id": "source-bound",
                "alternatives": ["Stable visible text"],
                "source_location": "main > h1",
                "observation_status": "Visible in all three mixed-session observations",
                "source_evidence_sha256": "a" * 64,
            }],
        }
        validate_acceptance_contract(rules)
        self.assertTrue(evaluate_stable_task("Stable visible text", rules)["stable_task_pass"])
        broken = deepcopy(rules)
        broken["stable_text_anchors"][0]["source_evidence_sha256"] = "not-a-hash"
        with self.assertRaisesRegex(ValueError, "Invalid or duplicate stable text"):
            validate_acceptance_contract(broken)

    def test_variable_record_policy_calculates_eighty_percent_from_source(self):
        policy = {
            "list_type": "variable", "source_observed_count": 30,
            "required_ratio": 0.8, "source_observation_counts": [30, 30, 30],
            "minimum_records": 24,
            "source_evidence_sha256": "a" * 64,
            "reason": "Changing source list observed independently before provider output.",
        }
        rules = {"version": 1, "schema": "named_link", "minimum_records": 24,
                 "hosts": ["example.test"], "entry_path": r"/item/\d+",
                 "record_count_policy": policy}
        text = "\n".join(f"[Item {index}](https://example.test/item/{index})"
                         for index in range(1, 25))
        result = evaluate_stable_task(text, rules, "https://example.test/list")
        self.assertTrue(result["stable_task_pass"], result)
        self.assertEqual(result["stable_record_list_type"], "variable")
        self.assertEqual(result["stable_source_observed_record_count"], 30)
        self.assertEqual(result["stable_required_record_ratio"], 0.8)
        self.assertEqual(result["stable_record_coverage"], 0.8)
        too_short = "\n".join(f"[Item {index}](https://example.test/item/{index})"
                              for index in range(1, 24))
        self.assertFalse(evaluate_stable_task(
            too_short, rules, "https://example.test/list")["stable_task_pass"])
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_acceptance_contract({**rules, "minimum_records": 23})
        wrong_policy = deepcopy(rules)
        wrong_policy["record_count_policy"]["minimum_records"] = 23
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_acceptance_contract(wrong_policy)

    def test_variable_record_policy_rounds_26_times_eighty_percent_up_to_21(self):
        policy = {
            "list_type": "variable", "source_observed_count": 26,
            "required_ratio": 0.8, "source_observation_counts": [26, 26, 26],
            "source_evidence_sha256": "c" * 64,
            "reason": "Changing source list observed independently before provider output.",
        }
        rules = {"version": 1, "schema": "named_link", "minimum_records": 21,
                 "hosts": ["example.test"], "entry_path": r"/item/\d+",
                 "record_count_policy": policy}
        validate_acceptance_contract(rules)
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_acceptance_contract({**rules, "minimum_records": 20})

    def test_record_policy_keeps_two_completed_environments_when_third_open_failed(self):
        policy = {
            "list_type": "variable", "source_observed_count": 21,
            "required_ratio": 0.8, "source_observation_counts": [20, 21],
            "source_evidence_sha256": "d" * 64,
            "reason": "A failed third open was retained but did not lower the threshold.",
        }
        rules = {
            "version": 1, "schema": "named_link", "minimum_records": 17,
            "hosts": ["example.test"], "entry_path": r"/item/\d+",
            "record_count_policy": policy,
        }
        validate_acceptance_contract(rules)
        one_completed = deepcopy(rules)
        one_completed["record_count_policy"]["source_observation_counts"] = [21]
        with self.assertRaisesRegex(ValueError, "Invalid record count policy"):
            validate_acceptance_contract(one_completed)

    def test_fixed_record_policy_requires_the_full_observed_list(self):
        policy = {
            "list_type": "fixed", "source_observed_count": 30,
            "required_ratio": 1.0, "source_observation_counts": [30, 30, 30],
            "source_evidence_sha256": "b" * 64,
            "reason": "The declared fixed table contains all thirty records.",
        }
        rules = {"version": 1, "schema": "named_link", "minimum_records": 30,
                 "hosts": ["example.test"], "entry_path": r"/item/\d+",
                 "record_count_policy": policy}
        validate_acceptance_contract(rules)
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_acceptance_contract({**rules, "minimum_records": 29})

    def test_product_titles_prices_order_and_markdown_are_not_ground_truth(self):
        for text in ("Alpha shirt\n$12.50\n\nBeta shirt\n$8",
                     "## New coat\n\nPrice: €99.25\n\n## 最新名称\nPrice: USD 300",
                     "[Renamed second](/product/2) $1,000.00\n[Renamed first](/product/1) $0.00"):
            with self.subTest(text=text):
                self.assertTrue(self.check(text)["stable_task_pass"])

    def test_duplicate_cards_cannot_meet_count_or_change_price_to_fake_uniqueness(self):
        for text in ("Same coat\n$10\n\nSame coat\n$10", "Same coat\n$10\n\nSame coat\n$99"):
            result = self.check(text)
            self.assertFalse(result["stable_task_pass"])
            self.assertEqual(result["stable_unique_record_count"], 1)
            self.assertIn("duplicate_records", result["stable_task_errors"])

    def test_number_count_alone_or_columns_are_not_complete_product_cards(self):
        for text in ("2 products", "Alpha\nBeta\n$10\n$20", "$10\n$20", "Alpha\n\nBeta", "",
                     "Alpha\n-$10\nBeta\n$20", "Alpha\n$-10\nBeta\n$20", "Alpha\n$10garbage\nBeta\n$20"):
            with self.subTest(text=text):
                self.assertFalse(self.check(text)["stable_task_pass"])

    def test_missing_card_cannot_borrow_another_card_fields(self):
        for text in ("Alpha\nBeta\n$10", "Alpha\n$10\nBeta", "Alpha\n$10\nBeta\nPrice:"):
            self.assertFalse(self.check(text)["stable_task_pass"])

    def test_optional_financing_and_shipping_are_typed_if_present(self):
        good = "Alpha\n$10\nor 4 x$2.50\nFree shipping\n\nBeta\n$12"
        self.assertTrue(self.check(good)["stable_task_pass"])
        for bad in (good.replace("4 x$2.50", "0 x$2.50"), good.replace("4 x$2.50", "many payments"),
                    good.replace("Free shipping", "Shipping: unknown")):
            self.assertFalse(self.check(bad)["stable_task_pass"])

    def test_catalog_requires_price_description_and_integer_review_count_per_card(self):
        text = "$10\nSame model\nBlue machine\n3 reviews\n\n$20\nSame model\nRed machine\n0 reviews"
        self.assertTrue(self.check(text, "catalog_product")["stable_task_pass"])
        self.assertTrue(self.check(text.replace("$10", "$900").replace("Blue machine", "New device description"),
                                   "catalog_product")["stable_task_pass"])
        for bad in (text.replace("3 reviews", ""), text.replace("Blue machine", ""),
                    text.replace("3 reviews", "three reviews"), text.replace("Red machine", "Blue machine")):
            self.assertFalse(self.check(bad, "catalog_product")["stable_task_pass"])

    def test_quote_author_and_tags_are_owned_not_separate_counts(self):
        text = "“A completely new idea.”\nby New Author\nTags: nature future\n\n“Another thought.”\nby Second Author\nTags: science"
        self.assertTrue(self.check(text, "quote")["stable_task_pass"])
        for bad in (text.replace("by New Author", ""), text.replace("Tags: nature future", "Tags:"),
                    text.replace("by New Author", "by")):
            self.assertFalse(self.check(bad, "quote")["stable_task_pass"])
        mixed = '“First thought.”\n“Second thought.”\nby First Author\nby Second Author\nTags: first\nTags: second'
        self.assertFalse(self.check(mixed, "quote")["stable_task_pass"])

    def test_reviews_allow_new_dates_but_require_valid_dates_and_owned_text(self):
        for text in ("2026-08-31\nNew review of the product\n\n2026-09-01\nAnother review of it",
                     "2027-01-01 A different opinion\n2027-01-01 A second opinion"):
            self.assertTrue(self.check(text, "review")["stable_task_pass"])
        for text in ("2026-02-30 Bad calendar date\n2026-09-01 Valid review text",
                     "2026-09-01\n2026-09-02\nOnly one review text",
                     "2026-09-01 Repeat review\n2026-09-01 Repeat review"):
            self.assertFalse(self.check(text, "review")["stable_task_pass"])

    def test_reviews_accept_commonmark_escaped_iso_dates(self):
        text = (r"2023\-05\-18 First independently returned review" "\n\n"
                r"2023\-05\-19 Second independently returned review")
        result = self.check(text, "review")
        self.assertTrue(result["stable_task_pass"], result["stable_task_errors"])

    def test_name_description_requires_owned_blocks_not_arbitrary_line_pairing(self):
        self.assertTrue(self.check("New phone\nNew phone has a brighter display.\n\nOther phone\nIt has a different camera.",
                                   "name_description")["stable_task_pass"])
        self.assertFalse(self.check("First name\nSecond name\nFirst description\nSecond description", "name_description")["stable_task_pass"])
        self.assertFalse(self.check("New phone\nNew phone\n\nOther phone\nOther phone", "name_description")["stable_task_pass"])
        markdown = "# Phone catalog\n\n## Phone Alpha\n\nA new generation display.\n\n## Phone Beta\n\nA more compact device."
        self.assertTrue(self.check(markdown, "name_description")["stable_task_pass"])
        self.assertFalse(self.check(markdown.replace("A new generation display.", ""), "name_description")["stable_task_pass"])

    def test_api_operation_types_and_method_path_identity(self):
        text = "GET\n/new/{id}\nRead a new object\n\nPOST /new Create a new object"
        self.assertTrue(self.check(text, "api_operation")["stable_task_pass"])
        for bad in (text.replace("GET", "FETCH"), text.replace("/new/{id}", "https://wrong.test/new"),
                    text.replace("Read a new object", ""), "GET /new First summary\nGET /new Different summary"):
            self.assertFalse(self.check(bad, "api_operation")["stable_task_pass"])

    def test_api_operation_native_markdown_link_layout(self):
        text = ('POST[/pet](#/pet/addPet)Add a new pet to the store\n'
                'GET[/pet/{petId}](#/pet/getPetById)Find pet by ID')
        result = self.check(text, 'api_operation')
        self.assertTrue(result['stable_task_pass'], result['stable_task_errors'])

    def test_operation_group_headings_and_footer_do_not_extend_summary(self):
        anchors = [{"id": "read", "alternatives": ["GET\n/new/{id}\nRead a new object"]},
                   {"id": "create", "alternatives": ["POST\n/new\nCreate a new object"]}]
        for text in ("### First group\nOverview of first group\nGET\n/new/{id}\nRead a new object\n"
                     "### Second group\nOverview of second group\nPOST /new Create a new object\n"
                     "#### Models\nModelOne\nModelTwo",
                     "### GET /new/{id}\nRead a new object\n### POST /new\nCreate a new object\n"
                     "## Unrelated appendix\nAppendix text"):
            result = self.check(text, "api_operation", stable_text_anchors=anchors)
            self.assertTrue(result["stable_task_pass"], result)
            self.assertEqual([r["fields"]["summary"] for r in result["stable_records"]],
                             ["Read a new object", "Create a new object"])

    def test_operation_summary_cannot_be_borrowed_from_next_group_or_footer(self):
        anchors = [{"id": "read", "alternatives": ["GET\n/new/{id}\nRead a new object"]}]
        for text in ("GET /new/{id}\n### Next group\nRead a new object\nPOST /new Create a new object",
                     "GET /new/{id}\n### POST /new\nRead a new object\nCreate a new object",
                     "GET /new/{id} Wrong summary\nPOST /new Create a new object\n"
                     "#### Footer\nRead a new object"):
            result = self.check(text, "api_operation", stable_text_anchors=anchors)
            self.assertFalse(result["stable_task_pass"])
            self.assertFalse(result["stable_content_pass"])
        swapped = "### First\nGET /new/{id} Create a new object\n### Second\nPOST /new Read a new object"
        result = self.check(swapped, "api_operation", stable_text_anchors=anchors)
        self.assertTrue(result["stable_structure_pass"])
        self.assertFalse(result["stable_content_pass"])

    def test_operation_summary_soft_wrap_stays_inside_its_record(self):
        anchors = [{"id": "read", "alternatives": ["GET\n/new/{id}\nRead a new object"]}]
        result = self.check("## Group\nGET /new/{id}\nRead a new\nobject\n## Next\nPOST /new Create an object",
                            "api_operation", stable_text_anchors=anchors)
        self.assertTrue(result["stable_task_pass"], result)

    def test_taxonomy_uses_field_type_not_current_family_literals(self):
        self.assertTrue(self.check("## Testidae\n\n**Changedidae**", "taxonomy_family")["stable_task_pass"])
        self.assertFalse(self.check("Navigation\nHome\nAbout\nContact", "taxonomy_family")["stable_task_pass"])
        self.assertFalse(self.check("Testidae\nTestidae", "taxonomy_family")["stable_task_pass"])

    def test_markdown_tables_have_owned_typed_fields_and_empty_cells_fail(self):
        text = "| Name | Price |\n| --- | --- |\n| Alpha | $10 |\n| Beta | $20 |"
        self.assertTrue(self.check(text)["stable_task_pass"])
        self.assertFalse(self.check(text.replace("Beta | $20", "Beta |"))["stable_task_pass"])
        self.assertFalse(self.check(text.replace("Beta | $20", "Beta | twenty"))["stable_task_pass"])
        self.assertFalse(self.check(text.replace("Beta | $20", "Alpha | $20"))["stable_task_pass"])

    def test_all_other_schemas_accept_typed_markdown_tables(self):
        rows = {
            "quote": ("Quote | Author | Tags", "One idea | Someone | life", "Another idea | Someone Else | work"),
            "review": ("Date | Text", "2026-01-01 | New opinion", "2026-01-02 | Different opinion"),
            "catalog_product": ("Title | Price | Description | Reviews", "A model | $10 | Blue device | 4", "A model | $20 | Red device | 3"),
            "name_description": ("Name | Description", "Phone Alpha | Blue screen device", "Phone Beta | Green screen device"),
            "api_operation": ("Method | Path | Summary", "GET | /one | Read object", "POST | /two | Create object"),
            "taxonomy_family": ("Family", "Newidae", "Otheridae"),
        }
        for schema, (header, first, second) in rows.items():
            text = f"| {header} |\n| " + " | ".join("---" for _ in header.split("|")) + f" |\n| {first} |\n| {second} |"
            with self.subTest(schema=schema):
                self.assertTrue(self.check(text, schema)["stable_task_pass"])

    def test_named_links_deduplicate_identity_and_reject_wrong_site(self):
        rules = {"hosts": ["example.test"], "entry_path": r"/item/\d+"}
        text = "[New title](/item/91)\n[Different title](/item/92)"
        self.assertTrue(self.check(text, "named_link", **rules)["stable_task_pass"])
        for bad in (text.replace("/item/92", "/item/91?x=2"), text.replace("/item/", "https://evil.test/item/"),
                    text.replace("New title", "Details"), text.replace("/item/", "/category/")):
            self.assertFalse(self.check(bad, "named_link", **rules)["stable_task_pass"])
        plain = "First title\nhttps://example.test/item/1\nSecond title\nhttps://example.test/item/2"
        self.assertFalse(self.check(plain, "named_link", **rules)["stable_task_pass"])
        self.assertFalse(self.check("[Bad URL](https://[invalid/item/1)", "named_link", **rules)["stable_task_pass"])

    def test_named_links_reject_declared_non_entity_titles(self):
        rules = {
            "hosts": ["example.test"],
            "entry_path": r"/item/\d+",
            "title_exclusion_patterns": [r"発売中"],
        }
        text = "[発売中](/item/1)\n[マリオカート ワールド](/item/2)"
        result = self.check(text, "named_link", minimum_records=1, **rules)
        self.assertTrue(result["stable_task_pass"], result)
        self.assertEqual(result["stable_unique_record_count"], 1)

    def test_named_links_must_match_frozen_source_name_and_link_pairs(self):
        rules = {"hosts": ["example.test"], "entry_path": r"/item/\d+"}
        allowed = sorted([
            named_entry_source_pair_hash("First item", "https://example.test/item/1", rules),
            named_entry_source_pair_hash("Second item", "https://example.test/item/2", rules),
        ])
        rules.update(
            source_record_pair_hashes=allowed,
            source_record_pairs_sha256=hashlib.sha256("\n".join(allowed).encode()).hexdigest(),
        )
        matched = self.check(
            "[First item](/item/1)\n[Second item](/item/2)", "named_link", **rules)
        self.assertTrue(matched["stable_task_pass"], matched)
        self.assertEqual(matched["stable_source_matched_record_count"], 2)
        self.assertEqual(matched["stable_source_mismatched_record_count"], 0)

        wrong_name = self.check(
            "[Invented item](/item/1)\n[Second item](/item/2)", "named_link", **rules)
        self.assertFalse(wrong_name["stable_task_pass"])
        self.assertIn("source_record_mismatch", wrong_name["stable_task_errors"])
        self.assertEqual(wrong_name["stable_source_matched_record_count"], 1)
        self.assertEqual(wrong_name["stable_source_mismatched_record_count"], 1)

    def test_named_links_accept_declared_tracking_path_suffix(self):
        rules = {"hosts": ["www.amazon.com"],
                 "entry_path": r"(?:/[^/]+)?/dp/[A-Z0-9]{10}(?:/ref=[^/?#]+)?"}
        text = ("[First product](https://www.amazon.com/first/dp/B012345678/ref=abc_1)\n"
                "[Second product](https://www.amazon.com/dp/B087654321/ref=abc_2)")
        result = self.check(text, "named_link", **rules)
        self.assertTrue(result["stable_task_pass"], result["stable_task_errors"])

    def test_stable_anchor_compares_visible_relative_autolinks_and_link_labels(self):
        rules = {
            "hosts": ["example.test"],
            "entry_path": r"/item/\d+",
            "stable_text_anchors": [{
                "id": "help",
                "alternatives": [
                    "Questions: /help/start, /help/advanced, or Support."
                ],
            }],
        }
        artifact = ("Questions: </help/start>, </help/advanced>, or "
                    "[Support](https://support.example.test/).\n"
                    "[Alpha](/item/1)\n[Beta](/item/2)")
        result = self.check(artifact, "named_link", **rules)
        self.assertTrue(result["stable_task_pass"], result["stable_task_errors"])

        missing = artifact.replace("</help/advanced>, ", "")
        result = self.check(missing, "named_link", **rules)
        self.assertTrue(result["stable_structure_pass"])
        self.assertFalse(result["stable_content_pass"])
        self.assertIn("missing_stable_anchor:help", result["stable_task_errors"])

    def test_named_links_reject_route_labels_and_pure_actions(self):
        rules = {"hosts": ["example.test"], "entry_path": r"/item/\d+"}
        fake = ("[https://example.test/item/1](https://example.test/item/1)\n"
                "[/item/2?ref=search](/item/2?ref=search)\n"
                "[?ref=search](/item/3?ref=search)\n"
                "[See options](/item/4)\n"
                "[Click to see price](/item/5)\n"
                "[(74K)](/item/6)\n"
                "[前往登录](/item/7)")
        result = self.check(fake, "named_link", **rules)
        self.assertEqual(result["stable_complete_record_count"], 0)
        self.assertFalse(result["stable_task_pass"])

        real = "[Real product title](/item/1)\n[真实商品标题](/item/2)"
        result = self.check(real, "named_link", **rules)
        self.assertEqual(result["stable_complete_record_count"], 2)
        self.assertTrue(result["stable_task_pass"], result)

    def test_named_link_task_exclusions_reject_generic_card_labels_only(self):
        acceptance = {
            "version": 1,
            "schema": "named_link",
            "minimum_records": 3,
            "hosts": ["example.test"],
            "entry_path": r"/item/\d+",
            "record_name_exclusions": ["FreeTV"],
        }
        case = {
            "url": "https://example.test/catalog",
            "task_kind": "structure",
            "acceptance_contract": acceptance,
        }
        generic = score_attempt(
            case,
            b"[FreeTV](/item/1)\n[FreeTV](/item/2)\n[FreeTV](/item/3)",
            ok=True,
        )
        self.assertFalse(task_passed(True, generic, "structure"))
        self.assertEqual(generic["stable_complete_record_count"], 0)

        named = score_attempt(
            case,
            (b"[FreeTV](/item/0)\n[Alpha Show](/item/1)\n"
             b"[Beta Show](/item/2)\n[Gamma Show](/item/3)"),
            ok=True,
        )
        self.assertTrue(task_passed(True, named, "structure"))
        self.assertEqual(named["stable_complete_record_count"], 3)
        self.assertEqual(named["stable_excluded_record_names"], ["FreeTV"])

    def test_wrong_page_identity_and_eligible_gates(self):
        text = "Alpha\n$10\n\nBeta\n$20"
        rules = contract("product", page_patterns=[r"^Product catalog$"])
        self.assertFalse(evaluate_stable_task("Other page\n" + text, rules)["stable_task_pass"])
        self.assertTrue(evaluate_stable_task("Product catalog\n" + text, rules)["stable_task_pass"])
        self.assertFalse(evaluate_stable_task("Product catalog\n" + text, rules, eligible=False)["stable_task_pass"])

    def test_strict_contract_validation_before_snapshot_exemption(self):
        for value in ({}, None, {"version": True, "schema": "product", "minimum_records": 2},
                      contract("invented"), contract(["product"]), contract("product", count=0), contract("product", count=True),
                      contract("product", maximum_records=1), contract("product", page_patterns=["["]),
                      contract("product", typo=True), contract("named_link"),
                      contract("named_link", hosts=["evil.test/path"], entry_path="/item/.+"),
                      contract("product", record_name_exclusions=["Generic"]),
                      contract("named_link", hosts=["example.test"], entry_path="/item/.+",
                               record_name_exclusions=["Generic", "generic"])):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_acceptance_contract(value)

    def test_declared_facts_remain_required_with_a_record_contract(self):
        rules = contract("product")
        before = deepcopy(rules)
        result = evaluate_content("Alpha\n$10\n\nBeta\n$20", ["Old name"], required_facts=[
            {"id": "frozen", "alternatives": ["Old title $50"]}], min_content_chars=10000,
            ground_truth_main_content="Old title $50", acceptance_contract=rules)
        self.assertFalse(result["content_pass"])
        self.assertEqual(result["fact_coverage"], 0)
        self.assertIsNotNone(result["word_f1"])
        self.assertFalse(task_passed(True, result, "structure"))
        self.assertFalse(task_passed(False, result))
        self.assertEqual(rules, before)
        self.assertIsNone(result["factual_correctness"])

    def test_frozen_fixture_scoring_remains_unchanged_without_contract(self):
        result = evaluate_content("Old title", ["Old title"], ground_truth_main_content="Old title")
        self.assertTrue(result["content_pass"])
        self.assertEqual(result["word_f1"], 1.0)
        self.assertNotIn("stable_task_pass", result)
        self.assertEqual(EvalCase("case", "name", "https://example.test", "fixture", []).acceptance_contract, {})

    def test_stable_text_and_structure_are_separate_from_word_overlap(self):
        rules = contract("product", stable_text_anchors=[
            {"id": "section", "alternatives": ["Product catalog", "Available products"]},
            {"id": "explanation", "alternatives": ["Prices include tax"]}])
        text = "# Available products\nPrices include tax\n\nNew coat\n$19\n\nNew scarf\n$99"
        result = evaluate_content(text, [],
                                  ground_truth_main_content="Old title Old price", acceptance_contract=rules)
        self.assertTrue(result["stable_structure_pass"])
        self.assertTrue(result["stable_content_pass"])
        self.assertTrue(result["content_pass"])
        self.assertLess(result["word_f1"], 1.0)
        missing = evaluate_stable_task(text.replace("Prices include tax", "Different explanation"), rules)
        self.assertTrue(missing["stable_structure_pass"])
        self.assertFalse(missing["stable_content_pass"])
        self.assertFalse(missing["stable_task_pass"])
        self.assertEqual(missing["stable_anchor_results"][1]["matched"], [])
        incomplete = evaluate_stable_task(text.replace("$99", ""), rules)
        self.assertFalse(incomplete["stable_structure_pass"])
        self.assertTrue(incomplete["stable_content_pass"])
        self.assertFalse(incomplete["stable_task_pass"])

    def test_anchor_validation_and_numeric_boundaries(self):
        for anchors in ([{"id": "empty", "alternatives": []}],
                        [{"id": "empty", "alternatives": ["**"]}],
                        [{"id": "same", "alternatives": ["A"]}, {"id": "same", "alternatives": ["B"]}],
                        "Product catalog"):
            with self.assertRaises(ValueError):
                validate_acceptance_contract(contract("product", stable_text_anchors=anchors))
        result = self.check("Page 100\nAlpha\n$10\nBeta\n$20", stable_text_anchors=[
            {"id": "page", "alternatives": ["Page 10"]}])
        self.assertTrue(result["stable_structure_pass"])
        self.assertFalse(result["stable_content_pass"])

    def test_prompt_headings_do_not_veto_complete_cards(self):
        cards = "Alpha\n$10\n\nBeta\n$20"
        for heading in ("# Access denied", "403 Forbidden", "Verify you are human", "Just a moment...",
                        "W3 Performing security verification", "Making sure you're not a bot",
                        "# 京东验证", "验证中心", "身份核实", "验证一下，购物无忧",
                        "![](/shield.png)验证一下，购物无忧"):
            result = self.check(heading + "\n" + cards)
            self.assertTrue(result["stable_structure_pass"])
            self.assertTrue(result["stable_content_pass"])
            self.assertTrue(result["stable_task_pass"])
            self.assertTrue(result["stable_blocked_page"])

    def test_diagnostic_hint_does_not_override_target_content(self):
        text = "Custom access failure\nAlpha\n$10\n\nBeta\n$20"
        result = evaluate_content(text, [], diagnostic_any=["Custom access failure"],
                                  ground_truth_main_content=text, acceptance_contract=contract("product"))
        self.assertTrue(result["stable_structure_pass"])
        self.assertTrue(result["stable_content_pass"])
        self.assertTrue(result["content_pass"])
        self.assertTrue(task_passed(True, result))
        self.assertEqual(result["diagnostic_hits"], ["Custom access failure"])
        self.assertEqual(result["word_f1"], 1.0)
        self.assertTrue(task_passed(True, {**result, "stable_task_pass": True}))
        self.assertFalse(task_passed(False, {"stable_task_pass": True}))

    def test_block_page_words_in_explanatory_prose_are_not_automatically_block_pages(self):
        cards = "Alpha\n$10\n\nBeta\n$20"
        for prose in ("This catalog explains why an access denied message can occur.",
                      "本文介绍京东验证页面的处理方式。",
                      "遇到“身份核实”提示时，请先检查网址。",
                      "这篇文章解释了验证中心的设计。"):
            result = self.check(prose + "\n" + cards)
            self.assertTrue(result["stable_task_pass"], (prose, result))

    def test_same_schema_wrong_page_requires_explicit_stable_identity(self):
        text = "Unrelated store\nNew coat\n$10\n\nAnother coat\n$20"
        result = self.check(text, stable_text_anchors=[{"id": "page", "alternatives": ["Intended catalog"]}])
        self.assertTrue(result["stable_structure_pass"])
        self.assertFalse(result["stable_content_pass"])
        self.assertFalse(result["stable_task_pass"])

    def test_multiline_product_links_images_and_hard_breaks(self):
        for hard_break in ("  \n", "\\\n", "\\\\\n"):
            text = (f"[![Photo](/image(1).png)New winter coat{hard_break}€123.45](/product/new(1))\n"
                    f"[![Photo](/image2.png)Different boots{hard_break}USD 250](/product/new2)")
            with self.subTest(hard_break=hard_break):
                result = self.check(text)
                self.assertTrue(result["stable_task_pass"], result)
                self.assertEqual(result["stable_records"][0]["fields"]["name"], "New winter coat")
        self.assertFalse(self.check(text.replace("USD 250", "USD -250"))["stable_task_pass"])
        self.assertFalse(self.check(text.replace("Different boots", "New winter coat"))["stable_task_pass"])

    def test_explicit_product_links_cannot_borrow_other_link_fields(self):
        for text in ("[Alpha\n](/a)\n[\n$10](/b)\n[Beta\n$20](/c)",
                     "[![Alpha](/photo)\n$10](/a)\n[Beta\n$20](/b)",
                     "[Alpha\n$10](/a)\n[Beta\n](/b)"):
            self.assertFalse(self.check(text)["stable_task_pass"], text)

    def test_bullet_description_cards_with_inline_linked_titles(self):
        for text in ("- [![Picture](/img)](/a) [New phone](/a) A brighter display.\n"
                     "- [![Picture](/img2)](/b) [Other phone](/b) A different camera.",
                     "  * [New phone](/a)\nA brighter display.\n  * [Other phone](/b)\nA different camera.",
                     "1. New phone\nA brighter display.\n2. Other phone\nA different camera."):
            result = self.check(text, "name_description")
            self.assertTrue(result["stable_task_pass"], result)
            self.assertEqual(result["stable_records"][0]["fields"],
                             {"name": "New phone", "description": "A brighter display."})
        for text in ("- [New phone](/a)\n- [Other phone](/b) A different camera.",
                     "- [New phone](/a) A brighter display.\n- [New phone](/b) A different camera.",
                     "- [New phone](/a)\n- [Other phone](/b) A brighter display. A different camera."):
            self.assertFalse(self.check(text, "name_description")["stable_task_pass"])

    def test_image_bullet_then_linked_description_card(self):
        text = ("- [![New phone](/img)](/phone)\n"
                "[New phone](/phone) A brighter display.\n\n"
                "- [![Other phone](/img2)](/other)\n"
                "[Other phone](/other) A different camera.")
        result = self.check(text, "name_description")
        self.assertTrue(result["stable_task_pass"], result)
        self.assertEqual(result["stable_complete_record_count"], 2)

    def test_image_bullet_card_can_contain_blank_line_and_glued_inline_boundary(self):
        text = ("- [![New phone](/img)](/phone)\n"
                "[New phone](/phone) First sentence\n\nSecond sentence.\n\n"
                "- [![Other phone](/img2)](/other)\n"
                "[Other phone](/other) A brighterdisplay.")
        anchors = [
            {"id": "new", "alternatives": ["New phone\nFirst sentence Second sentence."]},
            {"id": "other", "alternatives": ["Other phone\nA brighter display."]},
        ]
        result = self.check(text, "name_description", stable_text_anchors=anchors)
        self.assertTrue(result["stable_task_pass"], result)

    def test_standalone_images_bound_repeated_title_description_cards(self):
        text = (
            "![](/alpha.jpg)\n\nVIP\n\nAlpha film\n\nA complete first description.\n\n"
            "Alpha film\n\n![](/beta.jpg)\n\nBeta film\n\n"
            "A complete second description.\n\nBeta film"
        )
        result = self.check(text, "name_description")
        self.assertTrue(result["stable_task_pass"], result)
        self.assertEqual(
            [record["fields"]["name"] for record in result["stable_records"]],
            ["Alpha film", "Beta film"],
        )

    def test_unowned_or_cross_image_text_cannot_form_description_cards(self):
        for text in (
            "Home\n\nNavigation summary.\n\nHome\n\nAbout\n\nCompany summary.\n\nAbout",
            "![](/alpha.jpg)\n\nAlpha film\n\n![](/beta.jpg)\n\n"
            "A complete first description.\n\nAlpha film\n\nBeta film",
            "![](/same-1.jpg)\n\nSame film\n\nFirst description.\n\nSame film\n\n"
            "![](/same-2.jpg)\n\nSame film\n\nSecond description.\n\nSame film",
        ):
            self.assertFalse(self.check(text, "name_description")["stable_task_pass"], text)

    def test_scoped_image_cards_are_not_parsed_twice(self):
        text = (
            "![](/alpha.jpg)\n\nAlpha film\n\nA complete first description.\n\n"
            "Alpha film\n\n![](/beta.jpg)\n\nBeta film\n\n"
            "A complete second description.\n\nBeta film"
        )
        result = evaluate_stable_task(
            text,
            contract("name_description", 2),
            "https://example.test/catalog",
            allow_closed_title_cards=True,
        )
        self.assertTrue(result["stable_task_pass"], result)
        self.assertEqual(result["stable_unique_record_count"], 2)
        self.assertEqual(len(result["stable_records"]), 2)

    def test_named_link_page_anchor_accepts_dropped_inline_link_spacing(self):
        text = ('- Do you have a question? Check out[/r/learnprogramming](/r/learnprogramming),'
                '[/r/cscareerquestions](/r/cscareerquestions), or[Stack Overflow](https://stackoverflow.com/).\n'
                '- [First post](https://example.com/r/programming/comments/1)\n'
                '- [Second post](https://example.com/r/programming/comments/2)\n'
                '- [Third post](https://example.com/r/programming/comments/3)')
        anchors = [{"id": "community", "alternatives": [
            'Do you have a question? Check out /r/learnprogramming, /r/cscareerquestions, or Stack Overflow.'
        ]}]
        result = self.check(text, "named_link", 3, stable_text_anchors=anchors,
                            hosts=["example.com"], entry_path=r"/r/programming/comments/\d+")
        self.assertTrue(result["stable_task_pass"], result)

    def test_quote_anchors_match_owned_fields_and_preserve_tag_boundaries(self):
        anchors = [{"id": "fixed", "alternatives": ['“An independent thought.”\nby Some Author\nTags: first second']}]
        texts = ('“An independent thought.”by [Some Author](/author) [(about)](/about)\n'
                 'Tags: [first](/tag/1)[second](/tag/2)\n##### Loading...',
                 '| Quote | Author | Tags |\n| --- | --- | --- |\n'
                 '| An independent thought. | Some Author | first second |')
        for text in texts:
            self.assertTrue(self.check(text, "quote", 1, stable_text_anchors=anchors)["stable_task_pass"])
        compact = self.check('“An independent thought.”by Some Author\nTags: firstsecond',
                             "quote", 1, stable_text_anchors=anchors)
        self.assertTrue(compact["stable_structure_pass"])
        self.assertFalse(compact["stable_content_pass"])
        wrong = self.check('“An independent thought.”by Wrong Author\nTags: first second',
                           "quote", 1, stable_text_anchors=anchors)
        self.assertTrue(wrong["stable_structure_pass"])
        self.assertFalse(wrong["stable_content_pass"])

    def test_quote_tags_can_span_native_markdown_link_lines(self):
        anchors = [{"id": "fixed", "alternatives": [
            '“An independent thought.”\nby Some Author\nTags: first second']}]
        text = ('“An independent thought.”by Some Author\nTags: [first]()\n[second]()\n'
                '##### Loading...\nQuotes by: Example')
        result = self.check(text, "quote", 1, stable_text_anchors=anchors)
        self.assertTrue(result["stable_task_pass"], result)
        self.assertEqual(result["stable_records"][0]["fields"]["tags"], "first second")

    def test_quote_pagination_is_not_owned_by_the_last_tags_field(self):
        anchors = [{"id": "fixed", "alternatives": [
            '“A day without sunshine is like, you know, night.”\nby Steve Martin\nTags: humor obvious simile']}]
        text = ('“A day without sunshine is like, you know, night.”\n'
                'by Steve Martin\nTags: humor obvious simile\nNext →')
        result = self.check(text, "quote", 1, stable_text_anchors=anchors)
        self.assertTrue(result["stable_task_pass"], result)
        self.assertEqual(result["stable_records"][0]["fields"]["tags"],
                         "humor obvious simile")

    def test_minimum_only_contract_ignores_unrelated_incomplete_candidate(self):
        rules = {"version": 1, "schema": "quote", "minimum_records": 1}
        text = ('" + template[\'quote\'] + " by " + template[\'author\'] + "\n'
                '“A real quote.” by Real Author Tags: one two')
        result = evaluate_stable_task(text, rules)
        self.assertTrue(result["stable_task_pass"], result)
        self.assertIn("ignored_incomplete_candidates", result["stable_record_warnings"])

    def test_tuple_anchor_cannot_match_unowned_footer_or_other_record(self):
        anchors = [{"id": "fixed", "alternatives": ["New phone\nA brighter display."]}]
        text = ("New phone A brighter display.\n\n"
                "- [New phone](/a) A different camera.\n- [Other phone](/b) A brighter display.")
        result = self.check(text, "name_description", stable_text_anchors=anchors)
        self.assertTrue(result["stable_structure_pass"])
        self.assertFalse(result["stable_content_pass"])

    def test_named_links_can_use_declared_query_identity(self):
        rules = {
            "hosts": ["charts.example"], "entry_path": r"/song/detail\.htm",
            "identity_query_keys": ["songId"],
        }
        text = ("[First song](https://charts.example/song/detail.htm?songId=1)\n"
                "[Second song](https://charts.example/song/detail.htm?songId=2)")
        self.assertTrue(self.check(text, "named_link", **rules)["stable_task_pass"])
        rules.pop("identity_query_keys")
        self.assertFalse(self.check(text, "named_link", **rules)["stable_task_pass"])

    def test_linked_products_require_owned_price_and_detail_path(self):
        rules = {
            "hosts": ["shop.example"], "entry_path": r"/products/[a-z]+",
            "price_patterns": [r"\$\s*\d+(?:\.\d{2})?"],
            "price_exclusion_patterns": [],
        }
        good = ("[Alpha](https://shop.example/products/alpha)\n$10.00\n\n"
                "[Beta](https://shop.example/products/beta)\n$20.00")
        self.assertTrue(self.check(good, "linked_product", 2, **rules)["stable_task_pass"])
        self.assertFalse(self.check(good.replace("$20.00", "20 reviews"),
                                    "linked_product", 2, **rules)["stable_task_pass"])

    def test_linked_product_query_identity_failure_does_not_discard_valid_sibling(self):
        rules = {
            "hosts": ["shop.example"], "entry_path": r"/product",
            "identity_query_keys": ["id"],
            "price_patterns": [r"\$\s*\d+(?:\.\d{2})?"],
            "price_exclusion_patterns": [],
        }
        mixed = ("[Missing identity](https://shop.example/product)\n$10.00\n\n"
                 "[Valid product](https://shop.example/product?id=2)\n$20.00")
        result = self.check(mixed, "linked_product", 1, **rules)
        self.assertTrue(result["stable_task_pass"], result)
        self.assertEqual(result["stable_complete_record_count"], 1)
        invalid = self.check(
            "[Missing identity](https://shop.example/product)\n$10.00",
            "linked_product", 1, **rules,
        )
        self.assertFalse(invalid["stable_task_pass"])
        self.assertEqual(invalid["stable_complete_record_count"], 0)

    def test_page_identity_uses_anchors_without_fake_records(self):
        rules = {
            "version": 1, "schema": "page_identity", "minimum_records": 0,
            "stable_text_anchors": [
                {"id": "brand", "alternatives": ["Example Search"]},
                {"id": "control", "alternatives": ["Search the web"]},
            ],
        }
        result = evaluate_stable_task("Example Search\nSearch the web", rules)
        self.assertTrue(result["stable_task_pass"], result)
        self.assertEqual(result["stable_record_count"], 0)


if __name__ == "__main__":
    unittest.main()
