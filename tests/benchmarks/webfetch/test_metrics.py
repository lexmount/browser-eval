import unittest

from lexbench_webfetch.metrics import evaluate_content, evaluate_resources, normalize_for_match, normalize_resource_url, summarize, task_passed, word_overlap


class MetricsTest(unittest.TestCase):
    def test_entity_decoding_preserves_markdown_anchor_boundaries(self):
        contract = {"version": 1, "schema": "body_text", "minimum_records": 0,
                    "stable_text_anchors": [{"id": "help", "alternatives": ["Help"]},
                                            {"id": "plugins", "alternatives": ["Plugins"]}]}
        positive = "A &amp; B [Images](/images)[Plugins](/plugins)Deep research [Help](/help)Settings"
        self.assertTrue(evaluate_content(positive, [], acceptance_contract=contract)["stable_task_pass"])
        negative = "A &amp; B Helpful settings and PluginsExtra"
        self.assertFalse(evaluate_content(negative, [], acceptance_contract=contract)["stable_task_pass"])

    def test_decoded_markdown_anchor_keeps_emphasis_and_links_equivalent(self):
        contract = {"version": 1, "schema": "body_text", "minimum_records": 0,
                    "stable_text_anchors": [{"id": "purpose", "alternatives": ["the official resource for the Team Fortress series"]}]}
        positive = "A &amp; B the official resource for the *[Team Fortress](/series)* series."
        self.assertTrue(evaluate_content(positive, [], acceptance_contract=contract)["stable_task_pass"])
        self.assertFalse(evaluate_content(positive.replace("official resource", "unrelated material"), [], acceptance_contract=contract)["stable_task_pass"])

    def test_html_required_pattern_counts_only_visible_prose(self):
        rule = [{"id": "order-number", "pattern": r"Order ID \d+", "minimum": 1}]
        hidden = "<html><body><script>Order ID 123</script><p>Page ready</p></body></html>"
        visible = "<html><body><p>Order ID 123</p></body></html>"
        self.assertFalse(evaluate_content(hidden, [], required_patterns=rule)["content_pass"])
        self.assertTrue(evaluate_content(visible, [], required_patterns=rule)["content_pass"])

    def test_required_result_section_remains_an_acceptance_gate_with_stable_rules(self):
        contract = {
            "version": 1,
            "schema": "body_text",
            "minimum_records": 0,
            "stable_text_anchors": [{
                "id": "page-section",
                "alternatives": ["Result"],
            }],
        }
        rules = [{
            "id": "rendered-result",
            "pattern": r"(?is)\bResult\b\s+(?!Loading\b).*?\bName\b.*?\bJohn\b",
        }]
        loading = evaluate_content(
            "const rows = ['John'];\nResult Loading...",
            [],
            acceptance_contract=contract,
            required_patterns=rules,
        )
        rendered = evaluate_content(
            "Result\nName\nJohn",
            [],
            acceptance_contract=contract,
            required_patterns=rules,
        )
        self.assertTrue(loading["stable_task_pass"])
        self.assertFalse(loading["content_pass"])
        self.assertFalse(task_passed(True, loading, "body"))
        self.assertTrue(rendered["content_pass"])
        self.assertTrue(task_passed(True, rendered, "body"))

    def test_only_pure_markdown_table_rules_are_layout(self):
        divider = 'December 2007\n| Sun | Mon | Tue |\n| :---- | ---: | --- |'
        self.assertEqual(normalize_for_match(divider), 'december 2007 sun mon tue')
        # A mixed row is data, and a non-table prose separator is retained.
        self.assertEqual(normalize_for_match('Alpha\n| --- | BAD | --- |\nBeta'),
                         'alpha | --- | bad | --- | beta')
        self.assertEqual(normalize_for_match('Company | Address | Contact'),
                         'company | address | contact')
        self.assertEqual(normalize_for_match('Alpha\n---\nBeta'), 'alpha --- beta')

    def test_adjacent_markdown_links_keep_distinct_label_boundaries(self):
        text='[change](https://example.com/a(b))[deep-thoughts](https://example.com)[thinking](u)[world](v)'
        reference='change deep-thoughts thinking world'
        self.assertEqual(normalize_for_match(text),reference)
        self.assertEqual(word_overlap(text,reference),(1.0,1.0,1.0))
        self.assertTrue(evaluate_content(text,[],required_facts=[{'id':'tags','alternatives':[reference]}])['content_pass'])
        # No guessing where boundaries were already lost in plaintext.
        self.assertFalse(evaluate_content('changedeep-thoughtsthinkingworld',[],required_facts=[{'id':'tags','alternatives':[reference]}])['content_pass'])
        self.assertEqual(normalize_for_match('pre[fix](u)'),'prefix')
        self.assertEqual(normalize_for_match('[read](u)able'),'readable')
        self.assertEqual(normalize_for_match('[5](u)[1](v)'),'51')
        self.assertFalse(evaluate_content('[change](u)[world](v)',[],required_facts=[{'id':'tags','alternatives':[reference]}])['content_pass'])

    def test_native_markdown_link_boundaries_preserve_visible_prose(self):
        content = ('Do you have a question? Check out[/r/learnprogramming](/r/learnprogramming),'
                   '[/r/cscareerquestions](/r/cscareerquestions), or[Stack Overflow](https://stackoverflow.com/).')
        expected = 'Do you have a question? Check out /r/learnprogramming, /r/cscareerquestions, or Stack Overflow.'
        self.assertEqual(normalize_for_match(content), normalize_for_match(expected))
        self.assertTrue(evaluate_content(
            content, [], required_facts=[{'id':'community','alternatives':[expected]}]
        )['content_pass'])
        self.assertEqual(normalize_for_match('pre[fix](u)'), 'prefix')
        self.assertEqual(normalize_for_match('[read](u)able'), 'readable')

    def test_substantial_replacement_character_damage_fails_text_output(self):
        result = evaluate_content('valid heading\n' + '\ufffd' * 25, ['valid heading'])
        self.assertTrue(result['text_encoding_corruption'])
        self.assertEqual(result['replacement_character_count'], 25)
        self.assertFalse(result['content_pass'])

    def test_stable_anchors_inside_minified_script_state_are_not_readable_body(self):
        contract = {
            'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'stable_text_anchors': [
                {'id': 'answer', 'alternatives': ['Required readable answer']},
            ],
        }
        bundle = ''.join(
            f'window.__STATE{i}__={{"text":"Required readable answer",'
            f'"payload":"{"x" * 900}"}};'
            for i in range(12)
        ) + '\n' + '\n'.join(f'//# sourceURL=/bundle-{i}.js' for i in range(12))
        result = evaluate_content(bundle, [], acceptance_contract=contract)
        self.assertTrue(result['script_payload_shell'])
        self.assertFalse(result['stable_task_pass'])
        self.assertIn('script_payload_shell', result['stable_task_errors'])

    def test_adjacent_numeric_links_cannot_invent_positive_number_hits(self):
        for text, visible in [('[-](u)[10](v)','-10'),('[10](u)[.5](v)','10.5'),
                              ('[10](u)[0](v)','100'),('[+](u)[10](v)','+10')]:
            with self.subTest(text=text):
                self.assertEqual(normalize_for_match(text),visible)
                self.assertFalse(evaluate_content(text,[],required_facts=[{'id':'number','alternatives':['10']}])['content_pass'])

    def test_markdown_balanced_urls_and_f1_share_layout_cleaning(self):
        raw='hello [world](https://example.com/a(b)) end'
        self.assertEqual(normalize_for_match(raw),'hello world end')
        self.assertEqual(word_overlap(raw,'hello world end'),(1.0,1.0,1.0))
        self.assertEqual(word_overlap('inter\u00adnational','international'),(1.0,1.0,1.0))
    def test_visible_text_formatting_without_fuzzy_semantics(self):
        reference='进入飞书管理后台，点击 AI 使用数据。'
        for text in ['进入[飞书管理后台](https://example.com)，点击 **AI** 使用数据。','进入飞书管理后台，点击AI使用数据。','进入飞书\n管理后台，点击 `AI` 使用数据。']:
            self.assertTrue(evaluate_content(text,[],required_facts=[{'id':'fact','alternatives':[reference]}])['content_pass'])
        for text,required in [('通过','不通过'),('10.5%','-10.5%'),('10','100'),('100','10'),('-10','10'),('10.5','10'),('therapist','the rapist')]:
            self.assertFalse(evaluate_content(text,[],required_facts=[{'id':'fact','alternatives':[required]}])['content_pass'])
        self.assertEqual(normalize_for_match('2 ** 3 ** 4'),'2 ** 3 ** 4')
    def test_invisible_layout_characters_are_not_required_facts(self):
        for char in ['\u200b','\ufeff','\u00ad']:
            for content,reference in [('进入后台，点击 AI 使用数据。','进入后台，点击 AI 使用数据。'+char),('增值'+char+'信息','增值信息')]:
                with self.subTest(char=repr(char)):
                    result=evaluate_content(content,[],required_facts=[{'id':'fact','alternatives':[reference]}])
                    self.assertTrue(result['content_pass'])
        self.assertFalse(evaluate_content('进入后台。',[],required_facts=[{'id':'fact','alternatives':['进入后台，点击 AI 使用数据。\u200b']}])['content_pass'])
        self.assertFalse(evaluate_content('anything',[],required_facts=[{'id':'empty','alternatives':['\u200b']}])['content_pass'])
        self.assertNotEqual(normalize_for_match('a\u200db'),normalize_for_match('ab'))
    def test_word_overlap_penalizes_missing_and_boilerplate_words(self) -> None:
        precision, recall, score = word_overlap("alpha beta beta navigation", "alpha beta beta gamma")
        self.assertEqual((precision, recall), (0.75, 0.75))
        self.assertEqual(score, 0.75)

    def test_matching_normalizes_whitespace_and_typography(self) -> None:
        self.assertEqual(normalize_for_match("A\nB — it’s ready"), "a b - it's ready")
        result = evaluate_content(
            "A B - it's ready", ["A\nB — it’s ready"],
            required_facts=[{"id": "normalized", "alternatives": ["A\nB — it’s ready"]}],
        )
        self.assertTrue(result["content_pass"])
        self.assertNotEqual(
            normalize_for_match('"enum": [\n  "option 1",\n  "option 2"\n],'),
            normalize_for_match('"enum":["option 1","option 2"],'),
        )
    def test_content_metrics_are_deterministic(self) -> None:
        result = evaluate_content("# Title\n[Link](https://example.com) Alpha", ["Alpha", "Beta"])
        self.assertTrue(result["content_pass"])
        self.assertEqual(result["marker_hits"], 1)
        self.assertEqual(result["marker_recall"], 0.5)
        self.assertEqual(result["markdown_headings"], 1)
        self.assertEqual(result["markdown_links"], 1)

    def test_ground_truth_requires_every_fact_and_keeps_prompt_diagnostics(self) -> None:
        facts = [
            {"id": "title", "alternatives": ["Example Domain"]},
            {"id": "purpose", "alternatives": ["documentation examples"]},
        ]
        partial = evaluate_content("Example Domain", ["Example Domain"], required_facts=facts)
        self.assertFalse(partial["content_pass"])
        self.assertEqual(partial["fact_coverage"], 0.5)
        blocked = evaluate_content(
            "Example Domain documentation examples Access denied",
            ["Example Domain"], required_facts=facts, diagnostic_any=["Access denied"],
        )
        self.assertTrue(blocked["content_pass"])
        self.assertEqual(blocked["diagnostic_hits"], ["Access denied"])

    def test_summary_separates_transport_and_content(self) -> None:
        base = {
            "provider": "p",
            "provider_type": "fixture",
            "case_id": "c",
            "case_name": "Case",
            "category": "static",
            "elapsed_ms": 100,
            "error_type": None,
        }
        records = [
            {**base, "ok": True, "metrics": {"content_pass": True, "marker_recall": 1.0, "content_chars": 10}},
            {**base, "ok": True, "elapsed_ms": 900, "metrics": {"content_pass": False, "marker_recall": 0.0, "content_chars": 5}},
        ]
        item = summarize(records)["providers"]["p"]
        self.assertEqual(item["transport_success_rate"], 1.0)
        self.assertEqual(item["task_pass_rate"], 0.5)
        self.assertEqual(item["failure_types"], {"content_validation_failure": 1})
        self.assertEqual(item["mean_content_ms"], 100)
        self.assertEqual(item["p50_ms"], 100)
        self.assertEqual(item["transport_p95_ms"], 900)

    def test_resource_discovery_uses_returned_urls_not_request_url(self) -> None:
        expected = [{
            "id": "video-mp4",
            "kind": "video",
            "urls": ["rabbit320.mp4"],
            "mime_types": ["video/mp4"],
        }]
        absent = evaluate_resources("", {}, expected, base_url="https://example.test/page.html")
        self.assertEqual(absent["resource_recall"], 0.0)
        present = evaluate_resources(
            "[fallback](rabbit320.mp4)",
            {"media": {"videos": [{"src": "./rabbit320.mp4"}]}},
            expected,
            base_url="https://example.test/page.html",
        )
        self.assertEqual(present["resource_recall"], 1.0)
        self.assertEqual(present["resource_results"][0]["matched_urls"], ["https://example.test/rabbit320.mp4"])
        failed = evaluate_resources(
            'error for https://example.test/rabbit320.mp4', {}, expected,
            base_url="https://example.test/page.html", eligible=False,
        )
        self.assertEqual(failed["resource_recall"], 0.0)

    def test_resource_discovery_unescapes_commonmark_url_punctuation(self) -> None:
        result = evaluate_resources(
            r'<img src="pic\_trulli.jpg">', {},
            [{"id": "image", "kind": "image", "urls": ["pic_trulli.jpg"]}],
            base_url="https://example.test/gallery/page.html",
        )
        self.assertEqual(result["resource_recall"], 1.0)
        self.assertEqual(result["resource_results"][0]["matched_urls"],
                         ["https://example.test/gallery/pic_trulli.jpg"])

    def test_encoded_relative_file_autolink_resolves_against_source_page(self) -> None:
        expected = 'https://download.example.test/files/bbb_sunflower.mp4.zip'
        result = evaluate_resources(
            '<bbb%5Fsunflower.mp4.zip>', {},
            [{"id":"archive","kind":"video-archive-link","urls":[expected]}],
            base_url='https://download.example.test/files/',
        )
        self.assertEqual(result['resource_results'][0]['matched_urls'], [expected])
        self.assertTrue(result['resource_pass'])
        # A filename in prose is not enough to prove that a resource address
        # was returned; this correction is limited to angle-delimited targets.
        prose = evaluate_resources(
            'The archive is named bbb_sunflower.mp4.zip.', {},
            [{"id":"archive","kind":"video-archive-link","urls":[expected]}],
            base_url='https://download.example.test/files/',
        )
        self.assertFalse(prose['resource_pass'])

    def test_visible_matching_ignores_markdown_link_spacing_before_punctuation(self) -> None:
        content = "Read [the guide](/guide) , then [continue](/next) ."
        required = "Read the guide, then continue."
        self.assertTrue(evaluate_content(
            content, [], required_facts=[{"id": "sentence", "alternatives": [required]}]
        )["content_pass"])

    def test_malformed_resource_candidate_does_not_abort_content_evaluation(self) -> None:
        result = evaluate_resources(
            "See http://[broken and https://example.test/valid.png",
            {"images": ["http://[also-broken", "/valid.png"]},
            [{"id": "image", "kind": "image", "urls": ["/valid.png"]}],
            base_url="https://example.test/page.html",
        )
        self.assertEqual(result["resource_recall"], 1.0)
        self.assertEqual(result["resource_candidate_urls"], ["https://example.test/valid.png"])

    def test_target_only_image_contract_rejects_non_target_with_provenance(self) -> None:
        result = evaluate_resources(
            "![Target](/target.jpg)",
            {"images": [{"src": "/target.jpg", "alt": "Target photo"}, "/logo.svg"]},
            [{"id": "target", "kind": "image", "urls": ["/target.jpg"]}],
            base_url="https://example.test/gallery",
            output_contract={"candidate_scope": "image_answer_fields", "unexpected": "reject"},
        )
        self.assertEqual(result["resource_recall"], 1.0)
        self.assertEqual(result["resource_precision"], 0.5)
        self.assertEqual(result["unexpected_resource_urls"], ["https://example.test/logo.svg"])
        self.assertFalse(result["resource_pass"])
        self.assertEqual(result["resource_candidates"], [
            {"url": "https://example.test/logo.svg", "sources": ["metadata.images"]},
            {"url": "https://example.test/target.jpg",
             "sources": ["content.markdown_image", "metadata.images.src"]},
        ])

    def test_target_only_image_contract_parses_img_src_and_srcset(self) -> None:
        result = evaluate_resources(
            '<img src="/target.jpg" srcset="/target.jpg 1x, /logo.png 2x">',
            {},
            [{"id": "target", "kind": "image", "urls": ["/target.jpg"]}],
            base_url="https://example.test/gallery",
            output_contract={"candidate_scope": "image_answer_fields", "unexpected": "reject"},
        )
        self.assertEqual(result["resource_recall"], 1.0)
        self.assertEqual(result["resource_precision"], 0.5)
        self.assertEqual(result["unexpected_resource_urls"],
                         ["https://example.test/logo.png"])
        self.assertFalse(result["resource_pass"])
        by_url = {row["url"]: row["sources"] for row in result["resource_candidates"]}
        self.assertEqual(by_url["https://example.test/target.jpg"],
                         ["content.html_img", "content.html_img_srcset"])
        self.assertEqual(by_url["https://example.test/logo.png"],
                         ["content.html_img_srcset"])

    def test_resource_discovery_reads_only_visible_inline_frame_documents(self) -> None:
        content = (
            '<iframe id="result" data-moli-frame-url="about:blank" '
            'srcdoc="&lt;img src=&quot;pic_trulli.jpg&quot;&gt;"></iframe>'
            '<iframe style="display:none" srcdoc="&lt;img src=&quot;ad.jpg&quot;&gt;"></iframe>'
        )
        result = evaluate_resources(
            content,
            {},
            [{"id": "target", "kind": "image", "urls": ["/html/pic_trulli.jpg"]}],
            base_url="https://www.w3schools.com/html/tryit.asp?filename=images",
            output_contract={"candidate_scope": "image_answer_fields", "unexpected": "reject"},
        )
        self.assertTrue(result["resource_pass"])
        self.assertEqual(
            result["resource_candidate_urls"],
            ["https://www.w3schools.com/html/pic_trulli.jpg"],
        )
        self.assertEqual(
            result["resource_candidates"][0]["sources"],
            ["content.frame_html_img"],
        )

    def test_invalid_target_only_contract_fails_closed(self) -> None:
        invalid = (
            ({"candidate_scope": "all", "unexpected": "reject"},
             "Unsupported resource candidate_scope"),
            ({"candidate_scope": "image_answer_fields", "unexpected": "drop"},
             "Unsupported resource unexpected policy"),
        )
        for contract, message in invalid:
            with self.subTest(contract=contract):
                with self.assertRaisesRegex(ValueError, message):
                    evaluate_resources(
                        "![Target](/target.jpg)", {},
                        [{"id": "target", "urls": ["/target.jpg"]}],
                        base_url="https://example.test/gallery",
                        output_contract=contract,
                    )

    def test_image_scope_ignores_ordinary_page_links(self) -> None:
        result = evaluate_resources(
            "[Article](https://example.test/article)\n"
            "<a href='https://example.test/related'>Related</a>\n"
            "![Target](https://cdn.example.test/target.webp)",
            {"links": ["https://example.test/navigation"],
             "extras": {"links": ["https://example.test/card"]}},
            [{"id": "target", "kind": "image", "urls": ["https://cdn.example.test/target.webp"]}],
            base_url="https://example.test/gallery",
            output_contract={"candidate_scope": "image_answer_fields", "unexpected": "reject"},
        )
        self.assertTrue(result["resource_pass"])
        self.assertEqual(result["resource_precision"], 1.0)
        self.assertEqual(result["resource_candidate_urls"], ["https://cdn.example.test/target.webp"])
        self.assertEqual(result["unexpected_resource_urls"], [])

    def test_image_scope_accepts_declared_alias_pattern_and_thumbnail_alias(self) -> None:
        contract = {"candidate_scope": "image_answer_fields", "unexpected": "reject"}
        alias = evaluate_resources(
            "", {"images": ["/photo-480w.jpg"]},
            [{"id": "photo", "urls": ["/photo-800w.jpg"], "aliases": ["/photo-480w.jpg"]}],
            base_url="https://example.test/gallery/", output_contract=contract,
        )
        self.assertTrue(alias["resource_pass"])
        self.assertEqual(alias["resource_precision"], 1.0)
        pattern = evaluate_resources(
            "", {"extras": {"imageLinks": ["https://cdn.example.test/photo_640.jpg"]}},
            [{"id": "photo", "url_patterns": [r"/photo_\d+\.jpg$"]}],
            base_url="https://example.test/gallery/", output_contract=contract,
        )
        self.assertTrue(pattern["resource_pass"])
        thumb = evaluate_resources(
            "", {"media": {"images": [{"src":
                "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/Earth.jpg/320px-Earth.jpg"}]}},
            [{"id": "earth", "urls": [
                "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/Earth.jpg/1280px-Earth.jpg"
            ], "aliases": [
                "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/Earth.jpg/320px-Earth.jpg"
            ]}], base_url="https://commons.wikimedia.org/wiki/File:Earth.jpg",
            output_contract=contract,
        )
        self.assertTrue(thumb["resource_pass"])

    def test_wikimedia_host_equivalence_is_only_for_the_same_thumbnail_asset(self) -> None:
        base = 'https://en.wikipedia.org/wiki/Apollo_11'
        upload_thumb = ('https://upload.wikimedia.org/wikipedia/commons/thumb/4/41/'
                        'Moon.jpg/330px-Moon.jpg?utm_source=en.wikipedia.org')
        migrated_thumb = ('https://thumb.wikimedia.org/wikipedia/commons/thumb/4/41/'
                          'Moon.jpg/960px-Moon.jpg?utm_campaign=parser')
        wrong_asset = ('https://thumb.wikimedia.org/wikipedia/commons/thumb/4/42/'
                       'Moon.jpg/960px-Moon.jpg?utm_campaign=parser')
        invalid_original = 'https://thumb.wikimedia.org/wikipedia/commons/4/41/Moon.jpg'
        upload_original = 'https://upload.wikimedia.org/wikipedia/commons/4/41/Moon.jpg'
        self.assertNotEqual(normalize_resource_url(upload_thumb, base),
                            normalize_resource_url(migrated_thumb, base))
        declared = [{'id': 'moon', 'urls': [upload_thumb], 'aliases': [migrated_thumb]}]
        self.assertTrue(evaluate_resources(migrated_thumb, {}, declared,
            base_url=base)['resource_pass'])
        self.assertFalse(evaluate_resources(wrong_asset, {}, declared,
            base_url=base)['resource_pass'])
        self.assertNotEqual(normalize_resource_url(upload_thumb, base),
                            normalize_resource_url(wrong_asset, base))
        self.assertNotEqual(normalize_resource_url(invalid_original, base),
                            normalize_resource_url(upload_original, base))

    def test_image_scope_rejects_extra_bare_extensionless_url(self) -> None:
        target = "https://images.unsplash.com/photo-1493673272479-a20888bcee10"
        result = evaluate_resources(
            target + "\nhttps://example.test/ordinary-page", {},
            [{"id": "photo", "urls": [target]}],
            base_url="https://unsplash.com/photos/example",
            output_contract={"candidate_scope": "image_answer_fields", "unexpected": "reject"},
        )
        self.assertFalse(result["resource_pass"])
        self.assertEqual(result["resource_candidate_urls"],
                         ["https://example.test/ordinary-page", target])
        self.assertEqual(result["resource_precision"], 0.5)

    def test_resource_queries_are_not_merged_by_known_host(self):
        base = 'https://example.test/gallery'
        for host in ('images.unsplash.com', 'images.pexels.com', 'images-assets.nasa.gov',
                     'www.nasa.gov', 'cdn.example.test'):
            with self.subTest(host=host):
                left, right = f'https://{host}/photo.jpg?crop=left', f'https://{host}/photo.jpg?crop=right'
                self.assertNotEqual(normalize_resource_url(left, base), normalize_resource_url(right, base))
                self.assertFalse(evaluate_resources(right, {}, [{'id': 'photo', 'urls': [left]}],
                    base_url=base)['resource_pass'])
                self.assertTrue(evaluate_resources(right, {}, [{'id': 'photo', 'urls': [left],
                    'aliases': [right]}], base_url=base)['resource_pass'])

    def test_extensionless_image_candidates_do_not_depend_on_reference(self):
        right, wrong = 'https://cdn.example/item/RIGHT', 'https://cdn.example/item/WRONG'
        contract = {'candidate_scope': 'image_answer_fields', 'unexpected': 'reject'}
        mixed = right + '\n' + wrong
        candidates = []
        for target in (right, wrong):
            result = evaluate_resources(mixed, {}, [{'id': 'image', 'urls': [target]}],
                base_url='https://example.test/', output_contract=contract)
            self.assertFalse(result['resource_pass'])
            self.assertEqual(result['resource_precision'], 0.5)
            candidates.append(result['resource_candidate_urls'])
        self.assertEqual(candidates[0], candidates[1])
        result = evaluate_resources(right, {}, [{'id': 'image', 'urls': [right]}],
            base_url='https://example.test/', output_contract=contract)
        self.assertTrue(result['resource_pass'])
        self.assertEqual(result['resource_precision'], 1.0)


if __name__ == "__main__":
    unittest.main()
