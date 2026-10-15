import unittest
import hashlib
import json
import tempfile
from pathlib import Path

from harness.web_fetch.task_outputs import require_native_task_routes
from harness.web_fetch.report_interfaces import SPECS, browser_task_interface_matrix, method_access, method_spec


class NativeBrowserVariantTests(unittest.TestCase):
    candidate = "moli-1.1.15"
    cases = [{"id": "body", "url": "https://example.com/", "track": "web", "task_kind": "body"}]

    def row(self, metadata, **fields):
        return {"provider": self.candidate, "provider_type": "moli_webfetch", "case_id": "body", "ok": True,
                "provider_metadata": metadata, **fields}

    def test_branch_build_has_same_native_body_acceptance_as_release(self):
        require_native_task_routes(self.cases, [self.row({"route": "ordinary_markdown", "output": "markdown"})])
        with self.assertRaises(ValueError):
            require_native_task_routes(self.cases, [self.row({"route": "json_body", "extractor": "node-html-markdown"})])

    def test_branch_configuration_failure_is_not_browser_capability(self):
        with self.assertRaises(ValueError):
            require_native_task_routes(self.cases, [self.row({"route": "ordinary_markdown", "output": "markdown"},
                                                         ok=False, error_type="configuration")])

    def test_current_stacked_candidate_is_registered_as_moli(self):
        from harness.web_fetch.methods import browser_method_label, native_browser_engine
        self.assertEqual(native_browser_engine("moli-stack-1432"), "moli")
        self.assertIn("1432", browser_method_label("moli-stack-1432"))

    def test_fixed_candidate_is_distinct_from_release_and_binds_its_lineage(self):
        from harness.web_fetch.methods import browser_method_label, browser_method_registry, native_browser_engine
        self.assertEqual(native_browser_engine("moli-fixed"), "moli")
        self.assertEqual(
            browser_method_label("moli-fixed"),
            "Moli 修复版（聚合 PR #1501）",
        )
        self.assertEqual(
            browser_method_label("moli-1.1.15"),
            "Moli 1.1.15（正式版）",
        )
        self.assertEqual(
            browser_method_registry()["moli-fixed"]["source_url"],
            "https://github.com/lexmount/moli/pull/1501",
        )
        self.assertNotEqual(browser_method_label("moli-fixed"), browser_method_label("moli-1.1.15"))

    def test_report_labels_mixed_fixed_evidence_by_measured_versions_and_counts(self):
        from harness.web_fetch.methods import browser_method_label, with_measured_method_labels

        @with_measured_method_labels
        def render(out, dataset, records):
            return browser_method_label("moli-fixed")

        records = [
            {"provider": "moli-fixed", "provider_metadata": {"binary_sha256": "a" * 64}},
            {"provider": "moli-fixed", "provider_metadata": {"binary_sha256": "a" * 64}},
            {"provider": "moli-fixed", "provider_metadata": {"binary_sha256": "b" * 64}},
        ]
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out / "implementation-provenance.json").write_text(json.dumps({
                "implementations": [
                    {"provider": "moli", "binary_sha256": "a" * 64, "version": "1.1.15"},
                    {"provider": "moli-fixed", "binary_sha256": "b" * 64,
                     "version": "1.1.17（60773df3）"},
                ]
            }))
            self.assertEqual(
                render(out, {}, records),
                "Moli 修复结果（2 例 1.1.15；1 例 1.1.17（60773df3））",
            )

    def test_report_uses_one_short_label_for_multiple_builds_of_same_fixed_release(self):
        from harness.web_fetch.methods import browser_method_label, with_measured_method_labels

        @with_measured_method_labels
        def render(out, dataset, records):
            return browser_method_label("moli-fixed")

        records = [
            {"provider": "moli-fixed", "provider_metadata": {"binary_sha256": "a" * 64}},
            {"provider": "moli-fixed", "provider_metadata": {"binary_sha256": "b" * 64}},
        ]
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out / "implementation-provenance.json").write_text(json.dumps({
                "implementations": [
                    {"provider": "moli-fixed", "binary_sha256": "a" * 64,
                     "version": "1.1.17（a737f2ae，保留结果）"},
                    {"provider": "moli-fixed", "binary_sha256": "b" * 64,
                     "version": "1.1.17（0d9a7ceb，修正结果）"},
                ]
            }))
            self.assertEqual(render(out, {}, records), "Moli 1.1.17（修复版）")

    def test_unknown_native_participant_cannot_bypass_route_validation(self):
        with self.assertRaises(ValueError):
            require_native_task_routes(self.cases, [self.row(
                {}, provider="unregistered-browser", provider_type="headless_markdown"
            )])

    def test_version_labeled_moli_uses_its_declared_provider_type(self):
        require_native_task_routes(self.cases, [self.row(
            {"route": "ordinary_markdown", "output": "markdown"},
            provider="moli-1.1.16-stack-1432",
        )])

    def test_interface_matrix_covers_every_family_for_six_configurations(self):
        cases = {family: {"id": family, "url": "https://example.com/file.pdf" if family == "document" else "https://example.com/",
                           "track": family, "task_kind": "resource" if family in {"image", "video"} else "body"}
                 for family in ["web", "dynamic", "challenge", "risk_control", "document", "image", "video"]}
        cases["media-file"] = {"id": "media-file", "url": "https://example.com/file.png",
                                 "track": "image", "task_kind": "file"}
        providers = ["moli", "lightpanda", "obscura", "chromium", "kitesurf", self.candidate]
        matrix = browser_task_interface_matrix(cases, providers)
        self.assertEqual(len(matrix["rows"]), 7)
        self.assertEqual(sum(len(row["cells"]) for row in matrix["rows"]), 42)
        for row in matrix["rows"]:
            self.assertEqual(row["cells"]["moli"], row["cells"][self.candidate])
        self.assertEqual(matrix["capabilities"]["moli"], matrix["capabilities"][self.candidate])
        dynamic = next(row for row in matrix["rows"] if row["track"] == "dynamic")
        dynamic_route = str(dynamic["cells"]["moli"])
        self.assertIn("--layout", dynamic_route)
        self.assertIn("--with-frames", dynamic_route)
        self.assertIn("不使用逐题 selector", dynamic_route)

    def test_measured_interface_matrix_uses_saved_route_and_wait_fields(self):
        cases = {
            "web": {"id": "web", "url": "https://example.test/", "track": "web",
                    "task_kind": "body"},
            "dynamic": {"id": "dynamic", "url": "https://example.test/app",
                        "track": "dynamic", "task_kind": "body"},
        }
        records = [
            {"provider": "moli", "case_id": "web", "provider_metadata": {
                "route": "ordinary_markdown", "dump": "markdown",
                "extractor": "moli_native_markdown", "output_contract": "markdown",
                "wait_until": "done"}},
            {"provider": "moli", "case_id": "dynamic", "provider_metadata": {
                "route": "dynamic_native_markdown", "dump": "markdown",
                "extractor": "moli_native_markdown", "output_contract": "markdown",
                "wait_until": "domstable"}},
            {"provider": "lightpanda", "case_id": "web", "provider_metadata": {}},
            {"provider": "lightpanda", "case_id": "dynamic", "provider_metadata": {
                "extractor": "lightpanda_native_markdown", "output_contract": "markdown",
                "wait_until": "load", "navigation_wait_until": "domcontentloaded"}},
        ]
        matrix = browser_task_interface_matrix(cases, ["moli", "lightpanda"], records)
        rows = {row["track"]: row for row in matrix["rows"]}
        self.assertIn("Moli 原生 Markdown", rows["web"]["cells"]["moli"][0]["description"])
        self.assertEqual(rows["web"]["cells"]["moli"][0]["wait"], "输出完成条件 done")
        self.assertEqual(rows["dynamic"]["cells"]["moli"][0]["wait"],
                         "输出完成条件 domstable")
        unknown = rows["web"]["cells"]["lightpanda"][0]["description"]
        self.assertIn("未提供可核对的接口路线", unknown)
        self.assertNotIn("原生 HTML", unknown)
        self.assertEqual(rows["dynamic"]["cells"]["lightpanda"][0]["wait"],
                         "导航等待 domcontentloaded；输出完成条件 load")

    def test_branch_interface_description_uses_native_moli_contract(self):
        self.assertIsNotNone(method_access(self.candidate, []))
        description = method_spec(self.candidate, self.row({"reported_version": "moli 1.1.15"}))
        self.assertIn("Moli", description["entry"])
        self.assertNotIn("pypdf", description["notes"])
        self.assertEqual(
            description["request"]["dynamic_body"],
            "moli fetch --dump markdown --layout --with-frames "
            "--wait-until domstable URL",
        )
        self.assertIn("不按题目设置 selector", description["notes"])

    def test_versioned_moli_entry_does_not_invent_an_html_route(self):
        for provider in (self.candidate, "moli-fixed"):
            description = method_spec(provider, self.row({
                "reported_version": "moli 1.1.15", "route": "ordinary_markdown",
                "dump": "markdown", "extractor": "moli_native_markdown",
                "wait_until": "done",
            }, provider=provider))
            self.assertEqual(description["entry"],
                             "CLI · Moli · 路线以逐次调用记录为准")
            self.assertNotIn("HTML", description["entry"])

    def test_moli_interface_discloses_native_default_timeout_policy(self):
        description = method_spec(self.candidate, self.row({
            "reported_version": "moli 1.1.17",
            "internal_timeout_policy": "native_default",
            "http_connect_timeout_ms": None,
        }))
        self.assertIn("未传入 Moli 内部超时参数", description["notes"])
        self.assertIn("保留该二进制默认值", str(description["params"]))

    def test_moli_base_timeout_description_covers_both_evidence_policies(self):
        for method in ("moli-webfetch", "moli"):
            description = str(SPECS[method]["params"])
            self.assertIn("内部参数以冻结的调用记录为准", description)
            self.assertIn("显式模式留出收尾和清理时间", description)
            self.assertIn("uniform", description)
            self.assertIn("沿用产品默认值", description)

    def test_moli_interface_discloses_observed_explicit_timeout_policy(self):
        from harness.web_fetch.report_i18n import translate_text

        for timeout_ms in (45_000, 40_500):
            with self.subTest(timeout_ms=timeout_ms):
                description = method_spec(self.candidate, self.row({
                    "reported_version": "moli 1.1.15",
                    "internal_timeout_policy": "explicit",
                    "http_connect_timeout_ms": timeout_ms,
                }))
                self.assertIn("显式 Moli 超时", description["notes"])
                self.assertIn(f"{timeout_ms:,} 毫秒", str(description["params"]))
                self.assertNotIn("产品默认值。", str(description["params"]))
                timeout_description = next(
                    detail for key, _, detail in description["params"] if key == "外层截止"
                )
                self.assertIn(f"{timeout_ms:,} ms", translate_text(timeout_description))

    def test_chromium_interface_uses_observed_wait_and_binds_converter_wrapper(self):
        digest = "a" * 64
        row = {"provider": "chromium", "provider_type": "headless_markdown",
               "provider_metadata": {"wait_until": "domcontentloaded",
                                     "navigation_wait_until": "load",
                                     "converter_sha256": digest}}
        description = method_spec("chromium", row)
        self.assertEqual(description["request"]["wait_until"], "load")
        self.assertEqual(description["request"]["converter_sha256"], digest)
        self.assertIn(digest, str(description["params"]))
        self.assertIn("代码块围栏", description["notes"])

    def test_chromium_interface_does_not_invent_unobserved_converter_hash(self):
        row = {"provider": "chromium", "provider_type": "headless_markdown",
               "provider_metadata": {"wait_until": "domcontentloaded"}}
        description = method_spec("chromium", row)
        self.assertNotIn("converter_sha256", description["request"])

    def test_branch_only_report_requires_native_interface_contract(self):
        from harness.web_fetch.storage import require_current_report_profile
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            comparison = {"kind": "headless_markdown", "timeout_seconds": 45,
                          "runs_per_case": 1, "concurrency": 1, "conversion": None,
                          "reference_binding_mode": "strict", "publication_state": "verified",
                          "unverified_reference_cases": [], "require_independent_result_review": False}
            profile = {"dataset_id": "branch", "comparison_kind": comparison["kind"],
                       "case_count": 1, "providers": [self.candidate],
                       "case_ids_sha256": hashlib.sha256(b'["body"]').hexdigest(),
                       **{k: v for k, v in comparison.items() if k != "kind"}}
            (root / "profile.json").write_text(json.dumps(profile))
            (root / "dataset.json").write_text(json.dumps({"id": "branch", "cases": self.cases,
                "coverage_plan": {"web": [self.candidate]}, "comparison": comparison}))
            row = self.row({"route": "ordinary_markdown", "output": "markdown"}, attempt_id="branch-body")
            (root / "results.jsonl").write_text(json.dumps(row) + "\n")
            with self.assertRaisesRegex(ValueError, "requires native_task_route_contract"):
                require_current_report_profile(root, root / "profile.json")
            profile["native_task_route_contract"] = 4
            (root / "profile.json").write_text(json.dumps(profile))
            require_current_report_profile(root, root / "profile.json")

    def test_interpretation_counts_engines_and_configurations_separately(self):
        from harness.web_fetch.report_explainer import _render_current_browser_article, _digest, _headless_article_input
        providers = ["moli", "lightpanda", "obscura", "chromium", "kitesurf", self.candidate]
        families = ["web", "dynamic", "challenge", "risk_control", "document", "image", "video"]
        cases = {f: {"id": f, "name": f, "url": "https://example.com/", "track": f,
                     "task_kind": "body", "ground_truth_main_content": "Required body"} for f in families}
        cases["media-file"] = {"id": "media-file", "name": "media-file",
                                 "url": "https://example.com/file.png", "track": "image",
                                 "task_kind": "file", "ground_truth_main_content": ""}
        records = [{"provider": p, "case_id": f, "attempt_id": p + "-" + f,
                    "ok": False, "metrics": {}, "elapsed_ms": 10, "started_at": "2026-10-10T00:00:00Z",
                    "artifact_path": "output.txt"} for p in providers for f in cases]
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out / "output.txt").write_text("Observed body")
            content = {"schema": "webfetch_browser_interpretation/1", "title": "配置对比",
                       "intro": "同一任务和答案。", "scoring": "按任务验收。", "conclusions": "保留实际结果。",
                       "evidence_sha256": _digest(_headless_article_input(cases, records, sorted(providers))),
                       "providers": {p: {"specimen_case_id": "web", "explanation": "未满足要求。"} for p in providers}}
            path = out / "article.json"
            path.write_text(json.dumps(content))
            body = _render_current_browser_article(out, cases, records,
                {"path": "article.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            self.assertIn("5 种浏览器、6 种配置", body)
            self.assertIn("Moli 1.1.15（正式版）", body)
            self.assertNotIn("6 个浏览器", body)
            self.assertEqual(body.count('data-browser-interface='), 42)
