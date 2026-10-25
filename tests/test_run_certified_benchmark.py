from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.web_fetch.certification import browser_observation_cases, main


def stage_command(call):
    return [call.args[0], *call.args[1]]


def stage_name(command):
    return command[0].__module__.rsplit(".", 1)[-1] + "." + command[0].__name__


class CertifiedBenchmarkTests(unittest.TestCase):
    def test_unresolved_selected_rules_fail_before_capture_or_provider_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / 'dataset.json'
            dataset.write_text(json.dumps({'cases': [{
                'id': 'provisional', 'rule_review': {'status': 'unresolved'}}]}))
            with patch('harness.web_fetch.certification.run') as invoked:
                with self.assertRaises(SystemExit):
                    main(['--dataset', str(dataset), '--output', str(root / 'run'),
                          '--work-root', str(root / 'work'), '--providers', 'fixture',
                          '--case-ids', 'provisional'])
            invoked.assert_not_called()
            self.assertFalse((root / 'work').exists())

    def test_diagnostic_execution_cannot_be_assembled_or_rendered_as_certified(self):
        from harness.web_fetch import benchmark_runner as run_benchmark
        from harness.web_fetch import reference_freeze as freeze_headless_references
        from harness.web_fetch import reference_freeze as postcheck_headless
        from harness.web_fetch.assembly import assemble
        from harness.web_fetch.reference_lifecycle import (
            ReferenceLifecycleError,
            require_verified_reference_checks,
        )
        from harness.web_fetch.report_build import build_report
        from harness.web_fetch.evidence import finalize_aggregate
        for mode, retries in (('declared', 0), ('native', 3)):
            with self.subTest(mode=mode, retries=retries), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); dataset=root/'dataset.json'; output=root/'run'
                case={'id':'stable','name':'Stable page','url':'https://example.test/page',
                      'category':'web','track':'web','expected_any':['Required label'],
                      'acceptance_contract':{'version':1,'schema':'body_text',
                          'minimum_records':0,'stable_text_anchors':[
                              {'id':'a','alternatives':['Required label']}]}}
                dataset.write_text(json.dumps({'tool':'web_fetch','comparison':{},'cases':[case]}))
                original=dataset.read_bytes()
                config=root/'provider.json'
                config.write_text(json.dumps({'providers':{'fixture':{'type':'fixture'}}}))
                (root/'evaluation/list-references').mkdir(parents=True)

                def execute(stage, arguments):
                    command = [stage, *arguments]
                    script=stage_name(command)
                    if script=='source_capture.main':
                        self.write_capture(Path(command[2]),'stable',text='Required label')
                    elif script=='reference_freeze.main':
                        with patch.object(freeze_headless_references,'ROOT',root):
                            freeze_headless_references.main(command[1:])
                    elif script=='benchmark_runner.main':
                        with patch.object(run_benchmark,'load_runtime_env'):
                            run_benchmark.main(command[1:])
                    elif script=='reference_freeze.postcheck_main':
                        postcheck_headless.postcheck_main(command[1:])
                    elif script=='evidence.finalize_main':
                        finalize_aggregate(output,root/'work/post-captures/observations.json')
                    else:self.fail('Unexpected external stage: '+script)

                with patch('harness.web_fetch.certification.run',side_effect=execute):
                    main(['--dataset',str(dataset),'--output',str(output),
                          '--work-root',str(root/'work'),'--providers','fixture',
                          '--provider-config',str(config),'--case-ids','stable',
                          '--retrieval-mode',mode,'--max-failure-retries',str(retries),
                          '--timeout','30','--diagnostic'])
                (output/'sources').mkdir(exist_ok=True)
                (output/'list-references').mkdir(exist_ok=True)
                with self.assertRaisesRegex(ReferenceLifecycleError,'[Dd]iagnostic'):
                    assemble(output,root/'certificates',[],root/'formal')
                self.assertFalse((root/'formal').exists())
                saved=json.loads((output/'dataset.json').read_text())
                self.assertEqual(saved['comparison']['evidence_state'],'diagnostic')
                self.assertEqual(saved['comparison']['unqualified_reference_cases'],[])
                batches=json.loads((output/'execution.json').read_text())['batches']
                self.assertTrue(all(b['parameters']['max_failure_retries']==retries for b in batches))
                self.assertTrue(all(b['parameters']['timeout']==30 for b in batches))
                self.assertTrue(all(b['evidence_state']=='diagnostic' and
                    b['unqualified_reference_cases']==[] for b in batches))
                # Dropping dataset display metadata cannot erase execution provenance.
                saved['comparison']={}
                with self.assertRaisesRegex(ReferenceLifecycleError,'[Dd]iagnostic'):
                    require_verified_reference_checks(batches,cases=saved)
                with self.assertRaisesRegex(ReferenceLifecycleError,'[Dd]iagnostic'):
                    build_report(output,root/'formal-report')
                self.assertFalse((root/'formal-report').exists())
                # A copied diagnostic run must not become a formal package,
                # even after its dataset display label is removed.
                from harness.web_fetch.report_package import validate_release
                with self.assertRaisesRegex(ReferenceLifecycleError,'[Dd]iagnostic'):
                    validate_release(output)
                (output/'dataset.json').write_text(json.dumps(saved))
                with self.assertRaisesRegex(ReferenceLifecycleError,'[Dd]iagnostic'):
                    validate_release(output)
                self.assertEqual(dataset.read_bytes(),original)

    def test_frozen_contract_execution_needs_no_historical_qualification_files(self):
        from harness.web_fetch import reference_freeze, benchmark_runner
        from harness.web_fetch.evidence import finalize_aggregate
        from harness.web_fetch.reference_lifecycle import require_verified_reference_checks
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / 'dataset.json'
            output = root / 'run'
            case = {'id': 'stable', 'name': 'Stable page',
                    'url': 'https://example.test/page', 'category': 'web',
                    'track': 'web', 'expected_any': ['Required label'],
                    'source_benchmark': {'risk_control': {'reference_proof': {
                        'root': 'historical', 'path': 'missing/browser.txt',
                        'sha256': 'a' * 64}}},
                    'acceptance_contract': {'version': 1, 'schema': 'body_text',
                        'minimum_records': 0, 'stable_text_anchors': [
                            {'id': 'a', 'alternatives': ['Required label']}]}}
            import hashlib
            fixed = {'id': 'fixed-answer', 'name': 'Fixed answer',
                     'url': 'https://example.test/fixed', 'category': 'web', 'track': 'web',
                     'expected_any': ['Required label'], 'ground_truth_main_content': 'Required label',
                     'source_verification': {'state': 'valid', 'checked_at': '2020-01-01T00:00:00Z',
                        'method': 'Independent source capture',
                        'reference_sha256': hashlib.sha256(b'Required label').hexdigest()}}
            resource = {'id': 'fixed-resource', 'name': 'Fixed image target',
                        'url': 'https://example.test/gallery', 'category': 'image', 'track': 'image',
                        'task_kind': 'resource', 'expected_any': ['https://example.test/image.png'],
                        'expected_resources': [{'id': 'image', 'urls': ['https://example.test/image.png']}],
                        'source_verification': {'state': 'valid', 'checked_at': '2020-01-01T00:00:00Z',
                            'method': 'Independent source capture',
                            'evidence_path': 'sources/fixed-resource/evidence.json',
                            'evidence_sha256': 'b' * 64}}
            dataset.write_text(json.dumps({'tool': 'web_fetch', 'comparison': {}, 'cases': [case, fixed, resource]}))
            original = dataset.read_bytes()
            from lexbench_webfetch.versions import freeze_release
            edition = root / 'benchmark-release.json'
            freeze_release(dataset, edition, benchmark='frozen-example', version='1.0.0')
            config = root / 'providers.json'
            config.write_text(json.dumps({'providers': {'fixture': {'type': 'fixture'}}}))
            (root / 'evaluation/list-references').mkdir(parents=True)
            stages = []

            def execute(stage, arguments):
                script = stage.__module__.rsplit('.', 1)[-1] + '.' + stage.__name__
                stages.append(script)
                if script == 'reference_freeze.main':
                    reference_freeze.main(arguments)
                elif script == 'benchmark_runner.main':
                    with patch.object(benchmark_runner, 'load_runtime_env'):
                        benchmark_runner.main(arguments)
                elif script == 'reference_freeze.postcheck_main':
                    self.fail('Frozen stable contracts must not trigger live-source recapture')
                elif script == 'evidence.finalize_main':
                    finalize_aggregate(output, root / 'work/post-captures/observations.json')
                else:
                    self.fail('Unexpected external work: ' + script)

            with patch('harness.web_fetch.certification.run', side_effect=execute):
                main(['--dataset', str(dataset), '--output', str(output),
                      '--work-root', str(root / 'work'), '--providers', 'fixture',
                      '--provider-config', str(config), '--case-ids', 'stable,fixed-answer,fixed-resource'])
            measured = json.loads((output / 'dataset.json').read_text())
            self.assertEqual(measured['cases'], [case, fixed, resource])
            self.assertEqual(dataset.read_bytes(), original)
            self.assertEqual((output / 'benchmark-input-release.json').read_bytes(), edition.read_bytes())
            release_binding = json.loads((output / 'execution.json').read_text())['aggregate']['benchmark_release']
            self.assertEqual(release_binding['sha256'], hashlib.sha256(edition.read_bytes()).hexdigest())
            self.assertEqual(measured['comparison']['source_reference_policy'],
                             'frozen_benchmark_inputs_with_explicit_live_exact_checks')
            batches = json.loads((output / 'execution.json').read_text())['batches']
            records = [json.loads(line) for line in (output / 'results.jsonl').read_text().splitlines()]
            self.assertEqual(len(records), 3)
            resource_record = next(row for row in records if row['case_id'] == 'fixed-resource')
            self.assertTrue(resource_record['metrics']['content_pass'])
            self.assertNotIn('collect_source_evidence.py', stages)
            require_verified_reference_checks(batches, cases=measured, records=records, source=output)

    def test_dispatches_complete_webmainbench_certification(self) -> None:
        arguments = ["--output-root", "/tmp/run", "--source", "/tmp/source.jsonl"]
        with patch("harness.web_fetch.webmainbench_certification.main") as invoked:
            main(["webmainbench-full", *arguments])
        invoked.assert_called_once_with(arguments)

    def test_missing_dynamic_execution_contract_fails_before_external_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);dataset=root/'dataset.json'
            dataset.write_text(json.dumps({'cases':[{'id':'dynamic-page','track':'dynamic'}]}))
            with patch('harness.web_fetch.certification.run') as invoked:
                with self.assertRaisesRegex(ValueError,'require retrieval_hints'):
                    main(['--dataset',str(dataset),'--output',str(root/'run'),
                          '--work-root',str(root/'work'),'--providers','p','--case-ids','dynamic-page'])
            invoked.assert_not_called()

    @staticmethod
    def write_capture(captures: Path, case_id: str, *, status: int = 200,
                      text: str = "reference") -> None:
        (captures / case_id).mkdir(parents=True, exist_ok=True)
        (captures / case_id / "reference.txt").write_text(text)
        (captures / "audit.json").write_text(json.dumps([
            {"case_id": case_id, "status": status, "missing_facts": []}
        ]))

    @staticmethod
    def write_output_dataset(command: list[str], source: Path) -> None:
        output = Path(command[command.index("--output") + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / "dataset.json").write_text(source.read_text())

    @staticmethod
    def write_browser_observations(command: list[str]) -> None:
        output = Path(command[command.index("--output") + 1])
        case_ids = command[command.index("--case-ids") + 1].split(",")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({case_id: {
            "case_id": case_id,
            "status": "success",
            "completion": {"satisfied": True},
        } for case_id in case_ids}))

    def test_builds_mandatory_lifecycle_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            dataset.write_text(json.dumps({"cases": [{
                "id": "javascript-spa", "track": "web", "task_kind": "body",
                "ground_truth_main_content": "reference",
                "source_verification": {"reference_mode": "live_exact",
                    "method": "Independent visible browser DOM; complete declared scope"
                },
            }]}))
            output, work = root / "run", root / "work"
            config = root / "instance-provider.json"
            config.write_text('{"providers": {"p": {"type": "fixture"}}}')
            original_config = config.read_bytes()

            def completed(stage, arguments):
                command = [stage, *arguments]
                script = stage_name(command)
                if script == "source_capture.main":
                    self.write_capture(Path(command[2]), "javascript-spa")
                if script == "browser_observations.main":
                    self.write_browser_observations(command)
                if script == "benchmark_runner.main":
                    self.write_output_dataset(command, dataset)
                if stage_name(command) == "reference_freeze.postcheck_main":
                    (work / "post-captures").mkdir(parents=True, exist_ok=True)
                    (work / "post-captures/observations.json").write_text("[]\n")

            with patch("harness.web_fetch.certification.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(output),
                      "--work-root", str(work), "--providers", "p",
                      "--case-ids", "javascript-spa", "--browser-binary", "/chrome",
                      "--provider-config", str(config)])
            benchmark = next(stage_command(call) for call in invoked.call_args_list
                             if stage_name(stage_command(call)) == 'benchmark_runner.main')
            self.assertEqual(benchmark[benchmark.index('--provider-config') + 1], str(config))
            self.assertEqual(config.read_bytes(), original_config)
            scripts = [stage_name(stage_command(call)) for call in invoked.call_args_list]
            self.assertEqual(scripts, [
                "source_capture.main", "browser_observations.main",
                "reference_freeze.main", "benchmark_runner.main",
                "source_capture.main",
                "browser_observations.main", "reference_freeze.postcheck_main",
                "evidence.finalize_main"])

    def test_requires_explicit_browser_for_rendered_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            dataset.write_text(json.dumps({"cases": [{
                "id": "javascript-spa", "track": "web", "task_kind": "body",
                "ground_truth_main_content": "reference",
                "source_verification": {"reference_mode": "live_exact",
                    "method": "Independent visible browser DOM; complete declared scope"
                },
            }]}))
            def completed(stage, arguments):
                command = [stage, *arguments]
                if stage_name(command) == "source_capture.main":
                    self.write_capture(Path(command[2]), "javascript-spa")
            with self.assertRaises(SystemExit), patch(
                    "harness.web_fetch.certification.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(root / "run"),
                      "--work-root", str(root / "work"), "--providers", "p",
                      "--case-ids", "javascript-spa"])
            scripts = [stage_name(stage_command(call)) for call in invoked.call_args_list]
            self.assertEqual(scripts, ["source_capture.main"])

    def test_forwards_explicit_reference_promotion_to_freeze_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);dataset=root/'dataset.json';promotion=root/'promotion.json'
            dataset.write_text(json.dumps({'cases':[{'id':'stable'}]}))
            promotion.write_text('{}')
            output,work=root/'run',root/'work'
            def completed(stage, arguments):
                command = [stage, *arguments]
                script=stage_name(command)
                if script=='source_capture.main':
                    self.write_capture(Path(command[2]),'stable')
                if script == 'benchmark_runner.main':
                    self.write_output_dataset(command, dataset)
                if stage_name(command)=='reference_freeze.postcheck_main':
                    (work/'post-captures').mkdir(parents=True, exist_ok=True)
                    (work/'post-captures/observations.json').write_text('[]\n')
            with patch('harness.web_fetch.certification.run',side_effect=completed) as invoked:
                main(['--dataset',str(dataset),'--output',str(output),'--work-root',str(work),
                      '--providers','p','--case-ids','stable',
                      '--reference-promotion',str(promotion)])
            commands=[stage_command(call) for call in invoked.call_args_list]
            freeze=next(command for command in commands
                        if stage_name(command)=='reference_freeze.main')
            benchmark=next(command for command in commands
                           if stage_name(command)=='benchmark_runner.main')
            self.assertIn(str(promotion),freeze)
            self.assertNotIn(str(promotion),benchmark)

    def test_http_capture_failure_uses_browser_for_any_case_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            dataset.write_text(json.dumps({"cases": [{
                "id": "previously-unknown-browser-fallback", "track": "web",
                "task_kind": "body", "ground_truth_main_content": "reference",
                "source_verification": {"reference_mode": "live_exact",
                    "method": "Independent HTTP response and reviewed article selector",
                    "selector": "main",
                },
            }]}))
            output, work = root / "run", root / "work"

            def completed(stage, arguments):
                command = [stage, *arguments]
                script = stage_name(command)
                if script == "source_capture.main":
                    self.write_capture(
                        Path(command[2]), "previously-unknown-browser-fallback",
                        status=403, text="")
                elif script == "browser_observations.main":
                    self.write_browser_observations(command)
                elif script == "benchmark_runner.main":
                    self.write_output_dataset(command, dataset)
                elif script == "reference_freeze.postcheck_main":
                    (work / "post-captures").mkdir(parents=True, exist_ok=True)
                    (work / "post-captures/observations.json").write_text("[]\n")

            with patch("harness.web_fetch.certification.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(output),
                      "--work-root", str(work), "--providers", "p",
                      "--case-ids", "previously-unknown-browser-fallback",
                      "--browser-binary", "/chrome"])
            commands = [stage_command(call) for call in invoked.call_args_list]
            browser_commands = [command for command in commands
                                if stage_name(command) ==
                                "browser_observations.main"]
            self.assertEqual(len(browser_commands), 2)
            self.assertTrue(all(
                "previously-unknown-browser-fallback" in command
                for command in browser_commands))
            freeze = next(command for command in commands
                          if stage_name(command) == "reference_freeze.main")
            self.assertIn("--browser-observations", freeze)
            benchmark_index = next(
                index for index, command in enumerate(commands)
                if stage_name(command) == "benchmark_runner.main")
            freeze_index = next(
                index for index, command in enumerate(commands)
                if stage_name(command) == "reference_freeze.main")
            self.assertLess(freeze_index, benchmark_index)

    def test_post_http_failure_adds_browser_fallback_not_selected_pre_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            case_id = "http-became-unavailable"
            dataset.write_text(json.dumps({"cases": [{
                "id": case_id, "track": "web", "task_kind": "body",
                "ground_truth_main_content": "reference",
                "source_verification": {"reference_mode": "live_exact",
                    "method": "Independent HTTP response and reviewed article selector",
                    "selector": "main",
                },
            }]}))
            output, work = root / "run", root / "work"

            def completed(stage, arguments):
                command = [stage, *arguments]
                script = stage_name(command)
                if script == "source_capture.main":
                    is_post = Path(command[2]).name == "post-captures"
                    self.write_capture(Path(command[2]), case_id,
                                       status=403 if is_post else 200,
                                       text="" if is_post else "reference")
                elif script == "browser_observations.main":
                    self.write_browser_observations(command)
                elif script == "benchmark_runner.main":
                    self.write_output_dataset(command, dataset)
                elif script == "reference_freeze.postcheck_main":
                    (work / "post-captures/observations.json").write_text("[]\n")

            with patch("harness.web_fetch.certification.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(output),
                      "--work-root", str(work), "--providers", "p",
                      "--case-ids", case_id, "--browser-binary", "/chrome"])

            commands = [stage_command(call) for call in invoked.call_args_list]
            browsers = [command for command in commands
                        if stage_name(command) ==
                        "browser_observations.main"]
            self.assertEqual(len(browsers), 1)
            benchmark_index = next(i for i, command in enumerate(commands)
                                   if stage_name(command) == "benchmark_runner.main")
            self.assertGreater(commands.index(browsers[0]), benchmark_index)
            self.assertIn(case_id, browsers[0])
            postcheck = next(command for command in commands
                             if stage_name(command) == "reference_freeze.postcheck_main")
            self.assertIn("--resume", postcheck)
            self.assertIn("--browser-observations", postcheck)

    def test_http_capture_failure_without_browser_stops_before_provider_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            dataset.write_text(json.dumps({"cases": [{
                "id": "http-unavailable", "track": "web", "task_kind": "body",
                "ground_truth_main_content": "reference",
                "source_verification": {"reference_mode": "live_exact",
                    "method": "Independent HTTP response and reviewed article selector"
                },
            }]}))
            work = root / "work"

            def completed(stage, arguments):
                command = [stage, *arguments]
                if stage_name(command) == "source_capture.main":
                    self.write_capture(Path(command[2]), "http-unavailable",
                                       status=503, text="")

            with self.assertRaises(SystemExit), patch(
                    "harness.web_fetch.certification.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(root / "run"),
                      "--work-root", str(work), "--providers", "p",
                      "--case-ids", "http-unavailable"])
            scripts = [stage_name(stage_command(call)) for call in invoked.call_args_list]
            self.assertEqual(scripts, ["source_capture.main"])
            self.assertNotIn("benchmark_runner.main", scripts)

    def test_failed_pre_browser_observation_stops_before_freeze_and_provider_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            case_id = "browser-required"
            dataset.write_text(json.dumps({"cases": [{
                "id": case_id, "url": "https://example.com/",
                "track": "web", "task_kind": "body",
                "ground_truth_main_content": "reference",
                "source_verification": {"reference_mode": "live_exact",
                    "method": "Independent rendered browser DOM; complete declared scope"
                },
            }]}))

            def completed(stage, arguments):
                command = [stage, *arguments]
                script = stage_name(command)
                if script == "source_capture.main":
                    self.write_capture(Path(command[2]), case_id)
                elif script == "browser_observations.main":
                    output = Path(command[command.index("--output") + 1])
                    output.write_text(json.dumps({case_id: {
                        "case_id": case_id,
                        "status": "failure",
                        "completion": {"satisfied": False},
                    }}))

            with self.assertRaises(SystemExit), patch(
                    "harness.web_fetch.certification.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(root / "run"),
                      "--work-root", str(root / "work"), "--providers", "p",
                      "--case-ids", case_id, "--browser-binary", "/chrome"])

            scripts = [stage_name(stage_command(call)) for call in invoked.call_args_list]
            self.assertEqual(scripts, [
                "source_capture.main",
                "browser_observations.main",
            ])
            self.assertNotIn("reference_freeze.main", scripts)
            self.assertNotIn("benchmark_runner.main", scripts)

    def test_failed_post_browser_observation_cannot_reach_lifecycle_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            case_id = "browser-required"
            dataset.write_text(json.dumps({"cases": [{
                "id": case_id, "url": "https://example.com/",
                "track": "web", "task_kind": "body",
                "ground_truth_main_content": "reference",
                "source_verification": {"reference_mode": "live_exact",
                    "method": "Independent rendered browser DOM; complete declared scope"
                },
            }]}))

            def completed(stage, arguments):
                command = [stage, *arguments]
                script = stage_name(command)
                if script == "source_capture.main":
                    self.write_capture(Path(command[2]), case_id)
                elif script == "browser_observations.main":
                    output = Path(command[command.index("--output") + 1])
                    if output.name == "pre-browser.json":
                        self.write_browser_observations(command)
                    else:
                        output.write_text(json.dumps({case_id: {
                            "case_id": case_id,
                            "status": "failure",
                            "completion": {"satisfied": False},
                        }}))
                elif script == "benchmark_runner.main":
                    self.write_output_dataset(command, dataset)

            with self.assertRaises(SystemExit), patch(
                    "harness.web_fetch.certification.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(root / "run"),
                      "--work-root", str(root / "work"), "--providers", "p",
                      "--case-ids", case_id, "--browser-binary", "/chrome"])

            scripts = [stage_name(stage_command(call)) for call in invoked.call_args_list]
            self.assertIn("benchmark_runner.main", scripts)
            self.assertEqual(scripts[-1], "browser_observations.main")
            self.assertNotIn("reference_freeze.postcheck_main", scripts)
            self.assertNotIn("evidence.finalize_main", scripts)


