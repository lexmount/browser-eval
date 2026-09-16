import hashlib
import copy
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.report import generate_webmainbench, _wmb_pct, _wmb_body_state, _wmb_row_attrs
from harness.web_fetch.report_build import validate_webmainbench_correction
from harness.web_fetch.report_style import STYLE


PROVIDERS = ("kitesurf", "lightpanda", "obscura", "moli", "chromium")
METRICS = ("text_edit", "code_edit", "table_edit", "table_TEDS", "formula_edit")


class WebMainBenchReportTests(unittest.TestCase):
    def test_narrow_reliability_banner_wraps_instead_of_clipping(self):
        self.assertIn(
            '.reliability strong{white-space:normal;overflow-wrap:anywhere}',
            STYLE,
        )

    def test_search_index_bounds_logs_but_keeps_terminal_error(self):
        message='initial-warning\n'+'x'*50000+'\nterminal-navigation-error'
        record={'provider':'obscura','ok':False,'error_type':'browser_exit','error_message':message}
        attrs=_wmb_row_attrs({'id':'case','url':'https://example.test','meta':{}},record)
        self.assertLess(len(attrs), 2000)
        self.assertIn('initial-warning',attrs)
        self.assertIn('terminal-navigation-error',attrs)
        self.assertEqual(record['error_message'],message)

    def test_body_state_does_not_treat_empty_or_error_output_as_body(self):
        for english in (False, True):
            self.assertEqual(_wmb_body_state({'ok': True}, '  ', english),
                             'No body text' if english else '没有正文')
            self.assertEqual(_wmb_body_state({'ok': True}, 'ERR_ACCESS_DENIED', english),
                             'Error page, not target content' if english else '错误页，非目标正文')
            self.assertEqual(_wmb_body_state({'ok': True}, 'An ordinary article.', english),
                             'Body text produced' if english else '正文已生成')

    def test_small_nonzero_scores_do_not_round_to_zero(self):
        self.assertEqual(_wmb_pct(0), '0.0%')
        self.assertEqual(_wmb_pct(.5), '50.0%')
        self.assertEqual(_wmb_pct(None), '—')
        for value in (.00000732233609980177, .000234599901272541):
            self.assertGreater(float(_wmb_pct(value).rstrip('%')), 0)

    def test_correction_must_match_frozen_scope(self):
        dataset = {'cases': [{'id': 'case-one'}], 'coverage_plan': {'static_html': ['moli']},
                   'sample_manifest_sha256': 'a' * 64, 'source_dataset_sha256': 'b' * 64}
        receipt = {'case_id': 'case-one', 'providers': ['moli'], 'effects': {'moli': {}},
                   'sample_manifest_sha256': 'a' * 64, 'source_dataset_sha256': 'b' * 64}
        validate_webmainbench_correction(receipt, dataset)
        for change in [{'case_id': '../unknown'}, {'providers': ['unknown']},
                       {'providers': ['moli', 'moli']}, {'effects': {}},
                       {'source_dataset_sha256': 'c' * 64}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_webmainbench_correction({**receipt, **change}, dataset)

    def test_generates_matching_bilingual_route_inventory(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            case_id = "case-one"
            dataset = {
                "source_dataset_sha256": "a" * 64,
                "sample_manifest_sha256": "e" * 64,
                "coverage_plan": {"static_html": list(PROVIDERS)},
                "cases": [{
                    "id": case_id,
                    "url": "https://example.test/article",
                    "meta": {"language": "en", "level": "simple", "table": [], "code": [], "equation": []},
                    "input_html_sha256": "b" * 64,
                    "reference_sha256": hashlib.sha256(b"# Reference").hexdigest(),
                }],
            }
            (output / "references").mkdir()
            (output / "references" / f"{case_id}.md").write_text("# Reference", encoding="utf-8")
            (output / "sources").mkdir()
            (output / "sources" / f"{case_id}.html.txt").write_text("<h1>Frozen input</h1>", encoding="utf-8")
            records = []
            summaries = {}
            manifests = []
            for index, provider in enumerate(PROVIDERS, 1):
                score = index / 10
                records.append({
                    "provider": provider,
                    "case_id": case_id,
                    "attempt_id": f"{provider}__{case_id}__r001",
                    "run_id": f"run-{provider}",
                    "ok": True,
                    "total_elapsed_ms": index * 100,
                    "artifact_path": f"artifacts/{provider}/{case_id}.md",
                    "output_sha256": hashlib.sha256(f"output from {provider}".encode()).hexdigest(),
                    "physical_attempt_count": 1,
                    "metrics": {"overall": {"score": score}},
                })
                artifact = output / f"artifacts/{provider}/{case_id}.md"
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text(f"output from {provider}", encoding="utf-8")
                summaries[provider] = {
                    "request_successes": 1,
                    "request_success_rate": 1.0,
                    "official_aggregate": {
                        "scores": {"overall": score, **{name: score for name in METRICS}},
                        "denominators": {name: 1 for name in METRICS},
                    },
                }
                manifests.append({
                    "provider": provider,
                    "started_at": "2026-09-14T00:00:00Z",
                    "completed_at": "2026-09-14T00:01:00Z",
                    "case_count": 1,
                    "timeout_seconds": 45,
                    "runner_sha256": "d" * 64,
                    "implementation_identity": {
                        "reported_version": "test",
                        "binary_sha256": "c" * 64,
                        "deployment": "fixture",
                    },
                })
            generate_webmainbench(
                output,
                dataset,
                records,
                summaries,
                manifests,
                {"status": "passed", "reviewed_at": "2026-09-14T00:00:00Z", "checks": []},
            )
            chinese = {path.relative_to(output).as_posix() for path in output.rglob("*.html") if "en" not in path.relative_to(output).parts}
            english = {path.relative_to(output / "en").as_posix() for path in (output / "en").rglob("*.html")}
            self.assertEqual(chinese, english)
            self.assertEqual(len(chinese), 21)
            self.assertIn("attempts/kitesurf__case-one__r001.html", chinese)
            self.assertIn('data-wmb-table', (output / "analysis/index.html").read_text(encoding="utf-8"))
            analysis = (output / "analysis/index.html").read_text(encoding="utf-8")
            analysis_en = (output / "en/analysis/index.html").read_text(encoding="utf-8")
            self.assertIn('id="wmb-compare-rows"', analysis)
            self.assertIn('id="wmb-compare-rows"', analysis_en)
            self.assertIn('data-delivered="kitesurf lightpanda obscura moli chromium"', analysis)
            self.assertIn('data-wmb-filter="request" data-wmb-default="failed"', analysis)
            self.assertIn('data-wmb-filter="request" data-wmb-default="failed"', analysis_en)
            self.assertIn('value="failed"', analysis)
            self.assertIn('value="success"', analysis)
            guide = (output / "guide/index.html").read_text(encoding="utf-8")
            guide_en = (output / "en/guide/index.html").read_text(encoding="utf-8")
            for anchor in ("background", "results", "examples", "configuration", "limits", "outlook"):
                self.assertIn(f'id="{anchor}"', guide)
                self.assertIn(f'id="{anchor}"', guide_en)
            self.assertIn('data-kind="reference"', (output / "attempts/kitesurf__case-one__r001.html").read_text(encoding="utf-8"))
            self.assertIn("WebMainBench-545", (output / "index.html").read_text(encoding="utf-8"))
            self.assertIn("table-structure score validity did not", (output / "en/index.html").read_text(encoding="utf-8"))
            self.assertIn("Response and body", analysis_en)
            self.assertIn("API/worker response and final content", (output / "en/attempts/kitesurf__case-one__r001.html").read_text(encoding="utf-8"))
            for row in records:
                row['official_metrics'] = copy.deepcopy(row['metrics'])
                row['official_metrics']['table_TEDS'] = {'score': .024347507646390047, 'success': True}
                row['metrics']['table_TEDS'] = {'score': .00000732233609980177, 'success': True}
            revision_review = {'logical_results': 5, 'changed_scores': 5,
                               'impacts': {name: {'official': summary['official_aggregate'],
                                                 'corrected': summary['official_aggregate']}
                                           for name, summary in summaries.items()}}
            immutable_records = copy.deepcopy(records)
            generate_webmainbench(output, dataset, records, summaries, manifests,
                                 {'status': 'passed'}, teds_revision=({}, revision_review))
            self.assertEqual(records, immutable_records)
            for prefix, label, original in (('', '本地修正版', '官方原始分'),
                                            ('en/', 'locally corrected', 'Original official score')):
                for route in ('index.html', 'analysis/index.html', 'providers/kitesurf.html',
                              'attempts/kitesurf__case-one__r001.html'):
                    page = (output / prefix / route).read_text()
                    self.assertIn(label, page)
                detail = (output / prefix / 'attempts/kitesurf__case-one__r001.html').read_text()
                self.assertIn(original, detail)
                self.assertIn('0.000732234%', detail)
                review_page = (output / prefix / 'review/index.html').read_text()
                self.assertIn('wmb-correction-effects', review_page)
                self.assertIn('original-results.jsonl', review_page)
            # A successful API response containing an error page is not target body text.
            (output / 'artifacts/chromium/case-one.md').write_text('ERR_ACCESS_DENIED')
            generate_webmainbench(output, dataset, records, summaries, manifests,
                                 {'status': 'passed'}, teds_revision=({}, revision_review))
            self.assertEqual(records, immutable_records)
            for prefix, error_label in (('', '错误页，非目标正文'), ('en/', 'Error page, not target content')):
                analysis = (output / prefix / 'analysis/index.html').read_text()
                provider = (output / prefix / 'providers/chromium.html').read_text()
                detail = (output / prefix / 'attempts/chromium__case-one__r001.html').read_text()
                self.assertIn('data-delivered="kitesurf lightpanda obscura moli" data-failed="chromium"', analysis)
                for page in (analysis, provider):
                    self.assertIn('data-method="chromium" data-request="failed" data-failure="error_page"', page)
                    self.assertIn(error_label, page)
                self.assertIn(error_label, detail)


if __name__ == "__main__":
    unittest.main()
