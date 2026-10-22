"""Collection scenarios: missing sources, empty billing, and retained failures."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_search.report import load_run
from harness.web_search.runner import run


class CollectionScenarios(unittest.TestCase):
    def test_resume_retains_complete_cases_and_recollects_whole_interrupted_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            profile = json.loads(p.read_text())
            profile["cases"].append(dict(profile["cases"][0], id="second-case"))
            profile["protocol"]["retry"] = True
            p.write_text(json.dumps(profile))
            calls = []
            recovered = False

            def http(url, body=None, header=None, timeout=20):
                receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error="")
                if body is None:
                    return receipt, b"reference " * 20
                calls.append(url)
                if not recovered and len(calls) > 2:
                    return dict(receipt, http_status=402), b'{"error":"Insufficient Balance"}'
                return receipt, b'{"results":[{"url":"https://example.org/a","title":"Fixture"}]}'

            with (
                patch("harness.web_search.runner.http_call", side_effect=http),
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY="test", DEEPSEEK_API_KEY="test", BOCHA_API_KEY="test"),
            ):
                with self.assertRaisesRegex(ValueError, "Provider account error"):
                    run(p, root / "run", certified=True)
                original = (root / "run/results.jsonl").read_bytes()
                # A process can stop partway through the final row write.
                with (root / "run/results.jsonl").open("ab") as stream:
                    stream.write(b'{"id":"incomplete')
                recovered = True
                run(p, root / "run", resume=True, certified=True)
            _, rows, receipt = load_run(root / "run")
            self.assertEqual(len(calls), 10)  # four interrupted calls, then both three-method cases
            self.assertEqual(len(rows), 6)
            self.assertTrue(receipt["certified"])
            self.assertEqual((root / "run/resume-1/previous-results.jsonl").read_bytes(),
                             original + b'{"id":"incomplete')
            self.assertEqual(len(list((root / "run/resume-1/calls").glob("*.json"))), 4)
            contract = json.loads((root / "run/resume-1/contract.json").read_text())
            self.assertEqual(
                contract["recollect_case_ids"], ["ds-price-zh", "second-case"]
            )
            archived = next((root / "run/resume-1/calls").glob("*.json"))
            archived.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                load_run(root / "run")

    def test_account_errors_stop_the_cohort_after_declared_retry(self):
        for status in (401, 402):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                p = self.profile(root)
                profile = json.loads(p.read_text())
                profile["protocol"]["retry"] = True
                p.write_text(json.dumps(profile))
                calls = []

                def http(url, body=None, header=None, timeout=20):
                    receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error="")
                    if body is None:
                        return receipt, b"reference " * 20
                    calls.append(url)
                    return dict(receipt, http_status=status), b'{"error":{"message":"account unavailable"}}'

                with (
                    patch("harness.web_search.runner.http_call", side_effect=http),
                    patch("harness.web_search.runner.load_runtime_env"),
                    patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY="test", DEEPSEEK_API_KEY="test", BOCHA_API_KEY="test"),
                ):
                    with self.assertRaisesRegex(ValueError, "Provider account error"):
                        run(p, root / "run", certified=True)
                self.assertEqual(len(calls), 2)
                self.assertEqual(len(set(calls)), 1)  # no spending on the next participant
                rows = (root / "run/results.jsonl").read_text().splitlines()
                self.assertEqual(len(rows), 1)
                self.assertEqual(len(json.loads(rows[0])["attempts"]), 2)
                self.assertTrue((root / "run/interruption.json").exists())
                self.assertFalse((root / "run/receipt.json").exists())

    def profile(self, root):
        from harness.web_search.profiles import load_profile
        d = load_profile()
        d["cases"] = d["cases"][:1]
        d["protocol"].update(rounds=1, interval_s=0)
        d["protocol"].pop("reference_scope", None)
        d.pop("reference_checks", None)
        p = root / "input.json"
        p.write_text(json.dumps(d))
        return p

    def test_published_label_snapshot_requires_the_frozen_hash(self):
        from harness.web_search.runner import reference_available
        from harness.web_search.core import digest
        url = "https://example.org/pinned-labels.csv"
        raw = b"query,answer,url\n" * 20
        profile = {"reference_artifacts": {url: {"sha256": digest(raw)}}}
        receipt = {"http_status": 200, "transport_exit": 0}
        self.assertTrue(reference_available(profile, url, receipt, raw))
        self.assertFalse(reference_available(profile, url, receipt, raw + b"changed"))
        self.assertFalse(reference_available(profile, url, dict(receipt, transport_exit=28), raw))

    def test_failure_is_retained_after_the_single_declared_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            order = []

            def http(url, body=None, header=None, timeout=20):
                if body is None:
                    return dict(
                        http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""
                    ), b"reference " * 20
                order.append(url)
                return dict(
                    http_status=503 if "lexmount" in url else 200,
                    elapsed_ms=20,
                    transport_exit=0,
                    transport_error="",
                ), (b'{"error":"Unavailable"}' if "lexmount" in url else b'{"results":[]}')

            with (
                patch("harness.web_search.runner.http_call", side_effect=http),
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY="test", DEEPSEEK_API_KEY="test", BOCHA_API_KEY="test"),
            ):
                run(p, root / "run", certified=True)
            _, rows, receipt = load_run(root / "run")
            self.assertEqual(len(order), 4)
            self.assertEqual(len(rows), 3)
            self.assertEqual(len(rows[0]["attempts"]), 2)
            self.assertEqual(rows[1]["cost"], 0)
            self.assertTrue(rows[0]["error"])
            self.assertTrue(receipt["certified"])
            ref = next((root / "run/references").glob("*.bin"))
            ref.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                load_run(root / "run")

    def test_failed_reference_blocks_calls_before_spending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            with (
                patch(
                    "harness.web_search.runner.http_call",
                    return_value=(
                        dict(http_status=404, elapsed_ms=1, transport_exit=0, transport_error=""),
                        b"absent",
                    ),
                ) as call,
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY="test", DEEPSEEK_API_KEY="test", BOCHA_API_KEY="test"),
            ):
                with self.assertRaises(ValueError):
                    run(p, root / "run", certified=True)
            self.assertEqual(call.call_count, 1)

    def test_failed_call_retries_once_and_only_final_response_is_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            profile = json.loads(p.read_text())
            profile["methods"] = [profile["methods"][1]]
            profile["protocol"]["retry"] = True
            p.write_text(json.dumps(profile))
            calls = []

            def http(url, body=None, header=None, timeout=20):
                if body is None:
                    return dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""), b"reference " * 20
                calls.append(url)
                if len(calls) == 1:
                    return dict(http_status=503, elapsed_ms=20, transport_exit=0, transport_error=""), b'{"error":"busy"}'
                return dict(http_status=200, elapsed_ms=30, transport_exit=0, transport_error=""), b'{"results":[{"url":"https://example.org/p"}]}'

            with (
                patch("harness.web_search.runner.http_call", side_effect=http),
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, DEEPSEEK_API_KEY="test"),
            ):
                run(p, root / "run", certified=True)
            _, rows, _ = load_run(root / "run")
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(rows[0]["attempts"]), 2)
            self.assertEqual(rows[0]["results"][0]["url"], "https://example.org/p")
            self.assertGreaterEqual(rows[0]["elapsed_ms"], 50)
            first = root / "run" / rows[0]["attempts"][0]["evidence"]["path"]
            first.write_text("changed")
            with self.assertRaises(ValueError):
                load_run(root / "run")

    def test_valid_empty_response_is_a_result_not_an_api_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            profile = json.loads(p.read_text())
            profile["methods"] = [profile["methods"][1]]
            profile["protocol"]["retry"] = True
            p.write_text(json.dumps(profile))
            calls = []

            def http(url, body=None, header=None, timeout=20):
                if body is None:
                    return dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""), b"reference " * 20
                calls.append(url)
                return dict(http_status=200, elapsed_ms=20, transport_exit=0, transport_error=""), b'{"results":[]}'

            with (
                patch("harness.web_search.runner.http_call", side_effect=http),
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, DEEPSEEK_API_KEY="test"),
            ):
                run(p, root / "run", certified=True)
            _, rows, _ = load_run(root / "run")
            self.assertEqual(len(calls), 1)
            self.assertEqual(rows[0]["results"], [])


if __name__ == "__main__":
    unittest.main()


class ReferenceCaptureScenarios(unittest.TestCase):
    def test_identity_check_rejects_an_unrelated_http_200_page(self):
        from harness.web_search.runner import capture_references

        profile = {
            "protocol": {"reference_scope": "identity"},
            "cases": [{"anchors": ["https://example.org/paper"]}],
            "reference_checks": {
                "https://example.org/paper": {
                    "url": "https://example.org/paper",
                    "contains": ["Distinct paper title"],
                }
            },
        }
        receipt = dict(http_status=200, transport_exit=0)
        for raw, expected in [
            (b"login screen " * 30, False),
            (b"Distinct paper title " * 30, True),
        ]:
            with (
                tempfile.TemporaryDirectory() as tmp,
                patch("harness.web_search.runner.http_call", return_value=(receipt, raw)),
            ):
                refs = capture_references(profile, Path(tmp), "before")
                self.assertEqual(refs["https://example.org/paper"]["available"], expected)

    def test_page_chrome_changes_do_not_change_verified_identity(self):
        from harness.web_search.runner import reference_changes

        before = {"u": {"available": True, "sha256": "a"}}
        after = {"u": {"available": True, "sha256": "b"}}
        self.assertEqual(
            reference_changes({"protocol": {"reference_scope": "identity"}}, before, after), []
        )
        self.assertEqual(reference_changes({"protocol": {}}, before, after), ["u"])
        after["u"]["available"] = False
        self.assertEqual(
            reference_changes({"protocol": {"reference_scope": "identity"}}, before, after), ["u"]
        )

    def test_http_200_with_incomplete_transfer_is_not_qualified(self):
        from harness.web_search.runner import capture_references

        profile = {"protocol": {}, "cases": [{"anchors": ["https://example.org/"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            receipt = dict(
                http_status=200, elapsed_ms=20000, transport_exit=28, transport_error="timeout"
            )
            with patch(
                "harness.web_search.runner.http_call", return_value=(receipt, b"partial " * 50)
            ):
                refs = capture_references(profile, Path(tmp), "before")
            self.assertFalse(refs["https://example.org/"]["available"])


class TransportAndResumeBoundaries(unittest.TestCase):
    def test_subprocess_timeout_is_a_transport_result_with_partial_bytes(self):
        import subprocess
        from harness.web_search.runner import http_call
        def timeout(command, **kwargs):
            Path(command[command.index('--output') + 1]).write_bytes(b'partial\r\n')
            raise subprocess.TimeoutExpired(command, 25)
        with patch('harness.web_search.runner.subprocess.run', side_effect=timeout):
            receipt, raw = http_call('https://provider.test', {})
        self.assertEqual(receipt['transport_exit'], 28)
        self.assertEqual(receipt['http_status'], 0)
        self.assertEqual(raw, b'partial\r\n')

    def test_resume_rejects_live_source_drift_before_archiving_results(self):
        from harness.web_search.runner import prepare_resume
        from harness.web_search.core import digest
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = b'{"cases":[],"methods":[],"protocol":{"rounds":1}}'
            path = root / 'input.json'; path.write_bytes(profile)
            (root/'profile.json').write_bytes(profile)
            (root/'run-contract.json').write_text(json.dumps({'profile_sha256':digest(profile),'implementation':{}}))
            (root/'results.jsonl').write_bytes(b'')
            with self.assertRaisesRegex(ValueError, 'same live'):
                prepare_resume(path,root)
            self.assertFalse(list(root.glob('resume-*')))

    def test_complete_bad_jsonl_line_is_rejected_but_interrupted_tail_is_accepted(self):
        from harness.record_io import read_completed_records
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'rows.jsonl'
            path.write_bytes(b'{"id":"complete"}\n{"partial"')
            self.assertEqual(read_completed_records(path), [{'id':'complete'}])
            path.write_bytes(b'{"id":"complete"}\n{"partial"\n')
            with self.assertRaises(ValueError):
                read_completed_records(path)


def test_subprocess_timeout_retries_and_finishes_the_complete_matrix():
    from types import SimpleNamespace
    import subprocess
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        profile_path = CollectionScenarios().profile(root)
        profile = json.loads(profile_path.read_text())
        profile['methods'] = [profile['methods'][1]]
        profile['protocol']['retry'] = True
        profile_path.write_text(json.dumps(profile))
        provider_calls = []
        def subprocess_call(command, **kwargs):
            output = Path(command[command.index('--output') + 1])
            if '--data-binary' not in command:
                output.write_bytes(b'reference ' * 20)
            else:
                provider_calls.append(command)
                if len(provider_calls) == 1:
                    output.write_bytes(b'partial upstream response')
                    raise subprocess.TimeoutExpired(command, 25)
                output.write_bytes(b'{"results":[{"url":"https://example.org/success"}]}')
            return SimpleNamespace(stdout='200 0.001 0.002 0 0 0 200', stderr='', returncode=0)
        with (patch('harness.web_search.runner.subprocess.run',side_effect=subprocess_call),
              patch('harness.web_search.runner.load_runtime_env'),
              patch.dict(os.environ, DEEPSEEK_API_KEY='fixture')):
            run(profile_path,root/'run', certified=True)
        _, rows, receipt = load_run(root/'run')
        assert len(rows) == 1 and receipt['certified']
        assert len(provider_calls) == 2
        assert rows[0]['attempts'][0]['transport_exit'] == 28
        assert rows[0]['attempts'][1]['transport_exit'] == 0
        assert rows[0]['results'][0]['url'] == 'https://example.org/success'
        assert (root/'run'/rows[0]['attempts'][0]['evidence']['path']).read_bytes() == b'partial upstream response'


def test_direct_diagnostic_collection_remains_uncertified(tmp_path):
    profile_path = CollectionScenarios().profile(tmp_path)
    profile = json.loads(profile_path.read_text())
    profile['methods'] = [profile['methods'][1]]
    profile_path.write_text(json.dumps(profile))
    calls = []
    def http(url, body=None, **kwargs):
        calls.append(body is not None)
        return {'http_status':200,'elapsed_ms':1,'transport_exit':0,'transport_error':''}, (
            b'reference '*20 if body is None else b'{"results":[]}'
        )
    with (patch('harness.web_search.runner.http_call',side_effect=lambda url,body=None,*args,**kwargs:http(url,body)),
          patch('harness.web_search.runner.load_runtime_env'),
          patch.dict(os.environ,DEEPSEEK_API_KEY='fixture')):
        from harness.web_search.runner import main as direct_main
        direct_main(['--profile',str(profile_path),'--output',str(tmp_path/'run'),'--diagnostic'])
    receipt = json.loads((tmp_path/'run/receipt.json').read_text())
    assert receipt['complete'] is True
    assert receipt['certified'] is False
    assert json.loads((tmp_path/'run/run-contract.json').read_text())['diagnostic'] is True
    assert calls.count(True) == 1


def test_certified_entry_rejects_unqualified_sources_before_provider_calls(tmp_path):
    from harness.web_fetch.certification import main as certified_main
    profile_path = CollectionScenarios().profile(tmp_path)
    calls=[]
    def http(url,body=None,*args,**kwargs):
        calls.append(body is not None)
        return {'http_status':404,'elapsed_ms':1,'transport_exit':0,'transport_error':''}, b'not found'
    import pytest
    with (patch('harness.web_search.runner.http_call',side_effect=http),
          patch('harness.web_search.runner.load_runtime_env'),
          patch.dict(os.environ,LEXMOUNT_SEARCH_API_KEY='fixture',DEEPSEEK_API_KEY='fixture',BOCHA_API_KEY='fixture')):
        with pytest.raises(SystemExit) as failure:
            certified_main(['web-search','--profile',str(profile_path),'--output',str(tmp_path/'run')])
    assert failure.value.code == 2
    assert not any(calls)
    assert not (tmp_path/'run/receipt.json').exists()


def test_resume_reference_drift_preserves_every_saved_result_and_call(tmp_path):
    import pytest
    profile = CollectionScenarios().profile(tmp_path)
    output = tmp_path / "run"
    reference = b"reference " * 20
    def http(url, body=None, *args, **kwargs):
        if body is None:
            return dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""), reference
        return dict(http_status=402, elapsed_ms=1, transport_exit=0, transport_error=""), b'{"error":"account"}'
    with patch("harness.web_search.runner.http_call", side_effect=http), patch("harness.web_search.runner.load_runtime_env"), patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY="fixture", DEEPSEEK_API_KEY="fixture", BOCHA_API_KEY="fixture"):
        with pytest.raises(ValueError, match="Provider account error"):
            run(profile, output, certified=True)
        before = {p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob("*") if p.is_file()}
        reference = b"changed source " * 20
        with pytest.raises(ValueError, match="Reference recheck failed"):
            run(profile, output, resume=True, certified=True)
        assert not list(output.glob("resume-*"))
        for name, raw in before.items():
            assert (output / name).read_bytes() == raw
