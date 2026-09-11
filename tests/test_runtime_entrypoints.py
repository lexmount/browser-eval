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

    def test_oss_provider_configs_pin_historical_runtime_identity(self) -> None:
        expected = {
            'cowagent-built-in': 'ed5bb344cfe42cee443c8266ba47ee666d8d7471',
            'openclaw-built-in': '12c4bdf7482f24c85b011e9d858fdec3c245101c',
            'deepseek-harness-built-in': 'dd6322d604e00eec1ba5e0c8541159906a21094a',
        }
        for filename in ('providers.example.json', 'providers.resources.json'):
            providers = json.loads((ROOT / 'configs' / filename).read_text())['providers']
            self.assertEqual(
                providers['moli-webfetch']['expected_reported_version'], 'moli 1.1.4'
            )
            self.assertEqual(
                providers['moli-webfetch']['expected_binary_sha256'],
                'dbce6cc79b97eeb558416707a642ca20b9b1b8d6731655a695059d334adb6d5b',
            )
            for name, revision in expected.items():
                self.assertEqual(providers[name]['expected_repo_revision'], revision)
                self.assertTrue(providers[name]['required_paths'])
            for name in ('openclaw-built-in', 'deepseek-harness-built-in'):
                self.assertEqual(providers[name]['expected_node_version'], 'v24.15.0')
                self.assertEqual(providers[name]['command'][0], '${OPENCLAW_NODE:-node}')
            self.assertEqual(
                providers['openclaw-built-in']['expected_package_manager_version'],
                '12.1.0',
            )
            self.assertNotIn('expected_package_manager_version',
                             providers['deepseek-harness-built-in'])
            dsh_command = providers['deepseek-harness-built-in']['command']
            self.assertEqual(dsh_command[1:3], ['--import', 'tsx'])
            dsh_required = providers['deepseek-harness-built-in']['required_paths']
            for built_entrypoint in (
                'vendor/cordis/lib/index.js',
                'packages/llm/llm/lib/index.js',
                'packages/core/system-prompt/lib/index.js',
                'packages/core/tools/lib/index.js',
                'packages/web/web/lib/index.js',
                'packages/web/web-fetch-http/lib/index.js',
                'packages/web/tool-web/lib/index.js',
                'packages/guard/timeout-policy/lib/index.js',
            ):
                self.assertIn(f'{{repo_path}}/{built_entrypoint}', dsh_required)
            self.assertEqual(
                providers['langchain-webbase']['expected_library_versions'],
                {'langchain-community': '0.3.30'},
            )
            self.assertEqual(providers['langchain-webbase']['command'][0],
                             '${OSS_PYTHON:-.venv/bin/python}')
            self.assertEqual(providers['langchain-webbase']['expected_python_version'],
                             'Python 3.11.12')

    def test_oss_bootstrap_installs_pinned_package_managers_outside_source_trees(self) -> None:
        script = (ROOT / 'scripts/bootstrap_oss_baselines.sh').read_text()
        self.assertIn('node@24.15.0 pnpm@12.1.0', script)
        self.assertIn('openclaw_pnpm="$openclaw_node_root/node_modules/.bin/pnpm"', script)
        self.assertNotIn('dsh_pnpm', script)
        self.assertIn('sparse-checkout disable', script)
        self.assertNotIn('git -C "$target" clean -ffdX', script)
        dsh_install = script.index('corepack pnpm install --frozen-lockfile --ignore-scripts')
        dsh_clean = script.index('corepack pnpm run clean')
        dsh_build = script.index('corepack pnpm run build:lib:host')
        self.assertLess(dsh_install, dsh_clean)
        self.assertLess(dsh_clean, dsh_build)
        self.assertIn(
            '"$node_bin_dir/node" --import tsx '
            'node_modules/.pnpm/.browser-harness-dsh-worker.mts </dev/null',
            script,
        )

    def test_env_example_uses_the_bootstrapped_runtime_commands(self) -> None:
        env_example = (ROOT / '.env.example').read_text()
        self.assertIn(
            'OPENCLAW_NODE=~/.browser-eval/toolchains/openclaw-node/node_modules/node/bin/node',
            env_example,
        )
        self.assertIn(
            'OPENCLAW_PNPM=~/.browser-eval/toolchains/openclaw-node/node_modules/.bin/pnpm',
            env_example,
        )
        self.assertNotIn('DEEPSEEK_PNPM=', env_example)

    def test_dsh_worker_uses_execution_result_contract_and_fails_non_2xx(self) -> None:
        worker = (ROOT / 'scripts/oss/dsh_webfetch_worker.mts').read_text()
        self.assertNotIn('out.output', worker)
        self.assertIn('out.meta', worker)
        self.assertIn('out.value', worker)
        self.assertIn('statusCode < 200 || statusCode >= 300', worker)
        self.assertIn('missingStatus ? "invalid_response"', worker)
        self.assertNotIn('meta.url ?? request.url', worker)
        self.assertNotIn('final_url: request.url', worker)

    def test_langchain_worker_does_not_claim_unobserved_final_url(self) -> None:
        worker = (ROOT / 'scripts/oss/langchain_webbase_worker.py').read_text()
        self.assertNotIn('"final_url"', worker)
        self.assertIn('"document_metadata": metadata', worker)

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
        self.assertEqual(profile['implementation_bindings']['crawl4ai']['library_version'], '0.9.3')
        self.assertEqual(profile['implementation_bindings']['langchain-webbase']['library_version'], '0.3.30')
        self.assertEqual(profile['implementation_bindings']['moli-webfetch']['reported_version'], 'moli 1.1.4')
        case_ids = [case['id'] for case in dataset['cases']]
        self.assertEqual(
            profile['case_ids_sha256'],
            hashlib.sha256(json.dumps(
                case_ids, separators=(',', ':'), ensure_ascii=True
            ).encode()).hexdigest(),
        )
        self.assertEqual(profile['artifact_hub_base_url'], 'https://artifact-hub.app.lexmount.com')
        self.assertEqual(profile['artifact_hub_site'], 'RSdbALdbdNG6')
        self.assertEqual(
            profile['artifact_hub_expected_version'],
            'ver_21ee8ea85ef24f3cbeadeacd2657fda5',
        )

    def test_current_rerun_plan_is_exact_and_complete(self) -> None:
        dataset = json.loads((ROOT / 'evaluation/dataset.json').read_text())
        profile = json.loads((ROOT / 'evaluation/current-report.json').read_text())
        plan = json.loads((ROOT / 'evaluation/current-rerun.json').read_text())
        case_ids = {case['id'] for case in dataset['cases']}
        providers = set(profile['providers'])
        main = plan['main_matrix']
        speed = plan['same_window_speed_cohort']
        self.assertEqual(plan['schema_version'], 2)
        refreshed_live = {
            'live-016-navigation-menu', 'live-043-syntax',
            'live-063-navigation-menu', 'live-067-navigation-menu',
        }
        self.assertEqual(set(main['matched_case_ids']), refreshed_live)
        self.assertEqual(set(main['full_rerun_providers']), {
            'cloudflare-markdown', 'exa', 'firecrawl', 'moli-webfetch', 'tavily',
        })
        self.assertEqual(set(main['full_rerun_reason_by_provider']),
                         set(main['full_rerun_providers']))
        for entry in main['full_rerun_reason_by_provider'].values():
            self.assertTrue(entry['category'].strip())
            self.assertTrue(entry['reason'].strip())
        reason_groups = main['matched_case_reason_groups']
        explained = [case_id for group in reason_groups for case_id in group['case_ids']]
        self.assertEqual(len(explained), len(set(explained)))
        self.assertEqual(set(explained), set(main['matched_case_ids']))
        self.assertEqual(
            {group['category'] for group in reason_groups},
            {'source_input_refresh'},
        )
        for group in reason_groups:
            self.assertTrue(group['reason'].strip())
        pair_keys = {
            (entry['provider'], entry['case_id']) for entry in main['pair_replacements']
        }
        self.assertEqual(pair_keys, (
            {('crawl4ai', f'document-{number:02d}')
             for number in (1, 2, 3, 4, 6, 10)}
            | {('crawl4ai', case['id']) for case in dataset['cases']
               if case['track'] == 'dynamic'}
            | {(provider, case['id'])
               for provider in {
                   'deepseek-harness-built-in', 'openclaw-built-in',
                   'langchain-webbase',
               }
               for case in dataset['cases'] if case['track'] == 'dynamic'}
        ))
        self.assertEqual(len(pair_keys), len(main['pair_replacements']))
        for entry in main['pair_replacements']:
            self.assertTrue(entry['category'].strip())
            self.assertTrue(entry['reason'].strip())
        self.assertEqual(
            {entry['case_id'] for entry in main['offline_rescore_cases']},
            {
                'stable-wikipedia-ai-revision', 'stable-wcag21-recommendation',
                'live-041-python-programming-language', 'live-060-database',
                'live-061-version-control', 'live-062-natural-language-processing',
            },
        )
        for entry in main['offline_rescore_cases']:
            self.assertEqual(entry['category'], 'reference_only_correction')
            self.assertTrue(entry['reason'].strip())
            self.assertEqual(entry['attempts'], 12)
            self.assertTrue(entry['full_review'])
        expected_pairs = (
            {(provider, case_id) for provider in main['full_rerun_providers']
             for case_id in case_ids}
            | {(provider, case_id) for provider in providers
               for case_id in main['matched_case_ids']}
            | pair_keys
        )
        self.assertEqual(main['planned_new_calls'], len(expected_pairs))
        self.assertEqual(main['planned_new_calls'], 824)
        self.assertFalse(speed['required'])
        self.assertTrue(speed['reuse_existing'])
        self.assertEqual(speed['planned_new_calls'], 0)
        for key in ('dataset_sha256', 'results_sha256', 'execution_sha256',
                    'speed_comparison_sha256'):
            self.assertRegex(speed[key], r'^[0-9a-f]{64}$')
        self.assertEqual(set(speed['auxiliary_sha256']), {
            'scoring-revisions.json', 'reference-revisions.json'})
        for digest in speed['auxiliary_sha256'].values():
            self.assertRegex(digest, r'^[0-9a-f]{64}$')
        self.assertEqual(
            plan['total_planned_new_calls'],
            main['planned_new_calls'] + speed['planned_new_calls'],
        )

    def test_publication_receipt_must_target_current_site(self) -> None:
        profile = ROOT / 'evaluation/current-report.json'
        self.assertEqual(
            require_publication_site({'slug':'RSdbALdbdNG6'}, profile,
                                     'https://artifact-hub.app.lexmount.com'),
            'RSdbALdbdNG6',
        )
        with self.assertRaisesRegex(ValueError, 'expected'):
            require_publication_site({'slug':'UUAhhQMnrGwm'}, profile,
                                     'https://artifact-hub.app.lexmount.com')
        with self.assertRaisesRegex(ValueError, 'expected'):
            require_publication_site({'slug':'RSdbALdbdNG6'}, profile,
                                     'https://artifact-hub.dev.lexmount.net')
        with self.assertRaisesRegex(ValueError, 'requested'):
            require_publication_site(
                {'slug':'RSdbALdbdNG6','base_url':'https://artifact-hub.app.lexmount.com'},
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

    def test_publisher_does_not_read_the_published_site_back(self) -> None:
        source = (ROOT / 'scripts/publish_artifact_hub.py').read_text()
        self.assertNotIn("request(result['share_url']", source)
        self.assertNotIn("'unauthenticated_status'", source)
        self.assertNotIn('/api/preview/', source)
        self.assertNotIn('/export', source)
        self.assertIn("'versionId':version,'url':share_url", source)

    def test_dynamic_rendered_references_cannot_fall_back_to_http(self) -> None:
        source = (ROOT / "scripts/freeze_headless_references.py").read_text()
        self.assertIn("RENDERED_REFERENCE_CASES", source)
        self.assertIn("cid in RENDERED_REFERENCE_CASES and cid not in observations", source)


if __name__ == "__main__":
    unittest.main()
