"""Public report boundaries: evidence integrity, localization, and failure drilldown."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from harness.record_io import bytes_sha256
from harness.web_search.example import example
from harness.web_search.report import build, check, fairness_table, load_run, release_check


class ReportScenarios(unittest.TestCase):
    def test_check_rejects_unapproved_revision_even_with_updated_internal_hashes(self):
        revision = {
            "schema": "web-search-assessment-revision/1",
            "root_cause": "Synthetic reference control",
            "provider_calls": 0,
            "regression_ids": ["published-answer-explicit-aliases"],
            "cases": [],
        }
        from tests.web_search.test_native_revisions import revision_instance
        instance = revision_instance(self.root, revision)
        content = self.root / "revision-content.json"
        content.write_text(json.dumps({"assessment_revision": revision}), encoding="utf-8")
        build(self.out, source=self.root / "fixture", content=content, profile_path=instance)
        self.assertTrue(check(self.out))
        path = self.out / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["authored"], {"assessment_revision": revision})
        manifest["assessment_revision"]["contract"]["root_cause"] = "Unapproved replacement control"
        manifest["authored"]["assessment_revision"] = manifest["assessment_revision"]["contract"]
        manifest["content_sha256"] = bytes_sha256(json.dumps(manifest["authored"]).encode())
        from lexbench_websearch.inputs import implementation_sources
        implementation = implementation_sources()
        manifest["assessment_revision"]["implementation_sha256"] = {
            name: bytes_sha256(implementation[name].read_bytes())
            for name in manifest["assessment_revision"]["implementation_sha256"]
        }
        path.write_text(json.dumps(manifest), encoding="utf-8")
        for evidence_only in (False, True):
            with self.assertRaisesRegex(ValueError, "versioned scoring authority"):
                check(self.out, evidence_only=evidence_only)

    def test_dataset_links_current_inputs_and_in_tree_scoring_in_both_languages(self):
        build(self.out, source=self.root / "fixture", language="zh", include_english=True)
        for route in ("dataset.html", "en/dataset.html"):
            dom = BeautifulSoup((self.out / route).read_text(encoding="utf-8"), "html.parser")
            links = {a["href"] for a in dom.select("a[href]")}
            self.assertIn("https://huggingface.co/datasets/Lexmount/LexBench-Web-Search", links)
            self.assertIn("https://github.com/lexmount/webstone/tree/main/src/lexbench_websearch", links)
            self.assertNotIn("https://github.com/lexmount/lexbench-websearch", links)
        self.assertTrue(check(self.out))

    def test_dataset_can_link_hash_bound_original_query_sources(self):
        source = self.root / "original-query-source.txt"
        source.write_text("Original query inventory", encoding="utf-8")
        content = self.root / "source-content.json"
        content.write_text(json.dumps({
            "assets": [{"source": source.name, "path": "report-assets/query-source.txt", "sha256": bytes_sha256(source.read_bytes())}],
            "sections": [{"page": "dataset", "title": {"en": "Original queries", "zh": "原始查询"},
                          "paragraphs": [{"en": "Original definitions, independent of measured outcomes.", "zh": "原始题目，与实测结果分开保存。"}],
                          "sources": [{"label": {"en": "Inspect original source", "zh": "查看原始来源"}, "url": "report-assets/query-source.txt"}]}],
        }), encoding="utf-8")
        build(self.out, source=self.root / "fixture", content=content, include_english=True)
        for prefix, title in (("", "原始查询"), ("en/", "Original queries")):
            dom = BeautifulSoup((self.out / (prefix + "dataset.html")).read_text(encoding="utf-8"), "html.parser")
            self.assertIn(title, dom.get_text())
            link = dom.find("a", string="查看原始来源" if not prefix else "Inspect original source")
            from urllib.parse import urljoin, urlsplit
            page_url = (self.out / (prefix + "dataset.html")).as_uri()
            base_url = urljoin(page_url, dom.select_one("base")["href"])
            self.assertTrue(Path(urlsplit(urljoin(base_url, link["href"])).path).is_file())
        self.assertEqual((self.out / "report-assets/query-source.txt").read_bytes(), source.read_bytes())
        self.assertTrue(check(self.out))

    def test_review_cannot_silently_readmit_previously_excluded_references(self):
        from harness.web_search.report import assessment_profile

        profile = {"protocol": {}, "factual_quality_exclusions": ["bad-source"]}
        # A new reviewer confirming a source is not a reference readmission receipt.
        review = {"scope": "all", "adjudications": {
            "new-conflict:dataset": {"quality_excluded": True},
            "valid:dataset": {"quality_excluded": False},
        }}
        assessed = assessment_profile(profile, None, review)
        self.assertEqual(assessed["factual_quality_exclusions"], ["bad-source", "new-conflict"])
        self.assertEqual(profile["factual_quality_exclusions"], ["bad-source"])
        self.assertEqual(assessment_profile(profile, None, {"scope": "all", "adjudications": {}})["factual_quality_exclusions"], ["bad-source"])
        self.assertEqual(assessment_profile({"protocol": {}}, None, review)["factual_quality_exclusions"], ["new-conflict"])

    def test_content_hash_binds_rendered_snapshot_when_source_changes_during_build(self):
        from harness.web_search.report import authored_sections

        content = self.root / "changing-content.json"
        original = json.dumps({"reading_model": True, "sections": [{
            "page": "lead", "title": {"en": "Original finding", "zh": "原始结论"},
            "paragraphs": [{"en": "Original evidence.", "zh": "原始证据。"}],
        }]}).encode()
        content.write_bytes(original)

        def render(*args, **kwargs):
            content.write_text('{"sections": []}', encoding="utf-8")
            return authored_sections(*args, **kwargs)

        with patch("harness.web_search.report.authored_sections", side_effect=render):
            build(self.out, source=self.root / "fixture", content=content)
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["content_sha256"], bytes_sha256(original))
        self.assertIn("原始结论", (self.out / "index.html").read_text(encoding="utf-8"))
        self.assertNotEqual(manifest["content_sha256"], bytes_sha256(content.read_bytes()))

    def test_reader_routes_have_distinct_owners_and_keep_evidence(self):
        content = self.root / "concise-content.json"
        content.write_text(json.dumps({"presentation": "comparison-report", "reading_model": True,
                                       "sections": []}), encoding="utf-8")
        build(self.out, source=self.root / "fixture", content=content)
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        routes = {route for route in manifest["routes"] if "/" not in route}
        self.assertEqual(routes, {"index.html", "capabilities.html", "quality.html", "performance.html", "dataset.html", "results.html", "research.html"})
        home = BeautifulSoup((self.out / "index.html").read_text(encoding="utf-8"), "html.parser")
        self.assertEqual(len(home.select('[data-executive]')), 1)
        self.assertEqual(len(home.select('[data-populations]')), 1)
        self.assertEqual(len(home.select('[data-executive] tbody tr')), 2)
        nav = [a['href'] for a in home.select('nav a')]
        self.assertEqual(nav, ['index.html', 'capabilities.html', 'quality.html', 'performance.html', 'dataset.html', 'results.html'])
        performance = BeautifulSoup((self.out / "performance.html").read_text(encoding="utf-8"), "html.parser")
        self.assertEqual(len(performance.select('[data-cost-budget]')), 1)
        self.assertEqual(len(performance.select('[data-timing-decomposition]')), 1)
        self.assertTrue(any('details/' in route for route in manifest['routes']))
        self.assertTrue(check(self.out))
        # Missing a primary reading route cannot be hidden behind a successful build.
        home.select_one('nav a[href="quality.html"]').decompose()
        (self.out / 'index.html').write_text(str(home), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, 'Quality page'):
            check(self.out)

    def test_reference_exclusion_does_not_remove_cohort_measurements(self):
        from harness.web_search.report import cohort_summary
        from harness.web_search.report import load_run
        profile, rows, _ = load_run(self.root / "fixture")
        profile["report_cohorts"] = [{"id": "public", "label": {"zh": "公开组", "en": "Public"}, "case_ids": [c["id"] for c in profile["cases"]]}]
        profile["factual_quality_exclusions"] = [profile["cases"][0]["id"]]
        for row in rows:
            row["cohort"] = "public"
        for zh in (True, False):
            doc = BeautifulSoup(cohort_summary(profile, rows, zh), "html.parser")
            self.assertEqual(len(doc.select("tbody tr")), len(profile["methods"]))
            self.assertIn("实际请求" if zh else "physical requests", doc.get_text())


    def test_dataset_uses_reviewed_questions_without_rewriting_collected_calls(self):
        from harness.web_search.report import quality_dataset, metric_populations, dataset_context
        profile = {
            "cases": [
                {"id": "scenario", "origin": "community"},
                {"id": "valid", "reference_answer": "a"},
                {"id": "bad-reference", "reference_answer": "b"},
            ],
            "factual_quality_exclusions": ["bad-reference"],
            "report_cohorts": [{"id": "public", "case_ids": ["valid", "bad-reference"]}],
        }
        content = {
            "presentation": "comparison-report", "independent_benchmarks": True,
            "metric_populations": [
                {"id": "custom", "benchmark_root": True, "label": {"zh":"自建场景集","en":"Scenarios"}, "case_ids": ["scenario"]},
                {"id": "factual", "benchmark_root": True, "label": {"zh":"公开事实集","en":"Facts"}, "case_ids": ["valid", "bad-reference"]},
            ],
        }
        before = json.dumps([profile, content], sort_keys=True)
        active, authored = quality_dataset(profile, content)
        self.assertEqual([c["id"] for c in active["cases"]], ["scenario", "valid"])
        self.assertEqual(active["report_cohorts"][0]["case_ids"], ["valid"])
        self.assertEqual(metric_populations(active, authored)[1]["case_ids"], ["valid"])
        text = BeautifulSoup(dataset_context(active, authored, True), "html.parser").get_text()
        self.assertIn("公开事实集1", text)
        self.assertNotIn("保留 2 题", text)
        self.assertEqual(json.dumps([profile, content], sort_keys=True), before)

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
        from lexbench_websearch.scoring import evaluate
        source = self.root / "fixture"
        profile_path = source / "profile.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        for case in profile["cases"]:
            case.update(reference_answer="Fixture only", reference_urls=case["anchors"])
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        rows_path = source / "results.jsonl"
        rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
        cases = {case["id"]: case for case in profile["cases"]}
        for row in rows:
            row["metrics"] = evaluate(cases[row["case_id"]], row["results"])
        rows_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        receipt_path = source / "receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt.update(profile_sha256=bytes_sha256(profile_path.read_bytes()), results_sha256=bytes_sha256(rows_path.read_bytes()))
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        content = self.root / "content.json"
        content.write_text(json.dumps({"reading_model": True, "sections": [], "reading_sections": [
            {"key": "quality", "nav": {"en": "Quality", "zh": "质量"}, "title": {"en": "Quality", "zh": "质量"}}
        ]}), encoding="utf-8")
        build(self.out, source=source, content=content)
        soup = BeautifulSoup((self.out / "index.html").read_text(encoding="utf-8"), "html.parser")
        panel = soup.select_one('[data-factual]')
        self.assertIn('2/2', panel.get_text())
        self.assertIn('1/2', panel.get_text())  # failed query stays in denominator
        self.assertTrue(check(self.out))
        sensitivity_path = self.out / "factual-sensitivity.json"
        original = sensitivity_path.read_bytes()
        sensitivity = json.loads(original)
        first = next(iter(sensitivity["results"].values()))
        first["excluded_ranks"] = [1, 2, 3, 4, 5]
        sensitivity_path.write_text(json.dumps(sensitivity), encoding="utf-8")
        # Even a coherently rehashed package cannot change the diagnostic verdict.
        manifest_path = self.out / "manifest.json"
        original_manifest = manifest_path.read_bytes()
        manifest = json.loads(original_manifest)
        for item in manifest["bundle"]:
            if item["path"] == "factual-sensitivity.json":
                item["sha256"] = bytes_sha256(sensitivity_path.read_bytes())
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Factual sensitivity"):
            check(self.out)
        sensitivity_path.write_bytes(original)
        manifest_path.write_bytes(original_manifest)
        panel.select_one('td.win').string = '100% invented'
        (self.out / "index.html").write_text(str(soup), encoding="utf-8")
        with self.assertRaises(ValueError):
            check(self.out)

    def test_baseline_selection_does_not_mix_optional_modes_into_headline(self):
        content = self.root / "content.json"
        content.write_text(json.dumps({"reading_model": True, "sections": [], "overview_methods": ["lexmount"]}), encoding="utf-8")
        build(
            self.out,
            source=self.root / "fixture",
            content=content,
            include_english=True,
        )
        soup = BeautifulSoup((self.out / "index.html").read_text(encoding="utf-8"), "html.parser")
        rows = soup.select_one('table').select('tbody tr')
        self.assertEqual(len(rows), 1)
        self.assertIn('Fixture A', rows[0].get_text())
        self.assertTrue(check(self.out))
        content.write_text(json.dumps({"overview_methods": ["missing"]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            build(self.out, source=self.root / "fixture", content=content)

    def test_bilingual_detail_and_raw_response_are_navigable(self):
        build(self.out, source=self.root / "fixture", include_english=True)
        self.assertTrue(check(self.out))
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            {p.removeprefix("en/") for p in manifest["routes"] if p.startswith("en/")},
            {p for p in manifest["routes"] if not p.startswith("en/")},
        )
        self.assertIn("合成示例", (self.out / "index.html").read_text(encoding="utf-8"))

    def test_removing_failure_log_is_rejected(self):
        p = next((self.out / "details").glob("*deepseek*"))
        p.write_text(p.read_text(encoding="utf-8").replace("data-error", "removed"), encoding="utf-8")
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
        p.write_text(p.read_text(encoding="utf-8").replace("data-fairness", "removed"), encoding="utf-8")
        with self.assertRaises(ValueError):
            check(self.out)

    def test_concurrent_collection_discloses_shared_limits_and_loaded_latency(self):
        source = self.root / 'fixture'
        path = source / 'profile.json'
        profile = json.loads(path.read_text(encoding="utf-8"))
        profile['protocol'].update(concurrency=2, order='bounded calls')
        profile['execution_policy'] = {
            'purpose': 'quality', 'max_in_flight': 2,
            'resources': {m['id']: {'capacity': 2} for m in profile['methods']},
            'assignments': {m['id']: [m['id'], 'shared'] for m in profile['methods']},
        }
        profile['execution_policy']['resources']['shared'] = {
            'capacity': 1, 'reason': 'One shared service'}
        path.write_text(json.dumps(profile), encoding="utf-8")
        receipt_path = source / 'receipt.json'
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt['profile_sha256'] = bytes_sha256(path.read_bytes())
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        build(self.out, source=source, language='zh', include_english=True)
        self.assertTrue(check(self.out))
        for prefix in ('', 'en/'):
            page = self.out / (prefix + 'research.html')
            soup = BeautifulSoup(page.read_text(encoding="utf-8"), 'html.parser')
            section = soup.select_one('[data-execution-settings]')
            self.assertEqual([row.select('td')[1].get_text() for row in section.select('tbody tr')],
                             ['1', '1'])
            section.select('tbody td')[1].string = '2'
            original = page.read_text(encoding="utf-8")
            page.write_text(str(soup), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, 'Execution settings'):
                check(self.out)
            page.write_text(original, encoding="utf-8")
        home = (self.out / 'en/index.html').read_text(encoding="utf-8")
        self.assertNotIn('Serial API calls', home)
        self.assertIn('concurrent API calls', home)

    def test_five_result_report_labels_the_actual_cutoff(self):
        source = self.root / "fixture"
        path = source / "profile.json"
        profile = json.loads(path.read_text(encoding="utf-8"))
        profile["protocol"]["top_k"] = 5
        path.write_text(json.dumps(profile), encoding="utf-8")
        receipt_path = source / "receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["profile_sha256"] = bytes_sha256(path.read_bytes())
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        build(self.out, source=source)
        page = BeautifulSoup((self.out / "index.html").read_text(encoding="utf-8"), "html.parser")
        self.assertIn("目标页进前5条", page.get_text())
        self.assertNotIn("目标页进前10条", page.get_text())
        self.assertTrue(check(self.out))

    def test_preview_quality_panel_is_bound_to_scored_candidates(self):

        source = self.root / "fixture"
        profile_path = source / "profile.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["protocol"]["top_k"] = 5
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        receipt_path = source / "receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["profile_sha256"] = bytes_sha256(profile_path.read_bytes())
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        from harness.web_search.relevance import import_annotations
        from lexbench_websearch.relevance import candidate_packet
        measured = [json.loads(line) for line in (source / "results.jsonl").read_text(encoding="utf-8").splitlines()]
        packet, _ = candidate_packet(profile, measured)
        submission = {"packet_sha256": packet["packet_sha256"],
                      "judge": {"kind": "human", "name": "Fixture reviewer"},
                      "reviewed_case_ids": packet["plan"]["case_ids"],
                      "labels": {c["case_id"]: {x["candidate_id"]: 3 for x in c["candidates"]}
                                 for c in packet["blind_candidate_packets"]}}
        evidence = import_annotations(profile, measured, submission)
        relevance = self.root / "relevance.json"
        relevance.write_text(json.dumps(evidence), encoding="utf-8")
        content = self.root / "reading.json"
        content.write_text(json.dumps({"reading_model": True, "reading_sections": [
            {"key": "quality", "title": {"en": "Quality", "zh": "质量"}, "nav": {"en": "Quality", "zh": "质量"}}],
            "sections": []}), encoding="utf-8")
        build(self.out, source=source, content=content, relevance=relevance)
        self.assertTrue(check(self.out))
        page = self.out / "index.html"
        page.write_text(page.read_text(encoding="utf-8").replace("data-relevance", "removed", 1), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Preview relevance panel"):
            check(self.out)

    def test_quality_definitions_precede_results_and_are_bound(self):
        content = self.root / "definitions.json"
        content.write_text(json.dumps({"reading_model": True, "presentation": "comparison-report", "reading_sections": [], "sections": [
            {"page": "quality-definitions", "title": {"zh": "分数的含义", "en": "Grade definitions"}, "paragraphs": [{"zh": "三分表示直接满足", "en": "Three means directly useful"}]},
            {"page": "quality", "title": {"zh": "结果解释", "en": "Result interpretation"}, "paragraphs": [{"zh": "检查实际结果", "en": "Inspect actual results"}]}
        ]}), encoding="utf-8")
        build(self.out, source=self.root / "fixture", content=content)
        self.assertTrue(check(self.out))
        page = self.out / "quality.html"
        text = page.read_text(encoding="utf-8")
        self.assertLess(text.index("分数的含义"), text.index("结果解释"))
        page.write_text(text.replace("三分表示直接满足", "错误定义"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "bound content"):
            check(self.out)

    def combined_fixture(self):
        import shutil

        from harness.record_io import bytes_sha256

        source = self.root / "fixture"
        second = self.root / "second"
        shutil.copytree(source, second)
        profile = json.loads((second / "profile.json").read_text(encoding="utf-8"))
        for case in profile["cases"]:
            case["id"] = "extra-" + case["id"]
        (second / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
        rows = [json.loads(line) for line in (second / "results.jsonl").read_text(encoding="utf-8").splitlines()]
        for row in rows:
            row["case_id"] = "extra-" + row["case_id"]
            row["id"] = "extra-" + row["id"]
            old = second / row["evidence"]["path"]
            row["evidence"]["path"] = "calls/" + row["id"] + ".json"
            old.rename(second / row["evidence"]["path"])
        (second / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        receipt = json.loads((second / "receipt.json").read_text(encoding="utf-8"))
        receipt.update(
            profile_sha256=bytes_sha256((second / "profile.json").read_bytes()),
            results_sha256=bytes_sha256((second / "results.jsonl").read_bytes()),
        )
        (second / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        build(self.out, source=source, cohorts=[("extra", second)])
        return second

    def test_separate_collections_share_one_query_and_result_inventory(self):
        self.combined_fixture()
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["profile"]["cases"]), 4)
        self.assertEqual(len(manifest["rows"]), 8)
        self.assertEqual(len(manifest["receipt"]["separate_collections"]), 2)
        for route, count in [("dataset.html", 4), ("results.html", 8)]:
            page = BeautifulSoup((self.out / route).read_text(encoding="utf-8"), "html.parser")
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
        p.write_text(p.read_text(encoding="utf-8").replace("data-cohort-summary", "removed"), encoding="utf-8")
        with self.assertRaises(ValueError):
            check(self.out)

    def test_overlapping_collections_are_not_double_counted(self):
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            build(
                self.out, source=self.root / "fixture", cohorts=[("extra", self.root / "fixture")]
            )

    def test_public_receipt_reconstructs_results_without_duplicating_response_text(self):
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("results", manifest["rows"][0])
        self.assertTrue(check(self.out))
        manifest["rows"][0]["result_count"] += 1
        (self.out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "receipt disagrees"):
            check(self.out)

    def test_readable_report_has_bound_chapters_and_real_field_examples(self):
        p = self.root / "fixture/profile.json"
        case = json.loads(p.read_text(encoding="utf-8"))["cases"][0]["id"]
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
        content.write_text(json.dumps(data), encoding="utf-8")
        build(self.out, source=self.root / "fixture", content=content, include_english=True)
        self.assertTrue(check(self.out))
        p = self.out / "index.html"
        soup = BeautifulSoup(p.read_text(encoding="utf-8"), "html.parser")
        self.assertEqual(len(soup.select("[data-reading]")), 3)
        self.assertIn("Fixture only", soup.select_one("[data-specimen]").get_text())
        appendix = BeautifulSoup((self.out / "interpretation.html").read_text(encoding="utf-8"), "html.parser")
        self.assertIn("Fixture only", appendix.select_one("[data-specimen]").get_text())
        original = p.read_text(encoding="utf-8")
        soup.select_one("[data-reading]").decompose()
        p.write_text(str(soup), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "reader chapter"):
            check(self.out)
        soup = BeautifulSoup(original, "html.parser")
        soup.select_one("[data-specimen] blockquote").string = "Invented output"
        p.write_text(str(soup), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "specimen"):
            check(self.out)

    def test_interpretation_comparison_cannot_disappear_or_change_values(self):
        page = self.out / "interpretation.html"
        original = page.read_text(encoding="utf-8")
        soup = BeautifulSoup(original, "html.parser")
        soup.select_one("table").decompose()
        page.write_text(str(soup), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Interpretation core comparison|Column extremum highlight"):
            check(self.out)
        soup = BeautifulSoup(original, "html.parser")
        soup.select_one("table tbody tr td:nth-of-type(2)").string = "999"
        page.write_text(str(soup), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Interpretation core comparison|Column extremum highlight"):
            check(self.out)

    def test_source_filter_cannot_hide_or_misattribute_query_groups(self):
        p = self.out / "dataset.html"
        original = p.read_text(encoding="utf-8")
        soup = BeautifulSoup(original, "html.parser")
        soup.select_one('[data-origin-filter] option[value!="all"]').decompose()
        p.write_text(str(soup), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "query-source filter"):
            check(self.out)
        soup = BeautifulSoup(original, "html.parser")
        soup.select_one("[data-row]")["data-origin"] = "invented-community-source"
        p.write_text(str(soup), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "population mismatch"):
            check(self.out)

    def test_result_inventory_has_complete_provider_filter(self):
        page = BeautifulSoup((self.out / "results.html").read_text(encoding="utf-8"), "html.parser")
        methods = {method["id"] for method in json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))["profile"]["methods"]}
        self.assertEqual(
            {option["value"] for option in page.select("[data-method-filter] option")},
            {"all", *methods},
        )
        self.assertEqual(
            {row["data-method"] for row in page.select("[data-row]")}, methods
        )
        self.assertIn('[data-state="pass"]', (self.out / "results.html").read_text(encoding="utf-8"))

    def test_supplementary_research_preserves_its_bound_response(self):
        source = self.root / "source-response.json"
        source.write_text('{"summary":"A source extract","answer":"A generated answer"}', encoding="utf-8")
        content = self.root / "research-content.json"
        content.write_text(
            json.dumps(
                {
                    "assets": [
                        {
                            "source": source.name,
                            "path": "research/response.json",
                            "sha256": bytes_sha256(source.read_bytes()),
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
            ),
            encoding="utf-8"
        )
        build(self.out, source=self.root / "fixture", content=content, include_english=True)
        exported = self.out / "research/response.json"
        self.assertEqual(exported.read_bytes(), source.read_bytes())
        for route in ("research.html", "en/research.html"):
            page = BeautifulSoup((self.out / route).read_text(encoding="utf-8"), "html.parser")
            self.assertEqual(
                page.select_one("[data-evidence]")["data-evidence"], "research/response.json"
            )
        exported.write_text('{"answer":"Changed after validation"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            check(self.out)

    def test_duplicate_summary_is_rejected(self):
        p = next((self.out / "details").glob("*.html"))
        p.write_text(p.read_text(encoding="utf-8").replace("</main>", "<section data-summary></section></main>"), encoding="utf-8")
        with self.assertRaises(ValueError):
            check(self.out)

    def test_broken_evidence_target_is_rejected(self):
        p = next((self.out / "details").glob("*.html"))
        p.write_text(p.read_text(encoding="utf-8").replace('data-evidence="calls/', 'data-evidence="missing/'), encoding="utf-8")
        with self.assertRaises(ValueError):
            check(self.out)

    def test_nested_pages_work_with_host_injected_root_base(self):
        from urllib.parse import urljoin

        p = next((self.out / "details").glob("*.html"))
        soup = BeautifulSoup(p.read_text(encoding="utf-8"), "html.parser")
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
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        self.assertEqual(soup.select_one("[data-evidence]")["data-evidence"][:13], "social/calls/")
        self.assertEqual(soup.find("a", string="主评测")["href"], "index.html")
        self.assertTrue(soup.find("a", string="English")["href"].startswith("social/en/details/"))

    def test_visible_metric_cannot_disagree_with_evidence(self):
        p = self.out / "index.html"
        soup = BeautifulSoup(p.read_text(encoding="utf-8"), "html.parser")
        cell = soup.select("table tbody tr")[0].select("td")[3]
        cell.string = "0.01"
        p.write_text(str(soup), encoding="utf-8")
        with self.assertRaises(ValueError):
            check(self.out)

    def test_performance_page_cannot_invent_server_time(self):
        p = self.out / "performance.html"
        soup = BeautifulSoup(p.read_text(encoding="utf-8"), "html.parser")
        soup.select("table tbody tr")[0].select("td")[2].string = "100.00 (n=2)"
        p.write_text(str(soup), encoding="utf-8")
        with self.assertRaises(ValueError):
            check(self.out)

    def test_full_release_requires_browser_route_coverage(self):
        from harness.record_io import bytes_sha256

        proof = self.root / "visual.json"
        proof.write_text(
            json.dumps(
                {
                    "manifest_sha256": bytes_sha256((self.out / "manifest.json").read_bytes()),
                    "captures": [],
                    "interactions": [],
                }
            ),
            encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            release_check(self.out, proof)

    def test_single_language_release_does_not_require_a_language_switch(self):
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["languages"], ["zh"])
        notice = self.out / "en/index.html"
        self.assertIn("data-english-not-generated", notice.read_text(encoding="utf-8"))
        self.assertIn('href="../index.html"', notice.read_text(encoding="utf-8"))
        home = BeautifulSoup((self.out / "index.html").read_text(encoding="utf-8"), "html.parser")
        self.assertEqual(home.find("a", string="English")["href"], "en/index.html")
        from harness.web_search.visual import routes
        self.assertEqual({lang for lang, _, _ in routes(self.out)}, {"zh"})

    def test_missing_winner_marker_is_rejected(self):
        p = self.out / "index.html"
        doc = BeautifulSoup(p.read_text(encoding="utf-8"), 'html.parser')
        doc.select_one('.comparison-winner').unwrap()
        p.write_text(str(doc), encoding="utf-8")
        with self.assertRaises(ValueError):
            check(self.out)

    def test_wrong_winner_marker_is_rejected(self):
        p = self.out / "index.html"
        soup = BeautifulSoup(p.read_text(encoding="utf-8"), "html.parser")
        soup.select("table tbody tr")[1].select_one('td[data-metric="p50-ms"]').append(soup.select_one(".comparison-winner").extract())
        p.write_text(str(soup), encoding="utf-8")
        with self.assertRaises(ValueError):
            check(self.out)

    def test_partial_matrix_is_rejected_even_with_updated_file_hash(self):
        from harness.record_io import bytes_sha256

        p = self.root / "fixture"
        rows = (p / "results.jsonl").read_text(encoding="utf-8").splitlines()
        (p / "results.jsonl").write_text("\n".join(rows[:-1]), encoding="utf-8")
        receipt = json.loads((p / "receipt.json").read_text(encoding="utf-8"))
        receipt["results_sha256"] = bytes_sha256((p / "results.jsonl").read_bytes())
        (p / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_run(p)

    def test_tampered_raw_response_is_rejected(self):
        p = next((self.root / "fixture/calls").glob("*.json"))
        p.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_run(self.root / "fixture")


if __name__ == "__main__":
    unittest.main()


class RepeatedLatencyScenarios(unittest.TestCase):
    def test_all_rounds_and_unknown_cache_remain_visible(self):
        from lexbench_websearch.scoring import evaluate
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


def test_interface_specimen_keeps_every_method_and_full_results():
    from harness.web_search.report import interface_example
    import pytest
    p = {"cases": [{"id": "q", "query": "One common question"}],
         "methods": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}
    rows = [{"case_id": "q", "method": m["id"], "request": {"count": 5},
             "results": [{"url": "https://example.org/", "summary": "full" * 1000}],
             "evidence": {"path": m["id"] + ".json"}} for m in p["methods"]]
    html = interface_example(p, rows, "q", True)
    soup = BeautifulSoup(html, "html.parser")
    assert [x["data-interface-method"] for x in soup.select("[data-interface-method]")] == ["a", "b"]
    assert html.count("full" * 1000) == 2
    assert len(soup.select("[data-evidence]")) == 2
    with pytest.raises(ValueError, match="every method"):
        interface_example(p, rows[:1], "q", True)


def test_dataset_inventory_has_bounded_scroll_for_large_catalogs(tmp_path):
    from harness.web_search.example import example
    output = example(tmp_path).parent
    dom = BeautifulSoup((output / "dataset.html").read_text(encoding="utf-8"), "html.parser")
    inventory = dom.select_one(".scroll.inventory")
    assert inventory is not None
    assert inventory.select("tr[data-case-id]")


def test_main_quality_table_and_each_detail_share_all_frozen_grades(tmp_path):
    from harness.record_io import bytes_sha256
    from harness.web_search.relevance import import_annotations
    from lexbench_websearch.relevance import candidate_packet
    from harness.web_search.report import build, check
    from harness.web_search.example import example
    import pytest
    example(tmp_path)
    source = tmp_path / 'fixture'
    profile = json.loads((source / 'profile.json').read_text(encoding="utf-8"))
    profile['protocol']['top_k'] = 5
    (source / 'profile.json').write_text(json.dumps(profile), encoding="utf-8")
    receipt = json.loads((source / 'receipt.json').read_text(encoding="utf-8"))
    receipt['profile_sha256'] = bytes_sha256((source / 'profile.json').read_bytes())
    (source / 'receipt.json').write_text(json.dumps(receipt), encoding="utf-8")
    rows = [json.loads(line) for line in (source / 'results.jsonl').read_text(encoding="utf-8").splitlines()]
    packet, _ = candidate_packet(profile, rows)
    relevance = import_annotations(profile, rows, {'packet_sha256':packet['packet_sha256'], 'reviewed_case_ids':packet['plan']['case_ids'],
        'judge':{'kind':'human','name':'Fixture reviewer'}, 'labels':{p['case_id']:{c['candidate_id']:3 for c in p['candidates']} for p in packet['blind_candidate_packets']}})
    labels = tmp_path / 'labels.json'; labels.write_text(json.dumps(relevance), encoding="utf-8")
    content = tmp_path / 'content.json'; content.write_text(json.dumps({'presentation':'comparison-report','reading_model':True,'sections':[]}), encoding="utf-8")
    output = tmp_path / 'final'; build(output, source=source, content=content, relevance=labels)
    home = BeautifulSoup((output/'index.html').read_text(encoding="utf-8"),'html.parser')
    headers = [h.get_text() for h in home.select('[data-executive] th')]
    assert 'nDCG@5 × 100' in headers
    assert 'Precision@5' in headers
    assert 'Success@5' in headers
    assert not home.select_one('[data-executive] table').find_parent('details')
    quality = BeautifulSoup((output/'quality.html').read_text(encoding="utf-8"), 'html.parser')
    assert 'Precision@5' in quality.get_text() and 'Success@5' in quality.get_text()
    assert home.select_one('[data-executive] tbody td:nth-child(2)').get_text().strip('★ ') == '100.0'
    # A concise home must still expose every primary relevance measure.
    saved_home = (output / 'index.html').read_text(encoding="utf-8")
    for column in (2, 3, 4):
        broken = BeautifulSoup(saved_home, 'html.parser')
        for row in broken.select('[data-executive] tr'):
            row.select('th,td')[column - 1].decompose()
        (output / 'index.html').write_text(str(broken), encoding="utf-8")
        with pytest.raises(ValueError, match='Population summary|accessible control'):
            check(output)
    (output / 'index.html').write_text(saved_home, encoding="utf-8")
    result = BeautifulSoup((output/'results.html').read_text(encoding="utf-8"),'html.parser')
    assert not result.select('[data-method-filter]')  # No conflicting duplicate legacy inventory.
    data = json.loads(result.select_one('[data-comparison-data]').string)
    assert data['cases'][0]['outputs'][profile['methods'][0]['id']]['score']['ndcg'] == 1
    detail = next((output/'details').glob('*.html'))
    dom = BeautifulSoup(detail.read_text(encoding="utf-8"),'html.parser'); dom.select_one('[data-case-relevance] strong').string = 'invented score'
    detail.write_text(str(dom), encoding="utf-8")
    with pytest.raises(ValueError,match='Query quality calculation'):
        check(output)


def test_factual_detail_uses_ten_result_budget_without_changing_frozen_five_metrics(tmp_path):
    from harness.web_search.core import normalize
    from lexbench_websearch.scoring import evaluate
    example(tmp_path)
    source = tmp_path / 'fixture'
    profile = json.loads((source / 'profile.json').read_bytes())
    profile['protocol']['top_k'] = 10
    case = profile['cases'][0]
    case.update(reference_answer='Ada', reference_urls=['https://answer.invalid/ada'])
    (source / 'profile.json').write_text(json.dumps(profile), encoding="utf-8")
    rows = [json.loads(line) for line in (source / 'results.jsonl').read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row['case_id'] != case['id']:
            continue
        raw = {'results': [{'url': f'https://other.invalid/{i}', 'title': 'Unrelated'} for i in range(5)] + [{'url': case['reference_urls'][0], 'title': 'Ada'}]}
        path = source / row['evidence']['path']
        path.write_text(json.dumps(raw), encoding="utf-8")
        row['evidence']['sha256'] = bytes_sha256(path.read_bytes())
        row['results'] = normalize(raw)
        row['metrics'] = evaluate(case, row['results'])
        assert row['metrics']['labeled_source_hit_5'] == 0
    (source / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding="utf-8")
    receipt = json.loads((source / 'receipt.json').read_bytes())
    receipt.update(profile_sha256=bytes_sha256((source/'profile.json').read_bytes()), results_sha256=bytes_sha256((source/'results.jsonl').read_bytes()))
    (source / 'receipt.json').write_text(json.dumps(receipt), encoding="utf-8")
    output = tmp_path / 'ten-results'
    build(output, source=source)
    assert check(output)
    detail = BeautifulSoup((output / 'details' / (rows[0]['id'] + '.html')).read_text(encoding="utf-8"), 'html.parser')
    value = detail.find('td', string='前 10 条找到标注来源').find_next_sibling('td').get_text()
    assert float(value.replace(',', '')) == 1
    assert '前 5 条找到标注来源' not in detail.get_text()
    from harness.web_search.report import factual_panel
    factual = BeautifulSoup(factual_panel(profile, rows, True, compact=True), 'html.parser')
    assert [h.get_text() for h in factual.select('th')][1:3] == ['Hit@10（参考网页）', 'MRR@10（参考网页）']

    interpretation = output / 'interpretation.html'
    interpretation.write_text(interpretation.read_text(encoding="utf-8").replace('data-factual', 'removed-factual', 1), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match='Interpretation factual evidence'):
        check(output)


def test_metrics_use_only_available_annotations_and_prices_ignore_unknown_invoices():
    import copy
    import pytest
    from lexbench_websearch.scoring import evaluate
    from lexbench_websearch.relevance import candidate_packet, score
    from harness.web_search.report import population_summary, cost_panel, benchmark_operations, factual_panel
    profile = {'protocol': {'top_k': 5, 'rounds': 1, 'score_round': 0},
               'comparison_selection': {},
               'methods': [{'id': x, 'label': x, 'currency': 'USD', 'price_per_1000': None} for x in ('a','b')],
               'cases': [{'id':'known','family':'web','query':'Find the reference','anchors':['https://reference.test/page']},
                         {'id':'open','family':'web','query':'Explore the topic'},
                         {'id':'fact','family':'web','query':'Which number?','reference_answer':'42','reference_urls':['https://reference.test/fact']}]}
    rows=[]
    for case in profile['cases']:
        for method in profile['methods']:
            results = ([] if method['id']=='b' and case['id']!='open' else
                       [{'url':'https://other.test/page','title':'Other'}, {'url':'https://reference.test/page','title':'Reference'}] if case['id']=='known' else
                       [{'url':'https://reference.test/fact','title':'42'}] if case['id']=='fact' else
                       [{'url':'https://other.test/topic','title':'Topic'}])
            rows.append({'id':case['id']+'-'+method['id'],'case_id':case['id'],'method':method['id'],'round':0,
                         'results':results,'metrics':evaluate(case,results),'elapsed_ms':100,'cost':None,
                         'http_status':200,'error':''})
    original=copy.deepcopy(rows)
    packet,_=candidate_packet(profile,rows)
    grades={p['case_id']:{x['candidate_id']:(0 if x['url']=='https://other.test/page' else 3) for x in p['candidates']} for p in packet['blind_candidate_packets']}
    scored=score(profile,rows,packet['plan'],grades)
    content={'independent_benchmarks':True,'summary_metrics':'retrieval','metric_populations':[
        {'id':'custom','benchmark_root':True,'label':{'zh':'场景','en':'Scenarios'},'case_ids':['known','open']},
        {'id':'factual','benchmark_root':True,'label':{'zh':'事实','en':'Facts'},'case_ids':['fact']}],
        'catalog_pricing':{'a':{'amount':None,'currency':'USD','unit':'1000-successful-searches'},
                           'b':{'amount':5,'currency':'USD','unit':'1000-successful-searches','source':'https://prices.test/','checked_at':'2026-10-22'}}}
    for zh in (True,False):
        dom=BeautifulSoup(population_summary(profile,rows,content,zh,relevance_scores=scored),'html.parser')
        custom=dom.select_one('[data-population="custom"]')
        assert [x.get_text() for x in custom.select('table')[0].select('th')][1:4]==['nDCG@5 × 100','Precision@5','Success@5']
        assert not custom.select_one('[data-reference-retrieval]')
        assert len(dom.select('[data-population]')) == 2
        assert all(t.select('thead button[data-sort-column]') for t in dom.select('table'))
        assert not dom.select_one('[data-population="factual"]').find('th',string='nDCG@5 × 100')
        for article in dom.select('[data-population]'):
            assert article.select('table')[0].select('tbody tr')[0].select('td')[-1]['data-value']==''
            assert article.select('table')[0].select('tbody tr')[1].select('td')[-1]['data-value']=='5'
        for html in (str(dom),cost_panel(profile,rows,zh,content),benchmark_operations(profile,rows,content,zh)):
            assert '待确认' not in html and 'unknown billing' not in html
        factual=BeautifulSoup(factual_panel(profile,rows,zh,compact=True),'html.parser')
        assert 'MRR@5' in factual.get_text()
    assert rows==original
    with pytest.raises(ValueError, match='every method and query'):
        population_summary(profile, rows, content, False, relevance_scores=[r for r in scored if r['method'] != 'b'])
    content['catalog_pricing']['a'].update(amount=0.75, unit='1000-requests', source='https://prices.test/lexmount', checked_at='2026-11-12')
    priced = BeautifulSoup(population_summary(profile, rows, content, False, relevance_scores=scored), 'html.parser')
    assert priced.select_one('tbody tr td:last-child')['data-sort'] == '0.75'
    for zh in (False, True):
        for html in (population_summary(profile, rows, content, zh, relevance_scores=scored),
                     cost_panel(profile, rows, zh, content),
                     benchmark_operations(profile, rows, content, zh)):
            dom = BeautifulSoup(html, 'html.parser')
            basis = dom.select('[data-billing-basis]')
            assert {node['data-billing-basis'] for node in basis} == {
                '1000-requests', '1000-successful-searches'}
            assert all(node.get_text().strip() for node in basis)
            assert all(node.parent['data-metric-direction'] == 'neutral' for node in basis)
            assert all('win' not in node.parent.get('class', []) for node in basis)
    content['catalog_pricing']['b']['amount']=-1
    with pytest.raises(ValueError,match='catalog price'):
        population_summary(profile,rows,content,True,relevance_scores=scored)


@pytest.mark.parametrize('reading_model,section_page,filename', [
    (True, 'lead', 'index.html'),
    (False, 'method', 'research.html'),
    (False, 'interpretation', 'interpretation.html'),
    (False, 'summary', 'index.html'),
])
def test_bound_data_figures_render_in_both_languages_and_reject_wrong_images(
    tmp_path, reading_model, section_page, filename,
):
    from harness.web_search.report import authored_sections

    fixture = tmp_path / 'fixture'
    example(fixture)
    image = tmp_path / 'distribution.svg'
    image.write_text('<svg xmlns="http://www.w3.org/2000/svg"><title>Counts: 3 and 7</title></svg>', encoding='utf-8')
    data = {'reading_model': True, 'sections': [{'page': section_page,
            'title': {'en': 'Source coverage', 'zh': '来源覆盖'},
            'figures': [{'path': 'report-assets/distribution.svg',
                         'alt': {'en': '3 source hits, 7 misses', 'zh': '命中 3 题，未命中 7 题'},
                         'caption': {'en': 'All 10 queries.', 'zh': '全部 10 题。'}}]}],
            'assets': [{'source': image.name, 'path': 'report-assets/distribution.svg',
                        'sha256': bytes_sha256(image.read_bytes())}]}
    content = tmp_path / 'content.json'
    if not reading_model:
        data.pop('reading_model')
    content.write_text(json.dumps(data), encoding='utf-8')
    out = tmp_path / 'report'
    build(out, source=fixture / "fixture", content=content, include_english=True)
    assert check(out)
    for route in (filename, 'en/' + filename):
        dom = BeautifulSoup((out / route).read_text(encoding='utf-8'), 'html.parser')
        assert dom.select_one('[data-authored-figure] img')['src'] == 'report-assets/distribution.svg'
    page = out / filename
    original = page.read_text(encoding='utf-8')
    for missing in (False, True):
        dom = BeautifulSoup(original, 'html.parser')
        if missing:
            dom.select_one('[data-authored-figure]').decompose()
        else:
            dom.select_one('[data-authored-figure] img')['src'] = 'report-assets/wrong.svg'
        page.write_text(str(dom), encoding='utf-8')
        with pytest.raises(ValueError, match='figure|resource|link'):
            check(out)
    data['assets'] = []
    with pytest.raises(ValueError, match='hash-bound'):
        authored_sections(data, section_page, 'zh')


@pytest.mark.parametrize('site_prefix', ['', 'social'])
def test_owner_quote_assets_keep_report_root_links_in_both_editions(tmp_path, site_prefix):
    example(tmp_path, comparison=True)
    content_path = tmp_path / 'content.json'
    content = json.loads(content_path.read_text(encoding='utf-8'))
    quote = tmp_path / 'quote.txt'
    quote.write_text('Synthetic quote: USD 1 per 1,000 requests.', encoding='utf-8')
    content['assets'] = [{'source': quote.name, 'path': 'quotes/price.txt',
                          'sha256': bytes_sha256(quote.read_bytes())}]
    profile = json.loads((tmp_path / 'fixture/profile.json').read_bytes())
    content['catalog_pricing'] = {method['id']: {
        'amount': 1, 'currency': 'USD', 'unit': '1000-requests',
        'source': 'quotes/price.txt', 'source_kind': 'owner-quote',
        'checked_at': '2026-11-12'} for method in profile['methods']}
    content_path.write_text(json.dumps(content), encoding='utf-8')
    out = tmp_path / 'report' / site_prefix if site_prefix else tmp_path / 'report'
    build(out, source=tmp_path / 'fixture', content=content_path,
          relevance=tmp_path / 'labels.json', site_prefix=site_prefix, include_english=True)
    assert check(out)
    expected = (site_prefix + '/' if site_prefix else '') + 'quotes/price.txt'
    for route in ('performance.html', 'en/performance.html'):
        page = out / route
        original = page.read_text(encoding='utf-8')
        soup = BeautifulSoup(original, 'html.parser')
        links = soup.select('[data-cost-budget] a[href]')
        assert len(links) == 2 and all(link['href'] == expected for link in links)
        links[0]['href'] = 'wrong/quotes/price.txt'
        page.write_text(str(soup), encoding='utf-8')
        with pytest.raises(ValueError, match='Cost budget'):
            check(out)
        page.write_text(original, encoding='utf-8')
