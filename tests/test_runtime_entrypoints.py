from pathlib import Path
import hashlib
import json
import re
import tempfile
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

    def test_browser_use_routine_and_release_checks_are_separate(self) -> None:
        makefile = (ROOT / "Makefile").read_text()
        routine = makefile.split("browser-use-report-check:", 1)[1].split(
            "browser-use-release-check:", 1
        )[0]
        release = makefile.split("browser-use-release-check:", 1)[1]

        self.assertIn("check_browser_use_report.py", routine)
        self.assertIn("check_report_links.py", routine)
        self.assertNotIn("browser-use-visual-check", routine)
        self.assertIn("browser-use-report-check", release.splitlines()[0])
        self.assertIn("browser-use-visual-check", release)

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

    def test_oss_provider_configs_pin_current_runtime_identity(self) -> None:
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
        self.assertNotIn('error?.stack', worker)

    def test_openclaw_worker_does_not_publish_exception_stacks(self) -> None:
        worker = (ROOT / 'scripts/oss/openclaw_webfetch_worker.mts').read_text()
        self.assertNotIn('error?.stack', worker)
        self.assertIn('content: message', worker)

    def test_langchain_worker_does_not_claim_unobserved_final_url(self) -> None:
        worker = (ROOT / 'scripts/oss/langchain_webbase_worker.py').read_text()
        self.assertNotIn('"final_url"', worker)
        self.assertIn('"document_metadata": metadata', worker)

    def test_current_coverage_declares_all_reported_methods(self) -> None:
        dataset = json.loads((ROOT / "evaluation/dataset.json").read_text())
        expected = {
            "cloudflare-markdown", "cowagent-built-in", "crawl4ai",
            "deepseek-harness-built-in", "exa", "firecrawl",
            "langchain-webbase", "moli-webfetch", "openclaw-built-in", "tavily",
        }
        for track, providers in dataset["coverage_plan"].items():
            self.assertEqual(set(providers), expected, track)
            self.assertEqual(len(providers), 10, track)

    def test_current_report_profile_matches_runner_and_canonical_dataset(self) -> None:
        profile = json.loads((ROOT / 'evaluation/current-report.json').read_text())
        self.assertEqual(profile['conversion'],
                         'Native Markdown for Kitesurf, Lightpanda, Obscura and Moli; '
                         'Chromium whole-DOM HTML via node-html-markdown 2.0.0')
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
        self.assertEqual(profile['artifact_hub_site'], '6jNzpGiPozLW')
        self.assertRegex(profile['artifact_hub_expected_version'], r'^ver_[0-9a-f]+$')

    def test_publication_receipt_must_target_current_site(self) -> None:
        profile = ROOT / 'evaluation/current-report.json'
        self.assertEqual(current_publication_target(profile),
                         ('https://artifact-hub.app.lexmount.com', '6jNzpGiPozLW'))
        for slug in ('UUAhhQMnrGwm','UeZE1vfcEOrM'):
            with self.assertRaisesRegex(ValueError, 'expected'):
                require_publication_site({'slug':slug}, profile,
                                         'https://artifact-hub.app.lexmount.com')

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