if __name__ == "__main__":
    unittest.main()


def test_explicit_browser_reference_route_is_used_even_with_usable_http_capture(tmp_path):
    case = {
        "id": "explicit-route",
        "track": "web",
        "task_kind": "body",
        "ground_truth_main_content": "Required fact",
        "required_facts": [{"alternatives": ["Required fact"]}],
        "source_verification": {"reference_mode": "live_exact", "observation_route": "browser"},
    }
    (tmp_path / case["id"]).mkdir()
    (tmp_path / case["id"] / "reference.txt").write_text("Required fact")
    (tmp_path / "audit.json").write_text(json.dumps([{"case_id": case["id"], "status": 200}]))
    assert browser_observation_cases({"cases": [case]}, tmp_path, [case["id"]]) == [case["id"]]


def test_conflicting_http_route_and_browser_provenance_is_rejected(tmp_path):
    import pytest

    case = {
        "id": "conflicting-route",
        "track": "web",
        "task_kind": "body",
        "ground_truth_main_content": "Required fact",
        "required_facts": [{"alternatives": ["Required fact"]}],
        "source_verification": {
            "reference_mode": "live_exact",
            "observation_route": "http",
            "method": "Independent rendered browser DOM; complete declared scope",
        },
    }
    (tmp_path / "audit.json").write_text(json.dumps([{"case_id": case["id"], "status": 200}]))
    with pytest.raises(ValueError, match="contradicts browser reference provenance"):
        browser_observation_cases({"cases": [case]}, tmp_path, [case["id"]])


