import importlib.util
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
    def setUp(self):
        self.data = {
            "resource_population": {"chart_calls": 1045, "chart_tasks": 209},
            "configurations": [
                {
                    "id": "114_off",
                    "label": "Moli 1.1.4 · 关闭 layout",
                    "label_en": "Moli 1.1.4 · layout off",
                    "short_label": "1.1.4 关闭",
                    "short_label_en": "1.1.4 off",
                    "passed_cases": 0,
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

    def test_cpu_only_omits_pss_content_and_keeps_all_legend_entries(self):
        svg = self.render(("cpu",), "en")
        self.assertIn("Mean CPU time per execution (ms)", svg)
        for label in ("1.1.4 off", "1.1.8 off", "1.1.8 on"):
            self.assertIn(label, svg)
        self.assertNotIn("PSS", svg)
        self.assertNotIn("77.22 MiB", svg)

    def test_english_dual_panel_contains_both_resources_and_changes(self):
        svg = self.render(language="en")
        self.assertIn("Median peak process-tree PSS (MiB)", svg)
        self.assertIn("Mean CPU time per execution (ms)", svg)
        self.assertIn("80.54 MiB (+4.30%)", svg)
        self.assertIn("21.66 ms (+1.16%)", svg)

    def test_y_axis_expands_to_include_rates_above_eighty_percent(self):
        self.data["configurations"][2]["success_rate_pct"] = 95
        svg = self.render()
        self.assertIn(">100%</text>", svg)
        point = re.search(r'<circle cx="[^"]+" cy="([^"]+)" r="10" fill="#7652c7"', svg)
        self.assertIsNotNone(point)
        self.assertGreaterEqual(float(point.group(1)), 145)

    def test_unknown_configuration_id_has_clear_error(self):
        self.data["configurations"][0]["id"] = "future_config"
        with self.assertRaisesRegex(ValueError, "unknown configuration id.*future_config"):
            self.render()

    def test_missing_resource_population_field_has_clear_error(self):
        del self.data["resource_population"]["chart_tasks"]
        with self.assertRaisesRegex(ValueError, "resource_population missing required field.*chart_tasks"):
            self.render()


if __name__ == "__main__":
    unittest.main()
