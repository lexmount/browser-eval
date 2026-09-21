import importlib.util
import math
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "plot_moli_version_resources.py"
SPEC = importlib.util.spec_from_file_location("plot_moli_version_resources", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class MoliVersionResourceChartTests(unittest.TestCase):
    def test_near_identical_methods_keep_both_numbered_markers_visible(self):
        self.data["configurations"][1]["pss_mib"] = self.data["configurations"][0]["pss_mib"]
        self.data["configurations"][1]["success_rate_pct"] = 0.01
        svg = self.render(("pss",))
        points = re.findall(r'<circle cx="([^"]+)" cy="([^"]+)" r="12"', svg)
        self.assertEqual(len(points), 3)
        a, b = [(float(x), float(y)) for x, y in points[:2]]
        self.assertGreaterEqual(math.dist(a, b), 27)
        self.assertIn('stroke-width="1.5"', svg)

    def test_combined_chart_keeps_registry_order_and_baseline_without_cross_platform_deltas(self):
        historical = {**self.data, "baseline": "114_off"}
        mac = {"configurations": []}
        for i, passed in enumerate((1, 360, 255)):
            row = dict(self.data["configurations"][0])
            row.update(
                id=f"mac_{i}",
                passed_cases=passed,
                rss_mib=100 + i,
                label=f"mac {i}",
                label_en=f"mac {i}",
            )
            mac["configurations"].append(row)
        sources = historical["configurations"] + mac["configurations"]
        methods = [{**r, "final_passed_cases": r["passed_cases"]} for r in sources]
        combined = MODULE.combine_comparisons(methods, historical, mac)
        self.assertEqual(combined["baseline"], "114_off")
        self.assertEqual([r["id"] for r in combined["configurations"]], [r["id"] for r in methods])
        for row in combined["configurations"][3:]:
            self.assertIsNone(row["memory_change_pct"])
            self.assertIsNone(row["cpu_change_pct"])
            self.assertEqual(row["memory_kind"], "RSS")
        self.assertAlmostEqual(combined["configurations"][4]["success_change_pp"], 360 / 372 * 100)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "all.svg"
            for language in ("zh", "en"):
                MODULE.render_chart(combined, output, ("memory", "cpu"), language)
                text = output.read_text()
                self.assertIn("Linux PSS", text)
                self.assertIn("macOS RSS", text)
                self.assertIn("360/372", text)
                self.assertIn("+96.77", text)
                self.assertIn("(—)", text)
                self.assertEqual(text.count('<path d="M '), 6)
            for metric in ("memory", "cpu"):
                MODULE.render_chart(combined, output, (metric,), "en")
                text = output.read_text()
                self.assertIn("360/372", text)
                self.assertEqual(text.count('<path d="M '), 3)
                self.assertIn("MiB" if metric == "memory" else "ms", text)
                self.assertNotIn("21.41 ms" if metric == "memory" else "77.22 MiB", text)

    def setUp(self):
        self.data = {
            "resource_population": {
                "chart_calls": 1045,
                "chart_tasks": 209,
                "total_tasks": 557,
                "calls_per_task": 5,
            },
            "configurations": [
                {
                    "id": "114_off",
                    "label": "Moli 1.1.4 · 关闭 layout",
                    "label_en": "Moli 1.1.4 · layout off",
                    "short_label": "1.1.4 关闭",
                    "short_label_en": "1.1.4 off",
                    "passed_cases": 0,
                    "total_cases": 372,
                    "success_rate_pct": 0,
                    "pss_mib": 77.216796875,
                    "pss_change_pct": 0,
                    "cpu_ms": 21.406698564593302,
                    "cpu_change_pct": 0,
                },
                {
                    "id": "118_off",
                    "label": "Moli 1.1.8 · 关闭 layout",
                    "label_en": "Moli 1.1.8 · layout off",
                    "short_label": "1.1.8 关闭",
                    "short_label_en": "1.1.8 off",
                    "passed_cases": 1,
                    "total_cases": 372,
                    "success_rate_pct": 0.2688172043010753,
                    "pss_mib": 77.1015625,
                    "pss_change_pct": -0.1492348551916045,
                    "cpu_ms": 21.320574162679424,
                    "cpu_change_pct": -0.4023245417970611,
                },
                {
                    "id": "118_on",
                    "label": "Moli 1.1.8 · 开启 layout",
                    "label_en": "Moli 1.1.8 · layout on",
                    "short_label": "1.1.8 开启",
                    "short_label_en": "1.1.8 on",
                    "passed_cases": 255,
                    "total_cases": 372,
                    "success_rate_pct": 68.54838709677419,
                    "pss_mib": 80.537109375,
                    "pss_change_pct": 4.299987352978363,
                    "cpu_ms": 21.6555023923445,
                    "cpu_change_pct": 1.1622708985248087,
                },
            ],
        }

    def render(self, metrics=("pss", "cpu"), language="zh"):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "chart.svg"
            MODULE.render_chart(self.data, output, metrics, language)
            return output.read_text()

    def test_dual_panel_labels_absolute_values_and_baseline_changes(self):
        svg = self.render()
        for expected in (
            "77.22 MiB (+0.00%)",
            "77.10 MiB (-0.15%)",
            "80.54 MiB (+4.30%)",
            "21.41 ms (+0.00%)",
            "21.32 ms (-0.40%)",
            "21.66 ms (+1.16%)",
        ):
            self.assertIn(expected, svg)

    def test_pss_only_omits_cpu_content(self):
        svg = self.render(("pss",), "en")
        self.assertIn("Median peak process-tree PSS (MiB)", svg)
        for label in ("1.1.4 off", "1.1.8 off", "1.1.8 on"):
            self.assertIn(label, svg)
        self.assertNotIn("Moli 1.1.8 · layout on:", svg)
        self.assertNotIn("CPU", svg)
        self.assertNotIn("21.41 ms", svg)

    def test_macos_rss_cpu_chart_does_not_label_memory_as_pss(self):
        for row in self.data["configurations"]:
            row["rss_mib"] = row.pop("pss_mib")
            row["rss_change_pct"] = row.pop("pss_change_pct")
        for language in ("zh", "en"):
            with self.subTest(language=language):
                svg = self.render(("rss", "cpu"), language)
                self.assertIn("RSS", svg)
                self.assertIn("CPU", svg)
                self.assertNotIn("PSS", svg)
                self.assertIn("80.54 MiB (+4.30%)", svg)
                self.assertIn("21.66 ms (+1.16%)", svg)
                memory_only = self.render(("rss",), language)
                self.assertIn("RSS", memory_only)
                self.assertIn("80.54 MiB (+4.30%)", memory_only)
                self.assertNotIn("PSS", memory_only)
                self.assertNotIn("CPU", memory_only)

    def test_duplicate_metric_selection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicates"):
            self.render(("pss", "pss"))

    def test_cpu_only_omits_pss_content_and_keeps_all_legend_entries(self):
        svg = self.render(("cpu",), "en")
        self.assertIn("Mean CPU time per execution (ms)", svg)
        for label in ("1.1.4 off", "1.1.8 off", "1.1.8 on"):
            self.assertIn(label, svg)
        self.assertNotIn("PSS", svg)
        self.assertNotIn("77.22 MiB", svg)

    def test_chinese_single_metric_keeps_all_short_legend_entries(self):
        for metric in ("pss", "cpu"):
            with self.subTest(metric=metric):
                svg = self.render((metric,), "zh")
                for label in ("1.1.4 关闭", "1.1.8 关闭", "1.1.8 开启"):
                    self.assertIn(label, svg)
                self.assertNotIn("Moli 1.1.8 · 开启 layout:", svg)

    def test_english_dual_panel_contains_both_resources_and_changes(self):
        svg = self.render(language="en")
        self.assertIn("Resource experiment: 557 tasks × 5 calls", svg)
        self.assertIn("Median peak process-tree PSS (MiB)", svg)
        self.assertIn("Mean CPU time per execution (ms)", svg)
        self.assertIn("80.54 MiB (+4.30%)", svg)
        self.assertIn("21.66 ms (+1.16%)", svg)

    def test_y_axis_expands_to_include_rates_above_eighty_percent(self):
        self.data["configurations"][2]["success_rate_pct"] = 95
        svg = self.render()
        self.assertIn(">100%</text>", svg)
        point = re.search(r'<circle cx="[^"]+" cy="([^"]+)" r="12" fill="#7652c7"', svg)
        self.assertIsNotNone(point)
        self.assertGreaterEqual(float(point.group(1)), 145)

    def test_future_configuration_is_rendered_without_code_changes(self):
        future = dict(self.data["configurations"][2])
        future.update(
            {
                "id": "pr640_try_layout",
                "label": "Moli PR #640 · 失败后尝试 layout",
                "label_en": "Moli PR #640 · try layout after failure",
                "short_label": "PR #640 尝试布局",
                "short_label_en": "PR #640 try layout",
                "passed_cases": 300,
                "success_rate_pct": 300 / 372 * 100,
                "color": "#2775c9",
            }
        )
        self.data["configurations"].append(future)
        svg = self.render()
        self.assertIn("4 种配置", svg)
        self.assertIn("Moli PR #640 · 失败后尝试 layout", svg)
        self.assertIn("300/372 (80.6%)", svg)

    def test_mismatched_denominators_have_clear_error(self):
        self.data["configurations"][0]["total_cases"] = 371
        with self.assertRaisesRegex(ValueError, "same positive total_cases"):
            self.render()

    def test_missing_resource_population_field_has_clear_error(self):
        del self.data["resource_population"]["chart_tasks"]
        with self.assertRaisesRegex(
            ValueError, "resource_population missing required field.*chart_tasks"
        ):
            self.render()

    def test_resource_population_values_must_be_positive_integers(self):
        for value in ("557", 557.0, 0, True):
            with self.subTest(value=value):
                self.data["resource_population"]["total_tasks"] = value
                with self.assertRaisesRegex(
                    ValueError,
                    "resource_population field total_tasks must be a positive integer",
                ):
                    self.render()
        self.data["resource_population"]["total_tasks"] = 557

    def test_numeric_string_metric_values_render_after_validation(self):
        self.data["configurations"][0]["pss_mib"] = "77.216796875"
        self.data["configurations"][0]["cpu_ms"] = "21.406698564593302"
        svg = self.render()
        self.assertIn("77.22 MiB (+0.00%)", svg)
        self.assertIn("21.41 ms (+0.00%)", svg)

    def test_chinese_cpu_only_panel_has_cpu_labels_and_no_pss(self):
        svg = self.render(("cpu",), "zh")
        self.assertIn("单次执行平均 CPU 时间（ms）", svg)
        self.assertIn("21.66 ms (+1.16%)", svg)
        self.assertNotIn("PSS", svg)

    def test_empty_configuration_list_has_clear_error(self):
        self.data["configurations"] = []
        with self.assertRaisesRegex(ValueError, "configurations must contain at least one row"):
            self.render()

    def test_missing_configuration_field_has_clear_error(self):
        del self.data["configurations"][0]["pss_change_pct"]
        with self.assertRaisesRegex(
            ValueError, "configuration row 0 missing required field.*pss_change_pct"
        ):
            self.render()

    def test_color_rejects_svg_attribute_injection(self):
        self.data["configurations"][0]["color"] = '#fff" onload="alert(1)'
        with self.assertRaisesRegex(ValueError, "color must be a six-digit hex color"):
            self.render()

    def test_fractional_total_cases_has_clear_error(self):
        for row in self.data["configurations"]:
            row["total_cases"] = "372.5"
        with self.assertRaisesRegex(ValueError, "integer total_cases"):
            self.render()

    def test_duplicate_configuration_ids_have_clear_error(self):
        self.data["configurations"][1]["id"] = "114_off"
        with self.assertRaisesRegex(ValueError, "configuration ids must be unique"):
            self.render()

    def test_fractional_passed_cases_have_clear_error(self):
        self.data["configurations"][0]["passed_cases"] = 0.5
        with self.assertRaisesRegex(ValueError, "integer passed_cases"):
            self.render()

    def test_nonfinite_metric_has_clear_error(self):
        self.data["configurations"][0]["pss_mib"] = float("nan")
        with self.assertRaisesRegex(ValueError, "field pss_mib must be finite"):
            self.render()


if __name__ == "__main__":
    unittest.main()