def test_explicit_empty_arguments_do_not_dispatch_an_ambient_subcommand(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["caller", "risk-control", "--validate-only"])
    with patch("harness.web_fetch.risk_control.main") as invoked:
        with pytest.raises(SystemExit) as error:
            main([])
    assert error.value.code == 2
    invoked.assert_not_called()


def test_invalid_selected_reference_route_stops_before_any_capture(tmp_path):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps({"cases": [{"id": "one", "track": "web", "task_kind": "body", "ground_truth_main_content": "Required fact", "source_verification": {"reference_mode": "live_exact", "observation_route": "http", "method": "Independent rendered browser DOM; complete declared scope"}}]}))
    with patch("harness.web_fetch.certification.run") as invoked:
        with pytest.raises(ValueError, match="contradicts browser reference provenance"):
            main(["--dataset", str(dataset), "--case-ids", "one", "--providers", "p", "--output", str(tmp_path / "output"), "--work-root", str(tmp_path / "work")])
    invoked.assert_not_called()
    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "work").exists()


@pytest.mark.parametrize(
    "command",
    [
        ["run_certified_benchmark.py"],
        ["run_certified_benchmark.py", "risk-control"],
        ["run_certified_benchmark.py", "webmainbench-correction"],
        ["run_certified_benchmark.py", "webmainbench-full"],
        ["run_certified_benchmark.py", "wcxb"],
        ["run_certified_benchmark.py", "wcxb-latency"],
        ["run_risk_control_moli.py"],
        ["certify_webmainbench_case_correction.py"],
        ["correct_webmainbench_teds.py"],
    ],
)
def test_certification_commands_have_help_without_evidence_or_provider_calls(command, tmp_path):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    result = subprocess.run(
        [sys.executable, str(scripts / command[0]), *command[1:], "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_complete_certification_uses_caller_inputs_outside_checkout(tmp_path):
    from tests.package_smoke import verify_certified_cohort
    verify_certified_cohort(tmp_path)
