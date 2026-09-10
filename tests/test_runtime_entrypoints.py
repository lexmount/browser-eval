from pathlib import Path
import hashlib
import json
import re
import tempfile
import unittest

from scripts.run_headless_benchmark import require_expected_provider_versions
from harness.web_fetch.publication_target import require_publication_site, require_publication_version


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

    def test_current_coverage_declares_all_twelve_methods(self) -> None:
        dataset = json.loads((ROOT / "evaluation/dataset.json").read_text())
        expected = {
            "cloudflare-markdown", "cowagent-built-in", "crawl4ai",
            "deepseek-harness-built-in", "exa", "firecrawl",
            "langchain-webbase", "lexmount-cn", "lexmount-global",
            "moli-webfetch", "openclaw-built-in", "tavily",
        }
        for track, providers in dataset["coverage_plan"].items():
            self.assertEqual(set(providers), expected, track)
            self.assertEqual(len(providers), 12, track)

    def test_current_report_profile_matches_runner_and_canonical_dataset(self) -> None:
        profile = json.loads((ROOT / 'evaluation/current-report.json').read_text())
        dataset = json.loads((ROOT / 'evaluation/dataset.json').read_text())
        self.assertEqual(profile['conversion'], dataset['comparison']['conversion'])
        self.assertEqual(profile['comparison_kind'], dataset['comparison']['kind'])
        self.assertEqual(set(profile['providers']), set(dataset['coverage_plan']['web']))
        case_ids = [case['id'] for case in dataset['cases']]
        self.assertEqual(
            profile['case_ids_sha256'],
            hashlib.sha256(json.dumps(
                case_ids, separators=(',', ':'), ensure_ascii=True
            ).encode()).hexdigest(),
        )
        self.assertEqual(profile['artifact_hub_base_url'], 'https://artifact-hub.dev.lexmount.net')
        self.assertEqual(profile['artifact_hub_site'], 'UeZE1vfcEOrM')
        self.assertEqual(
            profile['artifact_hub_expected_version'],
            'ver_69bede8bffdd47ba88f6b1b95c6fa00b',
        )

    def test_current_rerun_plan_is_exact_and_complete(self) -> None:
        dataset = json.loads((ROOT / 'evaluation/dataset.json').read_text())
        profile = json.loads((ROOT / 'evaluation/current-report.json').read_text())
        plan = json.loads((ROOT / 'evaluation/current-rerun.json').read_text())
        case_ids = {case['id'] for case in dataset['cases']}
        providers = set(profile['providers'])
        main = plan['main_matrix']
        speed = plan['same_window_speed_cohort']
        self.assertEqual(len(main['rerun_case_ids']), 10)
        self.assertTrue(set(main['rerun_case_ids']) <= case_ids)
        self.assertEqual(len(main['deadline_replacement_attempt_ids']), 15)
        self.assertEqual(len(set(main['deadline_replacement_attempt_ids'])), 15)
        for attempt_id in main['deadline_replacement_attempt_ids']:
            provider, case_id, run = attempt_id.rsplit('__', 2)
            self.assertIn(provider, providers)
            self.assertIn(case_id, case_ids)
            self.assertEqual(run, 'r001')
        self.assertEqual(main['planned_new_calls'], 10 * len(providers) + 15)
        self.assertEqual(len(speed['case_ids']), 20)
        self.assertEqual(len(set(speed['case_ids'])), 20)
        self.assertTrue(set(speed['case_ids']) <= case_ids)
        self.assertEqual(speed['provider_count'], len(providers))
        self.assertEqual(speed['planned_new_calls'], 20 * len(providers))
        self.assertEqual(
            plan['total_planned_new_calls'],
            main['planned_new_calls'] + speed['planned_new_calls'],
        )

    def test_publication_receipt_must_target_current_site(self) -> None:
        profile = ROOT / 'evaluation/current-report.json'
        self.assertEqual(
            require_publication_site({'slug':'UeZE1vfcEOrM'}, profile,
                                     'https://artifact-hub.dev.lexmount.net'),
            'UeZE1vfcEOrM',
        )
        with self.assertRaisesRegex(ValueError, 'expected'):
            require_publication_site({'slug':'UUAhhQMnrGwm'}, profile,
                                     'https://artifact-hub.dev.lexmount.net')
        with self.assertRaisesRegex(ValueError, 'expected'):
            require_publication_site({'slug':'UeZE1vfcEOrM'}, profile,
                                     'https://artifact-hub.app.lexmount.com')
        with self.assertRaisesRegex(ValueError, 'requested'):
            require_publication_site(
                {'slug':'UeZE1vfcEOrM','base_url':'https://artifact-hub.app.lexmount.com'},
                profile,'https://artifact-hub.dev.lexmount.net')

    def test_publication_version_defaults_to_profile_and_rejects_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'profile.json'
            profile.write_text(json.dumps({
                'artifact_hub_expected_version': 'ver_profile',
            }))
            self.assertEqual(require_publication_version(profile), 'ver_profile')
            self.assertEqual(
                require_publication_version(profile, 'ver_profile'),
                'ver_profile',
            )
            with self.assertRaisesRegex(ValueError, 'expected'):
                require_publication_version(profile, 'ver_cli')

    def test_publication_version_fails_closed_when_profile_binding_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'profile.json'
            for value in (None, '', 'version-without-prefix', 7):
                profile.write_text(json.dumps({
                    'artifact_hub_expected_version': value,
                }))
                with self.subTest(value=value), self.assertRaisesRegex(
                    ValueError, 'artifact_hub_expected_version'
                ):
                    require_publication_version(profile)

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
