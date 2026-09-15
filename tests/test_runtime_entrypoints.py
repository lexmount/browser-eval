from pathlib import Path
import json
import re
import unittest

from scripts.run_headless_benchmark import require_expected_provider_versions
from harness.web_fetch.providers.headless import HEADLESS_CONVERSION
from harness.web_fetch.publication_target import current_publication_target, require_publication_site


ROOT = Path(__file__).resolve().parents[1]


class RuntimeEntrypointTests(unittest.TestCase):
    def test_repository_pins_python_and_uv(self) -> None:
        self.assertEqual((ROOT / ".python-version").read_text().strip(), "3.11")
        self.assertTrue((ROOT / "uv.lock").is_file())
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("$(UV) run --frozen python", makefile)
        self.assertIn("$(UV) sync --frozen --all-extras", makefile)
        self.assertIn("sys.version_info[:2] == (3, 11)", makefile)

    def test_versioned_moli_slots_reject_wrong_or_missing_binaries(self) -> None:
        valid = {
            "moli-1.0.6": {"reported_version": "moli 1.0.6"},
            "moli": {"reported_version": "moli 1.1.5"},
            "moli-1.1.3": {"reported_version": "moli 1.1.3"},
        }
        require_expected_provider_versions(valid)
        for value in (None, "moli 1.1.3"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                require_expected_provider_versions({
                    **valid,
                    "moli": {"reported_version": value},
                })

    def test_ci_uses_the_same_make_entrypoint(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("astral-sh/setup-uv@", workflow)
        self.assertIn("make setup", workflow)
        self.assertIn("make ci", workflow)
        self.assertNotIn("pip install", workflow)

    def test_web_fetch_docs_do_not_recommend_system_python(self) -> None:
        for relative in ("README.md", "harness/web_fetch/README.md", "harness/web_fetch/HEADLESS.md"):
            text = (ROOT / relative).read_text()
            direct = re.findall(r"(?m)^\s*(?:python|python3)(?:\s|$)", text)
            self.assertEqual(direct, [], relative)
            unfrozen = re.findall(r"(?m)^\s*uv run (?!--frozen\b)", text)
            self.assertEqual(unfrozen, [], relative)

    def test_headless_coverage_declares_all_seven_methods(self) -> None:
        dataset = json.loads((ROOT / "evaluation/dataset.json").read_text())
        expected = {"kitesurf", "lightpanda", "obscura", "moli-1.0.6",
                    "moli", "moli-1.1.3", "chromium"}
        for track, providers in dataset["coverage_plan"].items():
            self.assertEqual(set(providers), expected, track)
            self.assertEqual(len(providers), 7, track)

    def test_current_report_profile_matches_runner_and_canonical_dataset(self) -> None:
        profile = json.loads((ROOT / 'evaluation/current-report.json').read_text())
        self.assertEqual(profile['conversion'], HEADLESS_CONVERSION)
        self.assertEqual(profile['comparison_kind'], 'webmainbench_frozen_html_to_markdown')
        self.assertEqual(profile['dataset_id'], 'webmainbench-545-preselected-200-five-browsers')
        self.assertEqual(profile['case_count'], 200)
        self.assertEqual(profile['sample_manifest'], 'evaluation/public-webmainbench-200.json')
        self.assertEqual(
            profile['sample_manifest_sha256'],
            'eea643afddd35f3ae4ed85b689b3a89f6a503f8fce878d546fa56c97c26f8044',
        )
        self.assertEqual(
            set(profile['providers']),
            {'kitesurf', 'lightpanda', 'obscura', 'moli', 'chromium'},
        )
        self.assertEqual(profile['runs_per_case'], 1)
        self.assertEqual(profile['max_failure_retries'], 1)
        self.assertEqual(
            profile['source_dataset_sha256'],
            '0efaa4b49a45e320a27fe6e5a0b6aad5b57259fc3321ac3448519cacc74c537e',
        )
        self.assertEqual(profile['artifact_hub_base_url'], 'https://artifact-hub.app.lexmount.com')
        self.assertIsNone(profile['artifact_hub_site'])

    def test_publication_receipt_must_target_current_site(self) -> None:
        profile = ROOT / 'evaluation/current-report.json'
        with self.assertRaisesRegex(ValueError, 'no bound Artifact Hub site'):
            current_publication_target(profile)
        for slug in ('UUAhhQMnrGwm','UeZE1vfcEOrM'):
            with self.assertRaisesRegex(ValueError, 'missing artifact_hub_site'):
                require_publication_site({'slug':slug}, profile,
                                         'https://artifact-hub.app.lexmount.com')

    def test_publication_scripts_do_not_load_repository_eval_credentials(self) -> None:
        for relative in ('scripts/publish_artifact_hub.py',
                         'scripts/verify_artifact_hub.py',
                         'scripts/rename_artifact_hub.py'):
            self.assertNotIn('load_runtime_env', (ROOT/relative).read_text(), relative)

    def test_dynamic_rendered_references_cannot_fall_back_to_http(self) -> None:
        source = (ROOT / "scripts/freeze_headless_references.py").read_text()
        self.assertIn("RENDERED_REFERENCE_CASES", source)
        self.assertIn("cid in RENDERED_REFERENCE_CASES and cid not in observations", source)


if __name__ == "__main__":
    unittest.main()
