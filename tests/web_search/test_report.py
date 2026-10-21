"""Public report boundaries: evidence integrity, localization, and failure drilldown."""

import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from harness.web_search.core import digest
from harness.web_search.example import example
from harness.web_search.report import build, check, fairness_table, load_run, release_check


class ReportScenarios(unittest.TestCase):
    def test_paired_option_median_handles_an_even_query_count(self):
        from harness.web_search.report import mode_panel
        profile = {"methods": [
            {"id": "base", "adapter": "deepseek", "label": "Base", "output_options": {"need_content": False}},
            {"id": "body", "adapter": "deepseek", "label": "Body", "output_options": {"need_content": True}},
        ]}
        rows = [
            {"case_id": case, "method": mode, "elapsed_ms": elapsed, "results": []}
            for case, mode, elapsed in [("a", "base", 100), ("a", "body", 110),
                                       ("b", "base", 100), ("b", "body", 130)]
        ]
        soup = BeautifulSoup(mode_panel(profile, rows, False), "html.parser")
        values = [td.get_text() for td in soup.select("tbody tr td")]
        self.assertEqual(values[3:5], ["20.00", "20.00"])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        example(self.root)
        self.out = self.root / "report"

    def test_public_labels_are_scored_for_every_query_and_visible_panel_is_bound(self):
        from harness.web_search.core import evaluate
        source = self.root / "fixture"
        profile_path = source / "profile.json"
        profile = json.loads(profile_path.read_text())
        for case in profile["cases"]:
            case.update(reference_answer="Fixture only", reference_urls=case["anchors"])
        profile_path.write_text(json.dumps(profile))
        rows_path = source / "results.jsonl"
        rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
        cases = {case["id"]: case for case in profile["cases"]}
        for row in rows:
            row["metrics"] = evaluate(cases[row["case_id"]], row["results"])
        rows_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        receipt_path = source / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt.update(profile_sha256=digest(profile_path.read_bytes()), results_sha256=digest(rows_path.read_bytes()))
        receipt_path.write_text(json.dumps(receipt))
        content = self.root / "content.json"
        content.write_text(json.dumps({"reading_model": True, "sections": [], "reading_sections": [
            {"key": "quality", "nav": {"en": "Quality", "zh": "质量"}, "title": {"en": "Quality", "zh": "质量"}}
        ]}))
        build(self.out, source=source, content=content)
        soup = BeautifulSoup((self.out / "index.html").read_text(), "html.parser")
        panel = soup.select_one('[data-factual]')
        self.assertIn('2/2', panel.get_text())
        self.assertIn('1/2', panel.get_text())  # failed query stays in denominator
        self.assertTrue(check(self.out))
        sensitivity_path = self.out / "factual-sensitivity.json"
        original = sensitivity_path.read_bytes()
        sensitivity = json.loads(original)
        first = next(iter(sensitivity["results"].values()))
        first["excluded_ranks"] = [1, 2, 3, 4, 5]
        sensitivity_path.write_text(json.dumps(sensitivity))
        # Even a coherently rehashed package cannot change the diagnostic verdict.
        manifest_path = self.out / "manifest.json"
        original_manifest = manifest_path.read_bytes()
        manifest = json.loads(original_manifest)
        for item in manifest["bundle"]:
            if item["path"] == "factual-sensitivity.json":
                item["sha256"] = digest(sensitivity_path.read_bytes())
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Factual sensitivity"):
            check(self.out)
        sensitivity_path.write_bytes(original)
        manifest_path.write_bytes(original_manifest)
        panel.select_one('td.win').string = '100% invented'
        (self.out / "index.html").write_text(str(soup))
        with self.assertRaises(ValueError):
            check(self.out)

    def test_baseline_selection_does_not_mix_optional_modes_into_headline(self):
        content = self.root / "content.json"
        content.write_text(json.dumps({"reading_model": True, "sections": [], "overview_methods": ["lexmount"]}))
        build(
            self.out,
            source=self.root / "fixture",
            content=content,
            include_english=True,
        )
        soup = BeautifulSoup((self.out / "index.html").read_text(), "html.parser")
        rows = soup.select_one('table').select('tbody tr')
        self.assertEqual(len(rows), 1)
        self.assertIn('Lexmount', rows[0].get_text())
        self.assertTrue(check(self.out))
        content.write_text(json.dumps({"overview_methods": ["missing"]}))
        with self.assertRaises(ValueError):
            build(self.out, source=self.root / "fixture", content=content)

    def test_bilingual_detail_and_raw_response_are_navigable(self):
        build(self.out, source=self.root / "fixture", include_english=True)
        self.assertTrue(check(self.out))
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(
            {p.removeprefix("en/") for p in manifest["routes"] if p.startswith("en/")},
            {p for p in manifest["routes"] if not p.startswith("en/")},
        )
        self.assertIn("合成示例", (self.out / "index.html").read_text())

    def test_removing_failure_log_is_rejected(self):
        p = next((self.out / "details").glob("*deepseek*"))
        p.write_text(p.read_text().replace("data-error", "removed"))
        with self.assertRaises(ValueError):
            check(self.out)

    def test_equal_budgets_do_not_hide_short_or_empty_responses(self):
        profile = {"methods": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}
        rows = [
            dict(method=m, case_id=c, round=0, request={"count": 10}, results=[{}] * n)
            for m, c, n in [("a", "x", 10), ("b", "x", 10), ("a", "y", 3), ("b", "y", 0)]
        ]
        soup = BeautifulSoup(fairness_table(profile, rows, False), "html.parser")
        self.assertIn("1/2", soup.get_text())
        self.assertIn("0 × 1", soup.get_text())
        self.assertIn("3 × 1", soup.get_text())
        self.assertEqual(soup.get_text().count("10 × 2"), 2)

    def test_fairness_disclosure_cannot_be_removed(self):
        p = self.out / "research.html"
        p.write_text(p.read_text().replace("data-fairness", "removed"))
        with self.assertRaises(ValueError):
            check(self.out)

    def test_five_result_report_labels_the_actual_cutoff(self):
        source = self.root / "fixture"
        path = source / "profile.json"
        profile = json.loads(path.read_text())
        profile["protocol"]["top_k"] = 5
        path.write_text(json.dumps(profile))
        receipt_path = source / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["profile_sha256"] = digest(path.read_bytes())
        receipt_path.write_text(json.dumps(receipt))
        build(self.out, source=source)
        page = BeautifulSoup((self.out / "index.html").read_text(), "html.parser")
        self.assertIn("目标页进前5条", page.get_text())
        self.assertNotIn("目标页进前10条", page.get_text())
        self.assertTrue(check(self.out))

    def test_preview_quality_panel_is_bound_to_scored_candidates(self):
        import hashlib

        source = self.root / "fixture"
        profile_path = source / "profile.json"
        profile = json.loads(profile_path.read_text())
        profile["protocol"]["top_k"] = 5
        profile_path.write_text(json.dumps(profile))
        receipt_path = source / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["profile_sha256"] = digest(profile_path.read_bytes())
        receipt_path.write_text(json.dumps(receipt))
        case = profile["cases"][0]
        url = case["anchors"][0]
        from harness.web_search.core import canonical_url
        candidate = hashlib.sha256((case["id"] + "\0" + canonical_url(url)).encode()).hexdigest()[:12]
        evidence = {
            "plan": {"cutoff": 5, "case_ids": [case["id"]]},
            "labels": {case["id"]: {candidate: 3}},
            "judge": {"model": "fixture"},
            "blind_candidate_packets": [{"case_id": case["id"], "candidates": [{"candidate_id": candidate, "url": url}]}],
            "provider_rankings": {case["id"]: {m["id"]: [candidate] for m in profile["methods"]}},
        }
        relevance = self.root / "relevance.json"
        relevance.write_text(json.dumps(evidence))
        content = self.root / "reading.json"
        content.write_text(json.dumps({"reading_model": True, "reading_sections": [
            {"key": "quality", "title": {"en": "Quality", "zh": "质量"}, "nav": {"en": "Quality", "zh": "质量"}}],
            "sections": []}))
        build(self.out, source=source, content=content, relevance=relevance)
        self.assertTrue(check(self.out))
        page = self.out / "index.html"
        page.write_text(page.read_text().replace("data-relevance", "removed", 1))
        with self.assertRaisesRegex(ValueError, "Preview relevance panel"):
            check(self.out)

    def combined_fixture(self):
        import shutil

        from harness.web_search.core import digest

        source = self.root / "fixture"
        second = self.root / "second"
        shutil.copytree(source, second)
        profile = json.loads((second / "profile.json").read_text())
        for case in profile["cases"]:
            case["id"] = "extra-" + case["id"]
        (second / "profile.json").write_text(json.dumps(profile))
        rows = [json.loads(line) for line in (second / "results.jsonl").read_text().splitlines()]
        for row in rows:
            row["case_id"] = "extra-" + row["case_id"]
            row["id"] = "extra-" + row["id"]
            old = second / row["evidence"]["path"]
            row["evidence"]["path"] = "calls/" + row["id"] + ".json"
            old.rename(second / row["evidence"]["path"])
        (second / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        receipt = json.loads((second / "receipt.json").read_text())
        receipt.update(
            profile_sha256=digest((second / "profile.json").read_bytes()),
            results_sha256=digest((second / "results.jsonl").read_bytes()),
        )
        (second / "receipt.json").write_text(json.dumps(receipt))
        build(self.out, source=source, cohorts=[("extra", second)])
        return second

    def test_separate_collections_share_one_query_and_result_inventory(self):
        self.combined_fixture()
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(len(manifest["profile"]["cases"]), 4)
        self.assertEqual(len(manifest["rows"]), 8)
        self.assertEqual(len(manifest["receipt"]["separate_collections"]), 2)
        for route, count in [("dataset.html", 4), ("results.html", 8)]:
            page = BeautifulSoup((self.out / route).read_text(), "html.parser")
            self.assertEqual(len(page.select("[data-row]")), count)
            self.assertEqual(
                {r["data-cohort"] for r in page.select("[data-row]")}, {"primary", "extra"}
            )
            self.assertEqual(
                {o["value"] for o in page.select("[data-cohort-filter] option")},
                {"all", "primary", "extra"},
            )
        self.assertTrue(check(self.out))

    def test_combined_report_cannot_omit_its_group_summary(self):
        self.combined_fixture()
        p = self.out / "index.html"
        p.write_text(p.read_text().replace("data-cohort-summary", "removed"))
        with self.assertRaises(ValueError):
            check(self.out)

    def test_overlapping_collections_are_not_double_counted(self):
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            build(
                self.out, source=self.root / "fixture", cohorts=[("extra", self.root / "fixture")]
            )

    def test_public_receipt_reconstructs_results_without_duplicating_response_text(self):
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertNotIn("results", manifest["rows"][0])
        self.assertTrue(check(self.out))
        manifest["rows"][0]["result_count"] += 1
        (self.out / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "receipt disagrees"):
            check(self.out)

    def test_readable_report_has_bound_chapters_and_real_field_examples(self):
        p = self.root / "fixture/profile.json"
        case = json.loads(p.read_text())["cases"][0]["id"]
        content = self.root / "reading.json"
        data = {
            "reading_model": "capability-quality-cost/1",
            "reading_sections": [],
            "sections": [],
        }
        for key in ("capabilities", "quality", "cost"):
            data["reading_sections"].append(
                {"key": key, "title": {"en": key, "zh": key}, "nav": {"en": key, "zh": key}}
            )
            data["sections"].append(
                {
                    "page": key,
                    "title": {"en": "Explanation", "zh": "说明"},
                    "paragraphs": [{"en": "A result needs evidence.", "zh": "结果需要证据。"}],
                }
            )
        data["sections"][1]["specimens"] = [
            {
                "case_id": case,
                "method": "lexmount",
                "fields": ["snippet"],
                "label": {"en": "Measured snippet", "zh": "实测片段"},
            }
        ]
        data["sections"].append(
            {
                **data["sections"][1],
                "page": "interpretation",
                "title": {"en": "Evidence appendix", "zh": "证据附录"},
            }
        )
        content.write_text(json.dumps(data))
        build(self.out, source=self.root / "fixture", content=content, include_english=True)
        self.assertTrue(check(self.out))
        p = self.out / "index.html"
        soup = BeautifulSoup(p.read_text(), "html.parser")
        self.assertEqual(len(soup.select("[data-reading]")), 3)
        self.assertIn("Fixture only", soup.select_one("[data-specimen]").get_text())
        appendix = BeautifulSoup((self.out / "interpretation.html").read_text(), "html.parser")
        self.assertIn("Fixture only", appendix.select_one("[data-specimen]").get_text())
        original = p.read_text()
        soup.select_one("[data-reading]").decompose()
        p.write_text(str(soup))
        with self.assertRaisesRegex(ValueError, "reader chapter"):
            check(self.out)
        soup = BeautifulSoup(original, "html.parser")
        soup.select_one("[data-specimen] blockquote").string = "Invented output"
        p.write_text(str(soup))
        with self.assertRaisesRegex(ValueError, "specimen"):
            check(self.out)

    def test_interpretation_comparison_cannot_disappear_or_change_values(self):
        page = self.out / "interpretation.html"
        original = page.read_text()
        soup = BeautifulSoup(original, "html.parser")
        soup.select_one("table").decompose()
        page.write_text(str(soup))
        with self.assertRaisesRegex(ValueError, "Interpretation core comparison"):
            check(self.out)
        soup = BeautifulSoup(original, "html.parser")
        soup.select_one("table tbody tr td:nth-of-type(2)").string = "999"
        page.write_text(str(soup))
        with self.assertRaisesRegex(ValueError, "Interpretation core comparison"):
            check(self.out)

    def test_source_filter_cannot_hide_or_misattribute_query_groups(self):
        p = self.out / "dataset.html"
        original = p.read_text()
        soup = BeautifulSoup(original, "html.parser")
        soup.select_one('[data-origin-filter] option[value!="all"]').decompose()
        p.write_text(str(soup))
        with self.assertRaisesRegex(ValueError, "query-source filter"):
            check(self.out)
        soup = BeautifulSoup(original, "html.parser")
        soup.select_one("[data-row]")["data-origin"] = "invented-community-source"
        p.write_text(str(soup))
        with self.assertRaisesRegex(ValueError, "population mismatch"):
            check(self.out)

    def test_result_inventory_has_complete_provider_filter(self):
        page = BeautifulSoup((self.out / "results.html").read_text(), "html.parser")
        methods = {method["id"] for method in json.loads((self.out / "manifest.json").read_text())["profile"]["methods"]}
        self.assertEqual(
            {option["value"] for option in page.select("[data-method-filter] option")},
            {"all", *methods},
        )
        self.assertEqual(
            {row["data-method"] for row in page.select("[data-row]")}, methods
        )
        self.assertIn('[data-state="pass"]', (self.out / "results.html").read_text())

    def test_supplementary_research_preserves_its_bound_response(self):
        source = self.root / "source-response.json"
        source.write_text('{"summary":"A source extract","answer":"A generated answer"}')
        content = self.root / "research-content.json"
        content.write_text(
            json.dumps(
                {
                    "assets": [
                        {
                            "source": source.name,
                            "path": "research/response.json",
                            "sha256": digest(source.read_bytes()),
                        }
                    ],
                    "sections": [
                        {
                            "page": "method",
                            "title": {"en": "Supplementary evidence", "zh": "补充证据"},
                            "paragraphs": [],
                            "sources": [
                                {
                                    "label": {"en": "Open response", "zh": "打开响应"},
                                    "url": "research/response.json",
                                    "evidence": True,
                                }
                            ],
                        }
                    ],
                }
            )
        )
        build(self.out, source=self.root / "fixture", content=content, include_english=True)
        exported = self.out / "research/response.json"
        self.assertEqual(exported.read_bytes(), source.read_bytes())
        for route in ("research.html", "en/research.html"):
            page = BeautifulSoup((self.out / route).read_text(), "html.parser")
            self.assertEqual(
                page.select_one("[data-evidence]")["data-evidence"], "research/response.json"
            )
        exported.write_text('{"answer":"Changed after validation"}')
        with self.assertRaises(ValueError):
            check(self.out)

    def test_duplicate_summary_is_rejected(self):
        p = next((self.out / "details").glob("*.html"))
        p.write_text(p.read_text().replace("</main>", "<section data-summary></section></main>"))
        with self.assertRaises(ValueError):
            check(self.out)

    def test_broken_evidence_target_is_rejected(self):
        p = next((self.out / "details").glob("*.html"))
        p.write_text(p.read_text().replace('data-evidence="calls/', 'data-evidence="missing/'))
        with self.assertRaises(ValueError):
            check(self.out)

    def test_nested_pages_work_with_host_injected_root_base(self):
        from urllib.parse import urljoin

        p = next((self.out / "details").glob("*.html"))
        soup = BeautifulSoup(p.read_text(), "html.parser")
        self.assertIsNotNone(soup.head)
        root = "https://host.example/api/preview/token/site/"
        for a in soup.select("[data-evidence]"):
            target = urljoin(root, a["data-evidence"])
            self.assertTrue(target.startswith(root + "calls/"))
            self.assertTrue((self.out / a["data-evidence"]).is_file())
        self.assertEqual(urljoin(root, soup.nav.a["href"]), root + "index.html")

    def test_supplement_preserves_language_evidence_and_main_navigation(self):
        supplement = self.out / "social"
        build(
            supplement,
            source=self.root / "fixture",
            site_prefix="social",
            include_english=True,
        )
        self.assertTrue(check(supplement))
        page = next((supplement / "details").glob("*.html"))
        soup = BeautifulSoup(page.read_text(), "html.parser")
        self.assertEqual(soup.select_one("[data-evidence]")["data-evidence"][:13], "social/calls/")
        self.assertEqual(soup.find("a", string="主评测")["href"], "index.html")
        self.assertTrue(soup.find("a", string="English")["href"].startswith("social/en/details/"))

    def test_visible_metric_cannot_disagree_with_evidence(self):
        p = self.out / "index.html"
        soup = BeautifulSoup(p.read_text(), "html.parser")
        cell = soup.select("table tbody tr")[0].select("td")[3]
        cell.string = "0.01"
        p.write_text(str(soup))
        with self.assertRaises(ValueError):
            check(self.out)

    def test_performance_page_cannot_invent_server_time(self):
        p = self.out / "performance.html"
        soup = BeautifulSoup(p.read_text(), "html.parser")
        soup.select("table tbody tr")[0].select("td")[2].string = "100.00 (n=2)"
        p.write_text(str(soup))
        with self.assertRaises(ValueError):
            check(self.out)

    def test_full_release_requires_browser_route_coverage(self):
        from harness.web_search.core import digest

        proof = self.root / "visual.json"
        proof.write_text(
            json.dumps(
                {
                    "manifest_sha256": digest((self.out / "manifest.json").read_bytes()),
                    "captures": [],
                    "interactions": [],
                }
            )
        )
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            release_check(self.out, proof)

    def test_single_language_release_does_not_require_a_language_switch(self):
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(manifest["languages"], ["zh"])
        visual = self.root / "visual"
        visual.mkdir()
        states = {
            (route, 1280, "light") for route in (
                "home", "capabilities", "dataset", "results", "performance",
                "method", "interpretation",
            )
        }
        captures = []
        for route, width, theme in states:
            path = visual / f"zh-{route}-{width}-{theme}.png"
            path.write_bytes(f"{route}-{width}-{theme}".encode())
            captures.append({
                "language": "zh", "route_class": route, "width": width,
                "theme": theme, "path": path.name, "sha256": digest(path.read_bytes()),
                "page_overflow": False, "readable": True, "metrics": {},
            })
        proof = visual / "release.json"
        proof.write_text(json.dumps({
            "manifest_sha256": digest((self.out / "manifest.json").read_bytes()),
            "captures": captures,
            "interactions": [],
        }))
        self.assertTrue(release_check(self.out, proof))

    def test_missing_winner_marker_is_rejected(self):
        p = self.out / "index.html"
        p.write_text(p.read_text().replace('class="win"', 'class="neutral"'))
        with self.assertRaises(ValueError):
            check(self.out)

    def test_wrong_winner_marker_is_rejected(self):
        p = self.out / "index.html"
        soup = BeautifulSoup(p.read_text(), "html.parser")
        soup.select("table tbody tr")[1].select("td")[3]["class"] = ["win"]
        p.write_text(str(soup))
        with self.assertRaises(ValueError):
            check(self.out)

    def test_partial_matrix_is_rejected_even_with_updated_file_hash(self):
        from harness.web_search.core import digest

        p = self.root / "fixture"
        rows = (p / "results.jsonl").read_text().splitlines()
        (p / "results.jsonl").write_text("\n".join(rows[:-1]))
        receipt = json.loads((p / "receipt.json").read_text())
        receipt["results_sha256"] = digest((p / "results.jsonl").read_bytes())
        (p / "receipt.json").write_text(json.dumps(receipt))
        with self.assertRaises(ValueError):
            load_run(p)

    def test_tampered_raw_response_is_rejected(self):
        p = next((self.root / "fixture/calls").glob("*.json"))
        p.write_text("{}")
        with self.assertRaises(ValueError):
            load_run(self.root / "fixture")


if __name__ == "__main__":
    unittest.main()


class RepeatedLatencyScenarios(unittest.TestCase):
    def test_all_rounds_and_unknown_cache_remain_visible(self):
        from harness.web_search.core import evaluate
        from harness.web_search.report import latency_rounds

        profile = {"protocol": {"rounds": 2}, "methods": [{"id": "a", "label": "A"}]}
        rows = [
            dict(
                method="a",
                round=0,
                elapsed_ms=900,
                http_status=200,
                results=[{"url": "https://a.org"}],
                error="",
                cost=None,
                metrics=evaluate({}, []),
                cache={"state": "miss"},
            ),
            dict(
                method="a",
                round=1,
                elapsed_ms=100,
                http_status=200,
                results=[{"url": "https://a.org"}],
                error="",
                cost=None,
                metrics=evaluate({}, []),
                cache=None,
            ),
        ]
        soup = BeautifulSoup(latency_rounds(profile, rows, False), "html.parser")
        data = [[c.get_text() for c in r.select("td")] for r in soup.select("tbody tr")]
        self.assertEqual(data[0][3], "900.00")
        self.assertEqual(data[1][3], "100.00")
        self.assertIn("miss: 1", data[0][-1])
        self.assertIn("unknown: 1", data[1][-1])
