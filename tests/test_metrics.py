import unittest

from harness.web_fetch.metrics import evaluate_content, evaluate_resources, normalize_for_match, summarize, word_overlap


class MetricsTest(unittest.TestCase):
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
    def test_content_metrics_are_deterministic(self) -> None:
        result = evaluate_content("# Title\n[Link](https://example.com) Alpha", ["Alpha", "Beta"])
        self.assertTrue(result["content_pass"])
        self.assertEqual(result["marker_hits"], 1)
        self.assertEqual(result["marker_recall"], 0.5)
        self.assertEqual(result["markdown_headings"], 1)
        self.assertEqual(result["markdown_links"], 1)

    def test_ground_truth_requires_every_fact_and_rejects_error_pages(self) -> None:
        facts = [
            {"id": "title", "alternatives": ["Example Domain"]},
            {"id": "purpose", "alternatives": ["documentation examples"]},
        ]
        partial = evaluate_content("Example Domain", ["Example Domain"], required_facts=facts)
        self.assertFalse(partial["content_pass"])
        self.assertEqual(partial["fact_coverage"], 0.5)
        blocked = evaluate_content(
            "Example Domain documentation examples Access denied",
            ["Example Domain"], required_facts=facts, forbidden_any=["Access denied"],
        )
        self.assertFalse(blocked["content_pass"])
        self.assertEqual(blocked["forbidden_hits"], ["Access denied"])

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
        self.assertEqual(item["content_pass_rate"], 0.5)
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

    def test_malformed_resource_candidate_does_not_abort_content_evaluation(self) -> None:
        result = evaluate_resources(
            "See http://[broken and https://example.test/valid.png",
            {"images": ["http://[also-broken", "/valid.png"]},
            [{"id": "image", "kind": "image", "urls": ["/valid.png"]}],
            base_url="https://example.test/page.html",
        )
        self.assertEqual(result["resource_recall"], 1.0)
        self.assertEqual(result["resource_candidate_urls"], ["https://example.test/valid.png"])


if __name__ == "__main__":
    unittest.main()
