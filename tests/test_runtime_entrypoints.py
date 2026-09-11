from pathlib import Path
import json
import re
import unittest

from scripts.run_headless_benchmark import require_expected_provider_versions
from harness.web_fetch.providers.headless import HEADLESS_CONVERSION
from harness.web_fetch.publication_target import require_publication_site


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
            "moli": {"reported_version": "moli 1.1.1"},
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
        dataset = json.loads((ROOT / profile.get('dataset_file', 'evaluation/dataset.json')).read_text())
        self.assertEqual(profile['conversion'], dataset['comparison']['conversion'])
        self.assertEqual(profile['comparison_kind'], dataset['comparison']['kind'])
        declared = {provider for providers in dataset['coverage_plan'].values() for provider in providers}
        self.assertEqual(set(profile['providers']), declared)
        self.assertEqual(profile['artifact_hub_base_url'], 'https://artifact-hub.app.lexmount.com')
        self.assertEqual(profile['artifact_hub_site'], 'l9jZU6GPMLn6')

    def test_publication_receipt_must_target_current_site(self) -> None:
        profile = ROOT / 'evaluation/current-report.json'
        self.assertEqual(
            require_publication_site({'slug':'l9jZU6GPMLn6'}, profile,
                                     'https://artifact-hub.app.lexmount.com'),
            'l9jZU6GPMLn6',
        )
        with self.assertRaisesRegex(ValueError, 'expected'):
            require_publication_site({'slug':'UeZE1vfcEOrM'}, profile,
                                     'https://artifact-hub.app.lexmount.com')
        with self.assertRaisesRegex(ValueError, 'expected'):
            require_publication_site({'slug':'UUAhhQMnrGwm'}, profile,
                                     'https://artifact-hub.dev.lexmount.net')
        with self.assertRaisesRegex(ValueError, 'requested'):
            require_publication_site(
                {'slug':'l9jZU6GPMLn6','base_url':'https://artifact-hub.dev.lexmount.net'},
                profile,'https://artifact-hub.app.lexmount.com')

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
