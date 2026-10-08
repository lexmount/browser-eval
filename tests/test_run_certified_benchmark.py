from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.run_certified_benchmark import main


class CertifiedBenchmarkTests(unittest.TestCase):
    def test_diagnostic_execution_cannot_be_assembled_or_rendered_as_certified(self):
        from harness.web_fetch import benchmark_runner as run_benchmark
        from harness.web_fetch import reference_freeze as freeze_headless_references
        from harness.web_fetch.assembly import assemble
        from harness.web_fetch.reference_lifecycle import (
            ReferenceLifecycleError,
            require_verified_reference_checks,
        )
        from harness.web_fetch.report_build import build_report
        from scripts import postcheck_headless
        from scripts.finalize_benchmark_run import finalize_aggregate
        for mode in ('declared', 'native'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
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

                def execute(command):
                    script=Path(command[1]).name
                    if script=='collect_source_evidence.py':
                        self.write_capture(Path(command[3]),'stable',text='Required label')
                    elif script=='freeze_headless_references.py':
                        with patch.object(freeze_headless_references,'ROOT',root):
                            freeze_headless_references.main(command[2:])
                    elif script=='run_benchmark.py':
                        with patch.object(run_benchmark,'load_runtime_env'):
                            run_benchmark.main(command[2:])
                    elif script=='postcheck_headless.py':
                        with patch('sys.argv',command[1:]):postcheck_headless.main()
                    elif script=='finalize_benchmark_run.py':
                        finalize_aggregate(output,root/'work/post-captures/observations.json')
                    else:self.fail('Unexpected external stage: '+script)

                with patch('scripts.run_certified_benchmark.run',side_effect=execute):
                    main(['--dataset',str(dataset),'--output',str(output),
                          '--work-root',str(root/'work'),'--providers','fixture',
                          '--provider-config',str(config),'--case-ids','stable',
                          '--retrieval-mode',mode,'--diagnostic'])
                (output/'sources').mkdir(exist_ok=True)
                (output/'list-references').mkdir(exist_ok=True)
                with self.assertRaisesRegex(ReferenceLifecycleError,'[Dd]iagnostic'):
                    assemble(output,root/'certificates',[],root/'formal')
                self.assertFalse((root/'formal').exists())
                saved=json.loads((output/'dataset.json').read_text())
                self.assertEqual(saved['comparison']['evidence_state'],'diagnostic')
                self.assertEqual(saved['comparison']['unqualified_reference_cases'],['stable'])
                batches=json.loads((output/'execution.json').read_text())['batches']
                self.assertTrue(all(b['evidence_state']=='diagnostic' and
                    b['unqualified_reference_cases']==['stable'] for b in batches))
                # Dropping dataset display metadata cannot erase execution provenance.
                saved['comparison']={}
                with self.assertRaisesRegex(ReferenceLifecycleError,'[Dd]iagnostic'):
                    require_verified_reference_checks(batches,cases=saved)
                with self.assertRaisesRegex(ReferenceLifecycleError,'[Dd]iagnostic'):
                    build_report(output,root/'formal-report')
                self.assertFalse((root/'formal-report').exists())
                self.assertEqual(dataset.read_bytes(),original)

    def test_stable_rules_alone_cannot_be_mistaken_for_live_source_certification(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);dataset=root/'dataset.json'
            dataset.write_text(json.dumps({'cases':[{'id':'stable','acceptance_contract':{
                'version':1,'schema':'body_text','minimum_records':0,
                'stable_text_anchors':[{'id':'a','alternatives':['Required label']} ]}}]}))
            arguments=['--dataset',str(dataset),'--output',str(root/'run'),
                       '--work-root',str(root/'work'),'--providers','p','--case-ids','stable']
            with patch('scripts.run_certified_benchmark.run') as invoked:
                with self.assertRaises(SystemExit):main(arguments)
            invoked.assert_not_called()
            def completed(command):
                script=Path(command[1]).name
                if script=='collect_source_evidence.py':self.write_capture(Path(command[3]),'stable')
                if script=='run_benchmark.py':self.write_output_dataset(command,dataset)
            with patch('scripts.run_certified_benchmark.run',side_effect=completed):
                main([*arguments,'--diagnostic'])
            self.assertEqual(json.loads(dataset.read_text())['cases'][0]['acceptance_contract']['stable_text_anchors'][0]['alternatives'],['Required label'])

    def test_dispatches_complete_webmainbench_certification(self) -> None:
        arguments = ["--output-root", "/tmp/run", "--source", "/tmp/source.jsonl"]
        with patch("harness.web_fetch.webmainbench_certification.main") as invoked:
            main(["webmainbench-full", *arguments])
        invoked.assert_called_once_with(arguments)

    def test_missing_dynamic_execution_contract_fails_before_external_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);dataset=root/'dataset.json'
            dataset.write_text(json.dumps({'cases':[{'id':'dynamic-page','track':'dynamic'}]}))
            with patch('scripts.run_certified_benchmark.run') as invoked:
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
                "source_verification": {
                    "method": "Independent visible browser DOM; complete declared scope"
                },
            }]}))
            output, work = root / "run", root / "work"
            config = root / "instance-provider.json"
            config.write_text('{"providers": {"p": {"type": "fixture"}}}')
            original_config = config.read_bytes()

            def completed(command):
                script = Path(command[1]).name
                if script == "collect_source_evidence.py":
                    self.write_capture(Path(command[3]), "javascript-spa")
                if script == "capture_headless_browser_observations.py":
                    self.write_browser_observations(command)
                if script == "run_benchmark.py":
                    self.write_output_dataset(command, dataset)
                if any(Path(value).name == "postcheck_headless.py" for value in command):
                    (work / "post-captures").mkdir(parents=True, exist_ok=True)
                    (work / "post-captures/observations.json").write_text("[]\n")

            with patch("scripts.run_certified_benchmark.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(output),
                      "--work-root", str(work), "--providers", "p",
                      "--case-ids", "javascript-spa", "--browser-binary", "/chrome",
                      "--provider-config", str(config)])
            benchmark = next(call.args[0] for call in invoked.call_args_list
                             if Path(call.args[0][1]).name == 'run_benchmark.py')
            self.assertEqual(benchmark[benchmark.index('--provider-config') + 1], str(config))
            self.assertEqual(config.read_bytes(), original_config)
            scripts = [Path(call.args[0][1]).name for call in invoked.call_args_list]
            self.assertEqual(scripts, [
                "collect_source_evidence.py", "capture_headless_browser_observations.py",
                "freeze_headless_references.py", "run_benchmark.py",
                "collect_source_evidence.py",
                "capture_headless_browser_observations.py", "postcheck_headless.py",
                "finalize_benchmark_run.py"])

    def test_requires_explicit_browser_for_rendered_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            dataset.write_text(json.dumps({"cases": [{
                "id": "javascript-spa", "track": "web", "task_kind": "body",
                "ground_truth_main_content": "reference",
                "source_verification": {
                    "method": "Independent visible browser DOM; complete declared scope"
                },
            }]}))
            def completed(command):
                if any(Path(value).name == "collect_source_evidence.py" for value in command):
                    self.write_capture(Path(command[3]), "javascript-spa")
            with self.assertRaises(SystemExit), patch(
                    "scripts.run_certified_benchmark.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(root / "run"),
                      "--work-root", str(root / "work"), "--providers", "p",
                      "--case-ids", "javascript-spa"])
            scripts = [Path(call.args[0][1]).name for call in invoked.call_args_list]
            self.assertEqual(scripts, ["collect_source_evidence.py"])

    def test_forwards_explicit_reference_promotion_to_freeze_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);dataset=root/'dataset.json';promotion=root/'promotion.json'
            dataset.write_text(json.dumps({'cases':[{'id':'stable'}]}))
            promotion.write_text('{}')
            output,work=root/'run',root/'work'
            def completed(command):
                script=Path(command[1]).name
                if script=='collect_source_evidence.py':
                    self.write_capture(Path(command[3]),'stable')
                if script == 'run_benchmark.py':
                    self.write_output_dataset(command, dataset)
                if Path(command[1]).name=='postcheck_headless.py':
                    (work/'post-captures').mkdir(parents=True, exist_ok=True)
                    (work/'post-captures/observations.json').write_text('[]\n')
            with patch('scripts.run_certified_benchmark.run',side_effect=completed) as invoked:
                main(['--dataset',str(dataset),'--output',str(output),'--work-root',str(work),
                      '--providers','p','--case-ids','stable',
                      '--reference-promotion',str(promotion)])
            commands=[call.args[0] for call in invoked.call_args_list]
            freeze=next(command for command in commands
                        if Path(command[1]).name=='freeze_headless_references.py')
            benchmark=next(command for command in commands
                           if Path(command[1]).name=='run_benchmark.py')
            self.assertIn(str(promotion),freeze)
            self.assertNotIn(str(promotion),benchmark)

    def test_http_capture_failure_uses_browser_for_any_case_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            dataset.write_text(json.dumps({"cases": [{
                "id": "previously-unknown-browser-fallback", "track": "web",
                "task_kind": "body", "ground_truth_main_content": "reference",
                "source_verification": {
                    "method": "Independent HTTP response and reviewed article selector",
                    "selector": "main",
                },
            }]}))
            output, work = root / "run", root / "work"

            def completed(command):
                script = Path(command[1]).name
                if script == "collect_source_evidence.py":
                    self.write_capture(
                        Path(command[3]), "previously-unknown-browser-fallback",
                        status=403, text="")
                elif script == "capture_headless_browser_observations.py":
                    self.write_browser_observations(command)
                elif script == "run_benchmark.py":
                    self.write_output_dataset(command, dataset)
                elif script == "postcheck_headless.py":
                    (work / "post-captures").mkdir(parents=True, exist_ok=True)
                    (work / "post-captures/observations.json").write_text("[]\n")

            with patch("scripts.run_certified_benchmark.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(output),
                      "--work-root", str(work), "--providers", "p",
                      "--case-ids", "previously-unknown-browser-fallback",
                      "--browser-binary", "/chrome"])
            commands = [call.args[0] for call in invoked.call_args_list]
            browser_commands = [command for command in commands
                                if Path(command[1]).name ==
                                "capture_headless_browser_observations.py"]
            self.assertEqual(len(browser_commands), 2)
            self.assertTrue(all(
                "previously-unknown-browser-fallback" in command
                for command in browser_commands))
            freeze = next(command for command in commands
                          if Path(command[1]).name == "freeze_headless_references.py")
            self.assertIn("--browser-observations", freeze)
            benchmark_index = next(
                index for index, command in enumerate(commands)
                if Path(command[1]).name == "run_benchmark.py")
            freeze_index = next(
                index for index, command in enumerate(commands)
                if Path(command[1]).name == "freeze_headless_references.py")
            self.assertLess(freeze_index, benchmark_index)

    def test_post_http_failure_adds_browser_fallback_not_selected_pre_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            case_id = "http-became-unavailable"
            dataset.write_text(json.dumps({"cases": [{
                "id": case_id, "track": "web", "task_kind": "body",
                "ground_truth_main_content": "reference",
                "source_verification": {
                    "method": "Independent HTTP response and reviewed article selector",
                    "selector": "main",
                },
            }]}))
            output, work = root / "run", root / "work"

            def completed(command):
                script = Path(command[1]).name
                if script == "collect_source_evidence.py":
                    is_post = Path(command[3]).name == "post-captures"
                    self.write_capture(Path(command[3]), case_id,
                                       status=403 if is_post else 200,
                                       text="" if is_post else "reference")
                elif script == "capture_headless_browser_observations.py":
                    self.write_browser_observations(command)
                elif script == "run_benchmark.py":
                    self.write_output_dataset(command, dataset)
                elif script == "postcheck_headless.py":
                    (work / "post-captures/observations.json").write_text("[]\n")

            with patch("scripts.run_certified_benchmark.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(output),
                      "--work-root", str(work), "--providers", "p",
                      "--case-ids", case_id, "--browser-binary", "/chrome"])

            commands = [call.args[0] for call in invoked.call_args_list]
            browsers = [command for command in commands
                        if Path(command[1]).name ==
                        "capture_headless_browser_observations.py"]
            self.assertEqual(len(browsers), 1)
            benchmark_index = next(i for i, command in enumerate(commands)
                                   if Path(command[1]).name == "run_benchmark.py")
            self.assertGreater(commands.index(browsers[0]), benchmark_index)
            self.assertIn(case_id, browsers[0])
            postcheck = next(command for command in commands
                             if Path(command[1]).name == "postcheck_headless.py")
            self.assertIn("--resume", postcheck)
            self.assertIn("--browser-observations", postcheck)

    def test_http_capture_failure_without_browser_stops_before_provider_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            dataset.write_text(json.dumps({"cases": [{
                "id": "http-unavailable", "track": "web", "task_kind": "body",
                "ground_truth_main_content": "reference",
                "source_verification": {
                    "method": "Independent HTTP response and reviewed article selector"
                },
            }]}))
            work = root / "work"

            def completed(command):
                if Path(command[1]).name == "collect_source_evidence.py":
                    self.write_capture(Path(command[3]), "http-unavailable",
                                       status=503, text="")

            with self.assertRaises(SystemExit), patch(
                    "scripts.run_certified_benchmark.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(root / "run"),
                      "--work-root", str(work), "--providers", "p",
                      "--case-ids", "http-unavailable"])
            scripts = [Path(call.args[0][1]).name for call in invoked.call_args_list]
            self.assertEqual(scripts, ["collect_source_evidence.py"])
            self.assertNotIn("run_benchmark.py", scripts)

    def test_failed_pre_browser_observation_stops_before_freeze_and_provider_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            case_id = "browser-required"
            dataset.write_text(json.dumps({"cases": [{
                "id": case_id, "url": "https://example.com/",
                "track": "web", "task_kind": "body",
                "ground_truth_main_content": "reference",
                "source_verification": {
                    "method": "Independent rendered browser DOM; complete declared scope"
                },
            }]}))

            def completed(command):
                script = Path(command[1]).name
                if script == "collect_source_evidence.py":
                    self.write_capture(Path(command[3]), case_id)
                elif script == "capture_headless_browser_observations.py":
                    output = Path(command[command.index("--output") + 1])
                    output.write_text(json.dumps({case_id: {
                        "case_id": case_id,
                        "status": "failure",
                        "completion": {"satisfied": False},
                    }}))

            with self.assertRaises(SystemExit), patch(
                    "scripts.run_certified_benchmark.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(root / "run"),
                      "--work-root", str(root / "work"), "--providers", "p",
                      "--case-ids", case_id, "--browser-binary", "/chrome"])

            scripts = [Path(call.args[0][1]).name for call in invoked.call_args_list]
            self.assertEqual(scripts, [
                "collect_source_evidence.py",
                "capture_headless_browser_observations.py",
            ])
            self.assertNotIn("freeze_headless_references.py", scripts)
            self.assertNotIn("run_benchmark.py", scripts)

    def test_failed_post_browser_observation_cannot_reach_lifecycle_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            case_id = "browser-required"
            dataset.write_text(json.dumps({"cases": [{
                "id": case_id, "url": "https://example.com/",
                "track": "web", "task_kind": "body",
                "ground_truth_main_content": "reference",
                "source_verification": {
                    "method": "Independent rendered browser DOM; complete declared scope"
                },
            }]}))

            def completed(command):
                script = Path(command[1]).name
                if script == "collect_source_evidence.py":
                    self.write_capture(Path(command[3]), case_id)
                elif script == "capture_headless_browser_observations.py":
                    output = Path(command[command.index("--output") + 1])
                    if output.name == "pre-browser.json":
                        self.write_browser_observations(command)
                    else:
                        output.write_text(json.dumps({case_id: {
                            "case_id": case_id,
                            "status": "failure",
                            "completion": {"satisfied": False},
                        }}))
                elif script == "run_benchmark.py":
                    self.write_output_dataset(command, dataset)

            with self.assertRaises(SystemExit), patch(
                    "scripts.run_certified_benchmark.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(root / "run"),
                      "--work-root", str(root / "work"), "--providers", "p",
                      "--case-ids", case_id, "--browser-binary", "/chrome"])

            scripts = [Path(call.args[0][1]).name for call in invoked.call_args_list]
            self.assertIn("run_benchmark.py", scripts)
            self.assertEqual(scripts[-1], "capture_headless_browser_observations.py")
            self.assertNotIn("postcheck_headless.py", scripts)
            self.assertNotIn("finalize_benchmark_run.py", scripts)


if __name__ == "__main__":
    unittest.main()
