"""Matched retrieval scenarios for SERP services and finite account quotas."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from harness.web_search.core import digest, normalize, normalize_response, request_body
from lexbench_websearch.scoring import evaluate
from lexbench_websearch.inputs import definition_path, load_dataset, resolve_profile
from harness.web_search.revisions import apply_report_revision, collected_projection, normalization_regressions, NATIVE_CONTROLS
from harness.web_search.runner import prepare_execution_revision, run
from harness.web_search.delivery import export, verify
from harness.web_search.report import EVIDENCE_SCRIPT, build, check, load_run
from bs4 import BeautifulSoup


def synthetic_serp_profile():
    """Exercise adapters and finite budgets without a published report instance."""
    methods = []
    for name in ("lexmount", "browserbase", "brightdata", "serper"):
        options = {"zone": "fixture-zone"} if name == "brightdata" else {}
        if name == "browserbase":
            options = {"provider": "parallel", "mode": "advanced"}
        methods.append({"id": name, "label": name, "adapter": name,
                        "endpoint": f"https://{name}.invalid/search", "key_env": name.upper() + "_KEY",
                        "currency": "USD", "billing": "http-200", "price_per_1000": 1,
                        "output_options": options})
    return {"schema": "web-search/1", "title": "Synthetic SERP adapter scenarios",
            "protocol": {"top_k": 5, "normalize_top_k": True, "rounds": 1, "score_round": 0,
                         "concurrency": 1, "server_timeout_ms": 1000, "client_timeout_s": 2,
                         "interval_s": 0, "retry": False, "order": "complete provider batches"},
            "methods": methods,
            "cases": [{"id": f"fixture-{i}", "query": f"Synthetic query {i}", "family": "web",
                       "language": "en", "origin": "Synthetic fixture",
                       "anchors": [f"https://reference.invalid/{i}"]} for i in range(2)]}


class SerpContracts(unittest.TestCase):
    def test_interface_revision_preserves_scarce_calls_across_a_second_interruption(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = synthetic_serp_profile()
            # Resume preserves the predeclared order of a historical cohort.
            profile["protocol"]["order"] = "rotate methods by case"
            profile["cases"] = profile["cases"][:2]
            profile["methods"][0].pop("output_options", None)
            profile["protocol"].update(interval_s=0, max_calls_per_method={m["id"]: 3 if m["id"] == "lexmount" else 4 if m["id"] == "brightdata" else 2 for m in profile["methods"]})
            profile["protocol"].pop("reference_scope", None)
            profile.pop("reference_checks", None)
            profile.pop("reference_artifacts", None)
            original = root / "original.json"
            original.write_text(json.dumps(profile))
            calls, phase, revised_calls = [], 0, 0

            def http(url, body=None, header=None, timeout=20):
                nonlocal revised_calls
                receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error="")
                if body is None:
                    return receipt, b"reference " * 20
                if phase == 0 and len(calls) == 5 or phase == 1 and revised_calls == 1:
                    raise RuntimeError("Synthetic process interruption")
                method = next(m["id"] for m in profile["methods"] if m["endpoint"] == url)
                calls.append(method)
                if phase:
                    revised_calls += 1
                    if method == "lexmount":
                        self.assertEqual(set(body), {"query", "limit"})
                item = {"url": "https://example.org/", "link": "https://example.org/", "title": "Synthetic result"}
                return receipt, json.dumps({"organic": [item]} if method in {"brightdata", "serper"} else {"results": [item]}).encode()

            env = {m["key_env"]: "test-" + m["id"] for m in profile["methods"]}
            with patch("harness.web_search.runner.http_call", side_effect=http), patch("harness.web_search.runner.load_runtime_env"), patch.dict(os.environ, env):
                with self.assertRaisesRegex(RuntimeError, "interruption"):
                    run(original, root / "run", certified=True)
                original_bytes = (root / "run/results.jsonl").read_bytes()
                original_profile = original.read_bytes()
                profile["methods"][0]["output_options"] = {"interface": "public"}
                corrected = root / "corrected.json"
                corrected.write_text(json.dumps(profile))
                proof = root / "interface.json"
                proof.write_text('{"synthetic":true,"accepted_fields":["query","limit"]}')
                revision = {"schema": "web-search-execution-revision/1", "root_cause": "Synthetic service interface changed", "superseded_methods": ["lexmount"], "previous_profile_sha256": digest(original_profile), "profile_sha256": digest(corrected.read_bytes()), "evidence": [{"path": proof.name, "sha256": digest(proof.read_bytes())}]}
                revision_path = root / "revision.json"
                revision_path.write_text(json.dumps(revision))
                # A correction must account for old physical calls before spending again.
                profile["protocol"]["max_calls_per_method"]["lexmount"] = 2
                insufficient = root / "insufficient.json"
                insufficient.write_text(json.dumps(profile))
                revision_path.write_text(json.dumps({**revision, "profile_sha256": digest(insufficient.read_bytes())}))
                with self.assertRaisesRegex(ValueError, "physical call budget"):
                    prepare_execution_revision(insufficient, root / "run", revision_path)
                self.assertEqual((root / "run/profile.json").read_bytes(), original_profile)
                self.assertFalse((root / "run/resume-1").exists())
                revision_path.write_text(json.dumps(revision))
                phase = 1
                with self.assertRaisesRegex(RuntimeError, "interruption"):
                    run(corrected, root / "run", resume=True, execution_revision=revision_path, certified=True)
                phase = 2
                run(corrected, root / "run", resume=True, certified=True)
            _, rows, receipt = load_run(root / "run")
            self.assertEqual({m: calls.count(m) for m in set(calls)}, {"lexmount": 3, "browserbase": 2, "brightdata": 2, "serper": 2})
            self.assertEqual(len(rows), 8)
            self.assertEqual(receipt["physical_call_count"], 8)
            # Two interrupted sends remain billable/unknown, even without responses.
            self.assertEqual(receipt["total_retained_physical_call_count"], 11)
            self.assertTrue(receipt["certified"])
            self.assertEqual((root / "run/resume-1/previous-results.jsonl").read_bytes(), original_bytes)
            archived = next((root / "run/resume-1/calls").glob("*.json"))
            archived.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_run(root / "run")

    def test_malformed_tail_cannot_invalidate_scored_slots_or_promote_later_results(self):
        protocol = {"top_k": 5, "normalize_top_k": True, "result_url_resolution": "request-url/1"}
        pages = [{"link": f"https://example.org/{i}"} for i in range(6)]
        for adapter in ("brightdata", "serper"):
            for invalid in (None, "invalid", {"link": "javascript:alert(1)"}):
                with self.subTest(adapter=adapter, invalid=invalid):
                    result = normalize_response({"organic": pages[:5] + [invalid]}, {"adapter": adapter}, protocol)
                    self.assertEqual([r["url"] for r in result], [p["link"] for p in pages[:5]])
                    with self.assertRaises(ValueError):
                        normalize_response({"organic": pages[:4] + [invalid] + pages[5:]}, {"adapter": adapter}, protocol)

    def test_native_url_correction_uses_original_bytes_without_new_provider_calls(self):
        registry = NATIVE_CONTROLS
        revision = {"schema": "web-search-assessment-revision/1", "root_cause": "Native relative links were rejected and unscored tail entries invalidated the first five slots", "regression_ids": ["relative-serp-links", "unsafe-serp-links", "top-five-boundary"], "provider_calls": 0, "normalization": "request-url/1", "regressions_sha256": digest(registry.read_bytes())}
        normalization_regressions(revision["regressions_sha256"], normalize_response)
        profile = {"methods": [{"id": "native", "adapter": "brightdata"}], "protocol": {"top_k": 5, "normalize_top_k": True}, "cases": [{"id": "q", "family": "web", "anchors": ["https://www.google.com/goto?url=synthetic"]}]}
        raw = json.dumps({"organic": [{"link": "/goto?url=synthetic", "title": "Native result"}]}).encode()
        row = {"id": "q-native-r0", "case_id": "q", "method": "native", "http_status": 200, "transport_exit": 0, "elapsed_ms": 321, "cost": .0015, "error": "Result URL must be an HTTP(S) URL without credentials", "results": [], "request": {"url": "https://www.google.com/search?q=synthetic"}, "evidence": {"path": "calls/q.json", "sha256": digest(raw)}, "metrics": evaluate(profile["cases"][0], [])}
        with patch("harness.web_search.runner.http_call") as network:
            updated, rows, changes = apply_report_revision(profile, [row], revision, artifacts={"calls/q.json": raw}, normalizer=normalize_response)
            network.assert_not_called()
        self.assertEqual(rows[0]["metrics"]["anchor_hit_5"], 1)
        self.assertEqual(rows[0]["elapsed_ms"], 321)
        self.assertEqual(rows[0]["cost"], .0015)
        self.assertEqual(rows[0]["evidence"], row["evidence"])
        self.assertEqual(changes[0]["normalization"]["before_count"], 0)
        self.assertEqual(changes[0]["normalization"]["after_count"], 1)
        self.assertEqual(row["results"], [])
        self.assertNotIn("result_url_resolution", profile["protocol"])
        restored = collected_projection(profile, rows, {"calls/q.json": raw}, normalize_response)
        self.assertEqual(restored[0]["results"], row["results"])
        self.assertEqual(restored[0]["metrics"], row["metrics"])
        self.assertEqual(restored[0]["error"], row["error"])
        for broken in ({**revision, "regression_ids": []}, {**revision, "regressions_sha256": "0" * 64}):
            with self.assertRaises(ValueError):
                apply_report_revision(profile, [row], broken, artifacts={"calls/q.json": raw}, normalizer=normalize_response)
        with self.assertRaisesRegex(ValueError, "artifact binding"):
            apply_report_revision(profile, [row], revision, artifacts={"calls/q.json": raw + b"changed"}, normalizer=normalize_response)

    @unittest.skipUnless(shutil.which("node"), "Node executes the shipped evidence viewer")
    def test_reader_can_open_the_selected_native_result_for_every_response_shape(self):
        selected = {"url": "https://example.org/chosen", "snippet": "Exact native result"}
        other = {"url": "https://example.org/other"}
        shapes = [{"results": [other, selected]}, {"organic": [other, selected]},
                  {"data": {"webPages": {"value": [other, selected]}}},
                  {"status_code": 200, "body": json.dumps({"organic": [other, selected]})},
                  {"status_code": 200, "body": {"organic": [other, selected]}}]
        script = EVIDENCE_SCRIPT + "\nconsole.log(JSON.stringify(" + json.dumps(shapes) + ".map(v=>nativeEvidenceResult(v,1))));"
        result = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), [selected] * len(shapes))
        invalid = EVIDENCE_SCRIPT + "\ntry{nativeEvidenceResult({organic:[]},0);process.exit(1)}catch{process.exit(0)}"
        self.assertEqual(subprocess.run([shutil.which("node"), "-e", invalid]).returncode, 0)

    def test_queries_are_preserved_and_no_page_fetch_or_answer_requested(self):
        query = 'site:docs.python.org "asyncio" after:2025-01-01 中文'
        case, protocol = {"query": query}, {"top_k": 5}
        bb = request_body("browserbase", case, protocol, {"provider": "parallel", "mode": "advanced"})
        self.assertEqual(bb, {"query": query, "numResults": 5, "provider": "parallel", "mode": "advanced"})
        self.assertEqual(request_body("lexmount", case, protocol, {"interface": "public"}), {"query": query, "limit": 5})
        source = {"query": "a source query", "source": "github", "domains": ["github.com"]}
        self.assertEqual(request_body("lexmount", source, protocol, {"interface": "public"})["query"], request_body("browserbase", source, protocol)["query"])
        self.assertEqual(request_body("serper", case, protocol)["q"], query)
        bd = request_body("brightdata", case, protocol, {"zone": "serp_test"})
        params = parse_qs(urlsplit(bd["url"]).query)
        self.assertEqual(params["q"], [query])
        self.assertEqual(bd["format"], "json")
        self.assertEqual(bd["data_format"], "parsed")
        with self.assertRaisesRegex(ValueError, "zone"):
            request_body("brightdata", case, protocol)

    def test_only_first_five_organic_results_count_in_native_order(self):
        pages = [{"link": f"https://example.org/{i}", "title": str(i), "description": "text"} for i in range(10)]
        for adapter in ("brightdata", "serper"):
            with self.subTest(adapter=adapter):
                native = {"organic": pages, "ads": [{"link": "https://ad.example/"}], "answerBox": {"answer": "ignored"}}
                rows = normalize(native, adapter, 5)
                self.assertEqual([r["url"] for r in rows], [p["link"] for p in pages[:5]])
                self.assertTrue(all(r["snippet"] == "text" for r in rows))
                self.assertTrue(all(not r["content"] and not r["summary"] for r in rows))
                self.assertEqual(normalize({"organic": []}, adapter, 5), [])
                for invalid in ({}, {"organic": None}, {"organic": [None]}, {"organic": [], "error": "quota"}):
                    with self.assertRaises(ValueError):
                        normalize(invalid, adapter, 5)

    def test_brightdata_http_success_does_not_hide_upstream_failure(self):
        response = {"status_code": 200, "body": json.dumps({"organic": [{"link": "https://example.org/", "description": "Native snippet"}]})}
        self.assertEqual(normalize(response, "brightdata", 5)[0]["snippet"], "Native snippet")
        for value in ({"status_code": 500, "body": "blocked"}, {"status_code": 200, "body": "not JSON"}):
            with self.assertRaises(ValueError):
                normalize(value, "brightdata", 5)

    def test_no_missing_browserbase_snippet_is_invented(self):
        row = normalize({"results": [{"url": "https://example.org/", "title": "Only a title", "publishedDate": "2026-01-01"}]}, "browserbase", 5)[0]
        self.assertEqual(row["snippet"], "")
        self.assertEqual(row["date"], "2026-01-01")

    def test_insufficient_worst_case_budget_fails_before_output_or_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p = synthetic_serp_profile()
            p["protocol"]["retry"] = True
            p["protocol"]["max_calls_per_method"] = len(p["cases"])  # one retry doubles the ceiling
            profile = root / "profile.json"
            profile.write_text(json.dumps(p))
            with patch("harness.web_search.runner.http_call") as call:
                with self.assertRaisesRegex(ValueError, "budget"):
                    run(profile, root / "run")
                call.assert_not_called()
                self.assertFalse((root / "run").exists())

    def test_all_four_methods_receive_the_complete_matrix_with_native_authentication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = synthetic_serp_profile()
            profile["cases"] = profile["cases"][:2]
            profile["cases"][0]["provenance_kind"] = "community_exact"
            profile["protocol"].update(interval_s=0, max_calls_per_method=2)
            profile["protocol"].pop("reference_scope", None)
            profile.pop("reference_checks", None)
            profile.pop("reference_artifacts", None)
            p = root / "input.json"
            p.write_text(json.dumps(profile))
            calls = []

            def http(url, body=None, header=None, timeout=20):
                receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error="")
                if body is None:
                    return receipt, b"reference " * 20
                calls.append((url, body, header))
                pages = [{"link": f"https://example.org/{i}", "title": str(i), "description": "snippet"} for i in range(10)]
                if "brightdata" in url:
                    pages[0]["link"] = "/goto?url=synthetic"
                    value = {"status_code": 200, "body": json.dumps({"organic": pages})}
                    if parse_qs(urlsplit(body["url"]).query)["q"] == [profile["cases"][1]["query"]]:
                        value = {"status_code": 502, "body": "", "headers": {"x-brd-error-code": "captcha", "x-brd-error": "redirect location was rejected"}}
                    value.setdefault("headers", {})["set-cookie"] = ["synthetic-private-session"]
                    self.assertEqual(header, "Authorization: Bearer test-brightdata")
                elif "serper" in url:
                    value = {"organic": pages}
                    self.assertEqual(header, "X-API-KEY: test-serper")
                else:
                    value = {"results": [{"url": p["link"], "title": p["title"]} for p in pages[:5]]}
                    self.assertEqual(header, "x-bb-api-key: test-browserbase" if "browserbase" in url else "X-API-Key: test-lexmount")
                return receipt, json.dumps(value, indent=2).replace("\n", "\r\n").encode()

            env = {m["key_env"]: "test-" + m["id"] for m in profile["methods"]}
            with patch("harness.web_search.runner.http_call", side_effect=http), patch("harness.web_search.runner.load_runtime_env"), patch.dict(os.environ, env):
                run(p, root / "run", certified=True)
            _, rows, receipt = load_run(root / "run")
            self.assertEqual(len(calls), 8)
            self.assertEqual({(r["case_id"], r["method"]) for r in rows}, {(c["id"], m["id"]) for c in profile["cases"] for m in profile["methods"]})
            self.assertTrue(all(len(r["results"]) == (0 if r["method"] == "brightdata" else 5) for r in rows))
            self.assertEqual(receipt["physical_call_count"], 8)
            self.assertTrue(receipt["certified"])
            contract = json.loads((root / "run/run-contract.json").read_text())
            self.assertIn("no retries", contract["timer"])
            # Correct the saved native projection through the public report boundary,
            # preserving every paid call and exposing the correction's provenance.
            revision = {"schema": "web-search-assessment-revision/1", "root_cause": "Native relative links require the recorded request URL", "regression_ids": ["relative-serp-links", "unsafe-serp-links", "top-five-boundary"], "provider_calls": 0, "normalization": "request-url/1", "regressions_sha256": digest(NATIVE_CONTROLS.read_bytes())}
            content = root / "content.json"
            (root / "controls.json").write_bytes(NATIVE_CONTROLS.read_bytes())
            content.write_text(json.dumps({"presentation": "comparison-report", "summary_metrics": "retrieval", "sections": [], "assessment_revision": revision, "assets": [{"source": "controls.json", "path": "report-assets/scoring-regressions.json", "sha256": revision["regressions_sha256"]}]}))
            from tests.web_search.test_native_revisions import revision_instance
            instance = revision_instance(root, revision)
            with patch("harness.web_search.runner.http_call") as network:
                build(root / "report", source=root / "run", content=content, profile_path=instance)
                _, corrected_rows = check(root / "report", return_data=True)
                network.assert_not_called()
            manifest = json.loads((root / "report/manifest.json").read_text())
            for row in (r for r in rows if r["method"] == "brightdata"):
                path = row["evidence"]["path"]
                self.assertIn("synthetic-private-session", (root / "run" / path).read_text())
                self.assertNotIn("synthetic-private-session", (root / "report" / path).read_text())
                self.assertTrue(any(r["path"] == path for r in manifest["evidence_redactions"]))
            dataset_text = BeautifulSoup(
                (root / "report/dataset.html").read_text(), "html.parser"
            ).get_text()
            self.assertIn("接口请求相同数量", dataset_text)
            self.assertNotIn("三家", dataset_text)
            method_page = BeautifulSoup((root / "report/research.html").read_text(), "html.parser")
            fairness = method_page.select_one("[data-fairness]")
            self.assertNotIn("三家", fairness.get_text())
            self.assertEqual([row.select("td")[2].get_text() for row in fairness.select("tbody tr")], ["5 × 2", "5 × 2", "默认 × 2", "5 × 2"])
            performance = BeautifulSoup((root / "report/performance.html").read_text(), "html.parser")
            conditional = performance.select_one("[data-successful-latency]")
            self.assertIsNotNone(conditional)
            self.assertEqual([row.select("td")[2].get_text() for row in conditional.select("tbody tr")], ["2", "2", "1", "2"])
            failed_id = profile["cases"][1]["id"] + "-brightdata-r0"
            self.assertTrue(all(len(r["results"]) == (0 if r["id"] == failed_id else 5) for r in corrected_rows))
            self.assertEqual(len(manifest["assessment_revision"]["changes"]), 1)
            self.assertEqual(manifest["receipt"]["physical_call_count"], 8)
            detail = root / "report/details" / (failed_id + ".html")
            original_detail = detail.read_text()
            error = BeautifulSoup(original_detail, "html.parser").select_one("[data-error]").get_text()
            for value in ("status_code: 502", "x-brd-error-code: captcha", "redirect location was rejected"):
                self.assertIn(value, error)
            detail.write_text(original_detail.replace("x-brd-error-code: captcha", "x-brd-error-code: removed"))
            with self.assertRaisesRegex(ValueError, "Exact error"):
                check(root / "report")
            detail.write_text(original_detail)
            comparison = root / "report/results.html"
            original_comparison = comparison.read_text()
            parsed = BeautifulSoup(original_comparison, "html.parser")
            data_node = parsed.select_one("[data-comparison-data]")
            data = json.loads(data_node.string)
            self.assertEqual(data["primary_factual_metric"], "source")
            data["primary_factual_metric"] = "answer"
            data_node.string = json.dumps(data)
            comparison.write_text(str(parsed))
            with self.assertRaisesRegex(ValueError, "Result comparison"):
                check(root / "report")
            comparison.write_text(original_comparison)
            export(root / "report", root / "delivery")
            self.assertTrue(verify(root / "report", root / "delivery"))
            packed = json.loads((root / "delivery/delivery-index.json").read_text())
            self.assertTrue(all(r["evidence"]["path"] in packed["files"] for r in rows))
            manifest["assessment_revision"]["changes"] = []
            (root / "report/manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "provenance"):
                check(root / "report")

    def test_external_profile_composes_every_benchmark_case_and_reference(self):
        originals = [load_dataset(name) for name in ("scenarios-v1", "factual-v1")]
        profile = resolve_profile({"cases": {"field": "cases", "benchmark_datasets": [
            {"id": name, "sha256": digest(definition_path("datasets/" + name + ".json").read_bytes())}
            for name in ("scenarios-v1", "factual-v1")
        ]}})
        self.assertEqual(profile["cases"], [c for p in originals for c in p["cases"]])
