import unittest
from lexbench_webfetch.dynamic_lists import evaluate_dynamic_list, extract_linked_products, extract_named_entries


class DynamicListTests(unittest.TestCase):
    def setUp(self):
        self.contract = {"hosts": ["example.com"], "entry_path": r"/item/\d+", "minimum_entries": 3}

    def test_named_table_columns_must_be_complete_and_distinct(self):
        from lexbench_webfetch.stable_tasks import validate_acceptance_contract
        for columns in (
            {"markdown_title_column": "Name"},
            {"markdown_link_column": "Details"},
            {"markdown_title_column": "Name", "markdown_link_column": "Name"},
        ):
            with self.subTest(columns=columns):
                contract = {
                    "version": 1, "schema": "named_link", "minimum_records": 1,
                    "hosts": ["example.com"], "entry_path": r"/item/\d+",
                    "stable_text_anchors": [],
                    "entry_scope": {"html_selector": "tr[data-item]", **columns},
                }
                with self.assertRaisesRegex(ValueError, "distinct title and link columns"):
                    validate_acceptance_contract(contract)

    def test_declared_identity_deduplicates_tracking_paths_for_same_entity(self):
        contract = {
            "hosts": ["example.com"],
            "entry_path": r"/thing/[A-Z0-9]{4}(?:/ref=[^/?#]+)?",
            "entry_identity_pattern": r"/thing/([A-Z0-9]{4})(?:/|$)",
            "minimum_entries": 2,
        }
        content = "\n".join(
            [
                "[First](https://example.com/thing/AB12/ref=one)",
                "[First options](https://example.com/thing/AB12/ref=two)",
                "[Second](https://example.com/thing/CD34/ref=one)",
            ]
        )
        measured = evaluate_dynamic_list(content, contract, "https://example.com")
        self.assertEqual(measured["named_entry_count"], 2)
        self.assertTrue(measured["structure_pass"])

    def test_declared_query_identity_ignores_tracking_and_rejects_ambiguous_values(self):
        contract = {
            "hosts": ["example.com"],
            "entry_path": r"/item\.htm",
            "identity_query_keys": ["id", "v"],
            "minimum_entries": 2,
        }
        content = "\n".join([
            "[One](https://example.com/item.htm?id=1&tracking=a)",
            "[One again](https://example.com/item.htm?tracking=b&id=1)",
            "[Two](https://example.com/item.htm?v=2)",
            "[Missing](https://example.com/item.htm?tracking=c)",
            "[Ambiguous](https://example.com/item.htm?id=3&id=4)",
            "[Malformed](http://[broken)",
        ])
        entries = extract_named_entries(content, contract, "https://example.com/")
        self.assertEqual({entry["title"] for entry in entries}, {"One again", "Two"})

    def test_malformed_candidate_is_reported_without_discarding_valid_siblings(self):
        diagnostics = []
        entries = extract_named_entries(
            "[Broken](https://[invalid/item/1)\n[Valid](/item/2)",
            self.contract,
            "https://example.com/",
            diagnostics=diagnostics,
        )
        self.assertEqual(entries, [{"url": "https://example.com/item/2", "title": "Valid"}])
        self.assertEqual(diagnostics, ["invalid_link_output"])

    def test_scoped_product_parser_reuses_validated_query_identity(self):
        contract = {
            "hosts": ["example.com"], "entry_path": r"/product",
            "identity_query_keys": ["id"],
            "price_patterns": [r"\$\d+\.\d{2}"],
            "price_exclusion_patterns": [],
            "entry_scope": {
                "html_selector": ".product", "title_selector": ".name",
                "link_selector": "a.detail", "price_selector": ".price",
            },
        }
        content = (
            '<div class="product"><h2 class="name">Invalid</h2>'
            '<a class="detail" href="/product">Details</a>'
            '<span class="price">$10.00</span></div>'
            '<div class="product"><h2 class="name">Valid</h2>'
            '<a class="detail" href="/product?id=2">Details</a>'
            '<span class="price">$20.00</span></div>'
        )
        self.assertEqual(extract_linked_products(
            content, contract, "https://example.com/catalog",
        ), [{
            "url": "https://example.com/product?id=2",
            "title": "Valid", "price": "$20.00",
        }])

    def test_declared_link_base_resolves_relative_links_without_expanding_hosts(self):
        contract = {
            "hosts": ["www.example.com"],
            "entry_path": r"/news/item/\d+",
            "link_base_url": "https://www.example.com/news/",
            "minimum_entries": 1,
        }
        entries = extract_named_entries(
            '<a href="item/1">Story</a>', contract, "https://redirect.example.net/start"
        )
        self.assertEqual(entries, [{"url": "https://www.example.com/news/item/1", "title": "Story"}])
        self.assertEqual(extract_named_entries(
            '[Story](item/1)', contract, "https://redirect.example.net/start"), entries)
        self.assertEqual(extract_named_entries(
            '[Other](https://outside.example/news/item/2)', contract,
            "https://redirect.example.net/start"), [])
        self.assertEqual(extract_named_entries(
            'Read [Story](item/1) for context.', contract,
            "https://redirect.example.net/start"), [])

    def test_dense_flattened_link_collection_is_not_mistaken_for_prose(self):
        content = (
            '- Group - [First](https://example.com/item/1) - '
            '[Second](https://example.com/item/2) - '
            '[Third](https://example.com/item/3)'
        )
        self.assertEqual(
            {entry['title'] for entry in extract_named_entries(
                content, self.contract, 'https://example.com/'
            )},
            {'First', 'Second', 'Third'},
        )
        prose = (
            'Read [First](https://example.com/item/1), compare it carefully with '
            '[Second](https://example.com/item/2), then see '
            '[Third](https://example.com/item/3) for context.'
        )
        self.assertEqual(extract_named_entries(prose, self.contract, 'https://example.com/'), [])

    def test_image_alternative_and_unlinked_heading_do_not_name_a_record(self):
        content = ('[![First title](https://img.example/1.jpg)](/item/1)\n\n'
                   '## Different visible title\n\nDescription\n')
        self.assertEqual(extract_named_entries(content, self.contract, 'https://example.com/'), [])
        visible = content.replace('Different visible title', 'First title')
        self.assertEqual(extract_named_entries(visible, self.contract, 'https://example.com/'), [
            {'url': 'https://example.com/item/1', 'title': 'First title'}])

    def check(self, text, ok=True):
        return evaluate_dynamic_list(text, self.contract, "https://example.com", ok)

    def test_values_and_order_do_not_matter(self):
        for names in [("Alpha", "Beta", "Gamma"), ("全新条目三", "全新条目二", "全新条目一")]:
            r = self.check("\n".join(f"[{n}](/item/{i + 51})" for i, n in enumerate(names)))
            self.assertTrue(r["structure_pass"])
            self.assertIsNone(r["factual_correctness"])
            self.assertIsNone(r["snapshot_f1"])

    def test_no_url_only_padding_or_duplicates(self):
        self.assertFalse(
            self.check("[Details](/item/1)\n[Details](/item/2)\n[Details](/item/3)")[
                "structure_pass"
            ]
        )
        self.assertFalse(
            self.check("[A title](/item/1)\n[B title](/item/1?x=2)\n[C title](/item/1#x)")[
                "structure_pass"
            ]
        )

    def test_html_and_plain_text(self):
        self.assertFalse(
            self.check("".join(f'<a href="/item/{i}"><img alt="Title {i}"></a>' for i in range(3)))[
                "structure_pass"
            ]
        )
        self.assertFalse(
            self.check("\n".join(f"Title {i}\nhttps://example.com/item/{i}" for i in range(3)))[
                "structure_pass"
            ]
        )

    def test_wrong_host_and_failed_request(self):
        text = "\n".join(f"[Title {i}](https://evil.example/item/{i})" for i in range(3))
        self.assertFalse(self.check(text)["structure_pass"])
        self.assertFalse(
            self.check(text.replace("evil.example", "example.com"), False)["structure_pass"]
        )

    def test_nested_image_names_and_numeric_ranks(self):
        text = "\n".join(
            f"[![New title {i}](https://img.test/{i}.png) TV](/item/{i})" for i in range(3)
        )
        self.assertFalse(self.check(text)["structure_pass"])
        visible_names = "\n".join(
            f"[![Poster](https://img.test/{i}.png) Title {i}](/item/{i})" for i in range(3)
        )
        self.assertTrue(self.check(visible_names)["structure_pass"])
        accessible_image_links = "\n".join(
            f"[![New title {i}](https://img.test/{i}.png)](/item/{i})" for i in range(3)
        )
        self.assertFalse(self.check(accessible_image_links)["structure_pass"])
        text = "\n".join(f"- {i}\nhttps://example.com/item/{i}" for i in range(10))
        self.assertEqual(self.check(text)["named_entry_count"], 0)

    def test_adjacent_card_image_and_title_links_keep_named_record(self):
        text = "\n".join(
            f"{i}. [![Poster {i}](https://img.test/{i}.png)](/item/{i}) "
            f"[Title {i}](/item/{i})"
            for i in range(3)
        )
        result = self.check(text)
        self.assertTrue(result["structure_pass"])
        self.assertEqual(result["named_entry_count"], 3)

    def test_balanced_multiline_card_labels_own_their_relative_links(self):
        text = "\n".join(
            "[\nTop Rated\n\n## Product " + str(i) + "\n](/item/" + str(i) + ")" for i in range(3)
        )
        result = self.check(text)
        self.assertTrue(result["structure_pass"])
        self.assertEqual(result["named_entry_count"], 3)
        self.assertFalse(self.check("[\nUnclosed title\n(/item/1)")["structure_pass"])

    def test_prose_recommendations_are_not_list_cards(self):
        text = "Watch " + ", or ".join(f"[Title {i}](/item/{i})" for i in range(3)) + "."
        self.assertFalse(self.check(text)["structure_pass"])

    def test_plus_bullets_and_adjacent_multiline_cards_are_lists(self):
        bullets = "\n".join(f"  + [Title {i}](/item/{i})" for i in range(3))
        cards = " ".join(f"[Title {i}\nDetails](/item/{i})" for i in range(3))
        for text in (bullets, cards):
            with self.subTest(text=text):
                self.assertTrue(self.check(text)["structure_pass"])
                self.assertEqual(self.check(text)["named_entry_count"], 3)
        prose = " + consider ".join(f"[Title {i}\nDetails](/item/{i})" for i in range(3))
        self.assertFalse(self.check(prose)["structure_pass"])

    def test_preserve_access_query_and_scheme(self):
        entries = extract_named_entries(
            "[A title](/item/1?access_token=required&source=feed)",
            self.contract,
            "http://example.com",
        )
        self.assertEqual(
            entries,
            [
                {
                    "title": "A title",
                    "url": "http://example.com/item/1?access_token=required&source=feed",
                }
            ],
        )


def test_long_non_table_output_keeps_linear_processing_cost():
    import time
    from lexbench_webfetch.structured_tables import table_text_surface
    body = '\n'.join('Ordinary prose line number ' + str(i) for i in range(20000))
    started = time.monotonic()
    assert table_text_surface(body) == body
    assert time.monotonic() - started < 1.5
